import asyncio
import uuid
import logging

from app.database import async_session_maker
from app.pipeline.processor import InvoiceProcessor

logging.basicConfig(level=logging.DEBUG)

async def main():
    invoice_id = uuid.UUID("4aef3ab8-58aa-4836-a31c-e110a2d73eaf")
    async with async_session_maker() as session:
        processor = InvoiceProcessor(session)
        result = await processor.process(invoice_id)
        print(result)

if __name__ == "__main__":
    asyncio.run(main())
