"""Google Calendar CLI для Sana — на официальных библиотеках Google.

Команды:
  python gcal.py auth                         — одноразовый вход (откроет браузер)
  python gcal.py list [days]                  — события на N дней вперёд (по умолч. 7)
  python gcal.py add "<title>" "<start_iso>" [minutes] [description]
                                              — создать событие (start в ISO,
                                                напр. 2026-06-21T16:00, локальное время)

Секреты: читает gcp_oauth.json, токен в google_token.json (оба gitignored).
Вывод — компактный текст/JSON, чтобы Sana (claude -p) легко его читала.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/calendar"]
BASE = Path(__file__).resolve().parent
CREDS = BASE / "gcp_oauth.json"
TOKEN = BASE / "google_token.json"
# Часовой пояс по умолчанию — Астана (UTC+5).
TZ = os.environ.get("SANA_TZ", "Asia/Almaty")


def _service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return build("calendar", "v3", credentials=creds)


def cmd_auth() -> None:
    _service()
    print("OK: авторизация прошла, токен сохранён (google_token.json).")


def cmd_list(days: int = 7) -> None:
    svc = _service()
    now = dt.datetime.now(dt.timezone.utc)
    end = now + dt.timedelta(days=days)
    events = (
        svc.events()
        .list(
            calendarId="primary",
            timeMin=now.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        )
        .execute()
        .get("items", [])
    )
    if not events:
        print(f"Событий на ближайшие {days} дн. нет.")
        return
    for e in events:
        start = e["start"].get("dateTime", e["start"].get("date", ""))
        print(f"- {start} | {e.get('summary', '(без названия)')}")


def cmd_add(title: str, start_iso: str, minutes: int = 60, description: str = "") -> None:
    svc = _service()
    start = dt.datetime.fromisoformat(start_iso)
    end = start + dt.timedelta(minutes=minutes)
    body = {
        "summary": title,
        "description": description,
        "start": {"dateTime": start.isoformat(), "timeZone": TZ},
        "end": {"dateTime": end.isoformat(), "timeZone": TZ},
    }
    ev = svc.events().insert(calendarId="primary", body=body).execute()
    print(f"OK: создано «{title}» на {start_iso} ({minutes} мин). Ссылка: {ev.get('htmlLink')}")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    try:
        if cmd == "auth":
            cmd_auth()
        elif cmd == "list":
            cmd_list(int(sys.argv[2]) if len(sys.argv) > 2 else 7)
        elif cmd == "add":
            if len(sys.argv) < 4:
                print("Использование: add \"<title>\" \"<start_iso>\" [minutes] [description]")
                return
            title = sys.argv[2]
            start_iso = sys.argv[3]
            minutes = int(sys.argv[4]) if len(sys.argv) > 4 else 60
            description = sys.argv[5] if len(sys.argv) > 5 else ""
            cmd_add(title, start_iso, minutes, description)
        else:
            print(f"Неизвестная команда: {cmd}")
            print(__doc__)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
