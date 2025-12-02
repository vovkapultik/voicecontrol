import datetime
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Optional
import sys
import ctypes

import numpy as np
import sounddevice as sd
import soundfile as sf
try:
    import soundcard as sc  # type: ignore
except Exception:
    sc = None
from .devices import first_input_device, default_output_device, default_input_device, list_output_devices, list_wasapi_output_devices


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
        self._active_loopback_device: Optional[int] = None
        self._loopback_needs_flag: bool = False
        self._watch_thread: Optional[threading.Thread] = None
        self._watch_stop = threading.Event()
        self._sc_thread: Optional[threading.Thread] = None
        self._sc_stop = threading.Event()
        self._sc_speaker = None

    def _loopback_device(self) -> tuple[Optional[object], bool]:
        """Return (device, use_loopback_flag) for speaker capture."""
        if not sys.platform.startswith("win"):
            logging.warning("Speaker loopback capture is only supported on Windows (WASAPI).")
            return None, False
        try:
            # Prefer user-selected device if it is WASAPI-capable.
            wasapi_outputs = {idx for idx, _ in list_wasapi_output_devices()}
            target = self._pick_loopback_target(wasapi_outputs)
            if target is None:
                logging.error("No WASAPI output device found; loopback unavailable. Enable a WASAPI device (e.g., system speakers).")
                return None, False
            if target != self._active_loopback_device:
                logging.info("Using WASAPI output device %s for loopback", target)
                self._active_loopback_device = target

            # Prefer WasapiLoopback helper; fallback to loopback=True if not present.
            if hasattr(sd, "WasapiLoopback"):
                return sd.WasapiLoopback(target), False
            else:
                logging.info("WasapiLoopback helper not available; using loopback=True on device %s", target)
                return target, True
        except Exception as exc:
            logging.warning("Loopback device unavailable: %s", exc)
        return None, False

    def _pick_loopback_target(self, wasapi_outputs: set[int]) -> Optional[int]:
        if self.spk_device is not None and self.spk_device in wasapi_outputs:
            return self.spk_device
        default_out = default_output_device()
        if default_out is not None and default_out in wasapi_outputs:
            return default_out
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
        def start_mic(device: Optional[int]) -> bool:
            if device is not None and device == -1:
                device = None
            try:
                self._mic_stream = sd.InputStream(
                    device=device,
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="float32",
                    callback=self._enqueue(self.mic_queue),
                )
                self._mic_stream.start()
                logging.info("Mic capture started on %s", "default" if device is None else device)
                return True
            except Exception as exc:
                logging.error("Unable to start microphone capture (device=%s): %s", device, exc)
                return False

        # Try ordered candidates: configured -> default input -> first available
        candidates = []
        if self.mic_device is not None:
            candidates.append(self.mic_device)
        default_in = default_input_device()
        if default_in is not None and default_in not in candidates:
            candidates.append(default_in)
        alt = first_input_device()
        if alt is not None and alt not in candidates:
            candidates.append(alt)

        if not candidates:
            logging.error("No input devices available; microphone will not be recorded.")
        else:
            for cand in candidates:
                if start_mic(cand):
                    break
            else:
                logging.error("Unable to start microphone on any device candidate: %s", candidates)

        loopback, use_flag = self._loopback_device()
        if loopback:
            try:
                if use_flag:
                    logging.error("loopback flag not supported by this sounddevice build; skipping sounddevice loopback.")
                else:
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
                self._spk_stream = None
        else:
            logging.warning("Speaker loopback capture not available via sounddevice; attempting soundcard fallback.")
        if self._spk_stream is None:
            self._start_soundcard_loopback()
        self._start_output_watcher()

    def _stop_streams(self) -> None:
        self._stop_output_watcher()
        self._stop_soundcard_loopback()
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
            # Ensure we at least hit the configured chunk duration even if data is sparse.
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
                wasapi_outputs = {idx for idx, _ in list_wasapi_output_devices()}
                target = self._pick_loopback_target(wasapi_outputs)
                if target != self._active_loopback_device:
                    logging.info("Detected output change; switching loopback to %s", target)
                    self._restart_speaker(target)
                time.sleep(5)
            except Exception as exc:  # pragma: no cover - guard
                logging.debug("Output watch error: %s", exc)
                time.sleep(5)

    def _restart_speaker(self, target: Optional[int]) -> None:
        try:
            if self._spk_stream:
                self._spk_stream.stop()
                self._spk_stream.close()
                self._spk_stream = None
            self._stop_soundcard_loopback()
            self._active_loopback_device = target
            if target is None:
                logging.warning("No loopback target available to restart.")
                self._start_soundcard_loopback()
                return
            try:
                loopback, use_flag = (None, False)
                if hasattr(sd, "WasapiLoopback"):
                    loopback = sd.WasapiLoopback(target)
                else:
                    loopback, use_flag = target, True
                if use_flag:
                    raise TypeError("loopback flag unsupported in this sounddevice build")
                self._spk_stream = sd.InputStream(
                    device=loopback,
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="float32",
                    callback=self._enqueue(self.spk_queue),
                )
                self._spk_stream.start()
                logging.info("Speaker loopback restarted on device %s", target)
            except TypeError:
                logging.warning("Loopback flag not supported; falling back to soundcard loopback")
                self._start_soundcard_loopback()
            except Exception as exc:
                logging.error("Failed to restart speaker loopback: %s", exc)
                self._start_soundcard_loopback()
        except Exception as exc:
            logging.error("Failed to restart speaker loopback: %s", exc)

    def _start_soundcard_loopback(self) -> None:
        if sc is None:
            logging.error("soundcard module not available; cannot start fallback loopback.")
            return
        if self._sc_thread and self._sc_thread.is_alive():
            return
        try:
            loopback_mic = self._find_soundcard_loopback()
            if loopback_mic is None:
                logging.error("No soundcard loopback device found for fallback.")
                return
            self._sc_speaker = loopback_mic
            self._sc_stop.clear()

            def run() -> None:
                try:
                    self._init_com()
                    while not self._sc_stop.is_set() and self._running.is_set():
                        if hasattr(loopback_mic, "recorder"):
                            with loopback_mic.recorder(samplerate=self.sample_rate, channels=1, blocksize=1024) as rec:
                                while not self._sc_stop.is_set() and self._running.is_set():
                                    data = rec.record(numframes=1024)
                                    self._push_speaker_data(data)
                        elif hasattr(loopback_mic, "record"):
                            data = loopback_mic.record(numframes=1024, samplerate=self.sample_rate, channels=1)
                            self._push_speaker_data(data)
                            time.sleep(0.01)
                        else:
                            logging.error("Loopback device does not support recorder/record methods.")
                            break
                except Exception as exc_inner:
                    logging.error("Soundcard loopback thread failed: %s", exc_inner)

            self._sc_thread = threading.Thread(target=run, name="soundcard-loopback", daemon=True)
            self._sc_thread.start()
            logging.info("Started soundcard loopback fallback using %s", loopback_mic)
        except Exception as exc:
            logging.error("Unable to start soundcard loopback fallback: %s", exc)

    def _stop_soundcard_loopback(self) -> None:
        self._sc_stop.set()
        if self._sc_thread:
            self._sc_thread.join(timeout=2)
        self._sc_thread = None
        self._sc_speaker = None

    def _find_soundcard_loopback(self):
        """Try to locate a loopback-capable microphone from soundcard."""
        if sc is None:
            return None
        try:
            # Prefer default speaker loopback mic if available
            loopbacks = []
            if hasattr(sc, "all_microphones"):
                loopbacks = sc.all_microphones(include_loopback=True)
            if loopbacks:
                for mic in loopbacks:
                    name = getattr(mic, "name", "").lower()
                    if "loopback" in name or "cable output" in name or "virtual" in name:
                        return mic
                return loopbacks[0]
        except Exception as exc:
            logging.error("Error finding soundcard loopback device: %s", exc)
        return None

    def _push_speaker_data(self, data: Optional[np.ndarray]) -> None:
        """Normalize speaker data into float32 mono and enqueue."""
        if data is None:
            return
        try:
            if isinstance(data, (bytes, bytearray)):
                data = np.frombuffer(data, dtype="float32")
            data = np.asarray(data, dtype="float32")
            if data.ndim == 1:
                data = data.reshape(-1, 1)
            elif data.ndim > 1 and data.shape[1] > 1:
                data = data[:, :1]
            if data.size:
                self.spk_queue.put(data)
        except Exception as exc:
            logging.debug("Failed to enqueue speaker data: %s", exc)

    @staticmethod
    def _init_com() -> None:
        """Initialize COM for soundcard on Windows to avoid RPC_E_CHANGED_MODE (0x800401f0)."""
        if not sys.platform.startswith("win"):
            return
        try:
            ole32 = ctypes.windll.ole32  # type: ignore
            COINIT_MULTITHREADED = 0x0
            ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
        except Exception:
            pass
