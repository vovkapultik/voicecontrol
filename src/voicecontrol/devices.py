import logging
from typing import List, Tuple

import sounddevice as sd

DeviceInfo = Tuple[int, str]


def list_input_devices() -> List[DeviceInfo]:
    devices: List[DeviceInfo] = []
    try:
        for idx, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0:
                devices.append((idx, dev.get("name", f"Device {idx}")))
    except Exception as exc:
        logging.error("Failed to query devices: %s", exc)
    return devices


def default_input_device() -> int | None:
    try:
        default = sd.default.device[0]
        if default is None or default == -1:
            return None
        return int(default)
    except Exception:
        return None


def first_input_device() -> int | None:
    devices = list_input_devices()
    return devices[0][0] if devices else None


def default_output_device() -> int | None:
    try:
        default = sd.default.device[1]
        if default is None or default == -1:
            return None
        return int(default)
    except Exception:
        return None


def list_output_devices() -> List[DeviceInfo]:
    devices: List[DeviceInfo] = []
    try:
        for idx, dev in enumerate(sd.query_devices()):
            if dev.get("max_output_channels", 0) > 0:
                devices.append((idx, dev.get("name", f"Device {idx}")))
    except Exception as exc:
        logging.error("Failed to query output devices: %s", exc)
    return devices


def list_wasapi_output_devices() -> List[DeviceInfo]:
    """Return output devices on the WASAPI host API (required for loopback)."""
    results: List[DeviceInfo] = []
    try:
        hostapis = sd.query_hostapis()
        wasapi_indices = {i for i, api in enumerate(hostapis) if "WASAPI" in api.get("name", "").upper()}
        for idx, dev in enumerate(sd.query_devices()):
            if dev.get("max_output_channels", 0) <= 0:
                continue
            if dev.get("hostapi") in wasapi_indices:
                results.append((idx, dev.get("name", f"Device {idx}")))
    except Exception as exc:
        logging.error("Failed to query WASAPI output devices: %s", exc)
    return results


def has_wasapi_output_devices() -> bool:
    return bool(list_wasapi_output_devices())


def choose_wasapi_output(preferred_names: list[str] | None = None) -> int | None:
    """Pick the best WASAPI output device index.

    Preference order:
    1) Matches one of preferred_names (case-insensitive substring match)
    2) Non-Remote/virtual devices
    3) First available
    """
    devices = list_wasapi_output_devices()
    if not devices:
        return None
    preferred = [p.lower() for p in (preferred_names or [])]
    for idx, name in devices:
        lower = name.lower()
        if any(p in lower for p in preferred):
            return idx
    for idx, name in devices:
        lower = name.lower()
        if "remote audio" not in lower:
            return idx
    return devices[0][0]
