"""
Quran Integrity Scanner — Backend API
Deploy to Railway or Render (free tier works fine)

Endpoints:
  GET  /health            — health check
  GET  /extract?url=...   — extract & stream audio from YouTube URL
  GET  /info?url=...      — get video metadata only (no audio)
"""

import os
import re
import sys
import json
import subprocess
import tempfile
from flask import Flask, request, jsonify, send_file, make_response

# ── Auto-update yt-dlp on every startup ──────────────────────────────────────
subprocess.run(
    [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp", "-q"],
    capture_output=True,
)

app = Flask(__name__)

MAX_DURATION_SECONDS = 43200  # 12 hours max

# ── Proxy configuration ───────────────────────────────────────────────────────
PROXY_URL = os.environ.get("PROXY_URL", "").strip()

def get_ytdlp_proxy_args():
    if PROXY_URL:
        return ["--proxy", PROXY_URL]
    return []

# ── CORS — applied manually to every response ─────────────────────────────────
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Access-Control-Expose-Headers": "X-Video-Title, X-Video-Duration, X-Video-Channel",
}

@app.after_request
def apply_cors(response):
    for k, v in CORS_HEADERS.items():
        response.headers[k] = v
    return response

@app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
@app.route("/<path:path>", methods=["OPTIONS"])
def handle_options(path):
    response = make_response("", 204)
    for k, v in CORS_HEADERS.items():
        response.headers[k] = v
    return response

# ── Helpers ───────────────────────────────────────────────────────────────────
def is_valid_youtube_url(url: str) -> bool:
    patterns = [
        r"^https?://(www\.)?youtube\.com/watch\?v=[\w-]+",
        r"^https?://youtu\.be/[\w-]+",
        r"^https?://(www\.)?youtube\.com/shorts/[\w-]+",
    ]
    return any(re.match(p, url) for p in patterns)


def get_video_info(url: str) -> dict:
    result = subprocess.run(
        [
            "yt-dlp",
            "--dump-json",
            "--no-playlist",
            "--no-warnings",
            "--no-check-certificates",
            *get_ytdlp_proxy_args(),
            url,
        ],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise ValueError(f"yt-dlp error: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    return {
        "title": data.get("title", "Unknown"),
        "channel": data.get("uploader", "Unknown"),
        "duration": data.get("duration", 0),
        "thumbnail": data.get("thumbnail", ""),
        "view_count": data.get("view_count", 0),
    }

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "quran-integrity-scanner"})


@app.route("/info")
def info():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "Missing url parameter"}), 400
    if not is_valid_youtube_url(url):
        return jsonify({"error": "Invalid YouTube URL"}), 400
    try:
        meta = get_video_info(url)
        if meta["duration"] > MAX_DURATION_SECONDS:
            return jsonify({"error": f"Video too long (max {MAX_DURATION_SECONDS // 60} minutes)"}), 400
        return jsonify(meta)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/extract")
def extract():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "Missing url parameter"}), 400
    if not is_valid_youtube_url(url):
        return jsonify({"error": "Invalid YouTube URL"}), 400

    try:
        meta = get_video_info(url)
        if meta["duration"] > MAX_DURATION_SECONDS:
            return jsonify({"error": f"Video too long (max {MAX_DURATION_SECONDS // 60} minutes)"}), 400

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = tmp.name
        tmp.close()

        result = subprocess.run(
            [
                "yt-dlp",
                "--no-playlist",
                "--no-warnings",
                "--no-check-certificates",
                "--prefer-free-formats",
                "--format", "bestaudio",
                "-x",
                "--audio-format", "wav",
                "--audio-quality", "0",
                "--postprocessor-args", "ffmpeg:-ar 44100 -ac 2",
                *get_ytdlp_proxy_args(),
                "-o", tmp_path,
                url,
            ],
            capture_output=True, text=True, timeout=180,
        )

        actual_path = tmp_path
        for candidate in [tmp_path, tmp_path + ".wav", tmp_path.replace(".wav", "") + ".wav"]:
            if os.path.exists(candidate) and os.path.getsize(candidate) > 0:
                actual_path = candidate
                break
        else:
            return jsonify({"error": "Audio extraction failed", "detail": result.stderr[-500:]}), 500

        def cleanup():
            try:
                os.unlink(actual_path)
            except Exception:
                pass

        title_safe = meta["title"].encode("ascii", "ignore").decode()[:200]
        channel_safe = meta["channel"].encode("ascii", "ignore").decode()[:100]

        response = make_response(send_file(
            actual_path,
            mimetype="audio/wav",
            as_attachment=False,
            download_name="audio.wav",
        ))
        response.headers["X-Video-Title"] = title_safe
        response.headers["X-Video-Duration"] = str(meta["duration"])
        response.headers["X-Video-Channel"] = channel_safe
        response.call_on_close(cleanup)
        return response

    except subprocess.TimeoutExpired:
        return jsonify({"error": "Extraction timed out — video may be too large"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
