"""Проверки чистильщика латыни на именах, которые люди видели в карточках.

Все строки ниже взяты из базы: это те самые карточки, которые за две недели
показались пользователям вместе с фамилией ботаника в имени.
"""

from app.services.card_latin import candidate_latin, has_author_tail


def test_short_author_after_species():
    assert candidate_latin("Ficus elastica Roxb.") == "Ficus elastica"
    assert candidate_latin("Symphytum caucasicum M. Bieb.") == "Symphytum caucasicum"
    assert candidate_latin("Lens esculenta Moench") == "Lens esculenta"


def test_linnaeus_after_species():
    assert candidate_latin("Artemisia vulgaris L.") == "Artemisia vulgaris"
    assert candidate_latin("Malus baccata L.") == "Malus baccata"


def test_basionym_author_in_parentheses():
    assert candidate_latin("Lactarius resimus (Fr.) Fr.") == "Lactarius resimus"
    assert candidate_latin("Citrus maxima (Burm.) Merr.") == "Citrus maxima"
    assert candidate_latin("Citrus limon (L.) Burm. f.") == "Citrus limon"


def test_long_author_list():
    long_name = "Leucocybe connata (Schumach.) Vizzini, P.Alvarado, G.Moreno & Consiglio"
    assert candidate_latin(long_name) == "Leucocybe connata"


def test_species_written_with_a_capital_letter():
    """Старая орфография книги: «Acer Negundo L.» это клён, а не род Acer."""
    assert candidate_latin("Acer Negundo L.") == "Acer negundo"


def test_whole_name_in_capitals():
    assert candidate_latin("CAPSICUM ANNUUM L.") == "Capsicum annuum"


def test_cultivar_and_rank_words_are_dropped():
    assert candidate_latin("Ctenanthe oppenheimiana 'variegata'") == "Ctenanthe oppenheimiana"
    assert candidate_latin("Ceropegia linearis ssp. woodii") == "Ceropegia linearis"


def test_author_right_after_genus_leaves_the_genus():
    assert candidate_latin("Dianella Lam.") == "Dianella"
    assert candidate_latin("Hoya R. Br.") == "Hoya"


def test_clean_names_are_left_alone():
    assert not has_author_tail("Ficus elastica")
    assert not has_author_tail("Monstera")
    assert has_author_tail("Ficus elastica Roxb.")
    assert has_author_tail("Lactarius resimus (Fr.) Fr.")
