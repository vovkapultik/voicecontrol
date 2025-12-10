from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Tuple, List

from .config import ConfigManager
from .audio_recorder import AudioRecorder
from .devices import (
    list_wasapi_loopback_devices,
    list_output_devices,
    list_input_devices,
)


@dataclass
class DeviceStatus:
    text: str
    color: str
    selected: Optional[Tuple[int, str]] = None


class AppController:
    """Encapsulate recording control and device selection."""

    def __init__(self, config: ConfigManager, recorder: AudioRecorder) -> None:
        self.config = config
        self.recorder = recorder
        self.device_status = DeviceStatus("", "red", None)
        self.mic_status = DeviceStatus("", "red", None)
        self.is_recording = False

    # Recording control -------------------------------------------------
    def start_recording(self) -> tuple[bool, str]:
        try:
            self.recorder.chunk_seconds = self.config.config.chunk_seconds
            self.recorder.start()
            self.is_recording = True
            return True, "Recording"
        except Exception as exc:
            self.is_recording = False
            try:
                self.recorder.stop()
            except Exception:
                pass
            logging.exception("Failed to start recording: %s", exc)
            return False, f"Error: {exc}"

    def stop_recording(self) -> tuple[bool, str]:
        try:
            self.recorder.stop()
            self.is_recording = False
            return True, "Stopped"
        except Exception as exc:
            logging.exception("Failed to stop recording: %s", exc)
            return False, f"Error: {exc}"

    def toggle_recording(self) -> tuple[bool, str, bool]:
        """Toggle recording. Returns (ok, message, is_recording_now)."""
        if self.is_recording:
            ok, msg = self.stop_recording()
            return ok, msg, False
        ok, msg = self.start_recording()
        return ok, msg, ok if ok else False

    # Device selection --------------------------------------------------
    def available_devices(self) -> List[tuple[int, str]]:
        return list_wasapi_loopback_devices() or list_output_devices() or []

    def available_mics(self) -> List[tuple[int, str]]:
        return list_input_devices() or []

    def auto_select_device(self) -> tuple[List[tuple[int, str]], Optional[tuple[int, str]], DeviceStatus]:
        devices = self.available_devices()
        chosen: Optional[tuple[int, str]] = None
        for idx, name in devices:
            if self.recorder.probe_device(idx):
                chosen = (idx, name)
                self._apply_device(idx, name, auto=True)
                break
        if chosen is None:
            self._clear_device()
        return devices, chosen, self.device_status

    def auto_select_mic(self) -> tuple[List[tuple[int, str]], Optional[tuple[int, str]], DeviceStatus]:
        devices = self.available_mics()
        chosen: Optional[tuple[int, str]] = None
        for idx, name in devices:
            if self.recorder.probe_device(idx):
                chosen = (idx, name)
                self._apply_mic(idx, name, auto=True)
                break
        if chosen is None:
            self._clear_mic()
        return devices, chosen, self.mic_status

    def set_device(self, device_selection: Optional[int]) -> DeviceStatus:
        was_running = self.is_recording
        if was_running:
            self.stop_recording()

        if device_selection is None:
            self._clear_device()
        else:
            name = self._device_name(device_selection)
            self._apply_device(device_selection, name, auto=False)

        if was_running:
            self.start_recording()
        return self.device_status

    def set_mic(self, device_selection: Optional[int]) -> DeviceStatus:
        was_running = self.is_recording
        if was_running:
            self.stop_recording()

        if device_selection is None:
            self._clear_mic()
        else:
            name = self._mic_name(device_selection)
            self._apply_mic(device_selection, name, auto=False)

        if was_running:
            self.start_recording()
        return self.mic_status

    def _device_name(self, device_index: int) -> str:
        for idx, name in self.available_devices():
            if idx == device_index:
                return name
        return f"{device_index}"

    def _apply_device(self, device_index: int, name: str, auto: bool) -> None:
        self.config.update(spk_device=device_index)
        self.recorder.spk_device = device_index
        text = f"Auto-selected device {device_index}:{name}" if auto else f"Selected device {device_index}:{name}"
        self.device_status = DeviceStatus(text=text, color="green", selected=(device_index, name))

    def _clear_device(self) -> None:
        self.config.update(spk_device=None)
        self.recorder.spk_device = None
        self.device_status = DeviceStatus(
            text="We can't connect to any of your devices. Try manually.", color="red", selected=None
        )

    def _apply_mic(self, device_index: int, name: str, auto: bool) -> None:
        self.config.update(mic_device=device_index)
        self.recorder.mic_device = device_index
        text = f"Auto-selected mic {device_index}:{name}" if auto else f"Selected mic {device_index}:{name}"
        self.mic_status = DeviceStatus(text=text, color="green", selected=(device_index, name))

    def _clear_mic(self) -> None:
        self.config.update(mic_device=None)
        self.recorder.mic_device = None
        self.mic_status = DeviceStatus(
            text="We can't connect to any microphone. Try manually.", color="red", selected=None
        )

    def _mic_name(self, device_index: int) -> str:
        for idx, name in self.available_mics():
            if idx == device_index:
                return name
        return f"{device_index}"
