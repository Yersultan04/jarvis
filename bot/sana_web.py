"""Sana Web — локальный «командный центр» с HUD-аватаром.

Тот же мозг (claude -p), голос вход (Groq Whisper) и выход (edge-tts).
Запуск: python sana_web.py → http://127.0.0.1:8765
"""
from __future__ import annotations

import base64
import logging

from flask import Flask, jsonify, request, send_from_directory

from jarvis_api import JarvisAPI
from jarvis_bot import (
    CHELSEA_SETTINGS,
    CHELSEA_TIMEOUT,
    CLAUDE_BIN,
    GROQ_API_KEYS,
    GROQ_BASE_URL,
    GROQ_MODEL,
    WHISPER_GLOSSARY,
    WORKSPACE_DIR,
    _strip_tags,
    synthesize_voice,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("sana.web")

api = JarvisAPI("x", "", "", "")
api.configure_claude(CLAUDE_BIN or None, timeout=120)
api.configure_chelsea(WORKSPACE_DIR, CHELSEA_SETTINGS, timeout=CHELSEA_TIMEOUT)
if GROQ_API_KEYS:
    api.configure_groq(GROQ_API_KEYS, GROQ_BASE_URL, GROQ_MODEL)

app = Flask(__name__, static_folder="web", static_url_path="/static")


def _audio_b64(text: str) -> str | None:
    ogg = synthesize_voice(_strip_tags(text))
    return base64.b64encode(ogg).decode() if ogg else None


@app.route("/")
def index():
    return send_from_directory("web", "index.html")


@app.post("/api/ask")
def ask():
    data = request.get_json(force=True)
    text = (data.get("text") or "").strip()
    want_voice = bool(data.get("voice", True))
    if not text:
        return jsonify(error="empty"), 400
    try:
        ans = api.ask_chelsea(text)
        reply = ans.answer
    except Exception as exc:  # noqa: BLE001
        logger.exception("ask failed")
        return jsonify(error=str(exc)), 500
    audio = _audio_b64(reply) if want_voice else None
    return jsonify(reply=reply, audio=audio)


@app.post("/api/voice")
def voice():
    f = request.files.get("audio")
    if not f:
        return jsonify(error="no audio"), 400
    try:
        transcript = api.transcribe(f.read(), filename="rec.ogg", prompt=WHISPER_GLOSSARY)
        if not transcript:
            return jsonify(error="не расслышал"), 200
        ans = api.ask_chelsea(transcript)
        reply = ans.answer
    except Exception as exc:  # noqa: BLE001
        logger.exception("voice failed")
        return jsonify(error=str(exc)), 500
    return jsonify(transcript=transcript, reply=reply, audio=_audio_b64(reply))


if __name__ == "__main__":
    logger.info("Sana Web → http://127.0.0.1:8765")
    app.run(host="127.0.0.1", port=8765, threaded=True)
