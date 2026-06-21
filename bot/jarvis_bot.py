"""Jarvis Telegram-бот (Фаза 2 — текстовый вход).

Long-polling без тяжёлых фреймворков (только requests). Принимает текст →
спрашивает агента памяти → возвращает ответ с цитатами. Команда «запомни X»
дописывает новый факт в память и реиндексит.

Запуск:
    cp .env.example .env   # заполнить TELEGRAM_BOT_TOKEN + (опц.) ALLOWED_USERS
    python jarvis_bot.py
"""
from __future__ import annotations

import html
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import requests

from jarvis_api import AgentAnswer, JarvisAPI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("jarvis.bot")

# ---------------- config ----------------


def _load_env(path: Path) -> None:
    """Минимальный .env-лоадер (без зависимости от python-dotenv)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


_load_env(Path(__file__).with_name(".env"))

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API_BASE = os.environ.get("JARVIS_API_BASE", "http://127.0.0.1:8000")
RAG_ID = os.environ.get("JARVIS_RAG_ID", "")
ADMIN_EMAIL = os.environ.get("JARVIS_EMAIL", "")
ADMIN_PASSWORD = os.environ.get("JARVIS_PASSWORD", "")
# Список разрешённых Telegram user_id через запятую. Пусто = разрешить всем
# (НЕ рекомендуется — бот ходит в твою личную память).
ALLOWED_USERS = {
    u.strip() for u in os.environ.get("JARVIS_ALLOWED_USERS", "").split(",") if u.strip()
}

# Groq для лёгкого пути (search + 1 LLM-вызов) с ротацией по ключам.
GROQ_API_KEYS = [
    k.strip() for k in os.environ.get("GROQ_API_KEYS", "").split(",") if k.strip()
]
GROQ_BASE_URL = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
# G7: на лёгком хосте (GCP e2-micro, 1GB) rag-cms не поднимаем — быстрый путь
# (search+Groq) отключаем, всё уходит в claude -p (он читает память из файлов).
# Groq при этом остаётся для голоса (Whisper). 0 = выключить быстрый путь.
RAG_ENABLED = os.environ.get("JARVIS_RAG_ENABLED", "1") not in ("0", "false", "")
# Claude Code CLI (подписка): fallback при исчерпании Groq + режим качества /claude.
# Пусто = авто-детект через PATH.
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "")
CLAUDE_TIMEOUT = float(os.environ.get("CLAUDE_TIMEOUT", "150"))
# Полная Chelsea (Фаза A): claude -p в рабочем пространстве с залоченными правами.
WORKSPACE_DIR = os.environ.get("JARVIS_WORKSPACE", r"C:\Users\Acer\AI_Assistant")
CHELSEA_SETTINGS = os.environ.get(
    "JARVIS_CHELSEA_SETTINGS",
    str(Path(__file__).with_name("bot-settings.json")),
)
CHELSEA_TIMEOUT = float(os.environ.get("JARVIS_CHELSEA_TIMEOUT", "200"))
# Исполнитель задач по коду (Фаза D): отдельный профиль прав + долгий таймаут.
BUILDER_SETTINGS = os.environ.get(
    "JARVIS_BUILDER_SETTINGS",
    str(Path(__file__).with_name("bot-settings-dev.json")),
)
BUILDER_TIMEOUT = float(os.environ.get("JARVIS_BUILDER_TIMEOUT", "900"))
# G7: на VM код-репо живёт отдельно (симлинк за пределами WORKSPACE) → claude гейтит
# git-операции вне рабочего корня. Запускаем билдер прямо в репо. Пусто = workspace
# (на ноуте projects/* — реальные подпапки, git работает из коробки).
BUILDER_CWD = os.environ.get("JARVIS_BUILDER_CWD", "")
# Голос (Фаза B): edge-tts → mp3 → ffmpeg → ogg/opus → Telegram voice.
TTS_VOICE = os.environ.get("JARVIS_TTS_VOICE", "ru-RU-SvetlanaNeural")
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")
TTS_MAX_CHARS = int(os.environ.get("JARVIS_TTS_MAX_CHARS", "700"))

# Словарь имён для Whisper — смещает распознавание к нашим терминам.
WHISPER_GLOSSARY = (
    "Sana, Сана, Ерсултан, Med Triage, Incident Compass, Haul, Bilim, Elza, "
    "KazBench, Gottfried, Trello, Нуржан, Павлодар."
)

# G1 — контекст диалога. История (вопрос, ответ) на чат + время последней реплики.
# Сбрасывается по таймауту бездействия или команде /reset.
HISTORY_MAX = int(os.environ.get("JARVIS_HISTORY_MAX", "6"))        # пар реплик в памяти
HISTORY_TTL = float(os.environ.get("JARVIS_HISTORY_TTL", "1800"))   # сек бездействия → сброс
_chat_history: dict[int, list[tuple[str, str]]] = {}
_chat_last: dict[int, float] = {}


def get_history(chat_id: int) -> list[tuple[str, str]]:
    if time.time() - _chat_last.get(chat_id, 0) > HISTORY_TTL:
        _chat_history.pop(chat_id, None)  # протух — забываем
    return _chat_history.get(chat_id, [])


def push_history(chat_id: int, question: str, answer: str) -> None:
    hist = _chat_history.setdefault(chat_id, [])
    hist.append((question, answer))
    del hist[:-HISTORY_MAX]  # держим только последние HISTORY_MAX пар
    _chat_last[chat_id] = time.time()


def reset_history(chat_id: int) -> None:
    _chat_history.pop(chat_id, None)
    _chat_last.pop(chat_id, None)


# G5 — надёжность и аудит. Все действия Sana пишутся в JSONL (что, когда, итог).
AUDIT_DIR = Path(__file__).with_name("audit")
AUDIT_FILE = AUDIT_DIR / "actions.jsonl"
_audit_lock = threading.Lock()


def audit_log(
    chat_id: int,
    kind: str,
    request: str,
    *,
    route: str = "",
    status: str = "",
    dur_s: float = 0.0,
    error: str = "",
) -> None:
    """Дописать запись о действии в аудит-лог (append-only JSONL).

    kind — text/voice/image/task/brief/sync/undo; route — claude/groq/builder;
    status — ok/error/<agent-status>. Сбой записи лога не должен ронять ответ.
    """
    import json
    from datetime import datetime

    rec: dict = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "chat_id": chat_id,
        "kind": kind,
        "route": route,
        "request": (request or "")[:200],
        "status": status,
        "dur_s": round(dur_s, 1),
    }
    if error:
        rec["error"] = error[:300]
    try:
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        with _audit_lock, AUDIT_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        logger.exception("audit_log failed")


def recent_audit(n: int = 10) -> list[dict]:
    """Последние n записей аудита (в хронологическом порядке)."""
    import json

    if not AUDIT_FILE.exists():
        return []
    try:
        lines = AUDIT_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in lines[-n:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def format_audit(entries: list[dict]) -> str:
    """HTML-сводка последних действий для команды /audit."""
    icons = {
        "text": "💬", "voice": "🎤", "image": "🖼", "task": "🛠",
        "brief": "☀️", "sync": "🔄", "undo": "↩️",
    }
    lines = ["🧾 <b>Последние действия Sana</b>\n"]
    for e in entries:
        ts = (e.get("ts", "") or "")[5:16].replace("T", " ")   # MM-DD ЧЧ:ММ
        ic = icons.get(e.get("kind", ""), "•")
        st = "✅" if e.get("status") == "ok" else "⚠️"
        req = html.escape((e.get("request") or "")[:60])
        dur = e.get("dur_s", 0)
        lines.append(f"{st} {ic} <i>{ts}</i> · {req} <i>({dur}с)</i>")
    return "\n".join(lines)


def friendly_error(exc: Exception) -> str:
    """G5: перевести техническую ошибку в понятное пользователю сообщение (HTML)."""
    s = str(exc)
    low = s.lower()
    if "таймаут" in low or "timeout" in low or "timed out" in low:
        return ("⏱ <b>Не успела за отведённое время.</b>\n"
                "Сформулируй короче/конкретнее, а для большой работы — <code>/task</code>.")
    if "rc!=0" in s or "rc=" in s:
        return ("⚠️ <b>Внутренний сбой.</b> Попробуй ещё раз или переформулируй — "
                "обычно со второй попытки проходит.")
    if "connection" in low or "max retries" in low or "refused" in low or "resolve" in low:
        return "🔌 <b>Нет связи с сервисом.</b> Попробуй через минуту."
    return "⚠️ <b>Ошибка:</b> " + html.escape(s[:300])


# G2 — гибрид скорости. Вопрос-факт → Groq (быстро ~7с); действие/live → Claude (~22с).
# Слова, требующие ДЕЙСТВИЯ или живых инструментов (Trello/Calendar/Gmail/файлы) → Claude.
_ACTION_RE = re.compile(
    r"\b(заведи|завед|добав|запиши|запомни|поставь|постав|созда|измен|удал|отправ|"
    r"напиши|напис|ответь|ответ|сделай|сделать|перенеси|обнови|обнов|закрой|двин|"
    r"draft|черновик|почт|письм|gmail|календар|встреч|событи|трелло|trello|backlog|"
    r"задач|карточк|коммит|пуш|push|деплой)",
    re.IGNORECASE,
)


def needs_claude(text: str) -> bool:
    """True → нужен полный Claude (действие/живые инструменты). False → Groq-факт."""
    return bool(_ACTION_RE.search(text))

# Проактивность (Фаза C): утренний бриф в заданное время локально (Астана UTC+5).
OWNER_CHAT_ID = next(iter(ALLOWED_USERS), "")  # кому слать брифы (приватный чат: chat_id=user_id)
BRIEF_TIME = os.environ.get("JARVIS_BRIEF_TIME", "08:30")  # ЧЧ:ММ локально
BRIEF_ENABLED = os.environ.get("JARVIS_BRIEF_ENABLED", "1") not in ("0", "false", "")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

REMEMBER_PREFIXES = ("запомни ", "/remember ", "remember ")
# Префиксы для тяжёлого ReAct-агента (многошаговое рассуждение, дороже/медленнее).
DEEP_PREFIXES = ("/deep ", "/глубоко ", "глубоко ")
# Префиксы для режима качества — синтез через Claude на подписке (без лимитов).
CLAUDE_PREFIXES = ("/claude ", "/умный ", "умный ")


# ---------------- telegram helpers ----------------


def tg_call(method: str, **params) -> dict:
    resp = requests.post(f"{TG_API}/{method}", json=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def send_message(chat_id: int, text: str, *, html_mode: bool = False, **extra) -> int | None:
    """Отправить сообщение, вернуть message_id. Telegram лимит — 4096 символов.

    При html_mode шлём с parse_mode=HTML; если Telegram отвергнет разметку
    (битые entities) — повторяем без неё, чтобы пользователь всё равно получил текст.
    """
    text = text[:4000]
    params = dict(extra)
    if html_mode:
        params["parse_mode"] = "HTML"
    try:
        res = tg_call("sendMessage", chat_id=chat_id, text=text, **params)
        return res["result"]["message_id"]
    except Exception:  # noqa: BLE001
        if html_mode:
            return send_message(chat_id, _strip_tags(text), **extra)
        logger.exception("sendMessage failed")
        return None


def edit_message(chat_id: int, message_id: int, text: str, *, html_mode: bool = False) -> None:
    params: dict = {"chat_id": chat_id, "message_id": message_id, "text": text[:4000]}
    if html_mode:
        params["parse_mode"] = "HTML"
    try:
        tg_call("editMessageText", **params)
    except Exception:  # noqa: BLE001
        if html_mode:
            edit_message(chat_id, message_id, _strip_tags(text))
            return
        logger.exception("editMessageText failed")


def send_typing(chat_id: int) -> None:
    try:
        tg_call("sendChatAction", chat_id=chat_id, action="typing")
    except Exception:  # noqa: BLE001
        pass


def download_file(file_id: str) -> bytes:
    """Скачать файл из Telegram по file_id (getFile → download). Лимит бота — 20MB."""
    info = tg_call("getFile", file_id=file_id)
    file_path = info["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.content


def save_incoming(file_id: str, suffix: str) -> str:
    """Скачать файл из Telegram и сохранить в inbox рабочего пространства.
    Возвращает абсолютный путь (Sana прочитает его через Read)."""
    data = download_file(file_id)
    inbox = Path(WORKSPACE_DIR) / "projects" / "jarvis" / "bot" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    # имя без коллизий: по потоку + хвост file_id
    name = f"img_{threading.get_ident()}_{file_id[-8:]}{suffix}"
    path = inbox / name
    path.write_bytes(data)
    return str(path)


def synthesize_voice(text: str) -> bytes | None:
    """Текст → голос. edge-tts (mp3) → ffmpeg (ogg/opus для Telegram voice).

    Возвращает ogg-байты или None при сбое (тогда шлём только текст).
    """
    import asyncio
    import tempfile

    clean = text.strip()
    if not clean:
        return None
    if len(clean) > TTS_MAX_CHARS:
        clean = clean[:TTS_MAX_CHARS].rsplit(" ", 1)[0] + "…"
    try:
        import edge_tts
    except ImportError:
        logger.warning("edge-tts не установлен — голос-ответ выключен")
        return None

    tmp = Path(tempfile.gettempdir())
    mp3 = tmp / f"sana_tts_{threading.get_ident()}.mp3"
    ogg = tmp / f"sana_tts_{threading.get_ident()}.ogg"
    try:
        async def _gen() -> None:
            await edge_tts.Communicate(clean, TTS_VOICE).save(str(mp3))

        asyncio.run(_gen())
        # mp3 → ogg/opus (формат голосовых Telegram)
        proc = subprocess.run(
            [FFMPEG_BIN, "-y", "-i", str(mp3), "-c:a", "libopus", "-b:a", "32k", str(ogg)],
            capture_output=True,
            timeout=60,
        )
        if proc.returncode != 0:
            logger.warning("ffmpeg ошибка: %s", proc.stderr.decode("utf-8", "replace")[:200])
            return None
        return ogg.read_bytes()
    except Exception:  # noqa: BLE001
        logger.exception("synthesize_voice failed")
        return None
    finally:
        for f in (mp3, ogg):
            try:
                f.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass


def send_voice(chat_id: int, ogg_bytes: bytes) -> None:
    """Отправить голосовое сообщение (ogg/opus)."""
    try:
        requests.post(
            f"{TG_API}/sendVoice",
            data={"chat_id": chat_id},
            files={"voice": ("sana.ogg", ogg_bytes, "audio/ogg")},
            timeout=60,
        )
    except Exception:  # noqa: BLE001
        logger.exception("sendVoice failed")


# ---------------- formatting ----------------

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(text: str) -> str:
    """Снять HTML-теги и развернуть entities — запасной plain-text вид."""
    return html.unescape(_TAG_RE.sub("", text))


def md_to_tg_html(text: str) -> str:
    """Конвертировать markdown от Claude в безопасный Telegram-HTML.

    Поддержка: **жирный**, `код`, ## заголовки → жирный, маркеры списка → •.
    Сначала экранируем <>&, потом вставляем валидные теги — битой разметки не будет.
    """
    text = html.escape(text, quote=False)            # &, <, >
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", text)   # **bold**
    text = re.sub(r"(?m)^[ \t]{0,3}#{1,6}[ \t]*(.+?)[ \t]*$", r"<b>\1</b>", text)  # заголовки
    text = re.sub(r"(?m)^([ \t]*)[-*][ \t]+", r"\1• ", text)  # маркеры списка
    text = re.sub(r"\n{3,}", "\n\n", text)           # схлопнуть лишние пустые строки
    return text.strip()


def _pretty_sources(citations: list[dict]) -> str:
    seen: list[str] = []
    for c in citations:
        fn = (c.get("filename") or "").strip()
        if not fn:
            continue
        name = fn[:-3] if fn.endswith(".md") else fn      # убрать .md
        if name not in seen:
            seen.append(name)
    if not seen:
        return ""
    return "📎 <i>" + " · ".join(html.escape(s, quote=False) for s in seen) + "</i>"


def format_answer(ans: AgentAnswer) -> str:
    """Вернёт HTML-разметку (отправлять с html_mode=True)."""
    if ans.status != "succeeded":
        if ans.status == "failed" and "budget" in (ans.error or ""):
            return ("⚠️ <b>Не уложился в шаги.</b>\n"
                    "Попробуй сформулировать вопрос короче и конкретнее.")
        return f"⚠️ <b>Не получилось ответить</b> ({html.escape(ans.status)}). " + html.escape(ans.error or "")

    body = md_to_tg_html(ans.answer.strip()) or "<i>(пустой ответ)</i>"
    parts = [body]
    src = _pretty_sources(ans.citations)
    if src:
        parts.append("\n➖➖➖\n" + src)
    if ans.confidence is not None:
        parts.append(f"🎯 <i>Уверенность: {round(ans.confidence * 100)}%</i>")
    return "\n".join(parts)


# ---------------- handlers ----------------


def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True
    return str(user_id) in ALLOWED_USERS


def handle_message(api: JarvisAPI, msg: dict) -> None:
    chat_id = msg["chat"]["id"]
    user_id = msg.get("from", {}).get("id", 0)

    if not is_allowed(user_id):
        if msg.get("text") or msg.get("voice") or msg.get("audio"):
            send_message(
                chat_id,
                f"⛔ Нет доступа. Твой Telegram ID: {user_id}\n"
                "Добавь его в JARVIS_ALLOWED_USERS, чтобы пользоваться.",
            )
        return

    text = (msg.get("text") or "").strip()
    was_voice = False

    # Голос: войс/аудио → скачать → Groq Whisper → текст (Фаза B).
    voice = msg.get("voice") or msg.get("audio")
    if not text and voice:
        was_voice = True
        ph = send_message(chat_id, "🎤 Слушаю…")
        try:
            audio = download_file(voice["file_id"])
            text = api.transcribe(audio, prompt=WHISPER_GLOSSARY)
        except Exception as exc:  # noqa: BLE001
            logger.exception("transcribe failed")
            if ph:
                edit_message(chat_id, ph, "⚠️ <b>Не разобрал голос:</b> " + html.escape(str(exc)), html_mode=True)
            return
        if not text:
            if ph:
                edit_message(chat_id, ph, "🤷 Ничего не расслышал. Повтори?")
            return
        # показываем расшифровку, дальше обрабатываем как обычный текст
        if ph:
            edit_message(chat_id, ph, "🎤 <i>" + html.escape(text) + "</i>", html_mode=True)

    # Изображения/документы: фото или картинка-документ → Sana смотрит и разбирает (Фаза E).
    photo = msg.get("photo")
    document = msg.get("document")
    img_spec = None
    if photo:  # список размеров, берём самый крупный
        img_spec = (photo[-1]["file_id"], ".jpg")
    elif document and (document.get("mime_type", "")).startswith("image/"):
        ext = "." + document["mime_type"].split("/")[-1].replace("jpeg", "jpg")
        img_spec = (document["file_id"], ext)
    if img_spec:
        caption = (msg.get("caption") or "").strip()
        ph = send_message(chat_id, "🖼 Смотрю изображение…")
        t0 = time.time()
        try:
            path = save_incoming(img_spec[0], img_spec[1])
            instr = caption or "Что на этом изображении? Разбери по сути, action если нужен."
            prompt = (
                f"Пользователь прислал изображение. Посмотри файл через Read: {path}\n"
                f"Задача: {instr}\n"
                "Ответь кратко по сути; если нужно действие (заметка/Trello) — сделай."
            )
            ans = api.ask_chelsea(prompt)
            reply = format_answer(ans)
            audit_log(chat_id, "image", caption or "(без подписи)", route="claude",
                      status="ok", dur_s=time.time() - t0)
        except Exception as exc:  # noqa: BLE001
            logger.exception("image failed")
            reply = friendly_error(exc)
            audit_log(chat_id, "image", caption or "(без подписи)", route="claude",
                      status="error", dur_s=time.time() - t0, error=str(exc))
        if ph:
            edit_message(chat_id, ph, reply, html_mode=True)
        else:
            send_message(chat_id, reply, html_mode=True)
        return

    if not text:
        return

    if text in ("/start", "/help"):
        send_message(
            chat_id,
            "👋 <b>Я Sana</b> (Сана) — твой AI-оператор в кармане.\n\n"
            "Пиши обычным языком — я не просто отвечаю, а <b>действую</b>:\n"
            "💬 спрошу память: <i>«что по Haul?»</i>\n"
            "📌 заведу задачу/встречу: <i>«созвон с Нуржаном вс 16:00»</i>\n"
            "🧠 запомню: <i>«запомни …»</i>\n"
            "🎤 <b>голосом</b> — надиктуй войс, я расшифрую и сделаю\n"
            "🛠 <b>/task …</b> — сделаю задачу по коду: ветка + правки + тесты + отчёт\n"
            "📊 разберу статус проектов и риски\n\n"
            "🧾 <b>/audit</b> — что я делала (лог действий)\n"
            "↩️ <b>/undo</b> — отменить последнее обратимое действие\n"
            "🔄 <b>/sync</b> — сверить память с реальностью · 🧹 <b>/reset</b> — забыть диалог\n\n"
            "<i>Мозг: Claude на подписке, без лимитов. Ответ ~30-60с — думаю и делаю.</i>\n"
            "<i>Утром в " + BRIEF_TIME + " присылаю бриф сама. /brief — прямо сейчас.</i>\n"
            f"<i>Твой Telegram ID: {user_id}</i>",
            html_mode=True,
        )
        return

    if text in ("/brief", "/бриф"):
        send_message(chat_id, "📋 Собираю бриф…")
        threading.Thread(target=send_brief, args=(api,), daemon=True).start()
        return

    if text in ("/reset", "/сброс", "/забудь"):
        reset_history(chat_id)
        send_message(chat_id, "🧹 Контекст диалога очищен. Начнём с чистого листа.")
        return

    if text in ("/sync", "/синк", "/актуализируй"):
        send_message(chat_id, "🔄 Сверяю память с реальностью…")
        threading.Thread(target=run_sync, args=(api, chat_id), daemon=True).start()
        return

    if text in ("/audit", "/аудит", "/лог"):
        entries = recent_audit(10)
        if not entries:
            send_message(chat_id, "📋 Аудит-лог пуст.")
        else:
            send_message(chat_id, format_audit(entries), html_mode=True)
        return

    if text in ("/undo", "/отмена", "/отмени"):
        send_message(chat_id, "↩️ Смотрю последнее действие…")
        threading.Thread(target=run_undo, args=(api, chat_id), daemon=True).start()
        return

    low = text.lower()
    if low.startswith(("/task ", "/задача ", "/сделай ")):
        task = text.split(" ", 1)[1].strip() if " " in text else ""
        if not task:
            send_message(chat_id, "Что сделать? Напиши: /task <описание задачи>")
            return
        send_message(
            chat_id,
            "🛠 <b>Взяла в работу.</b>\n<i>Делаю в отдельной ветке, прогоню тесты, "
            "отчитаюсь. push/merge — за тобой. Это может занять несколько минут…</i>",
            html_mode=True,
        )
        threading.Thread(target=run_task, args=(api, chat_id, task), daemon=True).start()
        return

    # G2 гибрид: вопрос-факт → Groq (быстро ~7с), действие/живые инструменты → Claude.
    use_claude = needs_claude(text) or not GROQ_API_KEYS or not RAG_ENABLED
    send_typing(chat_id)
    placeholder = send_message(
        chat_id, "⚙️ Думаю и делаю…" if use_claude else "🔍 Смотрю в памяти…"
    )
    voiced = False
    t0 = time.time()
    route = "claude" if use_claude else "groq"
    try:
        hist = get_history(chat_id)
        if use_claude:
            ans = api.ask_chelsea(text, history=hist)
        else:
            ans = api.ask_cheap_ctx(text, history=hist)  # Groq + память, быстро
            # если в памяти ничего — добираем полным Claude
            if ans.status != "succeeded" or "ничего не нашёл" in (ans.answer or ""):
                route = "claude"
                ans = api.ask_chelsea(text, history=hist)
        reply = format_answer(ans)
        voiced = ans.status == "succeeded"
        if ans.status == "succeeded":
            push_history(chat_id, text, _strip_tags(reply)[:600])
        audit_log(chat_id, "voice" if was_voice else "text", text, route=route,
                  status="ok" if voiced else ans.status, dur_s=time.time() - t0)
    except Exception as exc:  # noqa: BLE001
        logger.exception("answer failed")
        reply = friendly_error(exc)
        audit_log(chat_id, "voice" if was_voice else "text", text, route=route,
                  status="error", dur_s=time.time() - t0, error=str(exc))
    if placeholder:
        edit_message(chat_id, placeholder, reply, html_mode=True)
    else:
        send_message(chat_id, reply, html_mode=True)

    # Голос-ответ: если спросили голосом — отвечаем и голосом (Фаза B.2).
    if was_voice and voiced:
        ogg = synthesize_voice(_strip_tags(reply))
        if ogg:
            send_voice(chat_id, ogg)


# ---------------- proactivity: morning brief (Фаза C) ----------------


def _brief_prompt() -> str:
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d (%A)")
    return (
        f"Сгенерируй короткий утренний бриф для Ерсултана. Сегодня {today}.\n"
        "Структура (с эмодзи, кратко, без воды):\n"
        "1. 📅 Встречи/дедлайны на сегодня-завтра — проверь РЕАЛЬНЫЙ Google Calendar: "
        "  python projects/jarvis/bot/gcal.py list 2\n"
        "  + Trello-карточки с due-датами + дедлайны проектов из памяти.\n"
        "2. 📧 Почта — проверь Gmail: python projects/jarvis/bot/gmail_tool.py list 15\n"
        "  Классифицируй каждое: REPLY (живой человек/бизнес, нужен ответ) / JOB (вакансии) / "
        "FYI (уведомления) / NOISE (спам/рассылки). В брифе: счётчики (N REPLY, N JOB, N FYI) "
        "и перечисли только REPLY-письма (от кого — тема). Для КАЖДОГО REPLY-письма, которого "
        "ещё НЕТ карточкой в Trello, заведи карточку «✉️ Ответить: <от> — <тема>» в список Backlog "
        "(доска «Projects — Kanban 2026»). Не дубли — сверься с существующими карточками.\n"
        "3. 🎯 Топ-3 приоритета на день.\n"
        "4. ⚠️ Горящее/риски по проектам (только реальное, из памяти/Trello).\n"
        "Не выдумывай — чего не знаешь, пропусти. Это автосообщение, не отвечай вопросом."
    )


def send_brief(api: JarvisAPI) -> None:
    """Сгенерировать и отправить утренний бриф владельцу."""
    if not OWNER_CHAT_ID:
        logger.warning("OWNER_CHAT_ID пуст — бриф некому слать")
        return
    chat_id = int(OWNER_CHAT_ID)
    t0 = time.time()
    try:
        ans = api.ask_chelsea(_brief_prompt())
        body = format_answer(ans)
        audit_log(chat_id, "brief", "утренний бриф", route="claude",
                  status="ok", dur_s=time.time() - t0)
    except Exception as exc:  # noqa: BLE001
        logger.exception("brief failed")
        body = friendly_error(exc)
        audit_log(chat_id, "brief", "утренний бриф", route="claude",
                  status="error", dur_s=time.time() - t0, error=str(exc))
    send_message(chat_id, "☀️ <b>Утренний бриф от Sana</b>\n\n" + body, html_mode=True)


_SYNC_TASK = (
    "Сверь память с реальностью (G3 актуализация). Шаги:\n"
    "1. Прочитай projects/jarvis/memory_inbox/notes.md и ключевые факты из MEMORY.md.\n"
    "2. Проверь Trello-доску «Projects — Kanban 2026»: что уже в Done.\n"
    "3. Найди РАСХОЖДЕНИЯ: факты в памяти, которые устарели (задача закрыта, статус сменился).\n"
    "4. Допиши в notes.md актуальные пометки (формат: «- ДАТА — <факт> ЗАКРЫТО/ИЗМЕНЕНО»).\n"
    "5. Кратко отчитайся в Telegram: что устарело и что обновил. Если всё актуально — так и скажи."
)


def run_sync(api: JarvisAPI, chat_id: int) -> None:
    """G3: Sana сверяет память с Trello/реальностью и правит устаревшее."""
    t0 = time.time()
    try:
        ans = api.ask_chelsea(_SYNC_TASK)
        body = format_answer(ans)
        audit_log(chat_id, "sync", "актуализация памяти", route="claude",
                  status="ok", dur_s=time.time() - t0)
    except Exception as exc:  # noqa: BLE001
        logger.exception("sync failed")
        body = friendly_error(exc)
        audit_log(chat_id, "sync", "актуализация памяти", route="claude",
                  status="error", dur_s=time.time() - t0, error=str(exc))
    send_message(chat_id, "🔄 <b>Актуализация памяти</b>\n\n" + body, html_mode=True)


def run_task(api: JarvisAPI, chat_id: int, task: str) -> None:
    """Фоновый исполнитель задачи по коду (Фаза D): правит в ветке + коммитит, отчёт."""
    t0 = time.time()
    try:
        ans = api.ask_builder(task, cwd=BUILDER_CWD or None)
        report = format_answer(ans)
        head = "✅ <b>Готово.</b>\n\n"
        audit_log(chat_id, "task", task, route="builder",
                  status="ok", dur_s=time.time() - t0)
    except Exception as exc:  # noqa: BLE001
        logger.exception("run_task failed")
        report = friendly_error(exc)
        head = "⚠️ <b>Задача не завершена:</b>\n\n"
        audit_log(chat_id, "task", task, route="builder",
                  status="error", dur_s=time.time() - t0, error=str(exc))
    send_message(chat_id, head + report, html_mode=True)


# G5 — отмена последнего обратимого действия. Sana читает аудит-лог и откатывает
# ТОЛЬКО внутреннее/обратимое (L2): Trello-карточку, запись в notes.md, событие.
_UNDO_TASK = (
    "Отмени ПОСЛЕДНЕЕ обратимое действие, которое ты (Sana) сделала. Шаги:\n"
    "1. Прочитай аудит-лог: projects/jarvis/bot/audit/actions.jsonl (последние строки) — "
    "это что ты делала и когда.\n"
    "2. Определи последнее ДЕЙСТВИЕ-изменение: создание Trello-карточки, запись в "
    "memory_inbox/notes.md, создание события календаря. Обычные вопросы-ответы "
    "пропускай — отменять там нечего.\n"
    "3. Откати его:\n"
    "   - Trello-карточка → заархивируй её (mcp__trello archive_card).\n"
    "   - запись в notes.md → удали последний добавленный блок (Edit).\n"
    "   - событие календаря → gcal удалять не умеет: сообщи, что удалить нужно вручную, дай детали.\n"
    "4. ЛИМИТ НА НЕОБРАТИМОЕ: отменяй только внутреннее/обратимое (L2). НИКОГДА не "
    "трогай внешнее/необратимое (отправленные письма, push, merge, деплой) — если "
    "последнее действие было таким, откажись и объясни почему.\n"
    "5. Кратко отчитайся: что именно отменила. Если отменять нечего — так и скажи."
)


def run_undo(api: JarvisAPI, chat_id: int) -> None:
    """G5: отменить последнее обратимое действие Sana (по аудит-логу)."""
    t0 = time.time()
    try:
        ans = api.ask_chelsea(_UNDO_TASK)
        body = format_answer(ans)
        status = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.exception("undo failed")
        body = friendly_error(exc)
        status = "error"
    audit_log(chat_id, "undo", "отмена последнего действия", route="claude",
              status=status, dur_s=time.time() - t0)
    send_message(chat_id, "↩️ <b>Отмена</b>\n\n" + body, html_mode=True)


def _gh_ci_red(repo: str = "Yersultan04/incident-compass") -> str | None:
    """Лёгкая проверка: последний CI на main красный? Возвращает заголовок run или None."""
    try:
        r = subprocess.run(
            ["gh", "run", "list", "--repo", repo, "--branch", "main",
             "--limit", "1", "--json", "conclusion,displayTitle"],
            capture_output=True, timeout=30, cwd=WORKSPACE_DIR,
        )
        import json
        data = json.loads(r.stdout.decode("utf-8", "replace") or "[]")
        if data and data[0].get("conclusion") == "failure":
            return data[0].get("displayTitle", "CI")[:80]
    except Exception:  # noqa: BLE001
        pass
    return None


def _cal_soon(api: JarvisAPI) -> str | None:
    """Событие календаря в ближайшие ~70 мин? Возвращает строку или None."""
    try:
        from datetime import datetime
        out = subprocess.run(
            [api._claude_bin and "python" or "python", "projects/jarvis/bot/gcal.py", "list", "1"],
            capture_output=True, timeout=40, cwd=WORKSPACE_DIR,
        ).stdout.decode("utf-8", "replace")
        # gcal печатает "- <ISO> | <title>"; ищем событие в ближайший час
        now = datetime.now()
        for line in out.splitlines():
            if "|" not in line:
                continue
            try:
                iso = line.split("|")[0].strip("- ").strip()
                when = datetime.fromisoformat(iso.replace("Z", "+00:00")).replace(tzinfo=None)
                mins = (when - now).total_seconds() / 60
                if 0 <= mins <= 70:
                    return f"{line.split('|')[1].strip()} через {int(mins)} мин"
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return None


# Состояние watcher'а (дедуп — не слать одно и то же).
_watch_state: dict[str, str] = {}


def watcher_tick(api: JarvisAPI) -> None:
    """G4: проверить горящее, слать алерт только при изменении. Без claude -p (дёшево)."""
    if not OWNER_CHAT_ID:
        return
    chat_id = int(OWNER_CHAT_ID)
    # CI красный — алерт при появлении (не спамим повторно)
    ci = _gh_ci_red()
    if ci and _watch_state.get("ci") != ci:
        _watch_state["ci"] = ci
        send_message(chat_id, f"🔴 <b>CI красный</b> (incident-compass)\n<i>{html.escape(ci)}</i>", html_mode=True)
    elif not ci:
        _watch_state.pop("ci", None)
    # событие календаря скоро — алерт один раз
    soon = _cal_soon(api)
    if soon and _watch_state.get("cal") != soon:
        _watch_state["cal"] = soon
        send_message(chat_id, f"📅 <b>Скоро встреча:</b> {html.escape(soon)}", html_mode=True)
    elif not soon:
        _watch_state.pop("cal", None)


WATCH_EVERY_MIN = int(os.environ.get("JARVIS_WATCH_MIN", "30"))  # частота watcher'а
WATCH_QUIET_FROM = int(os.environ.get("JARVIS_QUIET_FROM", "23"))  # тихие часы: с
WATCH_QUIET_TO = int(os.environ.get("JARVIS_QUIET_TO", "8"))       # до


def scheduler_loop(api: JarvisAPI) -> None:
    """Раз в минуту: BRIEF_TIME → бриф; каждые WATCH_EVERY_MIN → watcher (вне тихих часов)."""
    from datetime import datetime
    last_fired_date = ""
    last_watch = 0.0
    while True:
        try:
            now = datetime.now()
            hhmm = now.strftime("%H:%M")
            today = now.strftime("%Y-%m-%d")
            if BRIEF_ENABLED and hhmm == BRIEF_TIME and last_fired_date != today:
                last_fired_date = today
                logger.info("планировщик: шлю утренний бриф")
                send_brief(api)
            # G4 watcher — вне тихих часов, не чаще WATCH_EVERY_MIN
            quiet = (WATCH_QUIET_FROM <= now.hour or now.hour < WATCH_QUIET_TO)
            if not quiet and time.time() - last_watch >= WATCH_EVERY_MIN * 60:
                last_watch = time.time()
                watcher_tick(api)
        except Exception:  # noqa: BLE001
            logger.exception("scheduler tick failed")
        time.sleep(60)


# ---------------- main loop ----------------


def main() -> None:
    missing = [
        name
        for name, val in (
            ("TELEGRAM_BOT_TOKEN", BOT_TOKEN),
            ("CLAUDE_BIN / claude в PATH", CLAUDE_BIN or "auto"),
        )
        if not val
    ]
    if missing:
        raise SystemExit(f"Не заданы переменные окружения: {', '.join(missing)}")

    # rag-cms креды больше не обязательны (полная Chelsea читает память из файлов).
    api = JarvisAPI(API_BASE, ADMIN_EMAIL, ADMIN_PASSWORD, RAG_ID)
    api.configure_claude(CLAUDE_BIN or None, timeout=CLAUDE_TIMEOUT)
    api.configure_chelsea(WORKSPACE_DIR, CHELSEA_SETTINGS, timeout=CHELSEA_TIMEOUT)
    api.configure_builder(BUILDER_SETTINGS, timeout=BUILDER_TIMEOUT)
    if GROQ_API_KEYS:
        api.configure_groq(GROQ_API_KEYS, GROQ_BASE_URL, GROQ_MODEL)  # для голоса (Whisper)
        logger.info("голос: Groq Whisper (%d ключ(а))", len(GROQ_API_KEYS))
    # папка для «запомни» (Chelsea пишет сюда; путь относительно рабочего пространства)
    (Path(WORKSPACE_DIR) / "projects" / "jarvis" / "memory_inbox").mkdir(
        parents=True, exist_ok=True
    )
    logger.info(
        "режим: полная Chelsea (claude -p в %s, права из %s)",
        WORKSPACE_DIR, CHELSEA_SETTINGS,
    )

    me = tg_call("getMe")["result"]
    logger.info("бот запущен: @%s", me.get("username"))
    if not ALLOWED_USERS:
        logger.warning(
            "JARVIS_ALLOWED_USERS пуст — бот ответит ЛЮБОМУ. Задай свой ID для приватности."
        )

    # Проактивность: планировщик утреннего брифа (Фаза C).
    if BRIEF_ENABLED and OWNER_CHAT_ID:
        threading.Thread(target=scheduler_loop, args=(api,), daemon=True).start()
        logger.info("проактивность: утренний бриф в %s (chat %s)", BRIEF_TIME, OWNER_CHAT_ID)

    offset: int | None = None
    while True:
        try:
            params: dict = {"timeout": 30}
            if offset is not None:
                params["offset"] = offset
            res = requests.get(
                f"{TG_API}/getUpdates", params=params, timeout=40
            ).json()
        except Exception:  # noqa: BLE001
            logger.exception("getUpdates failed — пауза 5с")
            time.sleep(5)
            continue

        for update in res.get("result", []):
            offset = update["update_id"] + 1
            msg = update.get("message") or update.get("edited_message")
            if msg:
                # Каждое сообщение — в своём потоке: долгий /deep (≈50с) не блокирует
                # остальные запросы, бот остаётся отзывчивым.
                threading.Thread(
                    target=_safe_handle, args=(api, msg), daemon=True
                ).start()


def _safe_handle(api: JarvisAPI, msg: dict) -> None:
    try:
        handle_message(api, msg)
    except Exception:  # noqa: BLE001
        logger.exception("handle_message failed")


if __name__ == "__main__":
    main()
