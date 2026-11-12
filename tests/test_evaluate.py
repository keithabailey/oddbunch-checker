from oddbunch.evaluate import evaluate, normalise, rank_candidates
from oddbunch.menu import parse

REMOVE = ["Gooseberries", "Seafood Mushrooms"]
REPLACE = ["Asian Pears", "Apples", "Satsumas"]


def test_normalise():
    assert normalise("Fresh Gooseberries") == normalise("gooseberries") == "gooseberry"
    assert normalise("Local Potatoes") == "potato"
    assert normalise("Fresh Banana ") == normalise("Bananas")
    assert normalise("Kale Slaw") != normalise("Kale")
    assert normalise("Oyster Mushroom") != normalise("Seafood Mushrooms")
    assert normalise("Mandarins", {"mandarin": "satsuma"}) == "satsuma"


def test_seed_prefs_on_fixture(fixture_html):
    m = parse(fixture_html)
    p = evaluate(m.names, REMOVE, REPLACE)
    assert p.flagged == ["Gooseberries"]
    assert p.free.remove == "Gooseberries"
    assert p.free.candidates == ["Asian Pears", "Apples", "Satsumas"]
    assert p.paid == []


def test_two_dislikes_one_free_one_paid(fixture_html):
    names = parse(fixture_html).names + ["Seafood Mushrooms"]
    p = evaluate(names, REMOVE, REPLACE)
    assert p.free.remove == "Gooseberries"
    assert [s.remove for s in p.paid] == ["Seafood Mushrooms"]
    assert all(s.paid for s in p.paid)


def test_priority_order_follows_remove_list(fixture_html):
    names = parse(fixture_html).names + ["Seafood Mushrooms"]
    p = evaluate(names, ["Seafood Mushrooms", "Gooseberries"], REPLACE)
    assert p.free.remove == "Seafood Mushrooms"


def test_no_match_means_no_action(fixture_html):
    p = evaluate(parse(fixture_html).names, ["Durian"], REPLACE)
    assert p.free is None and p.flagged == []


def test_fallbacks_filtered_by_form_options():
    assert rank_candidates(REPLACE, available=["Satsumas", "Apples", "Kiwi"]) == ["Apples", "Satsumas"]
    assert rank_candidates(REPLACE, available=["Fresh Satsumas"]) == ["Fresh Satsumas"]
    assert rank_candidates(REPLACE, exclude=["Asian Pears"]) == ["Apples", "Satsumas"]
