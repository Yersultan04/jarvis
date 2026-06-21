# G6 — Sana HUD наружу через Cloudflare Tunnel (sana.askrizz.com)

Цель: открыть веб-командный центр Sana по постоянному адресу **https://sana.askrizz.com**,
доступному только тебе (Cloudflare Access по Google-почте). Стоимость — **$0**.

**Почему туннель, а не проброс порта:** VM не открывает входящих портов наружу;
`cloudflared` сам инициирует исходящее соединение к Cloudflare, а Cloudflare
проксирует https → твой `localhost:8765`. Никаких дыр в фаерволе, бесплатный TLS.

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
   - **Service:** Type `HTTP`, URL `localhost:8765`
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

## Часть 3 — поднять веб-HUD и туннель сервисами (на VM)

Код уже в репо. Обнови и разверни сервисы:

```bash
cd ~/jarvis && git pull

# 1) gunicorn в venv (если ещё не стоит)
~/jarvis/.venv/bin/pip install -r ~/jarvis/bot/requirements.txt

# 2) проставь в bot/.env (Часть G6):
#    SANA_WEB_EMAIL=slvaita3@gmail.com
#    SANA_WEB_TOKEN=<python -c "import secrets;print(secrets.token_urlsafe(32))">  # опц.
nano ~/jarvis/bot/.env

# 3) отрендерить пути в юнитах и установить (USER/BOT_DIR/VENV как в sana.service)
U=$(whoami); BOT=~/jarvis/bot; VENV=~/jarvis/.venv
sed -e "s#__USER__#$U#g" -e "s#__BOT_DIR__#$BOT#g" -e "s#__VENV__#$VENV#g" \
    ~/jarvis/deploy/sana-web.service | sudo tee /etc/systemd/system/sana-web.service
sed -e "s#__USER__#$U#g" \
    ~/jarvis/deploy/cloudflared.service | sudo tee /etc/systemd/system/cloudflared.service

sudo systemctl daemon-reload
sudo systemctl enable --now sana-web cloudflared
systemctl status sana-web cloudflared --no-pager
```

---

## Часть 4 — проверка

1. На VM локально: `curl -s localhost:8765/ | head` → должен прийти HTML HUD (без Cf —
   локальный доступ разрешён).
2. В браузере (на телефоне/ноуте): **https://sana.askrizz.com** → экран Cloudflare
   Access → войди почтой → откроется HUD. Скажи/напиши Sana — ответит.
3. Открой URL в режиме инкогнито без входа → должен показать гейт Access, **не** HUD.

---

## Эксплуатация
- **Логи туннеля:** `journalctl -u cloudflared -f` • **веба:** `~/jarvis/bot/web.log`.
- **Рестарт:** `sudo systemctl restart sana-web cloudflared`.
- **Обновить HUD:** `cd ~/jarvis && git pull && sudo systemctl restart sana-web`.
- **Сменить адрес/политику:** всё в дашборде Cloudflare (Tunnels / Access), код не трогаем.

## Ограничения (осознанно)
- **Скорость:** claude -p на e2-micro ~60–120с/ответ — для HUD-команд терпимо.
- **Тяжёлый `/task`** по большим репам (Haul/Elza) на 1GB не идёт — это про движок
  автономии (COMPANY_VISION Ф1), решаем воркером-ноутом/апгрейдом, не в G6.
- **Бот + веб вместе** оба зовут claude -p: одновременные тяжёлые запросы могут
  упереться в RAM. MemoryMax (бот 900M / веб 600M) + swap 2G сглаживают; при OOM —
  снизить web до 1 потока или гонять HUD только когда нужно.
