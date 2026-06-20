# Sana → Google Calendar + Gmail (Фаза F)

Чтобы Sana реально ставила встречи и читала/драфтила письма, нужен Google
MCP-сервер с твоей авторизацией. Google требует OAuth-доступ — это единственный
шаг, который делаешь ты (бесплатно, один раз). Дальше всё подключаю я.

---

## Что делаешь ТЫ (один раз, ~10 мин, $0)

### 1. Создать проект в Google Cloud
- https://console.cloud.google.com/ → вверху «Select a project» → **New Project**
- Имя: `Sana Assistant` → Create.

### 2. Включить API
В поиске вверху найди и нажми **Enable** для:
- **Google Calendar API**
- **Gmail API**

### 3. OAuth consent screen (экран согласия)
- Меню → **APIs & Services → OAuth consent screen**
- User type: **External** → Create
- App name: `Sana`, твой email в support + developer contact → Save and continue
- Scopes — пропусти (Save and continue)
- **Test users → Add users → впиши свой gmail** (slvaita3@gmail.com) → Save
  (пока приложение в test-режиме, доступ только у тебя — это нормально и безопасно)

### 4. Создать OAuth Client ID
- **APIs & Services → Credentials → Create Credentials → OAuth client ID**
- Application type: **Desktop app** → name `Sana Desktop` → Create
- В окне нажми **Download JSON** → сохрани файл.

### 5. Положить файл боту
Переименуй скачанный файл в **`gcp_oauth.json`** и положи в:
```
C:\Users\Acer\AI_Assistant\projects\jarvis\bot\gcp_oauth.json
```
(он gitignored — секрет в репо не попадёт)

---

## Что делаю Я (после того как файл на месте)
1. Ставлю Google MCP-серверы (Calendar + Gmail).
2. Одноразовый OAuth: откроется браузер → подтвердишь доступ своим аккаунтом →
   refresh-токен сохранится (дальше работает 24/7 без браузера).
3. Подключаю боту через выделенный `sana-mcp.json` (Trello + Calendar + Gmail),
   разрешаю инструменты в правах, обновляю поведение:
   - «поставь встречу / добавь в календарь» → реальное событие в Google Calendar
     (вместо Trello-заглушки)
   - «какие у меня встречи завтра» → читает календарь
   - «ответь Виталию» → драфт письма в Gmail (отправку — только с твоим «ок»)

---

## Безопасность
- `gcp_oauth.json` и токен — gitignored, не в репо.
- Gmail: Sana может читать и **готовить черновики**; отправка письма — только после
  твоего явного подтверждения (внешнее действие, L4).
- Приложение в test-режиме = доступ только у твоего аккаунта.

Когда положишь `gcp_oauth.json` — скажи, и я доделаю подключение.
