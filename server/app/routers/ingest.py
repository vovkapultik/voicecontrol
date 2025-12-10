import base64
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..db import get_db
from ..streaming import streaming_hub

router = APIRouter(prefix="/api", tags=["ingest"])


async def _user_by_api_key(api_key: str, db: AsyncIOMotorDatabase) -> Optional[dict]:
    if not api_key:
        return None
    return await db.users.find_one({"api_key": api_key})


@router.websocket("/ws/audio")
async def audio_ws(ws: WebSocket, user_id: Optional[str] = None):
    await streaming_hub.register_listener(ws, user_id_filter=user_id)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        streaming_hub.unregister_listener(ws)


@router.post("/ingest")
async def ingest_audio(
    file: UploadFile = File(...),
    x_api_key: Optional[str] = Header(default=None, convert_underscores=False),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict:
    user = await _user_by_api_key(x_api_key or "", db)
    if not user:
        logging.warning("Unauthorized ingest attempt with API key: %s", x_api_key or "<empty>")
        raise HTTPException(status_code=401, detail="Invalid API key")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    now = datetime.now(timezone.utc)

    user_id = str(user["_id"])
    streaming_hub.touch_user(user_id, user.get("name", ""))

    payload = {
        "kind": "chunk",
        "user_id": user_id,
        "user": user.get("name"),
        "filename": file.filename or "chunk.wav",
        "received_at": now.isoformat(),
        "data": base64.b64encode(content).decode("ascii"),
    }
    await streaming_hub.broadcast_chunk(payload, user_id=user_id)
    return {"status": "ok", "bytes": len(content)}
