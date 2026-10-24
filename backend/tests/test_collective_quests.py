from app.services.collective_quests import care_genus_name, display_name, genus_of, slot_points


def test_points_rise_to_final_bonus():
    pts = [slot_points(k, 15) for k in range(1, 16)]
    assert pts[0] == 1                     # первая позиция дешевле всех
    assert pts[13] == 5                    # предпоследняя
    assert pts[14] == 15                   # финальная: 5 + 10
    assert pts == sorted(pts)              # цена не падает к концу квеста


def test_points_small_quests():
    assert slot_points(1, 2) == 1
    assert slot_points(2, 2) == 15
    assert slot_points(1, 1) == 15


def test_genus_of():
    assert genus_of("Hoya kerrii Craib") == "Hoya"
    assert genus_of("salix alba") == "Salix"
    assert genus_of("× Chitalpa tashkentensis") is None
    assert genus_of("") is None
    assert genus_of(None) is None


def test_display_name_noun_head():
    assert display_name([("Хойя керри", 5), ("Хойя мясистая", 3)]) == "Хойя"
    assert display_name([("Подберёзовик обыкновенный", 4), ("подберёзовик болотный", 1)]) == "Подберёзовик"


def test_display_name_adjective_first_takes_top_species():
    # «Белый» — прилагательное, родом его подписывать нельзя
    assert display_name([("Белый гриб", 7), ("Боровик сетчатый", 1)]) == "Белый гриб"


def test_display_name_skips_latin_only():
    assert display_name([("Crassula ovata", 3)]) is None
    assert display_name([("Crassula ovata", 3), ("Толстянка овальная", 1)]) == "Толстянка"


def test_display_name_nouns_with_adjective_like_endings():
    assert display_name([("Зверобой продырявленный", 4)]) == "Зверобой"
    assert display_name([("Цикорий обыкновенный", 2)]) == "Цикорий"


def test_display_name_drops_herbal_junk():
    assert display_name([("Гераниевое эфирное масло", 5), ("Пеларгония зональная", 2)]) == "Пеларгония"


def test_care_genus_name():
    assert care_genus_name(["АЛОЭ", "Алоэ, столетник", "алоэ приземистое"]) == "Алоэ"
    assert care_genus_name(["Молочаи", "М. Миля, или М. блестящий", "Молочай Миля",
                            "Молочай красивейший, Пуансеттия"]) == "Молочай"
    assert care_genus_name(["Цитрус", "Цитрусы", "ПОНЦИРУС"]) == "Цитрус"
    assert care_genus_name(["П. головчатый", "П. курчавый"]) is None
    assert care_genus_name(["Молочай Миля, Или Молочай блестящий", "ИЛИ пуансеттия"]) == "Молочай"


# ---- «Наш лес» по справочнику средней полосы ----------------------------------------

from datetime import datetime, timezone

from app.services.collective_quests import _month_keys, reference_from_counts, select_nature_pool


def _g(genus, devices=1):
    return {"genus": genus, "name_ru": genus, "plant_id": genus, "photo_url": "x", "devices": devices}


REF = {"Pinus": {"obs": 900, "species": ["Pinus sylvestris"]},
       "Amanita": {"obs": 400, "species": ["Amanita muscaria"]},
       "Betula": {"obs": 700, "species": ["Betula pendula"]},
       "Ulmus": {"obs": 50, "species": ["Ulmus glabra"]}}


def test_reference_from_counts_groups_by_genus():
    ref = reference_from_counts([
        {"count": 10, "taxon": {"name": "Pinus sylvestris", "rank": "species"}},
        {"count": 3, "taxon": {"name": "Pinus sibirica", "rank": "species"}},
        {"count": 5, "taxon": {"name": "Betula pendula", "rank": "species"}},
        {"count": 2, "taxon": {"name": "×", "rank": "hybrid"}},
    ])
    assert ref["Pinus"]["obs"] == 13 and ref["Pinus"]["species"] == ["Pinus sylvestris", "Pinus sibirica"]
    assert set(ref) == {"Pinus", "Betula"}


def test_nature_pool_drops_garden_and_south():
    pool = [_g("Pinus", 3), _g("Nicotiana", 2), _g("Albizzia", 1), _g("Amanita", 2)]
    kept, extra = select_nature_pool(pool, REF, need=2)
    assert [g["genus"] for g in kept] == ["Pinus", "Amanita"]
    assert extra == []


def test_nature_pool_tops_up_from_reference_by_observations():
    kept, extra = select_nature_pool([_g("Amanita")], REF, need=30, skip={"Ulmus"})
    assert [g["genus"] for g in kept] == ["Amanita"]
    assert extra == ["Pinus", "Betula"]          # по убыванию наблюдений, без уже взятых и без skip


def test_nature_pool_without_reference_unchanged():
    pool = [_g("Nicotiana"), _g("Pinus")]
    assert select_nature_pool(pool, None, need=30) == (pool, [])
    assert select_nature_pool(pool, {}, need=30) == (pool, [])


def test_month_keys_cross_year():
    assert _month_keys(datetime(2026, 1, 15, tzinfo=timezone.utc)) == ("nature_ref:2026-01", "nature_ref:2025-12")
    assert _month_keys(datetime(2026, 10, 24, tzinfo=timezone.utc)) == ("nature_ref:2026-10", "nature_ref:2026-09")
