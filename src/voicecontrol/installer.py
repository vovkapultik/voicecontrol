import ctypes
import logging
import os
import subprocess
import sys
from pathlib import Path

VB_CABLE_INSTALLER = Path(__file__).resolve().parent / "drivers" / "VBCable_Setup_x64.exe"


def is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def install_vb_cable() -> bool:
    """Attempt to install VB-Audio Virtual Cable silently if possible."""
    if not VB_CABLE_INSTALLER.exists():
        logging.error("VB-CABLE installer not found at %s", VB_CABLE_INSTALLER)
        return False

    cmd = [str(VB_CABLE_INSTALLER)]
    # VB-CABLE supports /S for silent install; fallback to normal if it fails.
    silent_args = "/S"

    try:
        if is_admin():
            logging.info("Running VB-CABLE installer with admin rights (silent).")
            result = subprocess.run(cmd + [silent_args], check=False)
            if result.returncode != 0:
                logging.warning("Silent install returned code %s, retrying non-silent.", result.returncode)
                result = subprocess.run(cmd, check=False)
            return result.returncode == 0
        else:
            logging.info("Requesting elevation for VB-CABLE installer.")
            rc = ctypes.windll.shell32.ShellExecuteW(
                None,
                "runas",
                str(VB_CABLE_INSTALLER),
                silent_args,
                None,
                1,
            )
            # ShellExecuteW returns >32 on success
            if rc <= 32:
                logging.error("ShellExecute returned %s when launching installer.", rc)
                return False
            return True
    except Exception as exc:
        logging.error("Failed to launch VB-CABLE installer: %s", exc)
        return False
