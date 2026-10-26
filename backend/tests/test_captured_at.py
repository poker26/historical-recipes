"""Время съёмки: мусор от старых клиентов не попадает в базу."""

from datetime import datetime, timedelta, timezone

from app.routers.identify import _capture_time, _parse_captured_at


def _near_now(dt):
    return abs(dt - datetime.now(timezone.utc)) < timedelta(seconds=5)


def test_real_times_kept():
    assert _parse_captured_at("2026-10-26T10:00:00Z") == datetime(2026, 10, 26, 10, tzinfo=timezone.utc)
    assert _parse_captured_at("1761472800000").year == 2025
    assert _parse_captured_at(None) is None


def test_ios_uptime_counter_replaced():
    # счётчик iOS: 17,8 суток в миллисекундах, как секунды это правдоподобный 2018 год
    assert _near_now(_capture_time("1540456000", "Apple"))
    assert _near_now(_capture_time("204976000000", "Apple"))  # как секунды это 8465 год
    assert _near_now(_parse_captured_at("204976000000"))
    assert _near_now(_parse_captured_at("1791-12-07T18:27:00Z"))  # кривой EXIF
