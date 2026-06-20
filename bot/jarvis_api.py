"""Тонкий клиент к локальному rag-cms API для Jarvis-бота.

Отвечает за: логин (JWT), запуск агента и опрос результата, запись новой
памяти (файл + индексация). Никакой бизнес-логики Telegram здесь нет — только
HTTP-обёртки над API памяти.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field

import requests

logger = logging.getLogger("jarvis.api")


@dataclass
class AgentAnswer:
    """Результат одного запуска агента."""

    status: str
    answer: str = ""
    confidence: float | None = None
    citations: list[dict] = field(default_factory=list)
    error: str | None = None


class JarvisAPI:
    """Клиент к rag-cms. Хранит JWT, авто-релогинится при 401."""

    def __init__(
        self,
        base_url: str,
        email: str,
        password: str,
        rag_id: str,
        *,
        timeout: float = 30.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._email = email
        self._password = password
        self._rag_id = rag_id
        self._timeout = timeout
        self._token: str | None = None
        # Лёгкий путь (search + 1 LLM-вызов). Заполняется через configure_groq().
        self._groq_keys: list[str] = []
        self._groq_base: str = "https://api.groq.com/openai/v1"
        self._groq_model: str = "llama-3.3-70b-versatile"
        self._groq_idx: int = 0
        # Fallback на Claude Code CLI (подписка, без потокенных лимитов).
        self._claude_bin: str | None = None
        self._claude_timeout: float = 120.0
        # Полная Chelsea (Фаза A): claude -p в рабочем пространстве с залоченными правами.
        self._workspace: str | None = None
        self._chelsea_settings: str | None = None
        self._chelsea_timeout: float = 180.0

    def configure_groq(self, keys: list[str], base_url: str, model: str) -> None:
        """Подключить Groq для лёгкого пути с ротацией по ключам."""
        self._groq_keys = [k for k in keys if k]
        self._groq_base = base_url.rstrip("/")
        self._groq_model = model

    def configure_claude(self, bin_path: str | None = None, *, timeout: float = 120.0) -> None:
        """Подключить `claude -p` (Claude Code CLI на подписке) как fallback/режим качества."""
        self._claude_bin = bin_path or shutil.which("claude") or shutil.which("claude.cmd")
        self._claude_timeout = timeout
        if self._claude_bin:
            logger.info("claude CLI: %s", self._claude_bin)
        else:
            logger.warning("claude CLI не найден — fallback на подписку недоступен")

    def configure_chelsea(
        self, workspace: str, settings_path: str, *, timeout: float = 180.0
    ) -> None:
        """Включить режим «полная Chelsea»: claude -p в рабочем пространстве с правами."""
        self._workspace = workspace
        self._chelsea_settings = settings_path
        self._chelsea_timeout = timeout

    # «Строитель» (Фаза D): отдельный профиль прав + долгий таймаут для задач по коду.
    _builder_settings: str | None = None
    _builder_timeout: float = 900.0

    def configure_builder(self, settings_path: str, *, timeout: float = 900.0) -> None:
        self._builder_settings = settings_path
        self._builder_timeout = timeout

    # ---------------- claude -p runner (G5: retry на сбоях) ----------------

    def _run_claude(
        self,
        args: list[str],
        prompt: str,
        *,
        timeout: float,
        cwd: str | None = None,
        label: str = "claude",
        retries: int = 1,
    ) -> str:
        """Запустить `claude -p` (промпт через stdin UTF-8) с retry на транзиентных сбоях.

        Retry — только на ненулевом коде возврата (холодный старт CLI иногда падает);
        на таймаут НЕ ретраим (он и так уже долгий, повтор удвоит ожидание).
        Возвращает stdout. Бросает RuntimeError с понятным префиксом при провале.
        """
        last_err = ""
        for attempt in range(retries + 1):
            try:
                proc = subprocess.run(
                    args,
                    input=prompt.encode("utf-8"),
                    capture_output=True,
                    timeout=timeout,
                    cwd=cwd,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"{label}: таймаут {int(timeout)}с") from exc
            if proc.returncode == 0:
                return proc.stdout.decode("utf-8", errors="replace").strip()
            last_err = proc.stderr.decode("utf-8", errors="replace").strip()
            logger.warning(
                "%s rc=%d (попытка %d/%d): %s",
                label, proc.returncode, attempt + 1, retries + 1, last_err[:200],
            )
            if attempt < retries:
                time.sleep(1.5)
        raise RuntimeError(f"{label} rc!=0: {last_err[:300]}")

    # Тонкая обёртка контекста для Telegram (CLAUDE.md делает основную работу).
    _TELEGRAM_BRIEF = (
        "Тебя зовут Sana (Сана) — это имя твоей личности для пользователя; "
        "представляйся как Sana, не как Chelsea. По-казахски «сана» = сознание/разум.\n"
        "Ты отвечаешь на сообщение из Telegram от Ерсултана (владелец, единственный "
        "пользователь). Это мессенджер — отвечай КРАТКО и по делу, без длинных "
        "преамбул и отчётов. Можешь ДЕЙСТВОВАТЬ: память, Trello, файлы задач/заметок.\n"
        "- «запомни X» → допиши в projects/jarvis/memory_inbox/notes.md (создай если нет) и подтверди.\n"
        "- «добавь в календарь / поставь встречу / что у меня завтра» → РЕАЛЬНЫЙ Google "
        "Calendar через CLI:\n"
        "    python projects/jarvis/bot/gcal.py list [дней]\n"
        "    python projects/jarvis/bot/gcal.py add \"<название>\" \"<ISO start, напр 2026-06-21T16:00>\" [минут] [описание]\n"
        "  Время локальное (Астана). После создания дай ссылку из вывода. Не дублируй в Trello.\n"
        "- Почта (Gmail) через CLI:\n"
        "    python projects/jarvis/bot/gmail_tool.py list [n]      — последние письма\n"
        "    python projects/jarvis/bot/gmail_tool.py read <id>     — прочитать\n"
        "    python projects/jarvis/bot/gmail_tool.py draft \"<кому>\" \"<тема>\" \"<текст>\" — ЧЕРНОВИК\n"
        "  Письма НЕ отправляй автономно — только черновик, отправит Ерсултан вручную.\n"
        "- НЕ делай необратимого (push в прод, деплой, удаление, секреты) — подготовь и спроси.\n"
        "- ПАМЯТЬ: свежие заметки projects/jarvis/memory_inbox/notes.md приоритетнее старых "
        "project_*.md. При противоречии верь свежему. Если заметил, что старый факт устарел "
        "(задача закрыта, статус изменился) — допиши актуальное в notes.md.\n"
        "- Не выдумывай; нет в памяти — скажи честно."
    )

    def ask_chelsea(
        self, message: str, history: list[tuple[str, str]] | None = None
    ) -> AgentAnswer:
        """Полная Chelsea: claude -p в рабочем пространстве, права из bot-settings.json.

        history — список (вопрос, ответ) предыдущих реплик этого диалога (G1: контекст).
        """
        if not (self._claude_bin and self._workspace and self._chelsea_settings):
            raise RuntimeError("Chelsea-режим не сконфигурирован (configure_chelsea)")
        ctx = ""
        if history:
            lines = ["--- ПРЕДЫДУЩИЙ ДИАЛОГ (контекст, не повторяй дословно) ---"]
            for q, a in history:
                lines.append(f"Ерсултан: {q}")
                lines.append(f"Sana: {a}")
            ctx = "\n".join(lines) + "\n\n"
        mem = self._memory_index()  # G7: индекс памяти для хостов без авто-памяти Claude
        mem_block = ""
        if mem:
            mem_block = (
                "--- ДОЛГОВРЕМЕННАЯ ПАМЯТЬ (индекс; детали по проекту — Read memory/<файл>.md) ---\n"
                f"{mem}\n\n"
            )
        prompt = (
            f"{self._TELEGRAM_BRIEF}\n\n{mem_block}{ctx}"
            f"--- СООБЩЕНИЕ ОТ ЕРСУЛТАНА ---\n{message}"
        )
        answer = self._run_claude(
            [
                self._claude_bin, "-p", "--output-format", "text",
                "--permission-mode", "default",
                "--settings", self._chelsea_settings,
            ],
            prompt,
            timeout=self._chelsea_timeout,
            cwd=self._workspace,
            label="Chelsea",
        )
        return AgentAnswer(status="succeeded", answer=answer, citations=[])

    def _memory_index(self, max_chars: int = 6000) -> str:
        """G7: индекс долговременной памяти (workspace/MEMORY.md) — для хостов без
        авто-памяти Claude (GCP VM). На ноуте файла в корне нет (память живёт в
        ~/.claude) → возвращает пусто, инъекция сама отключается."""
        if not self._workspace:
            return ""
        import os
        p = os.path.join(self._workspace, "MEMORY.md")
        try:
            with open(p, encoding="utf-8") as f:
                return f.read()[:max_chars]
        except OSError:
            return ""

    _BUILDER_BRIEF = (
        "Ты — Sana в режиме исполнителя задач по коду (автопилот). Работаешь "
        "автономно, но БЕЗОПАСНО. Workflow:\n"
        "1. Определи нужный проект/директорию (см. Project Folder Routing в CLAUDE.md).\n"
        "2. Создай отдельную ветку: git checkout -b sana/<краткое-имя>.\n"
        "3. Сделай МИНИМАЛЬНЫЕ изменения под задачу.\n"
        "4. Прогони тесты/линт если есть (pytest / npm test / ruff / py_compile).\n"
        "5. git add + git commit с понятным сообщением (conventional commits).\n"
        "6. НЕ делай git push, merge, deploy — это сделает Ерсултан вручную.\n"
        "В КОНЦЕ дай короткий отчёт для Telegram: что сделано, ветка, какие тесты "
        "прогнал и результат, изменённые файлы, риски. Если задача неясна или рискова — "
        "не гадай, опиши что нужно уточнить и остановись."
    )

    def ask_builder(self, task: str, *, cwd: str | None = None) -> AgentAnswer:
        """Режим исполнителя (Фаза D): claude -p со «строительными» правами, долгий
        таймаут. Правит код в ветке + коммитит, БЕЗ push/merge/deploy."""
        if not (self._claude_bin and self._builder_settings):
            raise RuntimeError("Builder-режим не сконфигурирован (configure_builder)")
        prompt = f"{self._BUILDER_BRIEF}\n\n--- ЗАДАЧА ОТ ЕРСУЛТАНА ---\n{task}"
        # retries=0 — задача дорогая (правки+тесты+коммит), автоповтор может
        # создать дубль-ветку/коммит. Сбой отдаём пользователю как есть.
        answer = self._run_claude(
            [
                self._claude_bin, "-p", "--output-format", "text",
                "--permission-mode", "default",
                "--settings", self._builder_settings,
            ],
            prompt,
            timeout=self._builder_timeout,
            cwd=cwd or self._workspace,
            label="builder",
            retries=0,
        )
        return AgentAnswer(status="succeeded", answer=answer, citations=[])

    # ---------------- voice: Groq Whisper STT ----------------

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        filename: str = "audio.ogg",
        model: str = "whisper-large-v3-turbo",
        language: str = "ru",
        prompt: str = "",
    ) -> str:
        """Расшифровать аудио через Groq Whisper ($0). Ротация по ключам при 429/5xx.

        Имя файла должно иметь поддерживаемое расширение (Groq определяет формат по
        нему): ogg/mp3/m4a/wav/webm/flac. Telegram-войс .oga → шлём как .ogg.
        `prompt` смещает распознавание к нужным терминам (имена проектов/людей).
        """
        if not self._groq_keys:
            raise RuntimeError("Groq-ключи не сконфигурированы для Whisper")
        last_err: Exception | None = None
        for offset in range(len(self._groq_keys)):
            idx = (self._groq_idx + offset) % len(self._groq_keys)
            key = self._groq_keys[idx]
            data = {"model": model, "language": language}
            if prompt:
                data["prompt"] = prompt
            import mimetypes
            ctype = mimetypes.guess_type(filename)[0] or "audio/ogg"
            try:
                resp = requests.post(
                    f"{self._groq_base}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {key}"},
                    files={"file": (filename, audio_bytes, ctype)},
                    data=data,
                    timeout=120,
                )
            except requests.RequestException as exc:
                last_err = exc
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                logger.warning("whisper key #%d → %d, ротирую", idx + 1, resp.status_code)
                last_err = RuntimeError(f"groq {resp.status_code}")
                continue
            if resp.status_code >= 400:
                # бизнес-ошибка (формат/модель) — ротация не поможет, показываем тело
                raise RuntimeError(f"Whisper {resp.status_code}: {resp.text[:300]}")
            self._groq_idx = idx
            return (resp.json().get("text") or "").strip()
        raise RuntimeError(f"Whisper недоступен: {last_err}")

    # ---------------- auth ----------------

    def login(self) -> None:
        resp = requests.post(
            f"{self._base}/api/auth/login",
            json={"email": self._email, "password": self._password},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        logger.info("logged in as %s", self._email)

    def _headers(self) -> dict[str, str]:
        if self._token is None:
            self.login()
        return {"Authorization": f"Bearer {self._token}"}

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        """HTTP-вызов с одной попыткой релогина при 401."""
        url = f"{self._base}{path}"
        kwargs.setdefault("timeout", self._timeout)
        resp = requests.request(method, url, headers=self._headers(), **kwargs)
        if resp.status_code == 401:
            logger.warning("401 — перелогиниваюсь")
            self.login()
            resp = requests.request(method, url, headers=self._headers(), **kwargs)
        return resp

    # ---------------- agent Q&A ----------------

    def ask(
        self,
        query: str,
        *,
        max_steps: int = 8,
        poll_interval: float = 4.0,
        poll_timeout: float = 240.0,
    ) -> AgentAnswer:
        """Запустить агента и дождаться финального ответа."""
        start_resp = self._request(
            "POST",
            f"/api/rags/{self._rag_id}/agent/runs",
            json={"query": query, "max_steps": max_steps},
        )
        start_resp.raise_for_status()
        run_id = start_resp.json()["id"]

        deadline = time.monotonic() + poll_timeout
        while time.monotonic() < deadline:
            time.sleep(poll_interval)
            r = self._request(
                "GET", f"/api/rags/{self._rag_id}/agent/runs/{run_id}"
            )
            r.raise_for_status()
            data = r.json()
            status = data.get("status")
            if status in {"succeeded", "failed", "error", "escalated"}:
                return AgentAnswer(
                    status=status,
                    answer=data.get("answer") or "",
                    confidence=data.get("confidence"),
                    citations=data.get("citations") or [],
                    error=data.get("error"),
                )
        return AgentAnswer(status="timeout", error="агент не ответил вовремя")

    # ---------------- cheap path: search + 1 LLM call ----------------

    def search(self, query: str, *, mode: str = "hybrid", top_k: int = 6) -> list[dict]:
        """Чистый ретрив без LLM (быстро, бесплатно). Возвращает hits."""
        resp = self._request(
            "POST",
            f"/api/rags/{self._rag_id}/search",
            json={"query": query, "mode": mode, "top_k": top_k},
        )
        resp.raise_for_status()
        return resp.json().get("hits", [])

    def _groq_chat(self, messages: list[dict], *, max_tokens: int = 700) -> str:
        """Один Groq-вызов с ротацией по ключам при 429/5xx. ~3-4K токенов."""
        if not self._groq_keys:
            raise RuntimeError("Groq не сконфигурирован (configure_groq не вызван)")
        last_err: Exception | None = None
        # по одному кругу на каждый ключ, начиная с текущего
        for offset in range(len(self._groq_keys)):
            idx = (self._groq_idx + offset) % len(self._groq_keys)
            key = self._groq_keys[idx]
            try:
                resp = requests.post(
                    f"{self._groq_base}/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": self._groq_model,
                        "messages": messages,
                        "temperature": 0.2,
                        "max_tokens": max_tokens,
                    },
                    timeout=self._timeout,
                )
            except requests.RequestException as exc:
                last_err = exc
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                logger.warning("groq key #%d → %d, ротирую", idx + 1, resp.status_code)
                last_err = RuntimeError(f"groq {resp.status_code}")
                continue
            resp.raise_for_status()
            # успешный ключ становится текущим — меньше холостых попыток дальше
            self._groq_idx = idx
            return resp.json()["choices"][0]["message"]["content"] or ""
        raise RuntimeError(f"все Groq-ключи недоступны: {last_err}")

    _RAG_SYSTEM = (
        "Ты — личный ассистент памяти. Отвечай на русском, кратко и по делу, "
        "опираясь ТОЛЬКО на приведённые фрагменты памяти. Указывай номера "
        "источников в квадратных скобках, напр. [1]. Если ответа во фрагментах "
        "нет — честно скажи, что в памяти этого нет. Не выдумывай."
    )

    @staticmethod
    def _build_context(hits: list[dict]) -> tuple[str, list[str]]:
        """Собрать текстовый контекст из hits + список уникальных источников."""
        blocks: list[str] = []
        seen: list[str] = []
        for i, h in enumerate(hits, start=1):
            fn = h.get("filename") or "?"
            blocks.append(f"[{i}] (источник: {fn})\n{(h.get('text') or '').strip()}")
            if fn not in seen:
                seen.append(fn)
        return "\n\n".join(blocks), seen

    def _claude_chat(self, system: str, user: str) -> str:
        """Один вызов `claude -p` (подписка, без потокенных лимитов). Промпт — через
        stdin в UTF-8 (аргументы .cmd на Windows ломают кириллицу)."""
        if not self._claude_bin:
            raise RuntimeError("claude CLI не сконфигурирован")
        prompt = f"{system}\n\n{user}"
        return self._run_claude(
            [self._claude_bin, "-p", "--output-format", "text"],
            prompt,
            timeout=self._claude_timeout,
            label="claude",
        )

    def ask_cheap(self, query: str, *, top_k: int = 6) -> AgentAnswer:
        """Лёгкий RAG: ретрив + Groq. При исчерпании ключей — fallback на claude -p."""
        hits = self.search(query, top_k=top_k)
        if not hits:
            return AgentAnswer(
                status="succeeded",
                answer="В памяти ничего не нашёл по этому вопросу.",
                citations=[],
            )
        context, sources = self._build_context(hits)
        user = f"Фрагменты памяти:\n\n{context}\n\nВопрос: {query}"
        try:
            answer = self._groq_chat(
                [
                    {"role": "system", "content": self._RAG_SYSTEM},
                    {"role": "user", "content": user},
                ]
            )
        except RuntimeError:
            if not self._claude_bin:
                raise
            logger.warning("Groq исчерпан → fallback на claude -p (подписка)")
            answer = self._claude_chat(self._RAG_SYSTEM, user)
        return AgentAnswer(
            status="succeeded",
            answer=answer.strip(),
            citations=[{"filename": fn} for fn in sources],
        )

    def _fresh_notes(self, max_chars: int = 2000) -> str:
        """G3: свежие заметки (memory_inbox/notes.md) — живой слой поверх rag-cms."""
        if not self._workspace:
            return ""
        import os
        p = os.path.join(self._workspace, "projects", "jarvis", "memory_inbox", "notes.md")
        try:
            with open(p, encoding="utf-8") as f:
                return f.read()[-max_chars:]
        except OSError:
            return ""

    def ask_cheap_ctx(
        self, query: str, *, history: list[tuple[str, str]] | None = None, top_k: int = 6
    ) -> AgentAnswer:
        """Быстрый путь (G2) с контекстом диалога (G1): ретрив + Groq.

        Реролвит местоимения по истории, ищет по памяти, отвечает кратко. ~7с.
        """
        # для ретрива склеиваем последний контекст (чтобы «а когда?» нашёл по теме)
        search_q = query
        if history:
            last_q = history[-1][0]
            search_q = f"{last_q} {query}"
        hits = self.search(search_q, top_k=top_k)
        fresh = self._fresh_notes()  # G3: свежий слой поверх старого индекса
        if not hits and not fresh:
            return AgentAnswer(status="succeeded", answer="В памяти ничего не нашёл по этому вопросу.")
        context, sources = self._build_context(hits)
        convo = ""
        if history:
            convo = "Предыдущий диалог:\n" + "\n".join(
                f"Q: {q}\nA: {a}" for q, a in history[-3:]
            ) + "\n\n"
        fresh_block = ""
        if fresh:
            fresh_block = (
                "СВЕЖИЕ ЗАМЕТКИ (приоритетнее старого индекса при противоречии):\n"
                f"{fresh}\n\n"
            )
        user = f"{convo}{fresh_block}Фрагменты памяти:\n\n{context}\n\nВопрос: {query}"
        try:
            answer = self._groq_chat(
                [{"role": "system", "content": self._RAG_SYSTEM},
                 {"role": "user", "content": user}]
            )
        except RuntimeError:
            if not self._claude_bin:
                raise
            answer = self._claude_chat(self._RAG_SYSTEM, user)
        return AgentAnswer(
            status="succeeded",
            answer=answer.strip(),
            citations=[{"filename": fn} for fn in sources],
        )

    def ask_claude(self, query: str, *, top_k: int = 6) -> AgentAnswer:
        """Режим качества: ретрив + синтез через `claude -p` (Claude на подписке)."""
        hits = self.search(query, top_k=top_k)
        if not hits:
            return AgentAnswer(
                status="succeeded",
                answer="В памяти ничего не нашёл по этому вопросу.",
                citations=[],
            )
        context, sources = self._build_context(hits)
        user = f"Фрагменты памяти:\n\n{context}\n\nВопрос: {query}"
        answer = self._claude_chat(self._RAG_SYSTEM, user)
        return AgentAnswer(
            status="succeeded",
            answer=answer.strip(),
            citations=[{"filename": fn} for fn in sources],
        )

    # ---------------- memory write ----------------

    def remember(self, text: str, *, source_tag: str = "telegram") -> str:
        """Сохранить новый факт в память: файл → upload → index.

        Каждый факт = отдельный маленький .md-файл (без удалений, без
        конфликтов имён). Возвращает имя созданного файла.
        """
        safe_ts = str(int(time.time()))
        filename = f"jarvis_note_{safe_ts}.md"
        body = f"# Заметка ({source_tag})\n\n{text.strip()}\n"

        files = {"files": (filename, body.encode("utf-8"), "text/markdown")}
        up = self._request(
            "POST", f"/api/rags/{self._rag_id}/files", files=files
        )
        up.raise_for_status()

        idx = self._request("POST", f"/api/rags/{self._rag_id}/index")
        idx.raise_for_status()
        logger.info("remembered note %s", filename)
        return filename
