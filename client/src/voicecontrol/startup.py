import logging
import os
import sys
from pathlib import Path

try:
    import winreg  # type: ignore
except ImportError:  # pragma: no cover - non-Windows
    winreg = None  # type: ignore

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "VoiceControlClient"


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _run_command() -> str:
    """Return command to launch the packaged client (exe only)."""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Auto-start is only supported for packaged executable.")
    return f'"{Path(sys.executable).resolve()}"'


def enable_startup() -> bool:
    if not (_is_windows() and winreg):
        logging.info("Startup registration skipped (non-Windows)")
        return False
    try:
        command = _run_command()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
        logging.info("Registered run on startup")
        return True
    except FileNotFoundError:
        # Create the key if missing
        try:
            command = _run_command()
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
            logging.info("Registered run on startup (created key)")
            return True
        except Exception as exc:  # pragma: no cover - defensive
            logging.error("Failed to create Run key: %s", exc)
            return False
    except Exception as exc:  # pragma: no cover - defensive
        logging.error("Failed to register startup: %s", exc)
        return False


def disable_startup() -> bool:
    if not (_is_windows() and winreg):
        logging.info("Startup deregistration skipped (non-Windows)")
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
        logging.info("Removed run on startup")
        return True
    except FileNotFoundError:
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logging.error("Failed to remove startup entry: %s", exc)
        return False


def is_enabled() -> bool:
    if not (_is_windows() and winreg and getattr(sys, "frozen", False)):
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False
