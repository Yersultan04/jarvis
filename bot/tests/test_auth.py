"""G6 auth-гейты: dashboard._is_authorized + sana_web HTTP-гейт.

Проверяем главную ловушку: cloudflared проксирует на 127.0.0.1, поэтому трафик
из интернета НЕ должен проходить как «локальный» только из-за IP.
"""
import dashboard as d


class _Req:
    def __init__(self, headers=None, ip="127.0.0.1"):
        self.headers = headers or {}
        self.remote_addr = ip
        self.method = "GET"


def test_local_without_cf_allowed():
    assert d._is_authorized(_Req()) is True


def test_via_cf_without_email_denied():
    # пришёл через Cloudflare (Cf-Ray), но Access не проставил почту → отказ,
    # даже если remote_addr == 127.0.0.1 (это адрес cloudflared, не клиента!)
    assert d._is_authorized(_Req({"Cf-Ray": "abc"})) is False


def test_via_cf_wrong_email_denied():
    req = _Req({"Cf-Ray": "abc", "Cf-Access-Authenticated-User-Email": "intruder@evil.com"})
    assert d._is_authorized(req) is False


def test_via_cf_owner_allowed():
    # любая из почт владельца по умолчанию пускается (список через запятую)
    for email in d._owner_emails():
        req = _Req({"Cf-Ray": "abc", "Cf-Access-Authenticated-User-Email": email})
        assert d._is_authorized(req) is True


def test_via_cf_both_default_owners_allowed():
    # обе дефолтные почты (slvaita3 + ersultan040403) проходят гейт
    assert {"slvaita3@gmail.com", "ersultan040403@gmail.com"} <= d._owner_emails()


def test_multi_email_env(monkeypatch):
    # SANA_WEB_EMAIL со списком через запятую → пускает каждую
    monkeypatch.setenv("SANA_WEB_EMAIL", "a@x.com, b@y.com")
    for email in ("a@x.com", "b@y.com"):
        req = _Req({"Cf-Ray": "abc", "Cf-Access-Authenticated-User-Email": email})
        assert d._is_authorized(req) is True
    req = _Req({"Cf-Ray": "abc", "Cf-Access-Authenticated-User-Email": "c@z.com"})
    assert d._is_authorized(req) is False


def test_bearer_token(monkeypatch):
    monkeypatch.setenv("SANA_WEB_TOKEN", "s3cret")
    ok = _Req({"Cf-Ray": "abc", "Authorization": "Bearer s3cret"})
    bad = _Req({"Cf-Ray": "abc", "Authorization": "Bearer nope"})
    assert d._is_authorized(ok) is True
    assert d._is_authorized(bad) is False


def test_owner_email_from_env(monkeypatch):
    # .env переопределяет владельца — читается в момент запроса, не на импорте
    monkeypatch.setenv("SANA_WEB_EMAIL", "boss@askrizz.com")
    req = _Req({"Cf-Ray": "abc", "Cf-Access-Authenticated-User-Email": "boss@askrizz.com"})
    assert d._is_authorized(req) is True


# --- sana_web: проверяем гейт на безопасном маршруте "/" (без вызова claude) ---


def test_sana_web_gate():
    import sana_web

    c = sana_web.app.test_client()
    # локально (test_client шлёт с 127.0.0.1, без CF) → 200
    assert c.get("/").status_code == 200
    # через CF без почты → 403
    assert c.get("/", headers={"Cf-Ray": "abc"}).status_code == 403
    # через CF с почтой владельца → 200
    owner = next(iter(sana_web.OWNER_EMAILS))
    r = c.get("/", headers={"Cf-Ray": "abc", "Cf-Access-Authenticated-User-Email": owner})
    assert r.status_code == 200
