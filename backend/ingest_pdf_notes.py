import asyncio
import re
import sys
import os

from sqlalchemy import select, func
from app.database import async_session_factory
from app.models import TariffRule, TariffEpoch
from app.core.llm import get_llm_provider

BATCH_SIZE = 20  # Commit every N chunks — prevents full-batch rollback
RATE_LIMIT_DELAY = 1.5  # Seconds between API calls to avoid 429


async def main():
    print("Starting PDF Notes Ingestion...")
    pdf_path = "data/ITC-HS 2022 Schedule 1 (Import Policy) PDF.pdf"

    if not os.path.exists(pdf_path):
        print(f"File not found: {pdf_path}")
        sys.exit(1)

    print("Extracting markdown from PDF (this might take a minute)...")
    try:
        import pymupdf4llm
        md_text = pymupdf4llm.to_markdown(pdf_path)
    except Exception as e:
        print(f"Failed to extract PDF: {e}")
        sys.exit(1)

    print(f"Extracted {len(md_text)} characters of markdown.")

    # Semantic chunking: split by markdown headings
    print("Chunking document...")
    chunks = re.split(r'\n(?=#+ )', md_text)
    chunks = [c.strip() for c in chunks if len(c.strip()) > 50]
    print(f"Created {len(chunks)} text chunks.")

    print("Initializing LLM provider for embeddings...")
    llm = get_llm_provider()

    async with async_session_factory() as session:
        # Get active epoch
        epoch_result = await session.execute(
            select(TariffEpoch).where(TariffEpoch.is_active == True)
        )
        epoch = epoch_result.scalar_one_or_none()
        if not epoch:
            print("No active TariffEpoch found! Run seed.py first.")
            sys.exit(1)

        # Check how many chunks are already ingested (for resume support)
        existing_count_result = await session.execute(
            select(func.count(TariffRule.id)).where(
                TariffRule.epoch_id == epoch.id,
                TariffRule.rule_type == "CLASSIFICATION_NOTE",
            )
        )
        existing_count = existing_count_result.scalar() or 0

        if existing_count > 0:
            print(f"Found {existing_count} existing note chunks. Clearing them for fresh ingestion...")
            from sqlalchemy import delete
            await session.execute(
                delete(TariffRule).where(
                    TariffRule.epoch_id == epoch.id,
                    TariffRule.rule_type == "CLASSIFICATION_NOTE",
                )
            )
            await session.commit()
            print("Cleared. Starting fresh ingestion.")

        print("Generating embeddings and inserting into DB...")

        inserted = 0
        failed = 0
        total_tokens = 0
        batch_buffer = []

        for i, chunk in enumerate(chunks):
            try:
                # Rate-limit: wait between every call to avoid 429
                if i > 0:
                    await asyncio.sleep(RATE_LIMIT_DELAY)

                # Extract chapter/section metadata from text
                chapter_match = re.search(r'(?i)chapter\s+(\d+)', chunk)
                chapter = chapter_match.group(1).zfill(2) if chapter_match else None

                section_match = re.search(r'(?i)section\s+([A-Z]+)', chunk)
                section = section_match.group(1) if section_match else None

                # Generate embedding (768-dim, enforced in llm.py)
                emb_res = await llm.embed(chunk)
                total_tokens += emb_res.tokens_used

                # Verify dimension before inserting
                emb_dim = len(emb_res.embedding)
                if emb_dim != 768:
                    print(f"  ⚠️ Chunk {i}: Got {emb_dim}-dim embedding, expected 768. Skipping.")
                    failed += 1
                    continue

                rule = TariffRule(
                    epoch_id=epoch.id,
                    hs_code=f"{chapter}00" if chapter else "0000",
                    section=section,
                    chapter=chapter,
                    description=chunk,
                    rule_type="CLASSIFICATION_NOTE",
                    jurisdiction="IN",
                    chunk_index=i,
                    embedding=emb_res.embedding,
                )
                session.add(rule)
                batch_buffer.append(i)
                inserted += 1

                # Commit in batches to prevent full-rollback on failure
                if len(batch_buffer) >= BATCH_SIZE:
                    await session.commit()
                    print(f"  [OK] Committed batch — {inserted}/{len(chunks)} chunks done")
                    batch_buffer = []

            except Exception as e:
                print(f"  [ERROR] Error on chunk {i}: {e}")
                failed += 1
                # Rollback just this chunk and continue
                await session.rollback()

        # Final commit for remaining chunks
        if batch_buffer:
            await session.commit()
            print(f"  [OK] Final batch committed — {inserted}/{len(chunks)} chunks done")

        print(f"\n{'='*60}")
        print(f"INGESTION COMPLETE")
        print(f"  Total chunks:   {len(chunks)}")
        print(f"  Inserted:       {inserted}")
        print(f"  Failed:         {failed}")
        print(f"  Tokens used:    {total_tokens}")
        print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
