import asyncio
import csv
import sys
from sqlalchemy import delete
from app.database import async_session_factory
from app.models import HSTariffTree

async def main():
    print("Starting HS CSV Ingestion...")
    csv_path = "data/hs_codes.csv"
    
    async with async_session_factory() as session:
        print("Clearing existing hs_tariff_tree...")
        await session.execute(delete(HSTariffTree))
        await session.flush()
        
        sections_created = {}
        nodes_created = {}
        
        section_titles = {
            "I": "Live Animals; Animal Products",
            "II": "Vegetable Products",
            "III": "Animal or Vegetable Fats and Oils",
            "IV": "Prepared Foodstuffs; Beverages, Spirits, and Vinegar; Tobacco",
            "V": "Mineral Products",
            "VI": "Products of the Chemical or Allied Industries",
            "VII": "Plastics and Articles Thereof; Rubber",
            "VIII": "Raw Hides and Skins, Leather, Furskins",
            "IX": "Wood and Articles of Wood; Wood Charcoal",
            "X": "Pulp of Wood or of other Fibrous Cellulosic Material; Paper",
            "XI": "Textiles and Textile Articles",
            "XII": "Footwear, Headgear, Umbrellas, Sun Umbrellas, Walking Sticks",
            "XIII": "Articles of Stone, Plaster, Cement, Asbestos, Mica",
            "XIV": "Natural or Cultured Pearls, Precious or Semi-Precious Stones",
            "XV": "Base Metals and Articles of Base Metal",
            "XVI": "Machinery and Mechanical Appliances; Electrical Equipment",
            "XVII": "Vehicles, Aircraft, Vessels and Associated Transport Equipment",
            "XVIII": "Optical, Photographic, Cinematographic, Measuring, Checking, Precision, Medical or Surgical Instruments and Apparatus",
            "XIX": "Arms and Ammunition; Parts and Accessories Thereof",
            "XX": "Miscellaneous Manufactured Articles",
            "XXI": "Works of Art, Collectors' Pieces and Antiques"
        }

        print(f"Reading {csv_path}...")
        try:
            with open(csv_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                
                for row in reader:
                    sec_roman = row["section"]
                    code = row["hscode"]
                    desc = row["description"]
                    parent_code = row["parent"]
                    csv_level = int(row["level"])
                    
                    if sec_roman not in sections_created:
                        sec_node = HSTariffTree(
                            code=sec_roman,
                            level=1,
                            description=section_titles.get(sec_roman, f"Section {sec_roman}"),
                            path=sec_roman,
                            is_leaf=False,
                            parent_id=None
                        )
                        session.add(sec_node)
                        await session.flush()
                        sections_created[sec_roman] = sec_node
                        nodes_created["TOTAL"] = sec_node
                    
                    if csv_level == 2:
                        db_level = 2
                    elif csv_level == 4:
                        db_level = 3
                    elif csv_level == 6:
                        db_level = 4
                    else:
                        db_level = 5
                        
                    parent_node = nodes_created.get(parent_code)
                    parent_id = parent_node.id if parent_node else sections_created[sec_roman].id
                    
                    is_leaf = (csv_level >= 6)
                    
                    node = HSTariffTree(
                        code=code,
                        level=db_level,
                        description=desc,
                        path=code,
                        is_leaf=is_leaf,
                        parent_id=parent_id
                    )
                    session.add(node)
                    nodes_created[code] = node
                    
                    if len(nodes_created) % 1000 == 0:
                        await session.flush()
                        print(f"  Processed {len(nodes_created)} rows...")
                        
            await session.commit()
            print(f"SUCCESS: Ingested {len(nodes_created)} HS codes!")
        except Exception as e:
            print(f"Error during ingestion: {e}")
            await session.rollback()
            sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
