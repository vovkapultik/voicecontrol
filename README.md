# VoiceControl Client

Python client for recording mic + system audio on Windows, gated by a master password fetched from the server. Source is under `src/voicecontrol`.

## Features
- Fetches master password from `GET {server}/api/client/password`; falls back to `123456` and shows “No internet access” if unreachable.
- Password-protected settings UI (Tk).
- Start/stop recording, set chunk length, set API key, toggle “run on startup” flag (Windows registry HKCU Run entry), pick speaker device for loopback capture.
- Captures system audio via loopback and saves chunked WAV files (default 30s) to `%LOCALAPPDATA%/voicecontrol/recordings` (loopback requires Windows/WASAPI).
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
  On Windows the artifact will be `dist/VoiceControlClient.exe` (on other OSes the extension may differ). Ensure PortAudio/WASAPI binaries and the VC runtime are present on the target; test the frozen binary for startup registration and device access. Code signing is recommended to reduce SmartScreen prompts.

## Loopback driver
- Speaker loopback requires a WASAPI-capable output. If none is found, the app shows an “Install loopback driver” button (expects `drivers/VBCable_Setup_x64.exe` bundled) to install VB-Audio Virtual Cable. Bundle the installer if licensing/compliance allows; after installation, restart the app to use the new loopback device.
