# Jarvis — $0 Personal AI Operator

**Цель:** превратить текущий стек (Chelsea-оркестратор + Groq + GH Actions) в интерактивного,
проактивного, мультимодального ассистента уровня «Jarvis» при нулевом бюджете.
Начинаем со **слоя память + RAG** поверх готовой платформы `rag-cms`.

**Статус:** Фаза 1 РАБОТАЕТ (2026-06-20). Память+RAG поднят локально, агент отвечает по памяти с цитатами. **Всё на Groq+Voyage ($0, стабильные ключи).**

## Как запустить (Фаза 1)

Стек живёт в `projects/jarvis/rag-cms/` (docker compose prod, без frontend для экономии RAM).

```powershell
$dir = "C:\Users\Acer\AI_Assistant\projects\jarvis\rag-cms"
docker compose --project-directory $dir -f "$dir\docker-compose.prod.yml" up -d --no-build postgres qdrant backend
```

- **API:** http://127.0.0.1:8000  • **Логин:** slvaita3@gmail.com / см. `.env` (BOOTSTRAP_ADMIN_PASSWORD)
- **RAG `Jarvis Memory`** (id в `.env`-заметках): русский FTS, 38 файлов памяти → 142 чанка
- **Конфиг $0** (в `projects/jarvis/rag-cms/.env`, gitignored):
  - Embeddings → **Voyage** voyage-3 (1024-dim)
  - Chat+rerank агента → **Groq** `llama-3.3-70b-versatile` (OpenAI-compat endpoint, ключи в корневом `.env`, 3 шт для ротации)
  - Groq оставлен и под будущий Whisper (Фаза 3).

### КЛЮЧЕВОЙ фикс — почему Groq заработал
SGR-агент в `loop.py` запрашивал `max_tokens=30000` на каждый шаг. Провайдеры (Groq/Gemini) считают **`input + max_tokens`** против лимита TPM → запрос виделся как ~35K и пробивал Groq free-tier 12K TPM (ошибка 413). **Фикс:** `loop.py` строка ~271 `max_tokens=30000 → 4096`. Реальный промпт ~5.4K + 4K = ~9.5K < 12K → Groq тянет. Это правка в коде, нужна пересборка образа (`docker compose ... build backend`).
> Gemini Flash рассматривался как fallback, но эфемерные `AQ.…`-токены (то, что приходило) имеют крошечную непредсказуемую квоту — отброшены. Постоянный `AIzaSy…`-ключ Gemini остаётся валидной альтернативой, если Groq TPD (100K/день) станет тесен.

### Критичные уроки запуска (не повторять ошибки)
1. **max_tokens vs TPM:** провайдеры считают input+max_tokens. Большой резерв на вывод пробивает лимит. См. фикс выше.
2. **Model snapshot per-RAG:** rag-cms замораживает `settings.models.llm_model` в БД при создании RAG. Смена env НЕ меняет модель существующего RAG. Фикс: `UPDATE rags SET settings = jsonb_set(...)` или пересоздать RAG.
3. **Compose env passthrough:** в `docker-compose.prod.yml` backend.environment изначально НЕ пробрасывал `LLM_API_BASE_URL`/`RERANK_*`/`EMBED_PROVIDER` — добавлено. Без них контейнер уходит в OpenRouter.
4. **pydantic int|None:** пустые `EMBED_DIM=`/`EMBED_API_KEY=` ломают старт (`int_parsing`) — не пробрасывать пустыми для Voyage.
5. **Postgres volume:** меняешь POSTGRES_PASSWORD → нужен `down -v` (пароль применяется только при инициализации пустого тома).
6. **Образ:** собирать backend из `projects/jarvis/rag-cms` (исходник Qdrant-only, без pgvector). Старый образ из inspect-копии требовал расширение `vector` → миграция падала.
7. **Groq TPD:** free tier 100K токенов/день на ключ. Каждый агент-ран ~50-60K → ~1.5 рана/день/ключ. Ротация по 3 ключам (`GROQ_API_KEY`, `_2`, `_3`) даёт ~4-5 ранов/день. Для интенсива — постоянный Gemini-ключ.

