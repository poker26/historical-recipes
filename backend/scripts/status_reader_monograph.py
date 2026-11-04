import asyncio
from app.temporal.client import get_temporal_client


async def main():
    d = await (await get_temporal_client()).get_workflow_handle("reader-monograph").describe()
    s = d.status
    print(s.name if hasattr(s, "name") else s)


if __name__ == "__main__":
    asyncio.run(main())
