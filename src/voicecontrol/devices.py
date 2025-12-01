import logging
import sys
from contextlib import contextmanager
from typing import List, Tuple, Optional

try:
    import pyaudiowpatch as pyaudio
except ImportError as exc:  # pragma: no cover - runtime guard
    raise RuntimeError(
        "PyAudioWPatch is required for WASAPI loopback. Install with: pip install --upgrade --force-reinstall PyAudioWPatch"
    ) from exc

DeviceInfo = Tuple[int, str]


@contextmanager
def _pa() -> pyaudio.PyAudio:
    pa = pyaudio.PyAudio()
    try:
        yield pa
    finally:
        pa.terminate()


def _is_wasapi(pa: pyaudio.PyAudio, device_info: dict) -> bool:
    try:
        host_info = pa.get_host_api_info_by_index(int(device_info.get("hostApi", -1)))
        return "WASAPI" in host_info.get("name", "").upper()
    except Exception:
        return False


def list_output_devices() -> List[DeviceInfo]:
    devices: List[DeviceInfo] = []
    if not sys.platform.startswith("win"):
        return devices
    try:
        with _pa() as pa:
            for idx in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(idx)
                if info.get("maxOutputChannels", 0) > 0 and not info.get("isLoopbackDevice", False):
                    devices.append((idx, info.get("name", f"Device {idx}")))
    except Exception as exc:
        logging.error("Failed to query output devices: %s", exc)
    return devices


def list_wasapi_loopback_devices() -> List[DeviceInfo]:
    """Return WASAPI loopback devices (isLoopbackDevice=True)."""
    devices: List[DeviceInfo] = []
    if not sys.platform.startswith("win"):
        return devices
    try:
        with _pa() as pa:
            for idx in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(idx)
                if not _is_wasapi(pa, info):
                    continue
                if info.get("isLoopbackDevice", False):
                    devices.append((idx, info.get("name", f"Device {idx}")))
    except Exception as exc:
        logging.error("Failed to query WASAPI loopback devices: %s", exc)
    return devices


def has_wasapi_output_devices() -> bool:
    return bool(list_wasapi_loopback_devices())


def default_output_device() -> int | None:
    if not sys.platform.startswith("win"):
        return None
    try:
        with _pa() as pa:
            info = pa.get_default_output_device_info()
            return int(info["index"])
    except Exception:
        return None


def default_wasapi_loopback_device() -> int | None:
    """Attempt to find the loopback device matching the default output."""
    if not sys.platform.startswith("win"):
        return None
    try:
        with _pa() as pa:
            try:
                host_api = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            except Exception:
                return None
            out_idx = host_api.get("defaultOutputDevice", -1)
            if out_idx == -1:
                return None
            out_info = pa.get_device_info_by_index(out_idx)
            if out_info.get("isLoopbackDevice"):
                return int(out_info["index"])

            # Try to find a paired loopback device by name.
            out_name = out_info.get("name", "").lower()
            for idx in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(idx)
                if not info.get("isLoopbackDevice", False):
                    continue
                if not _is_wasapi(pa, info):
                    continue
                if out_name and out_name in info.get("name", "").lower():
                    return int(info["index"])
            return None
    except Exception:
        return None


def choose_wasapi_loopback(preferred_names: list[str] | None = None) -> int | None:
    """Pick the best WASAPI loopback device index."""
    devices = list_wasapi_loopback_devices()
    if not devices:
        return None
    preferred = [p.lower() for p in (preferred_names or [])]
    for idx, name in devices:
        lower = name.lower()
        if any(p in lower for p in preferred):
            return idx
    default_loop = default_wasapi_loopback_device()
    if default_loop is not None:
        return default_loop
    return devices[0][0]
