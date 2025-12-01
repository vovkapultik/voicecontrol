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
        return int(default) if default is not None else None
    except Exception:
        return None

