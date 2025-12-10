from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set

from fastapi import WebSocket

from .schemas import RoomResponse


class StreamingHub:
    def __init__(self, active_ttl_seconds: int = 60) -> None:
        self._listeners: Set[WebSocket] = set()
        self._filters: Dict[WebSocket, Optional[str]] = {}
        self._active: Dict[str, Dict[str, object]] = {}
        self._active_ttl = timedelta(seconds=active_ttl_seconds)

    async def register_listener(self, websocket: WebSocket, user_id_filter: Optional[str] = None) -> None:
        await websocket.accept()
        self._listeners.add(websocket)
        self._filters[websocket] = user_id_filter

    def unregister_listener(self, websocket: WebSocket) -> None:
        self._listeners.discard(websocket)
        self._filters.pop(websocket, None)

    async def broadcast_chunk(self, payload: dict, user_id: str) -> None:
        dead = []
        for ws in list(self._listeners):
            user_filter = self._filters.get(ws)
            if user_filter and user_filter != user_id:
                continue
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.unregister_listener(ws)

    def touch_user(self, user_id: str, name: str) -> None:
        now = datetime.now(timezone.utc)
        self._active[user_id] = {"user_id": user_id, "name": name, "last_seen": now}
        self._prune()

    def remove_user(self, user_id: str) -> None:
        self._active.pop(user_id, None)

    def active_rooms(self) -> List[RoomResponse]:
        self._prune()
        return [
            RoomResponse(user_id=room["user_id"], name=room["name"], last_seen=room["last_seen"])
            for room in sorted(self._active.values(), key=lambda r: r["last_seen"], reverse=True)
        ]

    def _prune(self) -> None:
        cutoff = datetime.now(timezone.utc) - self._active_ttl
        stale = [uid for uid, meta in self._active.items() if meta["last_seen"] < cutoff]
        for uid in stale:
            self._active.pop(uid, None)

streaming_hub = StreamingHub(active_ttl_seconds=10)
