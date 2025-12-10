import secrets
import string
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


def generate_api_key(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


class Admin(BaseModel):
    id: Optional[str] = Field(alias="_id", default=None)
    email: str
    password_hash: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class User(BaseModel):
    id: Optional[str] = Field(alias="_id", default=None)
    name: str
    api_key: str = Field(default_factory=generate_api_key)
    created_at: datetime = Field(default_factory=datetime.utcnow)

