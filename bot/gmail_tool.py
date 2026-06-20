"""Gmail CLI для Sana — официальные библиотеки Google.

Команды:
  python gmail_tool.py auth                     — одноразовый вход (браузер)
  python gmail_tool.py list [n]                 — последние n писем (по умолч. 10)
  python gmail_tool.py read <message_id>        — прочитать письмо
  python gmail_tool.py draft "<to>" "<subject>" "<body>"
                                                — создать ЧЕРНОВИК (не отправляет!)

Отправка писем НЕ реализована намеренно — внешнее действие, только вручную
из Gmail после проверки черновика. Секреты: gcp_oauth.json + google_token_gmail.json
(оба gitignored).
"""
from __future__ import annotations

import base64
import sys
from email.mime.text import MIMEText
from pathlib import Path

# readonly (читать) + compose (черновики). Отправку не даём автономно.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]
BASE = Path(__file__).resolve().parent
CREDS = BASE / "gcp_oauth.json"
TOKEN = BASE / "google_token_gmail.json"


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
    return build("gmail", "v1", credentials=creds)


def _header(msg: dict, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def cmd_auth() -> None:
    _service()
    print("OK: Gmail авторизован, токен сохранён.")


def cmd_list(n: int = 10) -> None:
    svc = _service()
    msgs = (
        svc.users().messages().list(userId="me", maxResults=n, labelIds=["INBOX"]).execute().get("messages", [])
    )
    if not msgs:
        print("Входящих нет.")
        return
    for m in msgs:
        full = svc.users().messages().get(userId="me", id=m["id"], format="metadata",
                                          metadataHeaders=["From", "Subject", "Date"]).execute()
        frm = _header(full, "From")
        subj = _header(full, "Subject") or "(без темы)"
        print(f"- [{m['id']}] {frm} | {subj}")


def cmd_read(msg_id: str) -> None:
    svc = _service()
    full = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()
    print("От:", _header(full, "From"))
    print("Тема:", _header(full, "Subject"))
    print("Дата:", _header(full, "Date"))
    print("---")
    print((full.get("snippet") or "")[:1500])


def cmd_draft(to: str, subject: str, body: str) -> None:
    svc = _service()
    mime = MIMEText(body, _charset="utf-8")
    mime["to"] = to
    mime["subject"] = subject
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
    draft = svc.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
    print(f"OK: черновик создан (id {draft['id']}). Проверь и отправь вручную из Gmail → Черновики.")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    try:
        if cmd == "auth":
            cmd_auth()
        elif cmd == "list":
            cmd_list(int(sys.argv[2]) if len(sys.argv) > 2 else 10)
        elif cmd == "read":
            cmd_read(sys.argv[2])
        elif cmd == "draft":
            if len(sys.argv) < 5:
                print('Использование: draft "<to>" "<subject>" "<body>"')
                return
            cmd_draft(sys.argv[2], sys.argv[3], sys.argv[4])
        else:
            print(f"Неизвестная команда: {cmd}")
            print(__doc__)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
