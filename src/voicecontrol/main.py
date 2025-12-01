import logging
import os
import signal
import sys
import atexit

from .audio_recorder import AudioRecorder
from .auth import MasterPasswordProvider
from .config import ConfigManager
from .controller import AppController
from .ui import AppUI
from . import startup, config as config_module


def main() -> None:
    log_dir = config_module.APP_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "voicecontrol.log"

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, level_name, logging.INFO)

    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        from logging.handlers import RotatingFileHandler

        handlers.append(RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"))
    except Exception:
        pass

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )
    logging.info("v0 voicecontrol started")

    if not sys.platform.startswith("win"):
        logging.error("VoiceControl Client runs on Windows only (WASAPI required).")
        print("VoiceControl Client runs on Windows only.", file=sys.stderr)
        return

    def chunk_ready(path: str) -> None:
        # Placeholder for future streaming to server.
        logging.debug("Chunk ready for upload: %s", path)

    # Log available devices for diagnostics using PyAudioWPatch.
    if log_level <= logging.DEBUG:
        try:
            import pyaudiowpatch as pyaudio
        except Exception as exc:
            logging.debug("Could not enumerate devices (PyAudioWPatch missing?): %s", exc)
        else:
            try:
                pa = pyaudio.PyAudio()
                try:
                    logging.debug(
                        "Host APIs: %s", [pa.get_host_api_info_by_index(i).get("name") for i in range(pa.get_host_api_count())]
                    )
                    for i in range(pa.get_device_count()):
                        dev = pa.get_device_info_by_index(i)
                        logging.debug(
                            "Device %s: %s (in=%s out=%s loopback=%s hostapi=%s)",
                            i,
                            dev.get("name"),
                            dev.get("maxInputChannels"),
                            dev.get("maxOutputChannels"),
                            dev.get("isLoopbackDevice", False),
                            pa.get_host_api_info_by_index(int(dev.get("hostApi", -1))).get("name", "?"),
                        )
                    try:
                        default_out = pa.get_default_output_device_info()
                        logging.debug("Default output device: %s", default_out.get("name"))
                    except Exception:
                        pass
                finally:
                    pa.terminate()
            except Exception as exc:
                logging.debug("Could not enumerate devices: %s", exc)

    cfg_mgr = ConfigManager()

    # Sync startup setting with registry on launch.
    if cfg_mgr.config.run_on_startup:
        startup.enable_startup()
    else:
        startup.disable_startup()

    recorder = AudioRecorder(
        output_dir=cfg_mgr.recordings_dir(),
        chunk_seconds=cfg_mgr.config.chunk_seconds,
        sample_rate=cfg_mgr.config.sample_rate,
        on_chunk=chunk_ready,
        spk_device=cfg_mgr.config.spk_device,
    )
    controller = AppController(cfg_mgr, recorder)
    password_provider = MasterPasswordProvider(
        server_base=cfg_mgr.config.server_base,
        api_key=cfg_mgr.config.api_key or None,
    )
    ui = AppUI(controller, password_provider)

    def shutdown(*_args) -> None:
        try:
            recorder.stop()
        finally:
            sys.exit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, shutdown)
        except Exception:
            pass
    atexit.register(recorder.stop)

    ui.run()


if __name__ == "__main__":
    main()
