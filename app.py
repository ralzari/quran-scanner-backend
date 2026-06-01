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
import glob
import shutil
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

ANDROID_UA = "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/91.0.4472.120 Mobile Safari/537.36"

def get_ytdlp_proxy_args():
    if PROXY_URL:
        return [
            "--proxy", PROXY_URL,
            "--socket-timeout", "30",
            "--retries", "3",
        ]
    return []

def get_ytdlp_client_args():
    return [
        "--extractor-args", "youtube:player_client=android",
        "--user-agent", ANDROID_UA,
        "--no-check-certificates",
        "--prefer-free-formats",
    ]

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
            *get_ytdlp_client_args(),
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
            return jsonify({"error": f"Video too long (max {MAX_DURATION_SECONDS // 3600} hours)"}), 400
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

    tmp_dir = None
    try:
        meta = get_video_info(url)
        if meta["duration"] > MAX_DURATION_SECONDS:
            return jsonify({"error": f"Video too long (max {MAX_DURATION_SECONDS // 3600} hours)"}), 400

        tmp_dir = tempfile.mkdtemp()
        output_template = os.path.join(tmp_dir, "audio.%(ext)s")

        result = subprocess.run(
            [
                "yt-dlp",
                "--no-playlist",
                "--no-warnings",
                "--format", "bestaudio/best",
                "--no-post-overwrites",
                "-o", output_template,
                *get_ytdlp_client_args(),
                *get_ytdlp_proxy_args(),
                url,
            ],
            capture_output=True, text=True, timeout=300,
        )

        downloaded_files = glob.glob(os.path.join(tmp_dir, "audio.*"))
        if not downloaded_files:
            return jsonify({"error": "Audio extraction failed", "detail": result.stderr[-500:]}), 500

        downloaded_file = downloaded_files[0]

        if not os.path.exists(downloaded_file) or os.path.getsize(downloaded_file) == 0:
            return jsonify({"error": "Downloaded file is empty"}), 500

        ext = os.path.splitext(downloaded_file)[1]
        title_safe = meta["title"].encode("ascii", "ignore").decode()[:200]
        channel_safe = meta["channel"].encode("ascii", "ignore").decode()[:100]

        def cleanup():
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

        response = make_response(send_file(
            downloaded_file,
            mimetype="audio/webm",
            as_attachment=False,
            download_name="audio" + ext,
        ))
        response.headers["X-Video-Title"] = title_safe
        response.headers["X-Video-Duration"] = str(meta["duration"])
        response.headers["X-Video-Channel"] = channel_safe
        response.call_on_close(cleanup)
        return response

    except subprocess.TimeoutExpired:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({"error": "Extraction timed out — video may be too large"}), 504
    except Exception as e:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