**Проверено (2026-06-20) на Groq:** «дедлайн Med Triage» → 31 авг 2026 (conf 0.95); «аккаунты KazBench» → GitHub Yersultan04 / HF Yersultan03 (conf 0.9); «score Haul» → 7.5/10 (conf 0.9). **3/3 с верными цитатами.**

---

## Зафиксированные решения

| Решение | Выбор | Обоснование |
|---------|-------|-------------|
| STT (распознавание речи) | **Groq Whisper** (облако) | Бесплатно, 2000 запросов/день, уже в стеке. Голос уходит в облако Groq — приемлемо |
| Память (UI для рук) | **Trilium Notes** | Родной REST API — боту проще писать/читать, чем у Obsidian (нужен плагин) |
| Память (поисковый мозг) | **rag-cms** (готовая платформа) | Production-grade агентный RAG, нужно конфигурировать, а не кодить |
| Embeddings | **Voyage free tier** | ~200M токенов бесплатно, 0 нагрузки на ноут, стек уже на нём по умолчанию |
| LLM для агента | **Groq** (Llama-3.3-70B) | Бесплатно, OpenAI-совместим → подключается через `llm_api_base_url` override |
| Хостинг память+RAG | **Локально (ноут, docker compose)** | Стек создан под compose. $0, приватно. Память не обязана быть 24/7 |
| Хостинг бота 24/7 | **Отложено** (Oracle отвалился) | Кандидат: Google Cloud e2-micro (Always Free). Решаем в Фазе 3 |

---

## Итоги фактчека (2026-06-19)

| Проверка | Результат | Влияние |
|----------|-----------|---------|
| Groq Whisper бесплатный? | **Да** — 2000 аудио-запросов/день, без карты. ($0.04/час только сверх лимита) | Голос НЕ заблокирован |
| Oracle ARM RAM 2026? | **12 GB** (с 15 июня 2026 урезали с 24). Старые 4/24 инстансы останавливают/биллят | Планируем не под Oracle (отвалился при регистрации) |
| Telegram getFile лимит? | **20 MB** на скачивание ботом (50 MB на отправку) | Не проблема: войс ~1MB/мин = 20 мин речи. Длинные аудио → self-hosted Bot API (2GB) |

---

## Что уже есть в rag-cms (НЕ строим заново)

- FastAPI + Postgres 16 + Qdrant + hybrid search (dense + sparse, RRF fusion)
- ReAct+SGR агент: ≤40 шагов, 12 tools, dedup-guard, grounding-проверка цитат
- Users + JWT auth, per-RAG изоляция (своя Qdrant-коллекция на RAG)
- Contextual chunk enrichment (Anthropic-style), Vision OCR fallback
- Per-RAG язык FTS (есть `russian`), Alembic-миграции, SSE-стриминг
- Docker compose: dev / prod / onprem; React+Vite фронт
- **Ключевое:** каждая LLM-роль читает свой `*_API_BASE_URL` + `*_API_KEY`
  → весь стек делается $0 через конфиг, без правки кода

---

## Целевая $0-архитектура

