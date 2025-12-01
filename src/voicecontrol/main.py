import logging
import signal
import sys
import atexit

from .audio_recorder import AudioRecorder
from .auth import MasterPasswordProvider
from .config import ConfigManager
from .ui import AppUI
from . import startup, config as config_module


def main() -> None:
    log_dir = config_module.APP_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "voicecontrol.log"

    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        from logging.handlers import RotatingFileHandler

        handlers.append(RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"))
    except Exception:
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )

    def chunk_ready(path: str) -> None:
        # Placeholder for future streaming to server.
        logging.debug("Chunk ready for upload: %s", path)

    # Log available devices for diagnostics.
    try:
        import sounddevice as sd

        for i, dev in enumerate(sd.query_devices()):
            logging.info("Device %s: %s (in=%s out=%s)", i, dev.get("name"), dev.get("max_input_channels"), dev.get("max_output_channels"))
        logging.info("Default devices: %s", sd.default.device)
    except Exception as exc:
        logging.warning("Could not enumerate devices: %s", exc)

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
        mic_device=cfg_mgr.config.mic_device,
        spk_device=cfg_mgr.config.spk_device,
    )
    password_provider = MasterPasswordProvider(
        server_base=cfg_mgr.config.server_base,
        api_key=cfg_mgr.config.api_key or None,
    )
    ui = AppUI(cfg_mgr, recorder, password_provider)

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
