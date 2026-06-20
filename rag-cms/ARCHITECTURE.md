# rag-cms — Architecture

## Что это

Сервис, на котором клиент создаёт «задачу» (RAG instance), загружает файлы и
получает:

- свой UI (страница `/rag/{id}` в общей SPA),
- свой API (`/api/rags/{id}/...`),
- свой агентный RAG (ReAct + SGR, ~40 шагов, tools, эскалация) — *фаза 2*.

Каждый RAG изолирован: своя Qdrant-коллекция, свой каталог файлов, свои чанки
в Postgres. Удаление RAG'а каскадно убирает всё.

## Поток данных (MVP-1)

```
USER                        BACKEND                       STORAGE
─────                       ───────                       ───────
POST /api/rags         →    create Rag(id, …)        →    rows: rags
                            ensure_qdrant_collection →    qdrant: rag_<id>
                            mkdir data/rags/<id>/    →    fs

POST /rags/{id}/files  →    save files               →    fs
                            insert File rows         →    rows: files

POST /rags/{id}/index  →    BackgroundTask:
                              for each File:
                                parse_pdf            (PyMuPDF)
                                chunk_pages          (heading-aware)
                                embed (Voyage)       (batched)
                                upsert (Qdrant)      →    qdrant: rag_<id>
                                insert Chunk rows    →    rows: chunks
                              update IngestRun       →    rows: ingest_runs

POST /rags/{id}/search →    embed_query (Voyage)
                            dense ← qdrant.search
                            sparse ← pg ts_rank_cd
                            RRF fusion
                            hydrate Chunk rows      ←    rows: chunks
                            return hits
```

## Per-RAG изоляция

| Layer | Изоляция |
|---|---|
| Filesystem | `data/rags/<rag_id>/files/<file_id>.<ext>` |
| Postgres | Все `chunks`/`files`/`ingest_runs` имеют `rag_id`-индекс, удаление каскадное |
| Qdrant | Отдельная коллекция `rag_<uuid-no-dashes>`, размерность из `Rag.embed_dim` |
| API | `rag_id` обязательный path-параметр всех ресурсных endpoint'ов |

Авторизация в MVP-1 не реализована — фронт ходит напрямую. Добавим в фазе 3
(JWT/API-key per-RAG или OTP как в ai-doc-agent).

## Chunking

`backend/app/ingestion/chunker.py` — greedy sentence packing:
- цель: `[CHUNK_MIN_TOKENS, CHUNK_MAX_TOKENS]` (130–300 по умолчанию);
- overlap в `CHUNK_OVERLAP` токенов между соседними чанками;
- захват ближайшего heading (`CHAPTER N`, `\d.\d.\d Title`, …) — кладётся
  и в payload Qdrant, и в `chunks.heading`;
- гигантские предложения hard-split по токенам.

Это базовая стратегия. На фазе 2 добавим:
- OCR fallback для PDF со сканами (Vision-LLM через OpenRouter),
- структурный парсинг таблиц,
- contextual prepend (как в Anthropic contextual retrieval).

## Retrieval

- **Dense**: Voyage embedding запроса → `qdrant.search` с cosine, top-K = 50.
- **Sparse**: Postgres FTS на `to_tsvector('simple', heading || text)`,
  ранжируем `ts_rank_cd`, top-K = 50. Конфигурация `simple` — language-agnostic;
  для русского можно подменить на `russian` config.
- **Hybrid** = RRF (`1 / (k + rank)`, `k=60` по умолчанию), затем top-K = 30.
- LLM rerank — отдельная функция в фазе 2 (cross-encoder или OpenRouter judge).

## Phase 2 — Agent (реализовано)

Код: [backend/app/agent/](backend/app/agent/).

ReAct loop на ≤`AGENT_MAX_STEPS` (40 по умолчанию). LLM получает на каждом
шаге системный промпт + описание tools + EVIDENCE POOL + сжатую историю
последних шагов, возвращает строго один JSON-объект, валидируемый
`NextStepEnvelope` (pydantic discriminated union).

SGR реализован через `response_format={"type": "json_object"}` (OpenRouter
поддерживает на большинстве моделей) + ручную валидацию pydantic'ом. При
парс-ошибке делается ровно одна повторная попытка с сообщением об ошибке.

