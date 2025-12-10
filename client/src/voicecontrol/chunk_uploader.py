import logging
import queue
import threading
from typing import Callable, Optional
from urllib.parse import urljoin

import httpx


class ChunkUploader:
    """Background uploader that POSTs audio chunks to the server as they are produced."""

    def __init__(
        self,
        server_base: str,
        api_key: str,
        api_key_provider: Optional[Callable[[], str]] = None,
        endpoint: str = "/api/ingest",
        timeout: float = 10.0,
        max_queue: int = 200,
    ) -> None:
        self._endpoint_path = endpoint.lstrip("/")
        self._update_base(server_base)
        self.api_key = api_key
        self.api_key_provider = api_key_provider
        self.timeout = timeout
        self.queue: "queue.Queue[tuple[str, bytes]]" = queue.Queue(maxsize=max_queue)
        self._client = httpx.Client(timeout=self.timeout)
        self._running = threading.Event()
        self._worker: Optional[threading.Thread] = None

    def _update_base(self, server_base: str) -> None:
        base = (server_base or "").rstrip("/") + "/"
        self.server_base = base
        self.endpoint = urljoin(self.server_base, self._endpoint_path)

    def set_server_base(self, server_base: str) -> None:
        self._update_base(server_base)

    def start(self) -> None:
        if self._running.is_set():
            return
        self._running.set()
        self._worker = threading.Thread(target=self._run, name="chunk-uploader", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        if not self._running.is_set():
            return
        self._running.clear()
        if self._worker:
            self._worker.join(timeout=2)

    def enqueue(self, filename: str, data: bytes) -> None:
        try:
            self.queue.put_nowait((filename, data))
        except queue.Full:
            logging.warning("Upload queue full; dropping chunk %s", filename)

    def _run(self) -> None:
        while self._running.is_set() or not self.queue.empty():
            try:
                item = self.queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                self._upload(item)
            except Exception as exc:
                logging.warning("Failed to upload %s: %s", item[0], exc)
            finally:
                self.queue.task_done()

    def _upload(self, payload: tuple[str, bytes]) -> None:
        filename, data = payload
        api_key = (self.api_key_provider() if self.api_key_provider else self.api_key) or ""
        api_key = api_key.strip()
        if not api_key:
            logging.warning("Chunk upload skipped: missing API key for %s", filename)
            return
        headers = {"x_api_key": api_key}
        files = {"file": (filename, data, "audio/wav")}
        resp = self._client.post(self.endpoint, files=files, headers=headers)
        resp.raise_for_status()
