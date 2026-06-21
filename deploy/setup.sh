#!/usr/bin/env bash
# ============================================================
# Sana — провижининг GCP e2-micro (Ubuntu 22.04/24.04) для 24/7.
# Идемпотентно: можно запускать повторно. Запуск:  bash setup.sh
#
# Раскладка на VM:
#   ~/sana    = sana-memory (память, = WORKSPACE для claude -p), git pull по cron
#   ~/jarvis  = jarvis (код бота), симлинк ~/sana/projects/jarvis -> ~/jarvis
# ============================================================
set -euo pipefail

GH_USER="${GH_USER:-Yersultan04}"
CODE_REPO="${CODE_REPO:-jarvis}"
MEM_REPO="${MEM_REPO:-sana-memory}"
SANA_HOME="${SANA_HOME:-$HOME/sana}"      # память + WORKSPACE
CODE_DIR="${CODE_DIR:-$HOME/jarvis}"      # код бота
BOT_DIR="$CODE_DIR/bot"
VENV="$CODE_DIR/.venv"

log(){ printf '\n=== %s ===\n' "$*"; }

log "1/8 swap 2GB (1GB RAM мало для claude -p + node)"
if ! sudo swapon --show 2>/dev/null | grep -q /swapfile; then
  sudo fallocate -l 2G /swapfile 2>/dev/null || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

log "2/8 apt пакеты (python, ffmpeg, git, curl)"
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip python-is-python3 ffmpeg git curl ca-certificates

log "3/8 Node.js 20 (для Claude Code CLI)"
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi

log "4/8 Claude Code CLI"
if ! command -v claude >/dev/null 2>&1; then
  sudo npm install -g @anthropic-ai/claude-code
fi

log "5/8 клон репозиториев (память + код)"
# Нужен git-доступ к приватным репо: заранее `gh auth login` ИЛИ git credential с PAT.
if [ ! -d "$SANA_HOME/.git" ]; then
  git clone "https://github.com/$GH_USER/$MEM_REPO.git" "$SANA_HOME"
else
  git -C "$SANA_HOME" pull --ff-only || true
fi
if [ ! -d "$CODE_DIR/.git" ]; then
  git clone "https://github.com/$GH_USER/$CODE_REPO.git" "$CODE_DIR"
else
  git -C "$CODE_DIR" pull --ff-only || true
fi
# симлинк, чтобы путь `projects/jarvis/bot/...` резолвился из WORKSPACE как на ноуте
mkdir -p "$SANA_HOME/projects"
ln -sfn "$CODE_DIR" "$SANA_HOME/projects/jarvis"

log "6/8 python venv + зависимости"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r "$BOT_DIR/requirements.txt"

log "7/8 синк глобальных правил Claude (~/.claude) + .env"
mkdir -p "$HOME/.claude/rules"
cp "$SANA_HOME/.claude/CLAUDE.md" "$HOME/.claude/CLAUDE.md" 2>/dev/null || true
cp -r "$SANA_HOME/.claude/rules/." "$HOME/.claude/rules/" 2>/dev/null || true
if [ ! -f "$BOT_DIR/.env" ]; then
  cp "$CODE_DIR/deploy/.env.vm.example" "$BOT_DIR/.env"
  sed -i -e "s#__SANA_HOME__#$SANA_HOME#g" -e "s#__CODE_DIR__#$CODE_DIR#g" "$BOT_DIR/.env"
  echo "!! создан $BOT_DIR/.env — ЗАПОЛНИ секреты (nano $BOT_DIR/.env)"
fi

log "8/8 cron: git pull памяти каждые 10 мин (auto-sync)"
CRON_LINE="*/10 * * * * cd $SANA_HOME && git pull --ff-only >> $SANA_HOME/.memsync.log 2>&1"
# `|| true` — пустой crontab/grep без совпадений возвращает 1 и под set -e ронял скрипт
( crontab -l 2>/dev/null | grep -v "cd $SANA_HOME && git pull" || true ; echo "$CRON_LINE" ) | crontab - || true

# рендер systemd-юнита с реальными путями
sed -e "s#__USER__#$(whoami)#g" -e "s#__BOT_DIR__#$BOT_DIR#g" -e "s#__VENV__#$VENV#g" \
    "$CODE_DIR/deploy/sana.service" > /tmp/sana.service

cat <<EOF

============================================================
ГОТОВО (провижининг). Осталось 4 шага:

1) Вход в подписку Claude (headless OAuth):
     claude setup-token
   скопируй токен → впиши в $BOT_DIR/.env:  CLAUDE_CODE_OAUTH_TOKEN=...
   (НЕ задавай ANTHROPIC_API_KEY — он перебьёт подписку платным API)

2) Заполни остальные секреты в $BOT_DIR/.env:
     TELEGRAM_BOT_TOKEN, GROQ_API_KEYS  (nano $BOT_DIR/.env)

3) Перенеси Google OAuth с ноута (на ноуте, Git Bash):
     scp bot/gcp_oauth.json bot/google_token.json bot/google_token_gmail.json \\
         <vm-user>@<vm-ip>:$BOT_DIR/

4) Установи и запусти сервис:
     sudo cp /tmp/sana.service /etc/systemd/system/sana.service
     sudo systemctl daemon-reload
     sudo systemctl enable --now sana
     systemctl status sana --no-pager ; journalctl -u sana -f
============================================================
EOF
