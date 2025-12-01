import logging
from typing import Tuple

import httpx

DEFAULT_MASTER_PASSWORD = "123456"


class MasterPasswordProvider:
    def __init__(self, server_base: str, api_key: str | None = None) -> None:
        self.server_base = server_base.rstrip("/")
        self.api_key = api_key

    def fetch(self, timeout: float = 5.0) -> Tuple[str, bool]:
        """Return (password, offline_used)."""
        url = f"{self.server_base}/api/client/password"
        headers = {}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        try:
            resp = httpx.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            password = data.get("password") or resp.text.strip()
            if not password:
                raise ValueError("Empty password from server")
            return password, False
        except Exception as exc:
            logging.warning("Falling back to default master password: %s", exc)
            return DEFAULT_MASTER_PASSWORD, True

