"""Текстовые утилиты: strip-tags, markdown→TG-HTML, дружелюбные ошибки."""
import jarvis_bot as b


def test_strip_tags_unescapes():
    assert b._strip_tags("<b>привет</b> &amp; пока") == "привет & пока"


def test_md_to_html_bold_and_code():
    out = b.md_to_tg_html("**жирно** и `код`")
    assert "<b>жирно</b>" in out
    assert "<code>код</code>" in out


def test_md_to_html_escapes_angle_brackets():
    # пользовательские <> не должны стать тегами
    out = b.md_to_tg_html("a < b > c")
    assert "&lt;" in out and "&gt;" in out


def test_md_to_html_headers_and_bullets():
    out = b.md_to_tg_html("## Заголовок\n- пункт")
    assert "<b>Заголовок</b>" in out
    assert "• пункт" in out


def test_friendly_error_timeout():
    assert "⏱" in b.friendly_error(Exception("claude timed out"))


def test_friendly_error_connection():
    assert "🔌" in b.friendly_error(Exception("Max retries exceeded: connection refused"))


def test_friendly_error_rc():
    assert "Внутренний сбой" in b.friendly_error(Exception("claude rc!=0"))


def test_friendly_error_generic_is_escaped():
    out = b.friendly_error(Exception("<script>"))
    assert "&lt;script&gt;" in out
