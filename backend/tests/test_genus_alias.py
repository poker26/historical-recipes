from app.services.genus_alias import atlas_alias


def test_renamed_genus_maps_to_atlas_genus():
    assert atlas_alias("Goeppertia insignis") == "Calathea insignis"
    assert atlas_alias("Symphyotrichum novi-belgii") == "Aster novi-belgii"
    assert atlas_alias("Saintpaulia ionantha") == "Streptocarpus ionantha"


def test_unrenamed_or_empty_gives_none():
    assert atlas_alias("Calathea ornata") is None
    assert atlas_alias("Curio rowleyanus") is None   # под Senecio у нас крестовник
    assert atlas_alias("") is None
    assert atlas_alias(None) is None
