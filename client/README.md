# VoiceControl Client

Windows-only app that captures system audio via WASAPI loopback, chunks it, and now streams each chunk to the VoiceControl server. Source lives under `client/src/voicecontrol`.

## Features
- Password-protected settings UI (Tk); offline unlock fallback still `123456` if master password endpoint is unreachable.
- Loopback capture of system audio (plus optional mic) to `%LOCALAPPDATA%/voicecontrol/recordings`.
- Real-time streaming: each chunk is POSTed to `{server_base}/api/ingest` with the configured API key.
- Pick speaker/mic devices, set chunk length (default 2s for low latency), sample rate, and run-on-startup flag (HKCU Run).

## Quickstart (Windows)
```bash
cd client
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set PYTHONPATH=src
python app.py
```

Set `SERVER_BASE`, `API_KEY`, and optionally `CHUNK_SECONDS` in `.env` (or env vars). These values are read-only in the UI; start the server first so uploads succeed. If the server is unreachable at launch, enter `123456` to unlock (UI will note offline mode).

## Packaging hint
- Bundle with PyInstaller when ready for distribution:
  ```bash
  pip install -r requirements-dev.txt
  set PYTHONPATH=src
  pyinstaller --onefile -w --name VoiceControlClient app.py
  ```
  Target is Windows; the artifact will be `dist/VoiceControlClient.exe`. Ensure PortAudio/WASAPI binaries and the VC runtime are present on the target; test the frozen binary for startup registration and device access. Code signing is recommended to reduce SmartScreen prompts.
