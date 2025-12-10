from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class TokenResponse(BaseModel):
    token: str


class MessageResponse(BaseModel):
    status: str = "ok"


class CreatedResponse(BaseModel):
    id: str


class AdminLoginPayload(BaseModel):
    email: str
    password: str


class AdminCreatePayload(BaseModel):
    email: str
    password: str


class AdminResponse(BaseModel):
    id: str
    email: str
    created_at: Optional[datetime] = None


class UserCreatePayload(BaseModel):
    name: str = Field(..., min_length=1)


class UserUpdatePayload(BaseModel):
    name: str = Field(..., min_length=1)


class UserResponse(BaseModel):
    id: str
    name: str
    api_key: str
    created_at: Optional[datetime] = None


class ApiKeyRefreshResponse(BaseModel):
    status: str = "ok"
    api_key: str


class RoomResponse(BaseModel):
    user_id: str
    name: str
    last_seen: datetime
