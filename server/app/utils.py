from typing import Any, Dict

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException, status


def parse_object_id(raw: str, label: str = "id") -> ObjectId:
    """Convert a string to an ObjectId or raise a 400 error."""
    try:
        return ObjectId(raw)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid {label}")


def attach_str_id(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Return a shallow copy of the document with _id converted to id string."""
    copy = dict(doc)
    if "_id" in copy:
        copy["id"] = str(copy.pop("_id"))
    return copy
