import os
from functools import lru_cache
from dotenv import load_dotenv

# Load environment variables from a local .env if present.
load_dotenv()


class Settings:
    def __init__(self) -> None:
        self.mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
        self.mongo_db = os.getenv("MONGO_DB", "voicecontrol")
        self.host = os.getenv("HOST", "0.0.0.0")
        self.port = int(os.getenv("PORT", "8000"))
        self.jwt_secret = os.getenv("JWT_SECRET", "change-me-secret")
        self.jwt_exp_minutes = int(os.getenv("JWT_EXP_MINUTES", "1440"))


@lru_cache()
def get_settings() -> Settings:
    return Settings()
