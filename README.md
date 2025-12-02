# VoiceControl Client

Python client for recording local system audio on Windows, gated by a master password fetched from the server. Source is under `src/voicecontrol`. The app is Windows-only (WASAPI loopback).

## Features
- Fetches master password from `GET {server}/api/client/password`; falls back to `123456` and shows “No internet access” if unreachable.
- Password-protected settings UI (Tk).
- Start/stop recording, set chunk length, set API key, toggle “run on startup” flag (Windows registry HKCU Run entry), pick speaker device for loopback capture.
- Captures local system audio via WASAPI loopback and saves chunked WAV files (default 30s) to `%LOCALAPPDATA%/voicecontrol/recordings`.
- Hook in place (`on_chunk`) to stream uploaded chunks later.

## Quickstart (Windows)
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set PYTHONPATH=src
python -m voicecontrol
```

If the server is unreachable at launch, enter `123456` to unlock (UI will note offline mode).

## Packaging hint
- Bundle with PyInstaller when ready for distribution (install from `requirements-dev.txt`):
  ```bash
  pip install -r requirements-dev.txt
  set PYTHONPATH=src
  pyinstaller --onefile -w --name VoiceControlClient app.py
  ```
  The supported target is Windows; the artifact will be `dist/VoiceControlClient.exe`. Ensure PortAudio/WASAPI binaries and the VC runtime are present on the target; test the frozen binary for startup registration and device access. Code signing is recommended to reduce SmartScreen prompts.
