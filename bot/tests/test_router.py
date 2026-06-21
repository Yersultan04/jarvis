"""G2-роутер: вопрос-факт (Groq) vs действие/живые инструменты (Claude)."""
import jarvis_bot as b


def test_action_phrases_route_to_claude():
    for txt in [
        "заведи карточку в трелло",
        "запомни что дедлайн в пятницу",
        "поставь встречу на 16:00",
        "напиши черновик письма",
        "обнови статус задачи",
        "сделай коммит",
    ]:
        assert b.needs_claude(txt) is True, txt


def test_fact_questions_route_to_groq():
    for txt in [
        "что по Haul?",
        "какой дедлайн у Med Triage",
        "на каких аккаунтах опубликован KazBench",
        "напомни счёт матча",  # «напомни» != «запомни» — это вопрос
    ]:
        assert b.needs_claude(txt) is False, txt


def test_case_insensitive():
    assert b.needs_claude("ЗАВЕДИ задачу") is True