```
ВХОД                  Telegram-бот (Python, long-polling)
                        voice .ogg → getFile (≤20MB) → Groq Whisper → текст
                            │
                            ▼
ПОИСКОВЫЙ МОЗГ         rag-cms (локально на ноуте, docker compose)
(память + RAG)          ├─ FastAPI       :8000
                        ├─ Postgres 16   :5432   (метаданные, chunks, FTS)
                        ├─ Qdrant        :6333   (векторы, коллекция rag_jarvis)
                        └─ агент ReAct+SGR (отвечает по памяти с цитатами)
                            │
КОГНИЦИЯ ($0)           ├─ chat/агент → Groq Llama-3.3-70B (llm_api_base_url)
                        ├─ embeddings → Voyage free tier
                        └─ (приват) → Ollama на ноуте (позже)
                            │
                            ▼
ВЫХОД (TTS)           текст → edge-tts → .ogg → Telegram войс

ИСТОЧНИКИ ПАМЯТИ      Trilium (ручные заметки) + MEMORY.md + projects/ + mem0 dumps
                        → ingest в RAG 'Jarvis Memory'

ПРОАКТИВНОСТЬ          n8n (watchers: Gmail/Calendar/cron) — Фаза 4
24/7 ХОСТ             лёгкий бот → GCP e2-micro / Cloudflare Workers — Фаза 3+
```

---

## Фазы реализации

### Фаза 1 — Поднять память + RAG локально (СЛЕДУЮЩАЯ)
**Цель:** работающий RAG 'Jarvis Memory', отвечающий по моей текущей памяти.

1. Скопировать `rag-cms` → `projects/jarvis/rag-cms/` (из распакованного zip)
2. Настроить `.env` под $0:
   - `embed_provider=voyage`, `VOYAGE_API_KEY=...` (получить ключ)
   - `llm_api_base_url=https://api.groq.com/openai/v1`, `llm_api_key=<groq>`,
     `llm_model=llama-3.3-70b-versatile`
   - то же для `rerank_api_*` (или отключить rerank на старте)
   - `default_fts_language=russian`, `JWT_SECRET=<random>`,
     `BOOTSTRAP_ADMIN_EMAIL/_PASSWORD`
3. `docker compose up -d` → проверить health backend/Qdrant/Postgres
4. `alembic upgrade head` (если не авто)
5. Создать RAG `Jarvis Memory` через API (`fts_language=russian`)
6. **Ингест памяти:** залить `MEMORY.md`, `projects/**/*.md`, дампы mem0 →
   `POST /files` → `POST /index`
7. Тест: спросить агента «какой дедлайн у Med Triage?» → ждём цитату из памяти

**Критерий успеха:** агент отвечает на 3 вопроса по моей памяти с верными цитатами.

### Фаза 2 — Telegram-вход (текст)
- Бот принимает текст → `POST /agent/runs` → стримит ответ обратно
- Команда «запомни X» → пишет в Trilium/Markdown → авто-реиндекс

### Фаза 3 — Голос
- Войс → getFile → Groq Whisper → текст → агент → edge-tts → войс
- Кандидат хостинга 24/7 для бота решаем здесь (GCP e2-micro vs Cloudflare)

### Фаза 4 — Проактивность (watchers)
- n8n: триггеры Gmail/Calendar/дедлайны → уведомление/задача в Telegram

### Фаза 5 — Руки (browser-use / Playwright) и локальный Ollama для приватного
*(продвинутый этап, после стабильного ядра)*

---

## Риски и подводные камни

- **Groq TPM-лимит** (~12K токенов/мин на Llama-3.3-70B): длинные контексты →
  переключать на Gemini Flash. RAG это смягчает — в LLM идут только топ-чанки
- **Voyage free tier**: следить за расходом токенов (200M большой, но не вечный)
- **edge-tts**: хак над MS API, может отвалиться — не делать mission-critical
- **rag-cms `BackgroundTasks`**: индексация блокирует graceful-shutdown,
  не переживает рестарт uvicorn (известно из их фазы-4 TODO) — для локалки ок
- **Ноут не 24/7**: память доступна когда ноут включён. Для always-on нужен
  отдельный лёгкий бот-хост (Фаза 3+)
- **Embeddings размерность**: при смене модели существующие Qdrant-коллекции
  не переедут — пересоздавать RAG

---

## Next Action
Получить **Voyage API key** (voyageai.com) и **Groq API key** (console.groq.com),
затем выполнить Фазу 1, шаги 1–4 (копирование + .env + `docker compose up`).
