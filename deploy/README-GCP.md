# Sana 24/7 на GCP e2-micro (G7) — рунбук

Цель: Sana живёт круглосуточно на бесплатной VM Google Cloud (Always Free), даже
когда ноут выключен. Стоимость — **$0** (в рамках Always Free).

**Архитектура на VM:**
- `~/sana` — репо `sana-memory` (память + персона), = рабочее пространство для `claude -p`, обновляется `git pull` по cron (auto-sync с ноута).
- `~/jarvis` — репо `jarvis` (код бота), симлинк `~/sana/projects/jarvis → ~/jarvis`.
- Мозг = `claude -p` на твоей подписке (headless, без API-ключа).
- rag-cms **не поднимаем** (1GB RAM мало) → `JARVIS_RAG_ENABLED=0`, все вопросы идёт через `claude -p`, который читает память из файлов.
- Голос (Whisper) + Trello + Calendar/Gmail — по API/токенам, работают с VM.

---

## Часть 0 — что нужно с ноута заранее

1. **Память в git** (один раз + потом регулярно). На ноуте в Git Bash:
   ```bash
   bash projects/jarvis/deploy/sync-memory.sh
   ```
   Создаёт/обновляет приватный репо `sana-memory` снимком памяти. Запускай после
   важных изменений памяти (или повесь на Планировщик — см. «Авто-синк» внизу).

2. **Секреты, которые перенесёшь на VM** (НЕ в git): `bot/.env` значения
   (Telegram-токен, Groq-ключи) и Google OAuth — `bot/gcp_oauth.json`,
   `bot/google_token.json`, `bot/google_token_gmail.json`.

---

## Часть 1 — создать GCP-аккаунт и VM (делаешь ты в браузере)

1. Зайти на **console.cloud.google.com**, войти Google-аккаунтом.
2. Принять условия, **создать проект** (напр. `sana-247`).
3. Включить **биллинг** (нужна карта для верификации; Always Free не списывает,
   пока не выйдешь за лимиты). Лимиты Always Free: 1× e2-micro в регионах
   **us-west1 / us-central1 / us-east1**, 30GB standard disk.
4. **Compute Engine → создать инстанс:**
   - Name: `sana`
   - Region: `us-central1` (зона `us-central1-a`)
   - Machine: серия **E2**, тип **e2-micro** (2 vCPU, 1GB)
   - Boot disk: **Ubuntu 24.04 LTS**, тип **Standard persistent disk**, **30 GB**
   - Networking: поставить галочку **Allow HTTP** не нужно (бот по long-polling,
     входящих портов не требует).
   - Создать.
5. Когда инстанс поднимется — кнопка **SSH** (открывает терминал в браузере).
   Это самый простой вход, gcloud локально не нужен.

---

## Часть 2 — git-доступ к приватным репо (на VM)

`setup.sh` клонит приватные `jarvis` и `sana-memory`. Дай VM доступ одним из способов:

**Вариант A — GitHub CLI (проще):**
```bash
sudo apt-get update && sudo apt-get install -y gh
gh auth login        # выбери HTTPS, авторизуйся через браузер/код
```

**Вариант B — Personal Access Token:** создай classic PAT (scope `repo`) на
github.com/settings/tokens, затем на VM:
```bash
git config --global credential.helper store
# при первом clone введёшь логин Yersultan04 и PAT как пароль — git запомнит
```

---

## Часть 3 — провижининг (на VM)

```bash
# забрать setup.sh (склонировав код-репо во временную папку или curl raw)
git clone https://github.com/Yersultan04/jarvis.git ~/jarvis
bash ~/jarvis/deploy/setup.sh
```
`setup.sh` сделает: swap 2GB → apt (python/ffmpeg) → Node 20 → Claude CLI →
клон `sana-memory` в `~/sana` + симлинк → venv + зависимости → `~/.claude` правила
→ `bot/.env` из шаблона → cron `git pull` памяти каждые 10 мин → рендер
`sana.service`. В конце напечатает оставшиеся 4 шага.

---

## Часть 4 — секреты и запуск (на VM, по подсказке setup.sh)

```bash
# 1) вход в подписку Claude (headless)
claude setup-token            # скопируй токен
nano ~/jarvis/bot/.env        # впиши CLAUDE_CODE_OAUTH_TOKEN=..., TELEGRAM_BOT_TOKEN=..., GROQ_API_KEYS=...
```
> Важно: НЕ задавай `ANTHROPIC_API_KEY` — он перебьёт подписку платным API.

```bash
# 2) Google OAuth — перенести с ноута (на НОУТЕ, Git Bash). Узнай внешний IP VM в консоли:
scp projects/jarvis/bot/gcp_oauth.json projects/jarvis/bot/google_token.json \
    projects/jarvis/bot/google_token_gmail.json  <vm-user>@<vm-ip>:~/jarvis/bot/
```

```bash
# 3) сервис (на VM)
sudo cp /tmp/sana.service /etc/systemd/system/sana.service
sudo systemctl daemon-reload
sudo systemctl enable --now sana
systemctl status sana --no-pager
journalctl -u sana -f          # смотрим лог; должно быть "бот запущен: @ChelseaAI_bot"
```

Проверка: напиши боту в Telegram — ответит уже **с VM**. Выключи ноут — Sana жива.

---

## Эксплуатация

- **Логи:** `journalctl -u sana -f` или `~/jarvis/bot/bot.log`.
- **Рестарт:** `sudo systemctl restart sana`.
- **Обновить код бота:** на VM `cd ~/jarvis && git pull && sudo systemctl restart sana`.
- **Обновить память:** авто — cron тянет `~/sana` каждые 10 мин. Главное — пушить
  с ноута (`sync-memory.sh`).
- **Память расходится?** на VM `cd ~/sana && git pull`.

### Авто-синк памяти с ноута (опционально)
Чтобы VM всегда видела свежую память, повесь `sync-memory.sh` на Планировщик
Windows (напр. каждый час). В терминале Sana с `!`:
```
! schtasks /Create /TN "SanaMemSync" /TR "\"C:\Program Files\Git\bin\bash.exe\" -lc \"bash /c/Users/Acer/AI_Assistant/projects/jarvis/deploy/sync-memory.sh\"" /SC HOURLY /F
```

---

## Ограничения v1 (осознанно)
- **`/task` по тяжёлым репам** (Elza, Haul) на 1GB не потянет — оставить на ноуте,
  либо клонировать конкретный репозиторий на VM по требованию. Ядро (память,
  Trello, бриф, голос, Calendar/Gmail) — тянет.
- **Скорость:** `claude -p` на e2-micro медленнее ноута (~60-120с/ответ). Терпимо
  для «оператора в кармане»; для скорости позже — инстанс помощнее (уже не $0).
- **rag-cms (быстрый путь) выключен** — фактологические вопросы тоже идут в claude -p.
