from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from .config import get_settings
from typing import Optional

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None


async def get_db() -> AsyncIOMotorDatabase:
    global _client, _db
    if _db is None:
        settings = get_settings()
        _client = AsyncIOMotorClient(settings.mongo_uri)
        _db = _client[settings.mongo_db]
    return _db


async def close_db() -> None:
    global _client, _db
    if _client:
        _client.close()
    _client = None
    _db = None
