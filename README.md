# VoiceControl

Two-part setup for low-latency audio capture and playback:
- `client/`: Windows client that records system audio via WASAPI loopback and streams chunks to the server.
- `server/`: FastAPI-based stream server with a browser UI to monitor and play incoming audio in near real time.

## Server (stream target)
```bash
cd server
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export MONGO_URI="mongodb://localhost:27017"
export MONGO_DB="voicecontrol"
export JWT_SECRET="change-me"
export ADMIN_EMAIL="admin@example.com"
export ADMIN_PASSWORD="adminpass"
# or place these in server/.env (auto-loaded)
python -m app.main
```
Open `http://localhost:8000/` (admin portal) to log in (bootstrap admin is created from env vars above) and manage users; each user gets an API key for streaming. A simple WebSocket endpoint `/api/ws/audio` broadcasts incoming audio to connected listeners (e.g., build your own listener UI).

## Client (Windows)
```bash
cd client
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set PYTHONPATH=src
python app.py
```
In Settings, set `server_base` (default `http://localhost:8000`) and `api_key` to match the server. Start recording; chunks (default 2s) upload to `/api/ingest` and play in the server UI.

## Notes
- Client remains Windows-only (WASAPI loopback via PyAudioWPatch).
- Chunk streaming uses a background uploader with basic retry logging; uploaded files are deleted on success.
- Adjust `chunk_seconds` for latency vs. overhead trade-offs. Longer chunks reduce request count; shorter chunks improve near-real-time playback.
