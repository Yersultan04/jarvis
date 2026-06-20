# rag-cms

Multi-tenant платформа, хостящая агентные RAG'и. Создаёте задачу, загружаете
файлы — получаете изолированный RAG со своим API, своим UI и собственным
ReAct+SGR-агентом.

**Status: фаза 3 завершена + users.** Поверх ingestion + retrieval + ReAct/SGR-агента
добавлено: contextual chunk enrichment, vision OCR fallback, **users + JWT auth
(каждый user владеет своими RAG'ами)**, per-RAG язык FTS, Alembic-миграции.

## Стек

- **Backend**: FastAPI (Python 3.11+)
- **Metadata DB**: Postgres 16
- **Vector store**: Qdrant — отдельная коллекция на каждый RAG (`rag_<uuid>`)
- **Embeddings**: Voyage (`voyage-3` по умолчанию)
- **LLM**: OpenRouter (используется в rerank/агенте — фаза 2)
- **Frontend**: Vite + React + TS

## Структура

```
backend/app/
  main.py             FastAPI + CORS + auto-migrate
  config.py           pydantic-settings из .env
  db.py               SQLAlchemy async
  models.py           Rag, File, Chunk, IngestRun
  schemas.py          Pydantic IO-схемы
  clients/
    qdrant.py         per-RAG collection
    voyage.py         embed_documents / embed_query
    llm.py            OpenRouter (chat completions)
  ingestion/
    parser.py         PyMuPDF + txt/md
    chunker.py        heading-aware, ~300 токенов, overlap
    pipeline.py       parse → chunk → embed → store (background)
  retrieval/
    dense.py          Qdrant cosine search
    sparse.py         Postgres tsvector + websearch_to_tsquery
    hybrid.py         Reciprocal Rank Fusion
  api/
    rags.py           CRUD
    files.py          upload + delete
    ingest.py         start + status
    search.py         query
frontend/src/
  pages/RagList.tsx
  pages/RagDetail.tsx (/rag/:id)
  api.ts
data/rags/<rag_id>/files/  файлы пользователя
```

## Endpoints

| Метод | Путь | Описание |
|---|---|---|
| POST | `/api/rags` | Создать RAG (возвращает `id`) |
| GET | `/api/rags` | Список |
| GET | `/api/rags/{id}` | Детали |
| DELETE | `/api/rags/{id}` | Удалить (и Qdrant-коллекцию) |
| POST | `/api/rags/{id}/files` | Загрузить файлы (multipart `files=`) |
| GET | `/api/rags/{id}/files` | Список файлов |
| DELETE | `/api/rags/{id}/files/{file_id}` | Удалить файл |
| POST | `/api/rags/{id}/index` | Запустить индексацию (фоновая) |
| GET | `/api/rags/{id}/index/status` | Статус последнего run'а |
| POST | `/api/rags/{id}/search` | Hybrid search (body: `{query, mode, top_k}`) |
| POST | `/api/rags/{id}/agent/runs` | Стартовать запуск агента (body: `{query, max_steps?}`) |
| GET | `/api/rags/{id}/agent/runs` | Список запусков |
| GET | `/api/rags/{id}/agent/runs/{run_id}` | Статус + финальный ответ + цитаты |
| GET | `/api/rags/{id}/agent/runs/{run_id}/events?since=N` | Replay событий (JSONL) |
| GET | `/api/rags/{id}/agent/runs/{run_id}/stream` | SSE-стрим событий live |

## Запуск

### 1. Зависимости

```bash
cp .env.example .env
# заполнить VOYAGE_API_KEY и (для фазы 2) OPENROUTER_API_KEY
docker compose up -d
```

### 2. Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .
alembic upgrade head     # apply migrations
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Схему ведёт Alembic — никаких `create_all` на старте больше нет.
Новая миграция: `alembic revision --autogenerate -m "describe change"`.

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

UI на http://localhost:5173, API на http://localhost:8000.

## Агент (фаза 2)

ReAct loop на ≤40 шагов, на каждом шаге LLM возвращает один из трёх ответов
(`tool` / `final` / `escalate`), валидируемый pydantic-схемой
`NextStepEnvelope` ([backend/app/agent/schemas.py](backend/app/agent/schemas.py)).
Финальный ответ обязан цитировать только чанки из накопленного EVIDENCE POOL —
выдуманные citations отклоняются и шаг переигрывается.

**Инструменты** ([backend/app/agent/tools.py](backend/app/agent/tools.py)):
`hybrid_search`, `dense_search`, `sparse_search`, `decompose_and_search`,
`hyde_search`, `exact_lookup`, `fetch_page`, `fetch_document`, `list_files`,
`cache_fact`, `recall_fact`, `rerank_pool`.

**Query router** ([backend/app/agent/router.py](backend/app/agent/router.py)) —
до основного цикла классифицирует запрос (lookup / multi_entity / aggregate /
definition / free_text). Сначала пытаются дешёвые regex-эвристики (идентификаторы
типа `ARB-123`, ключевые слова сравнения/агрегации), при низкой уверенности —
LLM. Результат отдаётся агенту как advisory hint на первом шаге.

**LLM reranker** ([backend/app/retrieval/rerank.py](backend/app/retrieval/rerank.py))
— переранжирует чанки через LLM, фьюзится с retrieval-score (по умолчанию
0.7 LLM + 0.3 retrieval). Используется как tool `rerank_pool` — агент сам решает,
когда вызвать (после набора 8+ чанков, если топ слабый).

**Grounding pass** ([backend/app/agent/grounding.py](backend/app/agent/grounding.py))
— после `final_answer` каждая `citation.quote` проверяется на substring + fuzzy
(SequenceMatcher ratio ≥ 0.78) против указанного чанка. Confidence итогового
ответа ограничивается долей подтверждённых цитат. Полный отчёт публикуется
событием `grounding_report`.

## Phase 3 features

**Contextual chunk enrichment**
([backend/app/ingestion/enrichment.py](backend/app/ingestion/enrichment.py)) —
включается `CONTEXTUAL_ENRICHMENT=true`. Один LLM-call формирует короткое
саммари документа, затем чанки батчатся (по 8) и каждому генерится 1-предложение
ситуирующего контекста. Перед embedding контекст prepend'ится; в `chunks.text`
лежит оригинал, контекст хранится в `chunks.extra.context`. Подход — из Anthropic
Contextual Retrieval (взято у nsteam в leaderboard).

**Vision OCR fallback**
([backend/app/ingestion/ocr.py](backend/app/ingestion/ocr.py)) — для PDF-страниц
с менее чем `INGEST_OCR_MIN_CHARS` извлечённого текста рендерим страницу в
JPEG @ `INGEST_OCR_RENDER_DPI` и шлём через OpenRouter в `LLM_VISION_MODEL`
(по умолчанию `openai/gpt-4o-mini`). Запасной путь — оставляем оригинальный
короткий текст.

**Users + JWT** ([backend/app/auth.py](backend/app/auth.py)) — JWT в
`Authorization: Bearer …` для всех endpoint'ов кроме `/api/auth/login`,
`/api/auth/registration-status` и `/api/auth/register` (последний открыт ТОЛЬКО
пока в БД нет ни одного пользователя — bootstrap первого admin'а).

**Регистрация закрыта по дизайну.** Новых пользователей создаёт админ через
страницу `/admin/users` или `POST /api/auth/users`. Это правильное multi-tenant
поведение — никто не может прийти и создать себе аккаунт.

Каждый RAG имеет `owner_id`; user видит только свои RAG'и, admin видит все.
Пароли через bcrypt (passlib).

**Bootstrap первого админа — два способа на выбор:**
1. Через `.env`: задать `BOOTSTRAP_ADMIN_EMAIL` + `BOOTSTRAP_ADMIN_PASSWORD`.
   При первом старте, если таблица users пустая, аккаунт создаётся
   автоматически. После этого `register` отдаёт 403.
2. Через UI: открыть `/login` на свежей БД — увидишь форму
   "Создать первого администратора". Заполнил → залогинен → форма пропадает
   навсегда. Дальше — только через `/admin/users`.

Для SSE используется одноразовый **stream_token** на каждый run (выдаётся в
ответе `POST /agent/runs`), а не JWT в query — токен короткий, привязан к
`(rag_id, run_id)`, не светит главный секрет в access-логах.

Endpoints auth:
- `GET /api/auth/registration-status` — `{open: bool}`, открыт публично.
- `POST /api/auth/register` — bootstrap, открыт пока нет users.
- `POST /api/auth/login` — `{email, password}` → `{access_token, user}`.
- `GET /api/auth/me` — текущий user.
- `GET /api/auth/users` — список (admin only).
- `POST /api/auth/users` — создать (admin only).
- `PATCH /api/auth/users/{id}` — role / is_active / password (admin only).
- `DELETE /api/auth/users/{id}` — удалить (admin only).

Защита от self-lockout: нельзя демотировать/деактивировать/удалить последнего
админа; нельзя удалить собственный аккаунт.

**Per-RAG FTS language** — задаётся при создании RAG, хранится в
`rags.settings.fts_language` (`simple` | `english` | `russian` | ...).
Sparse search использует именно его в `to_tsvector`/`websearch_to_tsquery`.
Расширения `unaccent` и `pg_trgm` подключаются автоматически init-скриптом
[postgres/init/01_extensions.sql](postgres/init/01_extensions.sql).

**Стриминг**: события пишутся одновременно в JSONL
(`data/rags/<rag>/runs/<run>/events.jsonl`) и в in-memory queue.
SSE-endpoint раздаёт live при `running`, replay при terminal-статусе.

**UI**: `/rag/:id/chat` — список запусков, лог событий в реальном времени,
финальный ответ с цитатами (file + страница + точная цитата).

## On-prem / OSS-стек

В дополнение к облачной конфигурации (OpenRouter + Voyage) поддерживается
запуск на **open-weight** моделях. Архитектура:

- Каждая LLM-роль (`chat` / `vision` / `rerank`) читает свой `*_API_BASE_URL` +
  `*_API_KEY`. Если не задано — фолбэк на `OPENROUTER_*`.
- **Embeddings** — отдельный диспатчер ([backend/app/clients/embeddings.py](backend/app/clients/embeddings.py)).
  `EMBED_PROVIDER=voyage` (по умолчанию) или `EMBED_PROVIDER=openai` — для любого
  OpenAI-compatible `/v1/embeddings` endpoint'а (TEI / Infinity / vLLM /
  Together / Fireworks).

### Готовая on-prem / OSS-конфигурация ([.env.onprem.example](.env.onprem.example))

| Роль | Модель | Endpoint |
|---|---|---|
| Embeddings | `voyage-4-lite` (1024-dim, multilingual) | Voyage cloud |
| Chat / агент | `openai/gpt-oss-120b` | OpenRouter |
| Vision / OCR | `qwen/qwen3-vl-30b-a3b-instruct` | OpenRouter |
| Rerank | `openai/gpt-oss-20b` | OpenRouter |

Полностью managed — ничего своими руками не запускаем, только два API-ключа
(Voyage + OpenRouter).

**Альтернатива** — открыть embeddings (`BAAI/bge-m3` или `Qwen/Qwen3-Embedding-8B`,
топ-1 на MTEB) через self-hosted TEI или Together / Fireworks. В env переключить
`EMBED_PROVIDER=openai` и заполнить `EMBED_API_BASE_URL` / `EMBED_MODEL_NAME` /
`EMBED_DIM`. В [docker-compose.onprem.yml](docker-compose.onprem.yml) есть готовый
закомментированный блок `tei-bge-m3`.

### Запуск

```bash
cp .env.onprem.example .env
# заполнить VOYAGE_API_KEY + OPENROUTER_API_KEY
# JWT_SECRET сгенерировать: python -c "import secrets;print(secrets.token_urlsafe(48))"

docker compose -f docker-compose.prod.yml -f docker-compose.onprem.yml up -d
```

**Полностью offline** — расскомментируй `tei-bge-m3` и `vllm-qwen3vl` в
compose, заполни в env `EMBED_API_BASE_URL` / `LLM_API_BASE_URL` /
`VISION_API_BASE_URL` на свои self-hosted endpoints. Никакой трафик не
покинет машину.

### Внимание про размерность

Когда меняешь `EMBED_MODEL_NAME` / `EMBED_DIM`, **существующие RAG'и не
переедут** — их Qdrant-коллекции созданы под прежнюю размерность. Пересоздай
их (`DELETE /api/rags/{id}` → `POST` → re-upload → re-index). Новые RAG'и сразу
получат текущий `EMBED_DIM`.

## Что дальше (фаза 4)

- **CISC voting / multi-persona debate** (ai-doc-agent) — N параллельных
  цепей, голосование по `final_answer`. Заметно повышает точность, но дорого.
- **Воркер вне веб-процесса**: `BackgroundTasks` блокирует graceful-shutdown
  и не выживает рестарт uvicorn. Кандидаты: arq, dramatiq, или просто
  собственный worker-процесс с очередью в Postgres.
- **Cross-reference link resolution** (Ilia Ris) — на ingestion детектить
  ссылки "see §4.2" / "см. п. 7" и привязывать чанки.
- **Embedding cache** для contextual enrichment по sha256 — сейчас на
  re-ingest всё считается заново.
- **Rate limiting** на per-RAG-key (защита от abuse при публичном деплое).
- **Удаление файлов**: текущий `DELETE /files/{id}` стирает только Postgres-строки
  и файл; чанки в Qdrant остаются. Нужно чистить и там.
