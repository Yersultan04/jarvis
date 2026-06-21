# G6 — Sana HUD наружу через Cloudflare Tunnel (sana.askrizz.com)

Цель: открыть веб-командный центр Sana по постоянному адресу **https://sana.askrizz.com**,
доступному только тебе (Cloudflare Access по Google-почте). Стоимость — **$0**.

**Что именно выставляем:** основной адрес ведёт на **дашборд Sana Corp** —
живой офис с агентами + пульт автономии (тумблеры ▶/■/⚡, статус, очередь). Он уже
встроен в бот (модуль `dashboard.py`, порт **8770**) и крутится 24/7 внутри
`sana.service` — отдельный веб-процесс для пульта поднимать НЕ нужно. Голосовой
HUD-сфера (`sana_web.py`, порт 8765) — опциональный второй адрес (см. «Доп.»).

**Почему туннель, а не проброс порта:** VM не открывает входящих портов наружу;
`cloudflared` сам инициирует исходящее соединение к Cloudflare, а Cloudflare
проксирует https → твой `localhost:8770`. Никаких дыр в фаерволе, бесплатный TLS.

**Почему URL не будет меняться:** это *named tunnel*, привязанный к DNS-записи
`sana.askrizz.com` в твоём аккаунте Cloudflare. Рестарт VM/туннеля адрес не меняет
(в отличие от quick-tunnel `*.trycloudflare.com`).

---

## Часть 0 — перевести askrizz.com на Cloudflare (один раз, делаешь ты)

Домен сейчас на Namecheap (BasicDNS). Для Tunnel + Access зона должна жить на Cloudflare.

1. **dash.cloudflare.com** → войти/зарегаться (бесплатно) → **Add a site** → `askrizz.com`
   → план **Free**.
2. Cloudflare покажет **2 своих nameserver** (вида `xxx.ns.cloudflare.com`). Скопируй.
3. **Namecheap** → Domain List → askrizz.com → Manage → **Nameservers** → выбери
   **Custom DNS** → вставь 2 NS от Cloudflare → ✓ сохранить.
4. Подожди пропагацию (обычно 10–60 мин, иногда дольше). Cloudflare пришлёт письмо
   «askrizz.com is now active». Существующие записи (если есть сайт) Cloudflare
   импортирует автоматически — проверь, что ничего не потерялось.

> ⚠️ Пока статус зоны в Cloudflare не «Active» — туннель и Access не заработают.

---

## Часть 1 — установить cloudflared на VM (делаешь ты, браузерный SSH)

```bash
# Debian/Ubuntu amd64
curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
  -o /tmp/cloudflared
sudo install -m 755 /tmp/cloudflared /usr/local/bin/cloudflared
cloudflared --version
```

---

## Часть 2 — создать туннель и приложение Access (делаешь ты, дашборд)

В Cloudflare: **Zero Trust** (левое меню; первый вход попросит выбрать team-name —
любой, бесплатный план до 50 пользователей).

### 2a. Туннель
1. **Networks → Tunnels → Create a tunnel** → тип **Cloudflared** → имя `sana`.
2. Cloudflare покажет команду установки с токеном (`eyJ...`). **Скопируй только токен**
   (длинная строка после `--token`). Положи его на VM в защищённый файл:
   ```bash
   echo 'CLOUDFLARED_TOKEN=ВСТАВЬ_ТОКЕН' | sudo tee /etc/cloudflared.env
   sudo chmod 600 /etc/cloudflared.env
   ```
3. На том же экране, шаг **Public Hostname → Add a public hostname**:
   - **Subdomain:** `sana`  • **Domain:** `askrizz.com`
   - **Service:** Type `HTTP`, URL `localhost:8770`  ← дашборд-пульт (встроен в бот)
   - Save. (DNS-запись `sana` Cloudflare создаст сам.)

