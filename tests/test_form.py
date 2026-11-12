from conftest import form_components
from oddbunch.form import FormError, item_name, plan_for, subscription_for

import pytest


def plan():
    return plan_for(form_components(), "west", "large-box")


def test_chain_resolves_by_condition_links():
    p = plan()
    assert p.region_value == "BC / AB" and p.subscription == "Large Box"
    assert p.subscription_field["label"] == "Your Subscription (BC / AB)"
    assert p.remove_field["label"] == "Item to Remove (Large Box - BC / AB)"
    assert p.box_field["label"] == "Box to choose from? (BC / AB)"
    assert "All Fruit" in p.add_fields and "" not in p.remove_options


def test_item_names_and_subscription():
    assert item_name("Fresh Gooseberries - 1 clamshell") == "Fresh Gooseberries"
    assert item_name("Local Potatoes - 1-2lb") == "Local Potatoes"
    assert subscription_for("large-box") == "Large Box"
    assert subscription_for("small-box-plus") == "Small Box Plus"


def test_remove_option_matches_menu_names():
    assert plan().remove_option("Gooseberries") == "Fresh Gooseberries - 1 clamshell"
    assert plan().remove_option("Durian") is None


def test_ranked_replacement_searches_every_box_own_first():
    p = plan()
    hit = p.find_replacement(["Asian Pears", "Apples", "Satsumas"])
    assert (hit.box, hit.option) == ("All Fruit", "Asian Pears - 2pieces")   # not in the Large Box list
    hit = p.find_replacement(["Satsumas", "Apples"])
    assert (hit.box, hit.name) == ("Large Box", "Fresh Apples")             # own box wins
    assert p.find_replacement(["Satsumas"]) is None
    assert p.find_replacement(["Nothing"]) is None


def test_only_boxes_and_exclude():
    p = plan()
    assert p.find_replacement(["Apples"], only_boxes=["All Fruit"]).option == "Fresh Apples - 6pieces"
    assert p.find_replacement(["Asian Pears", "Apples"], exclude=["Asian Pears"]).name == "Fresh Apples"


def test_unknown_subscription_is_a_clear_error():
    with pytest.raises(FormError, match="not offered"):
        plan_for(form_components(), "west", "jumbo-box")
