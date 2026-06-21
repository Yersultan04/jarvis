"""trello_api: фильтр карт по метке, перемещение, комментарий. Моки на requests."""
import trello_api
from trello_api import TrelloClient


class _Resp:
    def __init__(self, payload=None):
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


def test_cards_with_label_filters(monkeypatch):
    cards_json = [
        {"id": "1", "name": "взять", "desc": "d1", "idLabels": ["auto", "p1"], "url": "u1"},
        {"id": "2", "name": "пропустить", "desc": "d2", "idLabels": ["p2"], "url": "u2"},
        {"id": "3", "name": "тоже взять", "desc": "", "idLabels": ["auto"], "url": "u3"},
    ]
    monkeypatch.setattr(trello_api.requests, "get", lambda *a, **k: _Resp(cards_json))
    cards = TrelloClient("key", "tok").cards_with_label("listX", "auto")
    assert [c.id for c in cards] == ["1", "3"]
    assert cards[0].name == "взять"
    assert cards[0].desc == "d1"


def test_cards_with_label_none(monkeypatch):
    monkeypatch.setattr(trello_api.requests, "get", lambda *a, **k: _Resp([]))
    assert TrelloClient("k", "t").cards_with_label("l", "auto") == []


def test_move_card_sends_idlist(monkeypatch):
    seen = {}

    def fake_put(url, params=None, **k):
        seen["url"] = url
        seen["params"] = params
        return _Resp()

    monkeypatch.setattr(trello_api.requests, "put", fake_put)
    TrelloClient("key", "tok").move_card("card9", "listDone")
    assert "card9" in seen["url"]
    assert seen["params"]["idList"] == "listDone"
    assert seen["params"]["key"] == "key" and seen["params"]["token"] == "tok"


def test_add_comment_truncates(monkeypatch):
    seen = {}
    monkeypatch.setattr(trello_api.requests, "post",
                        lambda url, params=None, **k: seen.update(params=params) or _Resp())
    TrelloClient("k", "t").add_comment("c1", "x" * 20000)
    assert len(seen["params"]["text"]) == 16000
