#!/usr/bin/env bash
# ============================================================
# Ноут → sana-memory (git). Снимок канонической памяти + персоны Chelsea → push.
# VM подтянет по cron (git pull). Запускать на НОУТЕ (Git Bash) вручную или по
# расписанию (Планировщик Windows).
#
# Что синкается: персона Chelsea (CLAUDE.md/team.md), глобальные правила,
# долговременная память (MEMORY.md + memory/*.md), tasks/. Секреты — НЕ синкаются.
# ============================================================
set -euo pipefail

GH_USER="${GH_USER:-Yersultan04}"
MEM_REPO="${MEM_REPO:-sana-memory}"
WORK="${SANA_MEM_WORK:-$HOME/.sana-memory}"     # рабочая копия репо на ноуте
AIA="/c/Users/Acer/AI_Assistant"
MEM_SRC="/c/Users/Acer/.claude/projects/C--Users-Acer-AI-Assistant/memory"
GLOBAL_CLAUDE="/c/Users/Acer/.claude"

# --- рабочая копия репо (clone / init для пустого репо) ---
if [ ! -d "$WORK/.git" ]; then
  git clone "https://github.com/$GH_USER/$MEM_REPO.git" "$WORK" 2>/dev/null || {
    mkdir -p "$WORK"; git -C "$WORK" init -q
    git -C "$WORK" remote add origin "https://github.com/$GH_USER/$MEM_REPO.git"
  }
fi
git -C "$WORK" pull --ff-only origin main 2>/dev/null || true

# --- .gitignore репо памяти (код jarvis сюда не кладём — он в своём репо) ---
cat > "$WORK/.gitignore" <<'EOF'
projects/jarvis/
*token*.json
*secret*
*credentials*.json
.env
.env.*
.venv/
__pycache__/
EOF

# 1) персона Chelsea + команда
cp "$AIA/CLAUDE.md" "$WORK/CLAUDE.md"
[ -f "$AIA/team.md" ] && cp "$AIA/team.md" "$WORK/team.md"

# 2) глобальные правила пользователя (~/.claude)
mkdir -p "$WORK/.claude/rules"
cp "$GLOBAL_CLAUDE/CLAUDE.md" "$WORK/.claude/CLAUDE.md" 2>/dev/null || true
cp -r "$GLOBAL_CLAUDE/rules/." "$WORK/.claude/rules/" 2>/dev/null || true

# 3) долговременная память (MEMORY.md индекс + memory/*.md)
mkdir -p "$WORK/memory"
cp "$MEM_SRC/MEMORY.md" "$WORK/MEMORY.md" 2>/dev/null || true
cp "$MEM_SRC/"*.md "$WORK/memory/" 2>/dev/null || true

# 4) задачи (Chelsea на них ссылается)
mkdir -p "$WORK/tasks"
cp "$AIA/tasks/todo.md" "$WORK/tasks/todo.md" 2>/dev/null || true
cp "$AIA/tasks/lessons.md" "$WORK/tasks/lessons.md" 2>/dev/null || true

# --- защита: не пушим, если внутри захардкоженный секрет ---
LEAK=$(cd "$WORK" && git grep -nIE \
  'gsk_[A-Za-z0-9]{20}|AIzaSy[A-Za-z0-9_-]{20}|sk-[A-Za-z0-9]{20}|[0-9]{8,10}:AA[A-Za-z0-9_-]{30}' \
  -- . 2>/dev/null | grep -vE 'example|<.*>|XXXX|placeholder' || true)
if [ -n "$LEAK" ]; then
  echo "!! НАЙДЕН возможный секрет в памяти — push ОТМЕНЁН. Проверь:"; echo "$LEAK"
  exit 1
fi

# --- commit + push ---
cd "$WORK"
git add -A
if git diff --cached --quiet; then
  echo "память без изменений — пушить нечего"
else
  git commit -q -m "memory sync $(date '+%Y-%m-%d %H:%M')"
  git branch -M main
  git push -q -u origin main
  echo "память запушена в $GH_USER/$MEM_REPO"
fi
