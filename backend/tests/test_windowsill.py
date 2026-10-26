"""«Мой подоконник»: советы книг по сезону и расписание пушей."""

from datetime import date, datetime, timedelta, timezone

from app.jobs.windowsill_reminders import due
from app.services.windowsill import advice_now, season_of

MONO = {"care": [
    {"field": "light", "title": "Свет", "voices": [
        {"text": "Держат на светлом месте.", "source": "Книга А", "page": 10, "season": None}]},
    {"field": "water", "title": "Полив", "voices": [
        {"text": "Поливают обильно (летом).", "source": "Книга А", "page": 11, "season": "summer"},
        {"text": "Поливают умеренно (зимой).", "source": "Книга Б", "page": 5, "season": "winter"}]},
    {"field": "repotting", "title": "Пересадка", "voices": [
        {"text": "Пересаживают весной.", "source": "Книга Б", "page": 7, "season": None}]},
]}


def test_seasons():
    assert season_of(date(2026, 11, 3)) == "winter"
    assert season_of(date(2026, 3, 10)) == "spring"
    assert season_of(date(2026, 7, 1)) == "summer"
    assert season_of(date(2026, 10, 26)) == "autumn"


def test_winter_shows_winter_water_not_summer():
    lines = advice_now(MONO, "winter")
    assert [l["field"] for l in lines] == ["water", "light"]
    assert lines[0]["text"] == "Поливают умеренно (зимой)." and lines[0]["source"] == "Книга Б"


def test_spring_puts_repotting_first_and_autumn_previews_winter():
    assert advice_now(MONO, "spring")[0]["field"] == "repotting"
    assert advice_now(MONO, "autumn")[0]["text"] == "Поливают умеренно (зимой)."


def test_no_care_no_advice():
    assert advice_now({}, "winter") == [] and advice_now(None, "summer") == []


def test_push_schedule():
    now = datetime(2026, 11, 2, 9, tzinfo=timezone.utc)
    assert due("winter", "autumn", now - timedelta(days=3), now) == "season"
    assert due("winter", "winter", now - timedelta(days=3), now) is None
    assert due("winter", "winter", now - timedelta(days=14), now) == "check"
    assert due("winter", None, None, now) == "season"
