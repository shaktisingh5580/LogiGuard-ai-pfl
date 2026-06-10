import asyncio
from datetime import date
from sqlalchemy import text
from app.database import engine, Base, async_session_factory
from app.models import TariffEpoch, Client, HSTariffTree, DutyRate, TariffRule

async def seed():
    print("Enabling pgvector extension...")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        
    print("Creating tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    print("Tables created successfully.")

    async with async_session_factory() as db:
        print("Seeding tariff epoch...")
        epoch = TariffEpoch(
            name="india_fy2026",
            version="2026",
            effective_date=date(2025, 4, 1),
            is_active=True
        )
        db.add(epoch)
        await db.commit()
        await db.refresh(epoch)

        print("Seeding client...")
        client = Client(
            name="Demo Importer",
            code="DEMO"
        )
        db.add(client)

        print("Seeding HS codes...")
        nodes = [
            ("01", 1, "Live Animals; Animal Products"),
            ("0101", 2, "Live horses, asses, mules and hinnies"),
            ("09", 1, "Coffee, Tea, Mate and Spices"),
            ("0902", 2, "Tea, whether or not flavoured"),
            ("0902.10", 3, "Green tea (not fermented) in immediate packings"),
            ("0902.10.10", 4, "Green tea in packets up to 3 kg"),
            ("39", 1, "Plastics and Articles Thereof"),
            ("3926", 2, "Other articles of plastics"),
            ("3926.90", 3, "Other articles of plastics"),
            ("3926.90.99", 4, "Other articles of plastics, nes"),
        ]
        
        created_nodes = {}
        for code, level, desc in nodes:
            # simple mock parent resolution
            parent_code = code[:-2] if len(code) > 2 else None
            if code == "0902.10.10": parent_code = "0902.10"
            if code == "3926.90.99": parent_code = "3926.90"
            
            parent_id = created_nodes.get(parent_code).id if parent_code in created_nodes else None
            
            node = HSTariffTree(
                code=code,
                level=level,
                description=desc,
                path=code,
                is_leaf=(level == 4),
                parent_id=parent_id
            )
            db.add(node)
            await db.flush()  # to get ID
            created_nodes[code] = node

        print("Seeding duty rates...")
        rates = [
            ("0902.10.10", "BCD", 100.0),
            ("3926.90.99", "BCD", 15.0),
        ]
        for hs, dtype, r in rates:
            dr = DutyRate(
                epoch_id=epoch.id,
                hs_code=hs,
                country_code="IN",
                duty_type=dtype,
                rate_percent=r,
            )
            db.add(dr)

        print("Seeding rules...")
        rule = TariffRule(
            epoch_id=epoch.id,
            hs_code="0902",
            section="II",
            chapter="09",
            description="Green tea must be unfermented",
            chunk_index=0
        )
        db.add(rule)

        await db.commit()
        print("✅ Database seeding complete!")
        
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(seed())
