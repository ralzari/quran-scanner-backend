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
import subprocess
import tempfile
import threading
from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS

app = Flask(__name__)

# Allow all origins (lock this down to your frontend domain in production)
CORS(app, origins="*", expose_headers=["X-Video-Title", "X-Video-Duration", "X-Video-Channel"])

MAX_DURATION_SECONDS = 600  # 10 minutes max


def is_valid_youtube_url(url: str) -> bool:
    patterns = [
        r"^https?://(www\.)?youtube\.com/watch\?v=[\w-]+",
        r"^https?://youtu\.be/[\w-]+",
        r"^https?://(www\.)?youtube\.com/shorts/[\w-]+",
    ]
    return any(re.match(p, url) for p in patterns)


def get_video_info(url: str) -> dict:
    """Use yt-dlp to get video metadata without downloading."""
    result = subprocess.run(
        [
            "yt-dlp",
            "--dump-json",
            "--no-playlist",
            "--no-warnings",
            url,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise ValueError(f"yt-dlp error: {result.stderr.strip()}")

    import json
    data = json.loads(result.stdout)
    return {
        "title": data.get("title", "Unknown"),
        "channel": data.get("uploader", "Unknown"),
        "duration": data.get("duration", 0),
        "thumbnail": data.get("thumbnail", ""),
        "view_count": data.get("view_count", 0),
    }


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
        # Get metadata first to check duration
        meta = get_video_info(url)
        if meta["duration"] > MAX_DURATION_SECONDS:
            return jsonify({"error": f"Video too long (max {MAX_DURATION_SECONDS // 60} minutes)"}), 400

        # Create temp file for audio
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = tmp.name
        tmp.close()

        # Extract audio as WAV (best for Web Audio API analysis)
        result = subprocess.run(
            [
                "yt-dlp",
                "--no-playlist",
                "--no-warnings",
                "-x",                          # extract audio
                "--audio-format", "wav",
                "--audio-quality", "0",
                "--postprocessor-args", "-ar 44100 -ac 2",  # 44.1kHz stereo
                "-o", tmp_path,
                url,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        # yt-dlp may append extension
        actual_path = tmp_path
        if not os.path.exists(actual_path):
            actual_path = tmp_path.replace(".wav", ".wav.wav")
        if not os.path.exists(actual_path):
            return jsonify({"error": "Audio extraction failed", "detail": result.stderr}), 500

        # Schedule cleanup after sending
        def cleanup():
            try:
                os.unlink(actual_path)
            except Exception:
                pass

        response = send_file(
            actual_path,
            mimetype="audio/wav",
            as_attachment=False,
            download_name="audio.wav",
        )
        response.headers["X-Video-Title"] = meta["title"].encode("ascii", "ignore").decode()
        response.headers["X-Video-Duration"] = str(meta["duration"])
        response.headers["X-Video-Channel"] = meta["channel"].encode("ascii", "ignore").decode()
        response.headers["Access-Control-Expose-Headers"] = "X-Video-Title, X-Video-Duration, X-Video-Channel"
        response.call_on_close(cleanup)
        return response

    except subprocess.TimeoutExpired:
        return jsonify({"error": "Extraction timed out — video may be too large"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
