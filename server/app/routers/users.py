from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from ..auth import get_current_admin
from ..db import get_db
from ..models import generate_api_key
from ..schemas import ApiKeyRefreshResponse, MessageResponse, UserResponse, UserUpdatePayload
from ..streaming import streaming_hub
from ..utils import attach_str_id, parse_object_id

router = APIRouter(prefix="/admin/users", tags=["users"], dependencies=[Depends(get_current_admin)])


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user_name(
    user_id: str, payload: UserUpdatePayload, db: AsyncIOMotorDatabase = Depends(get_db)
) -> UserResponse:
    user_oid = parse_object_id(user_id, "user id")
    res = await db.users.find_one_and_update(
        {"_id": user_oid},
        {"$set": {"name": payload.name}},
        return_document=ReturnDocument.AFTER,
    )
    if not res:
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse(**attach_str_id(res))


@router.post("/{user_id}/refresh-key", response_model=ApiKeyRefreshResponse)
async def refresh_api_key(user_id: str, db: AsyncIOMotorDatabase = Depends(get_db)) -> ApiKeyRefreshResponse:
    new_key = generate_api_key()
    res = await db.users.update_one({"_id": parse_object_id(user_id, "user id")}, {"$set": {"api_key": new_key}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return ApiKeyRefreshResponse(api_key=new_key)


@router.delete("/{user_id}", response_model=MessageResponse)
async def delete_user(user_id: str, db: AsyncIOMotorDatabase = Depends(get_db)) -> MessageResponse:
    user_oid = parse_object_id(user_id, "user id")
    user = await db.users.find_one({"_id": user_oid})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await db.users.delete_one({"_id": user_oid})
    streaming_hub.remove_user(user_id)
    return MessageResponse()
