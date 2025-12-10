import logging

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from pathlib import Path

from .config import get_settings
from .db import close_db, get_db
from .auth import hash_password
from .routers import admin as admin_router
from .routers import ingest as ingest_router
from .routers import users as users_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("voicecontrol-server")

app = FastAPI(title="VoiceControl Stream Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin_router.router)
app.include_router(ingest_router.router)
app.include_router(users_router.router)


@app.get("/", response_class=HTMLResponse)
async def admin_portal() -> str:
    template = Path(__file__).resolve().parent / "templates" / "admin.html"
    with template.open("r", encoding="utf-8") as fh:
        return fh.read()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await close_db()


@app.on_event("startup")
async def startup_event() -> None:
    # Optional bootstrap admin via env vars: ADMIN_EMAIL / ADMIN_PASSWORD
    import os

    db = await get_db()
    admin_email = os.getenv("ADMIN_EMAIL")
    admin_password = os.getenv("ADMIN_PASSWORD")
    if admin_email and admin_password:
        existing = await db.admins.find_one({"email": admin_email.lower()})
        if not existing:
            await db.admins.insert_one(
                {"email": admin_email.lower(), "password_hash": hash_password(admin_password)}
            )
            logger.info("Bootstrap admin created: %s", admin_email)


def run() -> None:
    from uvicorn import run as uvicorn_run

    settings = get_settings()
    uvicorn_run("app.main:app", host=settings.host, port=settings.port, reload=False, log_level="info")


if __name__ == "__main__":
    run()
