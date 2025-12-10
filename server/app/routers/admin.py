from typing import List, Mapping, Any

from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..auth import authenticate_admin, create_admin_token, get_current_admin, hash_password
from ..db import get_db
from ..models import Admin, User
from ..schemas import (
    AdminCreatePayload,
    AdminLoginPayload,
    AdminResponse,
    CreatedResponse,
    MessageResponse,
    RoomResponse,
    TokenResponse,
    UserCreatePayload,
    UserResponse,
)
from ..streaming import streaming_hub
from ..utils import attach_str_id, parse_object_id

router = APIRouter(prefix="/admin", tags=["admin"])


def _user_response(doc: dict) -> UserResponse:
    data = attach_str_id(doc)
    return UserResponse(**data)


def _admin_response(doc: Mapping[str, Any]) -> AdminResponse:
    data = attach_str_id(doc)
    data.pop("password_hash", None)
    return AdminResponse(**data)


@router.post("/login", response_model=TokenResponse)
async def login(payload: AdminLoginPayload, db: AsyncIOMotorDatabase = Depends(get_db)) -> TokenResponse:
    admin = await authenticate_admin(payload.email, payload.password, db)
    if not admin:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = await create_admin_token(str(admin["_id"]))
    return TokenResponse(token=token)


@router.post("/admins", response_model=CreatedResponse, dependencies=[Depends(get_current_admin)])
async def create_admin(payload: AdminCreatePayload, db: AsyncIOMotorDatabase = Depends(get_db)) -> CreatedResponse:
    existing = await db.admins.find_one({"email": payload.email.lower()})
    if existing:
        raise HTTPException(status_code=400, detail="Admin already exists")
    doc = Admin(email=payload.email.lower(), password_hash=hash_password(payload.password)).dict(
        by_alias=True, exclude_none=True
    )
    result = await db.admins.insert_one(doc)
    return CreatedResponse(id=str(result.inserted_id))


@router.delete(
    "/admins/{admin_id}",
    response_model=MessageResponse,
    dependencies=[Depends(get_current_admin)],
)
async def delete_admin(admin_id: str, db: AsyncIOMotorDatabase = Depends(get_db)) -> MessageResponse:
    admin_oid = parse_object_id(admin_id, "admin id")
    existing = await db.admins.find_one({"_id": admin_oid})
    if not existing:
        raise HTTPException(status_code=404, detail="Admin not found")
    remaining = await db.admins.count_documents({})
    if remaining <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the last admin")
    await db.admins.delete_one({"_id": admin_oid})
    return MessageResponse()


@router.get("/admins", response_model=List[AdminResponse], dependencies=[Depends(get_current_admin)])
async def list_admins(db: AsyncIOMotorDatabase = Depends(get_db)) -> List[AdminResponse]:
    admins: List[AdminResponse] = []
    async for a in db.admins.find({}, projection={"email": 1, "created_at": 1}).sort("email"):
        admins.append(_admin_response(a))
    return admins


@router.post("/users", response_model=UserResponse, dependencies=[Depends(get_current_admin)])
async def create_user(payload: UserCreatePayload, db: AsyncIOMotorDatabase = Depends(get_db)) -> UserResponse:
    user_doc = User(name=payload.name).dict(by_alias=True, exclude_none=True)
    result = await db.users.insert_one(user_doc)
    user_doc["_id"] = result.inserted_id
    return _user_response(user_doc)


@router.get("/users", response_model=List[UserResponse], dependencies=[Depends(get_current_admin)])
async def list_users(db: AsyncIOMotorDatabase = Depends(get_db)) -> List[UserResponse]:
    users: List[UserResponse] = []
    async for u in db.users.find({}):
        users.append(_user_response(u))
    return users


@router.get("/rooms", response_model=List[RoomResponse], dependencies=[Depends(get_current_admin)])
async def list_active_rooms() -> List[RoomResponse]:
    return streaming_hub.active_rooms()
