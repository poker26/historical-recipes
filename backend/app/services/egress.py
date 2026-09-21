"""Выход наружу через несколько прокси с переключением на живой.

Зачем модуль. Прямого пути к PlantNet с прода нет: запрос без прокси уходит в
пустоту. Весь выход идёт через один туннель, и 21 сентября он умирал с девяти
утра до шести вечера — из 193 снимков за день 139 получили отказ, а человек
остался без ответа. Один туннель это одна точка отказа.

Туннелей на самом деле четыре, и они собраны в клиенте «Весёлый Бегемот»
(`server/singbox-android.json`): sing-box держит их списком и раз в минуту
переключается на живой. Здесь то же самое, только проще: идём по списку сверху
вниз, первый ответивший и работает.

Главная тонкость — имя. Сертификат у всех четырёх выдан на одно имя
`tt.begemot26.ru`, других имён в нём нет. Подключиться к запасному адресу
напрямую нельзя: прокси смотрит, какое имя запрошено при рукопожатии, и на
голый адрес рвёт связь, не разбираясь (замерено: 000 за четверть секунды на всех
четырёх). Поэтому соединяемся с нужным адресом, а имя в рукопожатии оставляем
прежним — тогда все четыре отвечают.
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass

import httpcore
import httpx


@dataclass(frozen=True)
class Hop:
    """Один выход наружу: куда подключаться и каким именем представляться."""

    host: str            # адрес, куда идёт соединение
    port: int
    hostname: str        # имя для рукопожатия, общее у всех выходов


class _PinnedBackend(httpcore.AnyIOBackend):
    """Соединяется с заданным адресом, но имя для рукопожатия не подменяет."""

    def __init__(self, hostname: str, host: str):
        self.hostname = hostname
        self.host = host

    async def connect_tcp(self, host, port, timeout=None, local_address=None,
                          socket_options=None):
        target = self.host if host == self.hostname else host
        return await super().connect_tcp(target, port, timeout=timeout,
                                         local_address=local_address,
                                         socket_options=socket_options)


def parse_hops(raw: str, hostname: str) -> list[Hop]:
    """Читает список выходов из настройки: ``адрес:порт`` через запятую."""
    hops: list[Hop] = []
    for piece in (raw or "").split(","):
        piece = piece.strip()
        if not piece:
            continue
        host, _, port = piece.partition(":")
        if not host:
            continue
        hops.append(Hop(host=host, port=int(port) if port.isdigit() else 443,
                        hostname=hostname))
    return hops


def hop_client(proxy_url: str, hop: Hop | None, timeout: float = 60) -> httpx.AsyncClient:
    """Клиент, который ходит наружу через указанный выход.

    Без выхода это обычный клиент с прокси из настроек — тот же путь, что был
    до появления запасных адресов.
    """
    if hop is None:
        return httpx.AsyncClient(timeout=timeout, proxy=proxy_url or None)

    proxy = httpx.Proxy(proxy_url)
    address = proxy.url.copy_with(username=None, password=None,
                                  host=hop.hostname, port=hop.port)
    pool = httpcore.AsyncHTTPProxy(
        proxy_url=str(address),
        proxy_auth=proxy.raw_auth,
        ssl_context=ssl.create_default_context(),
        network_backend=_PinnedBackend(hop.hostname, hop.host),
    )
    transport = httpx.AsyncHTTPTransport()
    transport._pool = pool
    return httpx.AsyncClient(timeout=timeout, transport=transport)
