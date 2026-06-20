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

### Готовность
setup.sh + sana.service подготовлю по команде «делаем GCP». Тебе останется только
создать VM и сделать `claude setup-token`.

---

## Рекомендация
Сейчас включить Слой 1 (одна команда выше) — бот переживёт ребуты сегодня же.
Слой 2 — когда решишь, что нужен always-on без ноута.
