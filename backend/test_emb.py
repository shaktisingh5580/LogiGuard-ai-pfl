import asyncio
from google import genai

async def main():
    client = genai.Client()
    
    try:
        resp = await client.aio.models.embed_content(model='text-embedding-004', contents='test')
        print('text-embedding-004 dim:', len(resp.embeddings[0].values))
    except Exception as e:
        print('text-embedding-004 error:', e)
        
    try:
        resp2 = await client.aio.models.embed_content(model='gemini-embedding-2', contents='test')
        print('gemini-embedding-2 dim:', len(resp2.embeddings[0].values))
    except Exception as e:
        print('gemini-embedding-2 error:', e)

if __name__ == "__main__":
    asyncio.run(main())
