"""Sana Web — локальный «командный центр» с HUD-аватаром.

Тот же мозг (claude -p), голос вход (Groq Whisper) и выход (edge-tts).
Запуск: python sana_web.py → http://127.0.0.1:8765
"""
from __future__ import annotations

import base64
import logging
import os

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

# --- Авторизация -----------------------------------------------------------
# HUD выставляется наружу через Cloudflare Tunnel + Cloudflare Access.
# Access сам гейтит вход по Google-почте и проставляет заголовок
# Cf-Access-Authenticated-User-Email. Здесь — defense-in-depth: пускаем только если
#   (1) запрос локальный (127.0.0.1 — локальная разработка), ИЛИ
#   (2) Cf-Access-Authenticated-User-Email совпадает с владельцем, ИЛИ
#   (3) предъявлен Bearer-токен SANA_WEB_TOKEN (запасной прямой доступ).
# Иначе 403 — даже если кто-то узнал URL туннеля.
_DEFAULT_OWNER = "slvaita3@gmail.com,ersultan040403@gmail.com"
# Разрешённые почты владельца (SANA_WEB_EMAIL — через запятую/точку с запятой).
OWNER_EMAILS = {e.strip().lower() for e in
                os.environ.get("SANA_WEB_EMAIL", _DEFAULT_OWNER).replace(";", ",").split(",")
                if e.strip()}
WEB_TOKEN = os.environ.get("SANA_WEB_TOKEN", "").strip()
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


@app.before_request
def _require_auth():
    if request.method == "OPTIONS":
        return None
    # cloudflared проксирует на 127.0.0.1, поэтому remote_addr НЕ годится для
    # «это локально». Признак прихода через Cloudflare — заголовки Cf-Ray /
    # Cf-Connecting-Ip (их ставит сам Cloudflare; снаружи на локальный порт,
    # слушающий только 127.0.0.1, их не подделать — туда достаёт лишь туннель).
    via_cf = bool(request.headers.get("Cf-Ray") or request.headers.get("Cf-Connecting-Ip"))
    if not via_cf and (request.remote_addr or "") in _LOCAL_HOSTS:
        return None  # настоящая локальная разработка
    cf_email = (request.headers.get("Cf-Access-Authenticated-User-Email") or "").strip().lower()
    if via_cf and cf_email and cf_email in OWNER_EMAILS:
        return None  # Cloudflare Access подтвердил владельца
    if WEB_TOKEN:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and auth[7:].strip() == WEB_TOKEN:
            return None  # запасной прямой доступ по токену
    logger.warning("auth denied: ip=%s via_cf=%s cf_email=%r path=%s",
                   request.remote_addr, via_cf, cf_email, request.path)
    return jsonify(error="forbidden"), 403


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
