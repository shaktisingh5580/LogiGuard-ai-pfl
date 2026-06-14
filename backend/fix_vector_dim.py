import asyncio
from sqlalchemy import text
from app.database import async_session_factory

async def main():
    async with async_session_factory() as session:
        # Drop the existing index first (it references the old dimension)
        await session.execute(text(
            "DROP INDEX IF EXISTS ix_tariff_rules_embedding"
        ))
        # Alter the column type
        await session.execute(text(
            "ALTER TABLE tariff_rules ALTER COLUMN embedding TYPE vector(768)"
        ))
        # Recreate the index with the new dimension
        await session.execute(text(
            "CREATE INDEX ix_tariff_rules_embedding ON tariff_rules USING ivfflat (embedding vector_cosine_ops)"
        ))
        await session.commit()
        print("SUCCESS: Column altered to vector(768) and index rebuilt!")

if __name__ == "__main__":
    asyncio.run(main())
