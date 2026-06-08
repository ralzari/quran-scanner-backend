"""
Quran Integrity Scanner — Backend API
Streaming download — starts sending audio bytes immediately so Railway's
5-minute HTTP timeout is never hit.

Endpoints:
  GET  /health            — health check
  GET  /info?url=...      — get video metadata
  GET  /extract?url=...   — stream audio directly from yt-dlp stdout
"""

import os
import re
import sys
import json
import subprocess
from flask import Flask, request, jsonify, make_response, Response

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
        return ["--proxy", PROXY_URL, "--socket-timeout", "60", "--retries", "2"]
    return []

def get_ytdlp_client_args():
    return [
        "--extractor-args", "youtube:player_client=android",
        "--user-agent", ANDROID_UA,
        "--no-check-certificates",
        "--prefer-free-formats",
    ]

# ── CORS ──────────────────────────────────────────────────────────────────────
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

    try:
        meta = get_video_info(url)
        if meta["duration"] > MAX_DURATION_SECONDS:
            return jsonify({"error": f"Video too long (max {MAX_DURATION_SECONDS // 3600} hours)"}), 400

        title_safe = meta["title"].encode("ascii", "ignore").decode()[:200]
        channel_safe = meta["channel"].encode("ascii", "ignore").decode()[:100]

        # Stream audio directly from yt-dlp stdout — no temp file needed
        # yt-dlp writes to stdout with -o - flag, Flask streams it chunk by chunk
        # This keeps the HTTP connection alive throughout the download
        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--no-warnings",
            "--format", "bestaudio/best",
            "-o", "-",  # output to stdout
            *get_ytdlp_client_args(),
            *get_ytdlp_proxy_args(),
            url,
        ]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        def generate():
            try:
                while True:
                    chunk = proc.stdout.read(1024 * 64)  # 64KB chunks
                    if not chunk:
                        break
                    yield chunk
            finally:
                proc.stdout.close()
                proc.wait()

        response = Response(
            generate(),
            mimetype="audio/webm",
            direct_passthrough=True,
        )
        response.headers["X-Video-Title"] = title_safe
        response.headers["X-Video-Duration"] = str(meta["duration"])
        response.headers["X-Video-Channel"] = channel_safe
        for k, v in CORS_HEADERS.items():
            response.headers[k] = v
        return response

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
