import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict
from dotenv import load_dotenv


def _default_app_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "voicecontrol"
    return Path.home() / ".local" / "share" / "voicecontrol"


APP_DIR = _default_app_dir()
CONFIG_PATH = APP_DIR / "config.json"
RECORDINGS_DIR = APP_DIR / "recordings"


@dataclass
class ClientConfig:
    server_base: str = "http://localhost:8000"
    api_key: str = "changeme"
    recording_enabled: bool = False
    run_on_startup: bool = False
    chunk_seconds: float = 1.0
    sample_rate: int = 48_000
    spk_device: int | None = None
    mic_device: int | None = None

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ClientConfig":
        return cls(
            server_base=payload.get("server_base", cls.server_base),
            api_key=payload.get("api_key", cls.api_key),
            recording_enabled=bool(payload.get("recording_enabled", cls.recording_enabled)),
            run_on_startup=bool(payload.get("run_on_startup", cls.run_on_startup)),
            chunk_seconds=float(payload.get("chunk_seconds", cls.chunk_seconds)),
            sample_rate=int(payload.get("sample_rate", cls.sample_rate)),
            spk_device=payload.get("spk_device"),
            mic_device=payload.get("mic_device"),
        )


class ConfigManager:
    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self.path = path
        self.config = ClientConfig()
        self.load()

    def load(self) -> None:
        try:
            load_dotenv()
            if self.path.exists():
                with self.path.open("r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                self.config = ClientConfig.from_dict(raw)
            else:
                self.save()

            # Override server_base and api_key from environment (.env), if provided.
            env_server = os.getenv("SERVER_BASE")
            env_key = os.getenv("API_KEY")
            if env_server:
                self.config.server_base = env_server
            if env_key:
                self.config.api_key = env_key
            # Enforce fixed 1s chunks regardless of persisted/configured values.
            self.config.chunk_seconds = 1.0
        except Exception as exc:  # pragma: no cover - defensive
            logging.exception("Failed to load config, using defaults: %s", exc)
            self.config = ClientConfig()

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", encoding="utf-8") as fh:
                json.dump(asdict(self.config), fh, indent=2)
        except Exception as exc:  # pragma: no cover - defensive
            logging.exception("Failed to persist config: %s", exc)

    def update(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
        self.save()

    def recordings_dir(self) -> Path:
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        return RECORDINGS_DIR