### 2b. Access (гейт безопасности — обязательно!)
1. **Access → Applications → Add an application** → **Self-hosted**.
2. **Application domain:** `sana.askrizz.com`.
3. **Add policy:** Action **Allow**, правило **Emails** → `slvaita3@gmail.com`
   (добавь и `ersultan040403@gmail.com`, если хочешь входить им). Save.
4. Identity provider: хватит дефолтного **One-time PIN** (код на почту). По желанию
   подключи **Google** для входа в один клик.

> Без 2b любой, кто узнает `sana.askrizz.com`, дойдёт до Sana (а у неё claude -p
> с правами на файлы/Trello). Access — это и есть замок.

---

## Часть 3 — поднять туннель сервисом (на VM)

Пульт (дашборд) уже работает внутри бота на `localhost:8770` — отдельный веб-процесс
не нужен. Нужно: подхватить auth-настройки в `.env` и поднять только туннель.

```bash
cd ~/jarvis && git pull
~/jarvis/.venv/bin/pip install -r ~/jarvis/bot/requirements.txt   # на случай новых зависимостей

# 1) проставь в bot/.env (Часть G6) и перезапусти бот, чтобы dashboard.py увидел auth:
#    SANA_WEB_EMAIL=slvaita3@gmail.com
#    SANA_WEB_TOKEN=<python -c "import secrets;print(secrets.token_urlsafe(32))">  # опц.
nano ~/jarvis/bot/.env
sudo systemctl restart sana          # бот (с дашбордом 8770) перечитает .env

# 2) туннель: токен уже положен в /etc/cloudflared.env (Часть 2a). Ставим сервис:
U=$(whoami)
sed -e "s#__USER__#$U#g" ~/jarvis/deploy/cloudflared.service \
    | sudo tee /etc/systemd/system/cloudflared.service
sudo systemctl daemon-reload
sudo systemctl enable --now cloudflared
systemctl status cloudflared --no-pager
```

---

## Часть 4 — проверка

1. На VM локально: `curl -s localhost:8770/api/state | head` → JSON состояния агентов
   (без Cf — локальный доступ разрешён). Если `forbidden` локально — что-то с auth.
2. В браузере (телефон/ноут): **https://sana.askrizz.com** → экран Cloudflare Access →
   войди почтой → откроется живой офис + пульт. Жми ▶/■/⚡ — автономия реагирует.
3. Инкогнито без входа → гейт Access, **не** дашборд. И `curl https://sana.askrizz.com/api/control`
   снаружи без Access → не должен дёргать автономию.

---

## Доп. — голосовой HUD вторым адресом (опционально)
Если захочешь и сферу-HUD (`sana_web.py`): подними `sana-web.service` (gunicorn, 8765)
и заведи в дашборде Cloudflare второй public hostname `hud.askrizz.com → localhost:8765`
+ своё Access-приложение. Команды установки сервиса — в комментариях `deploy/sana-web.service`.

## Эксплуатация
- **Логи туннеля:** `journalctl -u cloudflared -f` • **бота/дашборда:** `~/jarvis/bot/bot.log`.
- **Рестарт туннеля:** `sudo systemctl restart cloudflared`.
- **Обновить пульт:** `cd ~/jarvis && git pull && sudo systemctl restart sana` (дашборд в боте).
- **Сменить адрес/политику:** всё в дашборде Cloudflare (Tunnels / Access), код не трогаем.

## Ограничения (осознанно)
- **Скорость:** claude -p на e2-micro ~60–120с/ответ — для команд пульта терпимо.
- **Тяжёлый `/task`** по большим репам (Haul/Elza) на 1GB не идёт — это про движок
  автономии (COMPANY_VISION Ф1), решаем воркером-ноутом/апгрейдом, не в G6.
- **Пульт даёт управление автономией наружу** — поэтому Access обязателен: без него
  любой с URL дёргал бы ▶/■/⚡. Auth-гейт в `dashboard.py` — второй замок поверх Access.
