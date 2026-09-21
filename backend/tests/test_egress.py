"""Проверки разбора списка запасных выходов.

Адреса взяты из failsafe-конфигурации «Весёлого Бегемота»
(`server/singbox-android.json`): те же четыре туннеля, что держит клиент.
"""

from app.services.egress import Hop, parse_hops


def test_four_hops_from_the_setting():
    raw = "185.125.102.131:4431, 72.56.72.54:4431, 158.160.58.163:443, 38.180.191.171:4431"
    hops = parse_hops(raw, "tt.begemot26.ru")
    assert [h.host for h in hops] == ["185.125.102.131", "72.56.72.54",
                                      "158.160.58.163", "38.180.191.171"]
    assert [h.port for h in hops] == [4431, 4431, 443, 4431]
    assert {h.hostname for h in hops} == {"tt.begemot26.ru"}


def test_port_defaults_to_443_when_not_given():
    assert parse_hops("158.160.58.163", "tt.begemot26.ru") == [
        Hop(host="158.160.58.163", port=443, hostname="tt.begemot26.ru")]


def test_empty_setting_gives_no_hops():
    assert parse_hops("", "tt.begemot26.ru") == []
    assert parse_hops("  ,  ", "tt.begemot26.ru") == []
