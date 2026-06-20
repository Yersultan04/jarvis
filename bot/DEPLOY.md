# Sana — хостинг 24/7 ($0)

Двухслойный план: автостарт на ноуте (сейчас) + GCP e2-micro (always-on).

---

## Слой 1 — автостарт на ноуте (мгновенно, $0)

Бот сам поднимается при входе в Windows и сам перезапускается при падении
(watchdog). Работает, пока ноут включён.

**Файлы (готовы):**
- `sana_watchdog.bat` — цикл: убить дубли → запустить бота → при падении рестарт.
- `sana_launcher.vbs` — запускает watchdog скрыто (без окна).

**Регистрация (выполни один раз сам — нужны твои права):**
В терминале Sana набери с `!`:
```
! schtasks /Create /TN "SanaBot" /TR "wscript.exe \"C:\Users\Acer\AI_Assistant\projects\jarvis\bot\sana_launcher.vbs\"" /SC ONLOGON /RL HIGHEST /F
```
Проверить: `! schtasks /Query /TN SanaBot`
Запустить прямо сейчас, не дожидаясь перезагрузки: `! schtasks /Run /TN SanaBot`
Удалить: `! schtasks /Delete /TN SanaBot /F`

После этого бот переживает перезагрузки и краши. Останется ограничение —
ноут должен быть включён.

---

## Слой 2 — GCP e2-micro (always-on, $0, когда захочешь)

Google Cloud Always Free: 1 инстанс e2-micro (us-west1/central1/east1), 30GB диск,
1GB RAM. Живёт 24/7 бесплатно даже с выключенным ноутом.

### Что переносим
Ядро ассистента (НЕ все проекты — память тяжёлая для 1GB):
- Claude Code CLI (вход через подписку, без API-ключа)
- `projects/jarvis/bot/` (код бота)
- Память: `MEMORY.md` + `~/.claude/.../memory/` + `CLAUDE.md` + правила
- `.env` (токены), ffmpeg, python

### Шаги (ты + я)
1. **Ты:** создать GCP-аккаунт → VM e2-micro (Ubuntu 22.04, регион из Always Free).
2. **Я:** дам `setup.sh` — ставит node, claude CLI, python, ffmpeg, зависимости.
3. **Ты на VM:** `claude setup-token` (вход в подписку Claude, headless OAuth) →
   `export CLAUDE_CODE_OAUTH_TOKEN=...`. БЕЗ ANTHROPIC_API_KEY (иначе перебьёт подписку).
4. **Я:** systemd-юнит `sana.service` (автозапуск + рестарт), env, синк памяти.
5. Свап 2GB (1GB RAM мало для claude -p + node) — `setup.sh` включит.

### Нюансы
- **/task по проектам** (Elza, Haul) на 1GB не потянет тяжёлые репы — оставить на
  ноуте, либо клонировать конкретный репозиторий по требованию. Ассистентское ядро
  (память, Trello, бриф, голос, vision) — потянет.
- **Память-синк:** при изменении MEMORY на ноуте — пушить на VM (git/rsync). Или
  вести память в репозитории и `git pull` на VM по cron.
- **Trello/Groq/Gemini** — API, работают с VM из коробки.

### Готовность — ✅ КИТ ГОТОВ (G7, 2026-06-20)
Полный деплой-кит лежит в **`deploy/`**, пошаговый рунбук — **`deploy/README-GCP.md`**:
- `deploy/setup.sh` — провижининг Ubuntu (swap/node/claude/venv/clone/cron).
- `deploy/sana.service` — systemd-юнит (Restart=always, MemoryMax=900M).
- `deploy/sync-memory.sh` — ноут→`sana-memory` git auto-sync памяти (VM тянет по cron).
- `deploy/.env.vm.example` — конфиг VM (RAG выключен, claude -p timeout поднят).

Архитектура на VM: `~/sana` (репо `sana-memory`, = WORKSPACE, авто-pull) +
`~/jarvis` (код) симлинком. rag-cms НЕ поднимаем (1GB мало) → `JARVIS_RAG_ENABLED=0`,
всё через `claude -p`, который читает память из синканых файлов + инъекция индекса
`MEMORY.md` в промпт. Тебе остаётся: создать VM в GCP Console + `claude setup-token` +
перенести Google-токены. Подробно — `deploy/README-GCP.md`.

---

## Рекомендация
Слой 1 (автостарт на ноуте) — для «здесь и сейчас». Слой 2 (GCP) — по рунбуку
`deploy/README-GCP.md`, когда нужен always-on без ноута.
