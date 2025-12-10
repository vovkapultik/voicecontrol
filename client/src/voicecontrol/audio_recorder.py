import datetime
import io
import logging
import queue
import threading
import time
from typing import Callable, Optional
import sys

import numpy as np
import soundfile as sf
from .devices import (
    default_wasapi_loopback_device,
    list_wasapi_loopback_devices,
    choose_wasapi_loopback,
    list_input_devices,
    default_input_device,
)

try:
    import pyaudiowpatch as pyaudio
except ImportError as exc:  # pragma: no cover - runtime guard
    raise RuntimeError(
        "PyAudioWPatch is required for WASAPI loopback. Install with: pip install --upgrade --force-reinstall PyAudioWPatch"
    ) from exc


class AudioRecorder:
    """Capture system audio and stream chunked WAV payloads."""

    def __init__(
        self,
        chunk_seconds: int = 30,
        sample_rate: int = 48_000,
        on_chunk: Optional[Callable[[str, bytes], None]] = None,
        spk_device: Optional[int] = None,
        mic_device: Optional[int] = None,
    ) -> None:
        self.chunk_seconds = max(0.25, float(chunk_seconds))
        self.sample_rate = sample_rate
        self.on_chunk = on_chunk
        self.spk_device = spk_device
        self.mic_device = mic_device
        self._calibration_tone_seconds = 0.5
        self._calibration_tone_hz = 880.0
        self.spk_queue: queue.Queue[tuple[float, np.ndarray]] = queue.Queue()
        self.mic_queue: queue.Queue[tuple[float, np.ndarray]] = queue.Queue()
        self._running = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._spk_stream: Optional[pyaudio.Stream] = None
        self._mic_stream: Optional[pyaudio.Stream] = None
        self._active_loopback_device: Optional[int] = None
        self._watch_thread: Optional[threading.Thread] = None
        self._watch_stop = threading.Event()
        self._pa = pyaudio.PyAudio()
        self._chunk_counter = 0
        self._spk_rate = float(sample_rate)
        self._mic_rate = float(sample_rate)
        self._current_chunk_start = datetime.datetime.utcnow()
        self._start_mono: Optional[float] = None
        self._first_chunk_written = False
        self._calibration_end_mono: Optional[float] = None

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
        self._reset_buffers()
        self._start_mono = time.monotonic()
        self._running.set()
        self._start_streams()
        self._kick_calibration()
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
            self._spk_rate = self._device_rate(loopback) or float(self.sample_rate)
            self._spk_stream = self._open_loopback_stream(loopback, use_flag)
            self._spk_stream.start_stream()
        except Exception as exc:
            raise RuntimeError(f"Unable to start speaker capture: {exc}") from exc

        # Microphone capture (optional if device available)
        if self.mic_device is None:
            mic_idx = self._pick_mic_device()
            self.mic_device = mic_idx
        if self.mic_device is not None:
            try:
                self._mic_rate = self._device_rate(self.mic_device) or float(self.sample_rate)
                self._mic_stream = self._open_mic_stream(self.mic_device)
                self._mic_stream.start_stream()
            except Exception as exc:
                logging.warning("Unable to start microphone capture: %s", exc)
                self._mic_stream = None

        self._start_output_watcher()

    def _stop_streams(self) -> None:
        self._stop_output_watcher()
        for stream in (self._spk_stream, self._mic_stream):
            try:
                if stream:
                    stream.stop_stream()
                    stream.close()
            except Exception:
                pass
        self._spk_stream = None
        self._mic_stream = None

    @staticmethod
    def _enqueue(target_queue: queue.Queue[tuple[float, np.ndarray]], sample_rate: float):
        """Create a PyAudio callback that tags buffers with their start time."""
        time_offset: Optional[float] = None  # maps PortAudio time base to Python monotonic

        def callback(indata, frames, time_info, status):
            nonlocal time_offset
            if status:
                logging.debug("Input status: %s", status)
            arr = np.frombuffer(indata, dtype=np.float32)
            if arr.size:
                arr = arr.reshape(-1, 1)
                pa_time = None
                try:
                    pa_time = time_info.get("input_buffer_adc_time") or time_info.get("current_time")
                    if pa_time == 0:
                        pa_time = None
                except Exception:
                    pa_time = None

                if pa_time is not None:
                    if time_offset is None:
                        # Capture the offset between PortAudio's clock and Python's monotonic clock.
                        time_offset = time.monotonic() - float(pa_time)
                    ts = float(pa_time) + time_offset
                else:
                    # Fall back to assuming the buffer represents audio that started frames/sample_rate ago.
                    ts = time.monotonic() - (frames / float(sample_rate))
                target_queue.put((float(ts), arr.copy()))
            return None, pyaudio.paContinue

        return callback

    def _run(self) -> None:
        spk_buffer: list[tuple[float, np.ndarray]] = []
        mic_buffer: list[tuple[float, np.ndarray]] = []
        frames_target = int(self.chunk_seconds * self.sample_rate)
        chunk_start_mono: Optional[float] = self._start_mono
        chunk_start_wall: Optional[datetime.datetime] = self._wall_time_for(chunk_start_mono) if chunk_start_mono else None
        spk_offset: Optional[float] = None
        mic_offset: Optional[float] = None

        while self._running.is_set():
            self._drain_queue(self.spk_queue, spk_buffer, drop_before=self._calibration_end_mono)
            self._drain_queue(self.mic_queue, mic_buffer)

            earliest = None
            if spk_buffer:
                earliest = spk_buffer[0][0]
            if mic_buffer:
                earliest = mic_buffer[0][0] if earliest is None else min(earliest, mic_buffer[0][0])

            if chunk_start_mono is None:
                chunk_start_mono = earliest if earliest is not None else time.monotonic()
                chunk_start_wall = self._wall_time_for(chunk_start_mono)

            if spk_buffer and spk_offset is None and chunk_start_mono is not None:
                spk_offset = max(spk_buffer[0][0] - chunk_start_mono, 0.0)
            if mic_buffer and mic_offset is None and chunk_start_mono is not None:
                mic_offset = max(mic_buffer[0][0] - chunk_start_mono, 0.0)

            elapsed = time.monotonic() - chunk_start_mono
            spk_frames = int(sum(chunk[1].shape[0] for chunk in spk_buffer))
            mic_frames = int(sum(chunk[1].shape[0] for chunk in mic_buffer))
            max_frames = max(spk_frames, mic_frames)

            if not self._first_chunk_written and not spk_buffer and not mic_buffer:
                time.sleep(0.005)
                continue

            write_now = False
            if not self._first_chunk_written and (spk_buffer or mic_buffer):
                if chunk_start_mono is None:
                    chunk_start_mono = self._start_mono or (earliest if earliest is not None else time.monotonic())
                    chunk_start_wall = self._wall_time_for(chunk_start_mono)
                write_now = True

            if write_now or max_frames >= frames_target or elapsed >= self.chunk_seconds:
                if chunk_start_wall is None and chunk_start_mono is not None:
                    chunk_start_wall = self._wall_time_for(chunk_start_mono)
                duration = self._write_chunk(
                    spk_buffer,
                    mic_buffer,
                    chunk_start_wall or datetime.datetime.utcnow(),
                    final=False,
                    spk_offset=spk_offset,
                    mic_offset=mic_offset,
                )
                self._first_chunk_written = True
                spk_buffer.clear()
                mic_buffer.clear()
                if duration > 0 and chunk_start_mono is not None:
                    chunk_start_mono = chunk_start_mono + duration
                    if chunk_start_wall is not None:
                        chunk_start_wall = chunk_start_wall + datetime.timedelta(seconds=duration)
                    else:
                        chunk_start_wall = self._wall_time_for(chunk_start_mono)
                else:
                    chunk_start_mono = None
                    chunk_start_wall = None
                spk_offset = None
                mic_offset = None

            time.sleep(0.005)

        # Flush any remaining audio on stop without padding to full chunk length.
        self._drain_queue(self.spk_queue, spk_buffer, drop_before=self._calibration_end_mono)
        self._drain_queue(self.mic_queue, mic_buffer)
        if spk_buffer or mic_buffer:
            if chunk_start_wall is None:
                chunk_start_wall = self._wall_time_for(chunk_start_mono or time.monotonic())
            self._write_chunk(spk_buffer, mic_buffer, chunk_start_wall, final=True, spk_offset=spk_offset, mic_offset=mic_offset)

    def _drain_queue(
        self,
        src: queue.Queue[tuple[float, np.ndarray]],
        target: list[tuple[float, np.ndarray]],
        drop_before: Optional[float] = None,
    ) -> None:
        while True:
            try:
                ts, buf = src.get_nowait()
                if drop_before is not None and ts < drop_before:
                    continue
                target.append((ts, buf))
            except queue.Empty:
                break

    def _reset_buffers(self) -> None:
        # Drop any stale data from previous runs to avoid leaking old audio into new sessions.
        self.spk_queue = queue.Queue()
        self.mic_queue = queue.Queue()
        self._chunk_counter = 0
        self._spk_rate = float(self.sample_rate)
        self._mic_rate = float(self.sample_rate)
        self._start_mono = None
        self._first_chunk_written = False
        self._calibration_end_mono = None

    def _write_chunk(
        self,
        spk_buffer: list[tuple[float, np.ndarray]],
        mic_buffer: list[tuple[float, np.ndarray]],
        chunk_start: datetime.datetime,
        final: bool,
        spk_offset: Optional[float],
        mic_offset: Optional[float],
    ) -> float:
        try:
            spk_data = np.concatenate([b for _, b in spk_buffer]) if spk_buffer else None
            mic_data = np.concatenate([b for _, b in mic_buffer]) if mic_buffer else None

            # Resample if device rates differ.
            target_rate = float(self.sample_rate)
            if spk_data is not None and self._spk_rate != target_rate:
                spk_data = self._resample(spk_data, self._spk_rate, target_rate)
            if mic_data is not None and self._mic_rate != target_rate:
                mic_data = self._resample(mic_data, self._mic_rate, target_rate)

            # Recompute lengths after resample and choose desired length.
            frames_spk = spk_data.shape[0] if spk_data is not None else 0
            frames_mic = mic_data.shape[0] if mic_data is not None else 0
            spk_delay = int(round(max(spk_offset or 0.0, 0.0) * self.sample_rate))
            mic_delay = int(round(max(mic_offset or 0.0, 0.0) * self.sample_rate))

            target_frames = int(self.chunk_seconds * self.sample_rate)
            desired = max(
                frames_spk + spk_delay,
                frames_mic + mic_delay,
                target_frames if not final else 0,
            )
            if desired == 0:
                return 0.0

            def place(data: Optional[np.ndarray], delay: int) -> np.ndarray:
                body = data if data is not None else np.zeros((0, 1), dtype="float32")
                total = delay + body.shape[0]
                tail = desired - total if total < desired else 0
                head = np.zeros((delay, 1), dtype="float32") if delay > 0 else np.zeros((0, 1), dtype="float32")
                body_trim = body[: max(desired - delay, 0)]
                tail_pad = np.zeros((tail, 1), dtype="float32") if tail > 0 else np.zeros((0, 1), dtype="float32")
                return np.vstack([head, body_trim, tail_pad])

            spk_channel = place(spk_data, spk_delay)
            mic_channel = place(mic_data, mic_delay)

            # Headroom normalization to prevent clipping
            def normalize(data: np.ndarray) -> np.ndarray:
                peak = float(np.max(np.abs(data))) if data.size else 0.0
                if peak > 1.0:
                    data = data / peak
                return np.clip(data, -1.0, 1.0)

            spk_channel = normalize(spk_channel)
            mic_channel = normalize(mic_channel)

            # Do not time-shift streams; preserve original timing so silence stays where it occurred.
            combined = normalize((spk_channel + mic_channel) / 2.0)

            mono = combined  # single-channel WAV

            duration_seconds = mono.shape[0] / float(self.sample_rate)
            chunk_end = chunk_start + datetime.timedelta(seconds=duration_seconds)
            start_str = chunk_start.strftime("%Y%m%d-%H%M%S")
            end_str = chunk_end.strftime("%Y%m%d-%H%M%S")
            base_name = f"{start_str}-{end_str}"
            filename = f"{base_name}.wav"
            buffer = io.BytesIO()
            sf.write(buffer, mono, samplerate=self.sample_rate, subtype="PCM_16", format="WAV")
            data = buffer.getvalue()
            logging.debug("Prepared in-memory audio chunk: %s (%s bytes)", filename, len(data))
            if self.on_chunk:
                try:
                    self.on_chunk(filename, data)
                except Exception as exc:  # pragma: no cover - callback safety
                    logging.debug("Chunk callback failed: %s", exc)
            return duration_seconds
        except Exception as exc:
            logging.exception("Failed to write chunk: %s", exc)
        return 0.0

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
            stream_callback=self._enqueue(self.spk_queue, self.sample_rate),
        )

    def _open_mic_stream(self, mic_index: int):
        frames_per_buffer = 1024
        return self._pa.open(
            format=pyaudio.paFloat32,
            channels=1,
            rate=self.sample_rate,
            input=True,
            frames_per_buffer=frames_per_buffer,
            input_device_index=mic_index,
            stream_callback=self._enqueue(self.mic_queue, self.sample_rate),
        )

    def _pick_mic_device(self) -> Optional[int]:
        devices = list_input_devices()
        if self.mic_device is not None:
            return self.mic_device
        if not devices:
            return None
        # Prefer default input if available
        default = default_input_device()
        if default is not None:
            return default
        return devices[0][0]

    def _resample(self, data: np.ndarray, src_rate: float, target_rate: float) -> np.ndarray:
        """Simple linear resampler to target sample rate."""
        if src_rate == target_rate or data.size == 0:
            return data
        ratio = target_rate / src_rate
        target_len = max(1, int(round(data.shape[0] * ratio)))
        x_old = np.linspace(0, 1, num=data.shape[0], endpoint=False)
        x_new = np.linspace(0, 1, num=target_len, endpoint=False)
        resampled = np.interp(x_new, x_old, data[:, 0])
        return resampled.reshape(-1, 1).astype(np.float32)

    def _device_rate(self, device_index: int) -> Optional[float]:
        try:
            info = self._pa.get_device_info_by_index(int(device_index))
            return float(info.get("defaultSampleRate", self.sample_rate))
        except Exception:
            return None

    def _wall_time_for(self, mono_ts: float) -> datetime.datetime:
        """Best-effort conversion from monotonic seconds to wall clock."""
        delta = time.monotonic() - mono_ts
        return datetime.datetime.utcnow() - datetime.timedelta(seconds=delta)

    def _kick_calibration(self) -> None:
        """Play a tiny ping to force WASAPI to deliver initial buffers, then ignore it."""
        try:
            start = time.monotonic()
            self._calibration_end_mono = start + self._calibration_tone_seconds + 0.1
            thread = threading.Thread(target=self._play_calibration_ping, name="calibration-ping", daemon=True)
            thread.start()
        except Exception as exc:
            logging.debug("Failed to start calibration ping: %s", exc)
            self._calibration_end_mono = None

    def _play_calibration_ping(self) -> None:
        try:
            frames = int(self._calibration_tone_seconds * self.sample_rate)
            if frames <= 0:
                return
            t = np.arange(frames, dtype=np.float32) / float(self.sample_rate)
            tone = (0.2 * np.sin(2 * np.pi * self._calibration_tone_hz * t)).astype(np.float32)

            # Use a dedicated PyAudio instance for output to avoid interfering with input streams.
            pa_out = pyaudio.PyAudio()
            stream = pa_out.open(
                format=pyaudio.paFloat32,
                channels=1,
                rate=self.sample_rate,
                output=True,
                frames_per_buffer=1024,
            )
            try:
                stream.start_stream()
                stream.write(tone.tobytes())
                stream.stop_stream()
            finally:
                stream.close()
                pa_out.terminate()
        except Exception as exc:
            logging.debug("Calibration ping failed: %s", exc)

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
