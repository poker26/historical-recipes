"""Проверка, что моргание туннеля переживается повтором.

21 сентября соединение до PlantNet рвалось с девяти утра до шести вечера, и 151
снимок уехал в архив с отказом, хотя связь возвращалась через секунды.
"""

import httpx
import pytest

from app.services import plant_id


class _Client:
    """Клиент, который падает заданное число раз, а потом отвечает."""

    calls = 0

    def __init__(self, fails: int, answer):
        self.fails = fails
        self.answer = answer

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        type(self).calls += 1
        if type(self).calls <= self.fails:
            raise httpx.ConnectTimeout("туннель молчит")
        return self.answer


class _Answer:
    status_code = 200
    headers: dict = {}

    def json(self):
        return {"results": [{"score": 0.9, "species": {
            "scientificNameWithoutAuthor": "Ficus elastica",
            "scientificName": "Ficus elastica Roxb.",
            "genus": {"scientificNameWithoutAuthor": "Ficus"},
            "family": {"scientificNameWithoutAuthor": "Moraceae"},
            "commonNames": ["Фикус"]}, "gbif": {"id": "1"}}]}


@pytest.mark.asyncio
async def test_transient_break_is_retried(monkeypatch):
    _Client.calls = 0
    monkeypatch.setattr(plant_id.settings, "plantnet_api_key", "ключ", raising=False)
    monkeypatch.setattr(plant_id, "hop_client",
                        lambda *a, **kw: _Client(fails=2, answer=_Answer())())
    monkeypatch.setattr(plant_id.asyncio, "sleep", lambda *_: _noop())

    out = await plant_id.identify(images=[b"photo-bytes"])
    assert out["candidates"][0]["latin"] == "Ficus elastica"
    assert _Client.calls == 3


@pytest.mark.asyncio
async def test_giving_up_after_three_breaks(monkeypatch):
    _Client.calls = 0
    monkeypatch.setattr(plant_id.settings, "plantnet_api_key", "ключ", raising=False)
    monkeypatch.setattr(plant_id, "hop_client",
                        lambda *a, **kw: _Client(fails=99, answer=_Answer())())
    monkeypatch.setattr(plant_id.asyncio, "sleep", lambda *_: _noop())

    out = await plant_id.identify(images=[b"photo-bytes"])
    assert "error" in out
    assert _Client.calls == plant_id.PROXY_ATTEMPTS


async def _noop():
    return None
