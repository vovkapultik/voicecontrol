import datetime
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd
import soundfile as sf


class AudioRecorder:
    """Capture microphone + system audio and write chunked WAV files."""

    def __init__(
        self,
        output_dir: Path,
        chunk_seconds: int = 30,
        sample_rate: int = 48_000,
        on_chunk: Optional[callable] = None,
        mic_device: Optional[int] = None,
        spk_device: Optional[int] = None,
    ) -> None:
        self.output_dir = output_dir
        self.chunk_seconds = max(5, chunk_seconds)
        self.sample_rate = sample_rate
        self.on_chunk = on_chunk
        self.mic_device = mic_device
        self.spk_device = spk_device
        self.mic_queue: queue.Queue[np.ndarray] = queue.Queue()
        self.spk_queue: queue.Queue[np.ndarray] = queue.Queue()
        self._running = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._mic_stream: Optional[sd.InputStream] = None
        self._spk_stream: Optional[sd.InputStream] = None

    def _loopback_device(self) -> Optional[object]:
        """Return loopback device handle if supported (Windows WASAPI)."""
        try:
            if hasattr(sd, "WasapiLoopback"):
                target = self.spk_device if self.spk_device is not None else sd.default.device[1]
                return sd.WasapiLoopback(target)
        except Exception as exc:
            logging.warning("Loopback device unavailable: %s", exc)
        return None

    def start(self) -> None:
        if self._running.is_set():
            return
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._running.set()
        self._start_streams()
        self._worker = threading.Thread(target=self._run, name="audio-writer", daemon=True)
        self._worker.start()
        logging.info("Audio recorder started")

    def stop(self) -> None:
        if not self._running.is_set():
            return
        self._running.clear()
        if self._worker:
            self._worker.join(timeout=2)
        self._stop_streams()
        logging.info("Audio recorder stopped")

    def _start_streams(self) -> None:
        def start_mic(device: Optional[int]) -> bool:
            try:
                self._mic_stream = sd.InputStream(
                    device=device,
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="float32",
                    callback=self._enqueue(self.mic_queue),
                )
                self._mic_stream.start()
                if device is not None:
                    logging.info("Mic capture started on device %s", device)
                else:
                    logging.info("Mic capture started on default device")
                return True
            except Exception as exc:
                logging.error("Unable to start microphone capture (device=%s): %s", device, exc)
                return False

        if not start_mic(self.mic_device):
            if self.mic_device is not None:
                start_mic(None)

        loopback = self._loopback_device()
        if loopback:
            try:
                self._spk_stream = sd.InputStream(
                    device=loopback,
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="float32",
                    callback=self._enqueue(self.spk_queue),
                )
                self._spk_stream.start()
            except Exception as exc:
                logging.error("Unable to start speaker capture: %s", exc)
        else:
            logging.warning("Speaker loopback capture not available; recording mic only.")

    def _stop_streams(self) -> None:
        for stream in (self._mic_stream, self._spk_stream):
            try:
                if stream:
                    stream.stop()
                    stream.close()
            except Exception:
                pass
        self._mic_stream = None
        self._spk_stream = None

    @staticmethod
    def _enqueue(target_queue: queue.Queue[np.ndarray]):
        def callback(indata, frames, time_info, status):
            if status:
                logging.debug("Input status: %s", status)
            # Copy to decouple from PortAudio buffer
            target_queue.put(indata.copy())

        return callback

    def _run(self) -> None:
        mic_buffer: list[np.ndarray] = []
        spk_buffer: list[np.ndarray] = []
        frames_target = int(self.chunk_seconds * self.sample_rate)
        last_write = time.monotonic()

        while self._running.is_set():
            try:
                mic_buffer.append(self.mic_queue.get(timeout=0.1))
            except queue.Empty:
                pass
            try:
                spk_buffer.append(self.spk_queue.get_nowait())
            except queue.Empty:
                pass

            elapsed = time.monotonic() - last_write
            mic_frames = int(sum(chunk.shape[0] for chunk in mic_buffer))
            spk_frames = int(sum(chunk.shape[0] for chunk in spk_buffer))
            max_frames = max(mic_frames, spk_frames)

            if max_frames >= frames_target or elapsed >= self.chunk_seconds:
                self._write_chunk(mic_buffer, spk_buffer)
                mic_buffer.clear()
                spk_buffer.clear()
                last_write = time.monotonic()

    def _write_chunk(self, mic_buffer: list[np.ndarray], spk_buffer: list[np.ndarray]) -> None:
        try:
            mic_data = np.concatenate(mic_buffer) if mic_buffer else None
            spk_data = np.concatenate(spk_buffer) if spk_buffer else None
            frames = 0
            if mic_data is not None:
                frames = max(frames, mic_data.shape[0])
            if spk_data is not None:
                frames = max(frames, spk_data.shape[0])
            if frames == 0:
                return

            def pad(data: Optional[np.ndarray]) -> np.ndarray:
                if data is None:
                    return np.zeros((frames, 1), dtype="float32")
                if data.shape[0] < frames:
                    pad_len = frames - data.shape[0]
                    data = np.vstack([data, np.zeros((pad_len, 1), dtype="float32")])
                return data

            mic_channel = pad(mic_data)
            spk_channel = pad(spk_data)
            # Headroom normalization to prevent clipping
            def normalize(data: np.ndarray) -> np.ndarray:
                peak = float(np.max(np.abs(data))) if data.size else 0.0
                if peak > 1.0:
                    data = data / peak
                return np.clip(data, -1.0, 1.0)

            mic_channel = normalize(mic_channel)
            spk_channel = normalize(spk_channel)
            stereo = np.hstack([mic_channel, spk_channel])

            timestamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            filepath = self.output_dir / f"chunk_{timestamp}.wav"
            sf.write(filepath, stereo, samplerate=self.sample_rate, subtype="PCM_16")
            logging.info("Saved audio chunk: %s", filepath)
            if self.on_chunk:
                try:
                    self.on_chunk(filepath)
                except Exception as exc:  # pragma: no cover - callback safety
                    logging.warning("Chunk callback failed: %s", exc)
        except Exception as exc:
            logging.exception("Failed to write chunk: %s", exc)
