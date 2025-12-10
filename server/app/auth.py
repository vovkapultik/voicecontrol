import hashlib
import hmac
import os
from datetime import datetime, timedelta
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId

from .config import get_settings
from .db import get_db


bearer_scheme = HTTPBearer(auto_error=True)


def hash_password(password: str) -> str:
    salt = os.getenv("PASSWORD_SALT", "voicecontrol")
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def verify_password(password: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_password(password), stored_hash)


async def create_admin_token(admin_id: str) -> str:
    settings = get_settings()
    payload = {
        "sub": admin_id,
        "exp": datetime.utcnow() + timedelta(minutes=settings.jwt_exp_minutes),
        "scope": "admin",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


async def get_current_admin(
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    token = creds.credentials
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if payload.get("scope") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not an admin token")
    admin_id = payload.get("sub")
    if not admin_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    admin = await db.admins.find_one({"_id": ObjectId(admin_id)})
    if not admin:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin not found")
    return admin


async def authenticate_admin(email: str, password: str, db: AsyncIOMotorDatabase) -> Optional[dict]:
    admin = await db.admins.find_one({"email": email.lower()})
    if not admin:
        return None
    if not verify_password(password, admin.get("password_hash", "")):
        return None
    return admin
