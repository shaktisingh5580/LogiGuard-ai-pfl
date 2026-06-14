"""Comprehensive database seed for LogiGuard AI.

Populates the database with:
- A tariff epoch (India FY2026)
- A demo client
- HS tariff tree (Section → Chapter → Heading → Subheading → Leaf) using integer levels:
    1 = SECTION, 2 = CHAPTER, 3 = HEADING, 4 = SUBHEADING, 5 = TARIFF_LINE
- Duty rates for leaf codes
- Tariff rules with rule_type and jurisdiction

The retriever uses integer levels mapped via _LEVEL_NAMES.
"""

import asyncio
from datetime import date
from sqlalchemy import text
from app.database import engine, Base, async_session_factory
from app.models import TariffEpoch, Client, HSTariffTree, DutyRate, TariffRule


async def seed():
    print("Enabling pgvector extension...")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

    print("Creating tables (drop all first)...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    print("Tables created successfully.")

    async with async_session_factory() as db:
        # ── Tariff Epoch ───────────────────────────────────────────────────────
        print("Seeding tariff epoch...")
        epoch = TariffEpoch(
            name="india_fy2026",
            version="2026",
            effective_date=date(2025, 4, 1),
            is_active=True
        )
        db.add(epoch)
        await db.flush()
        await db.refresh(epoch)

        # ── Demo Client ────────────────────────────────────────────────────────
        print("Seeding client...")
        client = Client(name="Demo Importer", code="DEMO")
        db.add(client)
        await db.flush()

        # ── HS Tariff Tree ─────────────────────────────────────────────────────
        # Level encoding (integers):
        #   1 = SECTION
        #   2 = CHAPTER
        #   3 = HEADING
        #   4 = SUBHEADING
        #   5 = TARIFF_LINE (leaf)
        print("Seeding HS tariff tree...")
        # Format: (code, level_int, description, parent_code_or_None)
        tree_data = [
            # SECTION I — Live Animals
            ("I",          1, "Live Animals; Animal Products",           None),
            ("01",         2, "Live animals",                            "I"),
            ("0101",       3, "Live horses, asses, mules and hinnies",   "01"),
            ("0101.21",    4, "Horses: pure-bred breeding animals",       "0101"),
            ("0101.21.10", 5, "Pure-bred breeding horses",               "0101.21"),

            # SECTION II — Vegetable Products (Coffee, Tea, Spices)
            ("II",         1, "Vegetable Products",                      None),
            ("09",         2, "Coffee, Tea, Mate and Spices",            "II"),
            ("0902",       3, "Tea, whether or not flavoured",            "09"),
            ("0902.10",    4, "Green tea (not fermented) packings ≤ 3kg","0902"),
            ("0902.10.10", 5, "Green tea, packets up to 3 kg",           "0902.10"),
            ("0902.30",    4, "Black tea (fermented) and partly fermented","0902"),
            ("0902.30.10", 5, "Black tea in packets up to 3 kg",         "0902.30"),

            # SECTION VII — Plastics
            ("VII",        1, "Plastics and Articles Thereof",           None),
            ("39",         2, "Plastics and articles thereof",           "VII"),
            ("3926",       3, "Other articles of plastics",              "39"),
            ("3926.90",    4, "Other articles of plastics: other",       "3926"),
            ("3926.90.99", 5, "Other articles of plastics, not elsewhere specified", "3926.90"),

            # SECTION XVI — Machinery & Electronics
            ("XVI",        1, "Machinery and Mechanical Appliances; Electrical Equipment", None),
            ("84",         2, "Nuclear reactors, boilers, machinery and mechanical appliances", "XVI"),
            ("8471",       3, "Automatic data-processing machines and units thereof", "84"),
            ("8471.30",    4, "Portable automatic data-processing machines ≤ 10 kg", "8471"),
            ("8471.30.10", 5, "Laptops and notebook computers",          "8471.30"),
            ("8471.41",    4, "Comprising in same housing: processing unit and input/output", "8471"),
            ("8471.41.10", 5, "Desktop computers",                      "8471.41"),
            ("85",         2, "Electrical machinery and equipment",      "XVI"),
            ("8517",       3, "Telephone sets; other apparatus for transmission/reception", "85"),
            ("8517.12",    4, "Telephones for cellular networks or wireless",  "8517"),
            ("8517.12.10", 5, "Mobile phones (smartphones)",             "8517.12"),

            # SECTION XI — Textiles
            ("XI",         1, "Textiles and Textile Articles",           None),
            ("61",         2, "Articles of apparel and clothing accessories, knitted", "XI"),
            ("6109",       3, "T-shirts, singlets and other vests, knitted", "61"),
            ("6109.10",    4, "Of cotton",                               "6109"),
            ("6109.10.10", 5, "T-shirts of cotton",                     "6109.10"),
            ("62",         2, "Articles of apparel and clothing accessories, not knitted", "XI"),
            ("6201",       3, "Men's or boys' overcoats, jackets, suits etc", "62"),
            ("6201.93",    4, "Of man-made fibres",                      "6201"),
            ("6201.93.10", 5, "Men's jackets of man-made fibres",       "6201.93"),

            # SECTION XV — Base Metals
            ("XV",         1, "Base Metals and Articles Thereof",        None),
            ("73",         2, "Articles of iron or steel",               "XV"),
            ("7308",       3, "Structures and parts of structures of iron/steel", "73"),
            ("7308.90",    4, "Other structures of iron or steel",       "7308"),
            ("7308.90.99", 5, "Other structures of iron or steel, nes",  "7308.90"),
        ]

        created_nodes: dict[str, HSTariffTree] = {}
        for code, level, desc, parent_code in tree_data:
            parent_id = created_nodes[parent_code].id if parent_code and parent_code in created_nodes else None
            node = HSTariffTree(
                code=code,
                level=level,
                description=desc,
                path=code,
                is_leaf=(level == 5),
                parent_id=parent_id,
            )
            db.add(node)
            await db.flush()
            created_nodes[code] = node

        # ── Duty Rates ─────────────────────────────────────────────────────────
        print("Seeding duty rates...")
        duty_data = [
            ("0902.10.10", "BCD", 100.0),
            ("0902.30.10", "BCD", 100.0),
            ("3926.90.99", "BCD", 15.0),
            ("8471.30.10", "BCD", 0.0),
            ("8471.41.10", "BCD", 0.0),
            ("8517.12.10", "BCD", 20.0),
            ("6109.10.10", "BCD", 20.0),
            ("6201.93.10", "BCD", 20.0),
            ("7308.90.99", "BCD", 7.5),
            ("0101.21.10", "BCD", 30.0),
        ]
        for hs_code, duty_type, rate in duty_data:
            db.add(DutyRate(
                epoch_id=epoch.id,
                hs_code=hs_code,
                country_code="IN",
                duty_type=duty_type,
                rate_percent=rate,
            ))

        # ── Tariff Rules ───────────────────────────────────────────────────────
        # rule_type values correspond to what the rule engine expects.
        # Using NULL jurisdiction so they apply globally (engine uses OR IS NULL).
        print("Seeding tariff rules...")
        rules_data = [
            {
                "epoch_id": epoch.id,
                "hs_code": "0902",
                "section": "II",
                "chapter": "09",
                "rule_type": "CLASSIFICATION_NOTE",
                "applies_to_chapter": "09",
                "description": "Tea (heading 09.02) covers green tea (not fermented), black tea "
                               "(fermented) and partly fermented tea, whether or not flavoured. "
                               "Includes tea extracts and concentrates.",
                "jurisdiction": "IN",
                "chunk_index": 0,
            },
            {
                "epoch_id": epoch.id,
                "hs_code": "3926",
                "section": "VII",
                "chapter": "39",
                "rule_type": "CLASSIFICATION_NOTE",
                "applies_to_chapter": "39",
                "description": "Chapter 39 covers plastics and articles thereof. "
                               "3926.90.99 applies to articles of plastics not elsewhere specified, "
                               "including plastic components, fittings, and household articles.",
                "jurisdiction": "IN",
                "chunk_index": 0,
            },
            {
                "epoch_id": epoch.id,
                "hs_code": "8471",
                "section": "XVI",
                "chapter": "84",
                "rule_type": "CLASSIFICATION_NOTE",
                "applies_to_chapter": "84",
                "description": "Heading 84.71 covers automatic data-processing machines. "
                               "Laptops and notebooks fall under 8471.30. Desktop computers "
                               "with a processing unit and I/O in same housing fall under 8471.41.",
                "jurisdiction": "IN",
                "chunk_index": 0,
            },
            {
                "epoch_id": epoch.id,
                "hs_code": "8517",
                "section": "XVI",
                "chapter": "85",
                "rule_type": "CLASSIFICATION_NOTE",
                "applies_to_chapter": "85",
                "description": "Heading 85.17 covers mobile phones, smartphones, and cellular "
                               "handsets. These are classified under 8517.12.10 when imported into India.",
                "jurisdiction": "IN",
                "chunk_index": 0,
            },
            {
                "epoch_id": epoch.id,
                "hs_code": "6109",
                "section": "XI",
                "chapter": "61",
                "rule_type": "CLASSIFICATION_NOTE",
                "applies_to_chapter": "61",
                "description": "Chapter 61 covers knitted or crocheted clothing. "
                               "T-shirts and vests of cotton fall under 6109.10. "
                               "Synthetic or man-made fibre versions fall under 6109.90.",
                "jurisdiction": "IN",
                "chunk_index": 0,
            },
            {
                "epoch_id": epoch.id,
                "hs_code": "7308",
                "section": "XV",
                "chapter": "73",
                "rule_type": "CLASSIFICATION_NOTE",
                "applies_to_chapter": "73",
                "description": "Heading 73.08 covers structures and parts of structures of iron "
                               "or steel, including bridges, towers, lattice masts, roofs, and "
                               "prefabricated buildings.",
                "jurisdiction": "IN",
                "chunk_index": 0,
            },
        ]
        for rd in rules_data:
            db.add(TariffRule(**rd))

        await db.commit()
        print("SUCCESS: Database seeding complete!")
        print(f"   - {len(tree_data)} HS tariff tree nodes")
        print(f"   - {len(duty_data)} duty rates")
        print(f"   - {len(rules_data)} tariff rules")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