### Pre-loop: Query Router

Файл: [backend/app/agent/router.py](backend/app/agent/router.py).

До основного цикла классифицируем запрос:
- **regex-эвристики** (дёшево, без LLM): идентификаторы вида `ARB-123`/`295/2025`
  → `exact_lookup`; ключевые слова сравнения/агрегации → `decompose_and_search`.
- **LLM-fallback** при низкой confidence: GPT решает kind (lookup / multi_entity /
  aggregate / definition / free_text) и подсказывает первый tool + args.

Решение публикуется событием `router_decision` и попадает в системный промпт
как advisory hint только на первых двух шагах и только пока pool пуст —
дальше агент сам решает по обстановке.

### Tools (реализованы)

| Tool | Сигнатура | Покрывает |
|---|---|---|
| `hybrid_search` | `(query, top_k=10)` | базовый поиск (RRF dense+sparse) |
| `dense_search` / `sparse_search` | `(query, top_k)` | выбор стиля поиска |
| `decompose_and_search` | `(query, max_subqueries=4, top_k_each=6)` | multi-entity/comparative |
| `hyde_search` | `(query, top_k=10)` | rare-term / абстрактные запросы |
| `exact_lookup` | `(pattern, top_k=20)` | regex по chunk.text (PG `~*`) |
| `fetch_page` | `(file_id, page)` | все чанки страницы |
| `fetch_document` | `(file_id, max_pages=50)` | last-resort full-doc |
| `list_files` | `()` | дискавери `file_id`-ов для fetch_page |
| `cache_fact` / `recall_fact` | `(key, value?)` | scratchpad, переживает компакцию |
| `rerank_pool` | `(query?, top_n=10, blend=0.3)` | LLM-rerank текущего pool без новых поисков |

Финал и эскалация — это не tools, а отдельные kind'ы NextStep:
`{kind: "final", answer, citations, confidence}` или
`{kind: "escalate", reason, confidence}`. Это форсирует SGR-валидацию
на финальном шаге.

### Loop ([backend/app/agent/loop.py](backend/app/agent/loop.py))

На каждом шаге:
1. Собираем сообщения: system + user(query + budget + pool + compacted history + опц. nudge).
2. LLM → `_parse_step` → `NextStepEnvelope`.
3. В зависимости от `kind`:
   - **`tool`**: dedup-проверка (если та же `(tool, args)` повторилась ≥2 раз,
     блокируем с nudge'ом); иначе диспатчим, мерджим pool по `chunk_id`.
   - **`final`**: валидируем citations — каждая `chunk_id` должна быть в pool.
     Если pool не пуст и citations отсутствуют — отказываем, nudge'аем "цитируй
     или эскалируй". Иначе записываем результат, выходим.
   - **`escalate`**: записываем причину, выходим.
4. Сжатие истории: последние `HISTORY_KEEP_RECENT` шагов — подробно, остальные —
   однострочное summary `#N tool=X args={…} → first 160 chars of observation`.
5. Если за `max_steps` не пришли к final/escalate — `status=failed` с
   `budget_exhausted`.

Каждое решение, наблюдение, ошибка валидации и финальный ответ публикуются
в `EventBroker` (типы: `run_started`, `thought`, `tool_call`, `observation`,
`tool_blocked`, `parse_error`, `final_rejected`, `final_answer`, `escalated`,
`budget_exhausted`, `run_finished`, `run_failed`).

### Post-final: Grounding pass

Файл: [backend/app/agent/grounding.py](backend/app/agent/grounding.py).

Когда модель отдала `final_answer`, для каждой `Citation` мы проверяем, что
`quote` действительно встречается в указанном `chunk_id`:
1. **Substring** на нормализованном тексте (lower + whitespace squash).
2. **Substring** на агрессивно нормализованном (`[^a-zA-Z0-9] → space`) —
   ловит цитаты с переносами/пунктуацией.
3. **Fuzzy** через `difflib.SequenceMatcher` (порог ≥ 0.78).

`confidence` итогового ответа **ограничивается** долей подтверждённых цитат:
если 2 из 3 цитат не нашлись — final.confidence не может быть выше 0.33.
Полный per-citation отчёт публикуется событием `grounding_report`.

### Phase 3 additions

**Contextual chunk enrichment** ([backend/app/ingestion/enrichment.py](backend/app/ingestion/enrichment.py)) —
один LLM-call формирует саммари документа (по началу/середине/концу
со склейкой), потом чанки батчатся (`CONTEXTUAL_ENRICHMENT_BATCH=8`),
каждому генерируется 1-предложение ситуации. Перед embedding ставится перед
текстом чанка, в БД хранится отдельно в `chunks.extra.context`. Off по
умолчанию (`CONTEXTUAL_ENRICHMENT=false`).

**Vision OCR fallback** ([backend/app/ingestion/ocr.py](backend/app/ingestion/ocr.py)) —
PyMuPDF страница рендерится в JPEG @ 150 DPI и шлётся в vision-модель через
OpenRouter (`LLM_VISION_MODEL`). Триггер: длина извлечённого текста <
`INGEST_OCR_MIN_CHARS=200`. Применяется только к PDF.

**Auth** ([backend/app/auth.py](backend/app/auth.py)) — users + JWT, closed
registration:

- `users` table (email/password_hash/role/is_active), bcrypt через passlib,
  JWT HS256 через PyJWT с TTL = `JWT_TTL_HOURS` (24 по умолчанию).
- **Registration is closed.** `POST /api/auth/register` работает только пока
  в БД нет ни одного user'а (bootstrap первого admin'а). После этого 403.
