import datetime
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Optional
import sys

import numpy as np
import soundfile as sf
from .devices import (
    default_wasapi_loopback_device,
    list_wasapi_loopback_devices,
    choose_wasapi_loopback,
)

try:
    import pyaudiowpatch as pyaudio
except ImportError as exc:  # pragma: no cover - runtime guard
    raise RuntimeError(
        "PyAudioWPatch is required for WASAPI loopback. Install with: pip install --upgrade --force-reinstall PyAudioWPatch"
    ) from exc


class AudioRecorder:
    """Capture system audio and write chunked WAV files."""

    def __init__(
        self,
        output_dir: Path,
        chunk_seconds: int = 30,
        sample_rate: int = 48_000,
        on_chunk: Optional[callable] = None,
        spk_device: Optional[int] = None,
    ) -> None:
        self.output_dir = output_dir
        self.chunk_seconds = max(5, chunk_seconds)
        self.sample_rate = sample_rate
        self.on_chunk = on_chunk
        self.spk_device = spk_device
        self.spk_queue: queue.Queue[np.ndarray] = queue.Queue()
        self._running = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._spk_stream: Optional[pyaudio.Stream] = None
        self._active_loopback_device: Optional[int] = None
        self._watch_thread: Optional[threading.Thread] = None
        self._watch_stop = threading.Event()
        self._pa = pyaudio.PyAudio()
        self._chunk_counter = 0

    def _loopback_device(self) -> tuple[Optional[object], bool]:
        """Return (device, use_loopback_flag) for speaker capture."""
        if not sys.platform.startswith("win"):
            raise RuntimeError("Speaker loopback capture is supported on Windows only.")
        try:
            # Prefer user-selected device if it is WASAPI-capable.
            wasapi_outputs = {idx for idx, _ in list_wasapi_loopback_devices()}
            target = None
            # If user selected a specific device, honor it regardless of host API.
            if self.spk_device is not None:
                target = self.spk_device
            else:
                target = self._pick_loopback_target(wasapi_outputs)
            if target is None:
                logging.error("No WASAPI loopback device found; loopback unavailable. Enable a WASAPI device (e.g., system speakers) and ensure PyAudioWPatch is installed.")
                return None, False
            if target != self._active_loopback_device:
                logging.debug("Using WASAPI output device %s for loopback", target)
                self._active_loopback_device = target

            return target, True
        except Exception as exc:
            logging.warning("Loopback device unavailable: %s", exc)
        return None, False

    def _pick_loopback_target(self, wasapi_outputs: set[int]) -> Optional[int]:
        # If user configured a device and it's still present, honor it.
        if self.spk_device is not None and self.spk_device in wasapi_outputs:
            return self.spk_device
        # Prefer known virtual/loopback-friendly names (VB-CABLE, virtual).
        preferred = ["cable output", "vb-audio", "virtual", "loopback"]
        picked = choose_wasapi_loopback(preferred_names=preferred)
        if picked is not None and picked in wasapi_outputs:
            return picked
        default_loop = default_wasapi_loopback_device()
        if default_loop is not None and default_loop in wasapi_outputs:
            return default_loop
        if wasapi_outputs:
            return next(iter(wasapi_outputs))
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
        loopback, use_flag = self._loopback_device()
        if not loopback:
            raise RuntimeError("Speaker loopback capture not available on this system.")

        try:
            self._spk_stream = self._open_loopback_stream(loopback, use_flag)
            self._spk_stream.start_stream()
        except Exception as exc:
            raise RuntimeError(f"Unable to start speaker capture: {exc}") from exc

        self._start_output_watcher()

    def _stop_streams(self) -> None:
        self._stop_output_watcher()
        for stream in (self._spk_stream,):
            try:
                if stream:
                    stream.stop()
                    stream.close()
            except Exception:
                pass
        self._spk_stream = None

    @staticmethod
    def _enqueue(target_queue: queue.Queue[np.ndarray]):
        def callback(indata, frames, time_info, status):
            if status:
                logging.debug("Input status: %s", status)
            # Convert incoming bytes to float32 mono samples
            arr = np.frombuffer(indata, dtype=np.float32)
            if arr.size:
                arr = arr.reshape(-1, 1)
                target_queue.put(arr.copy())
            return None, pyaudio.paContinue

        return callback

    def _run(self) -> None:
        spk_buffer: list[np.ndarray] = []
        frames_target = int(self.chunk_seconds * self.sample_rate)
        last_write = time.monotonic()

        while self._running.is_set():
            try:
                spk_buffer.append(self.spk_queue.get(timeout=0.1))
            except queue.Empty:
                pass

            elapsed = time.monotonic() - last_write
            spk_frames = int(sum(chunk.shape[0] for chunk in spk_buffer))

            if spk_frames >= frames_target or elapsed >= self.chunk_seconds:
                self._write_chunk(spk_buffer)
                spk_buffer.clear()
                last_write = time.monotonic()

    def _write_chunk(self, spk_buffer: list[np.ndarray]) -> None:
        try:
            spk_data = np.concatenate(spk_buffer) if spk_buffer else None
            frames = spk_data.shape[0] if spk_data is not None else 0
            target_frames = int(self.chunk_seconds * self.sample_rate)
            frames = max(frames, target_frames)
            if frames == 0:
                return

            def pad(data: Optional[np.ndarray]) -> np.ndarray:
                if data is None:
                    return np.zeros((frames, 1), dtype="float32")
                if data.shape[0] < frames:
                    pad_len = frames - data.shape[0]
                    data = np.vstack([data, np.zeros((pad_len, 1), dtype="float32")])
                return data

            spk_channel = pad(spk_data)
            # Headroom normalization to prevent clipping
            def normalize(data: np.ndarray) -> np.ndarray:
                peak = float(np.max(np.abs(data))) if data.size else 0.0
                if peak > 1.0:
                    data = data / peak
                return np.clip(data, -1.0, 1.0)

            spk_channel = normalize(spk_channel)
            # Avoid saving completely silent chunks to reduce noise; require some activity.
            if np.max(np.abs(spk_channel)) < 1e-4:
                logging.debug("Chunk skipped due to silence.")
                return
            mono = spk_channel  # single-channel WAV

            self._chunk_counter += 1
            timestamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
            base_name = f"chunk_{timestamp}_{self._chunk_counter:04d}"
            filepath = self.output_dir / f"{base_name}.wav"
            # Avoid accidental overwrite if timestamps collide.
            suffix = 1
            while filepath.exists():
                filepath = self.output_dir / f"{base_name}_{suffix}.wav"
                suffix += 1
            sf.write(filepath, mono, samplerate=self.sample_rate, subtype="PCM_16")
            logging.debug("Saved audio chunk: %s", filepath)
            if self.on_chunk:
                try:
                    self.on_chunk(filepath)
                except Exception as exc:  # pragma: no cover - callback safety
                    logging.debug("Chunk callback failed: %s", exc)
        except Exception as exc:
            logging.exception("Failed to write chunk: %s", exc)

    def _start_output_watcher(self) -> None:
        if not sys.platform.startswith("win"):
            return
        if self._watch_thread and self._watch_thread.is_alive():
            return
        self._watch_stop.clear()
        self._watch_thread = threading.Thread(target=self._watch_output_changes, name="output-watch", daemon=True)
        self._watch_thread.start()

    def _stop_output_watcher(self) -> None:
        self._watch_stop.set()
        if self._watch_thread:
            self._watch_thread.join(timeout=2)
        self._watch_thread = None

    def _watch_output_changes(self) -> None:
        """Monitor for output device changes and restart loopback stream if needed."""
        while self._running.is_set() and not self._watch_stop.is_set():
            try:
                wasapi_outputs = {idx for idx, _ in list_wasapi_loopback_devices()}
                target = self._pick_loopback_target(wasapi_outputs)
                if target != self._active_loopback_device:
                    logging.debug("Detected output change; switching loopback to %s", target)
                    self._restart_speaker(target)
                time.sleep(5)
            except Exception as exc:  # pragma: no cover - guard
                logging.debug("Output watch error: %s", exc)
                time.sleep(5)

    def _restart_speaker(self, target: Optional[int]) -> None:
        try:
            if self._spk_stream:
                self._spk_stream.stop_stream()
                self._spk_stream.close()
                self._spk_stream = None

            self._active_loopback_device = target
            if target is None:
                raise RuntimeError("No loopback target available to restart.")

            try:
                loopback, use_flag = target, True

                self._spk_stream = self._open_loopback_stream(loopback, use_flag)
                self._spk_stream.start_stream()
                logging.debug("Speaker loopback restarted on device %s", target)
            except Exception as exc:
                raise RuntimeError(f"Failed to restart speaker loopback: {exc}") from exc
        except Exception as exc:
            logging.error("Failed to restart speaker loopback: %s", exc)

    def _open_loopback_stream(self, loopback: int, _needs_loopback_flag: bool):
        """Create a WASAPI loopback InputStream via PyAudioWPatch."""
        frames_per_buffer = 1024
        return self._pa.open(
            format=pyaudio.paFloat32,
            channels=1,
            rate=self.sample_rate,
            input=True,
            frames_per_buffer=frames_per_buffer,
            input_device_index=loopback,
            stream_callback=self._enqueue(self.spk_queue),
        )

    def probe_device(self, device_index: int) -> bool:
        """Attempt to open a loopback stream on the given device index."""
        try:
            stream = self._pa.open(
                format=pyaudio.paFloat32,
                channels=1,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=256,
                input_device_index=device_index,
            )
            stream.start_stream()
            stream.stop_stream()
            stream.close()
            return True
        except Exception as exc:
            logging.debug("Probe failed for device %s: %s", device_index, exc)
            return False
