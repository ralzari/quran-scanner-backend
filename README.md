# Quran Integrity Scanner — Backend

Flask API that extracts audio from YouTube URLs using yt-dlp and streams it to the frontend for forensic analysis.

## Deploy to Railway (Recommended — Free)

1. Go to https://railway.app and sign up (free)
2. Click **New Project → Deploy from GitHub repo**
3. Push this folder to a GitHub repo first, then connect it
   OR use **Railway CLI**:
   ```bash
   npm install -g @railway/cli
   railway login
   railway init
   railway up
   ```
4. Railway will auto-detect `nixpacks.toml` and install ffmpeg + Python
5. Your backend will be live at: `https://your-project.up.railway.app`

## Deploy to Render (Alternative — Free)

1. Go to https://render.com → New Web Service
2. Connect your GitHub repo
3. Set:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app --bind 0.0.0.0:$PORT --timeout 120`
4. Add **ffmpeg** via the Environment tab:
   - Under "Advanced", add a build command prefix: `apt-get install -y ffmpeg &&`
   
   OR use `render.yaml` (see below)

## render.yaml (optional, place in repo root)

```yaml
services:
  - type: web
    name: quran-scanner-backend
    env: python
    buildCommand: "apt-get install -y ffmpeg && pip install -r requirements.txt"
    startCommand: "gunicorn app:app --bind 0.0.0.0:$PORT --timeout 120"
    plan: free
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/info?url=YOUTUBE_URL` | Get video metadata (title, duration, channel) |
| GET | `/extract?url=YOUTUBE_URL` | Extract & stream audio as WAV |

## Environment Variables (optional)

None required. The app runs with defaults.

## Notes

- Max video duration: 10 minutes (configurable in app.py)
- Audio is extracted as 44.1kHz stereo WAV for best Web Audio API compatibility
- Temp files are cleaned up automatically after each request