- Admin создаёт пользователей через `POST /api/auth/users` или UI
  `/admin/users`. Поддерживается reset password, role-toggle, deactivation,
  delete. Защита: нельзя удалить/демотировать/деактивировать последнего
  admin'а, нельзя удалить себя.
- Альтернативный bootstrap: env `BOOTSTRAP_ADMIN_EMAIL` + `_PASSWORD` —
  создаются автоматически при первом старте FastAPI (lifespan), если таблица
  users пуста.
- `Rag.owner_id` FK на users; `list_rags` фильтруется по owner (admin видит
  все); `get_owned_rag` пускает только owner'а или admin'а.
- SSE-стрим авторизуется одноразовым `agent_runs.stream_token` (`?token=…`)
  потому что `EventSource` не несёт `Authorization` header. Токен связан с
  `(rag_id, run_id)`, ничего не раскрывает за пределами этого run'а.

**Per-RAG FTS language** — `rags.settings.fts_language` выбирается при
`POST /api/rags?fts_language=russian`. `sparse.py` берёт ts_config из
allow-list (`simple` / `english` / `russian` / `german` / ...). Расширения
`unaccent`, `pg_trgm` поднимаются [postgres/init/01_extensions.sql](postgres/init/01_extensions.sql).

**Alembic migrations** — [backend/alembic/](backend/alembic/), стартовая
миграция `0001_initial`. `create_all` убран из lifespan.

### Streaming ([backend/app/agent/events.py](backend/app/agent/events.py))

Каждый event дублируется:
- **durably** в `data/rags/<rag_id>/runs/<run_id>/events.jsonl`,
- **live** через `asyncio.Queue`-подписчиков `EventBroker`.

SSE-endpoint `/agent/runs/{run_id}/stream`:
- если run в терминальном статусе → отдаёт replay из JSONL и закрывается;
- иначе подписывается на broker и стримит до `stream_end`.

`?since=N` поддерживается и для replay, и для live (используется фронтом
при реконнекте).

## Открытые вопросы

- **Multi-tenant access**: API-keys per-RAG или общая auth перед API gateway?
- **Frontend на каждый RAG**: пока один SPA с роутингом. Если потребуется
  white-label под клиента — добавим `rags.theme`/`rags.brand_config` и
  будем рендерить разные стили по `rag_id` (без отдельной сборки).
- **Russian-friendly FTS**: подкрутить Postgres `ts_config` (russian +
  unaccent) — отложено до первого реального корпуса.
- **Storage scaling**: сейчас файлы на локальной FS. Для prod — S3-совместимое
  хранилище за `storage_path`.
