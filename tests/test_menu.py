import datetime as dt

import pytest

from oddbunch.menu import MenuError, is_current, parse


def test_parses_bc_list_from_raw_html(fixture_html):
    m = parse(fixture_html, "west")
    assert len(m.items) == 20
    assert "Gooseberries" in m.names and "Asian Pears" in m.names
    goose = next(i for i in m.items if i.name == "Gooseberries")
    assert goose.qty == "1 clamshell" and goose.origin == "Colombia"
    assert m.week_label == "NOV 11 - NOV 14"
    assert m.week_from == dt.date(2026, 11, 11) and m.week_to == dt.date(2026, 11, 14)


def test_regions_differ(fixture_html):
    east = parse(fixture_html, "east")
    assert "Seafood Mushrooms" in east.names
    assert "Seafood Mushrooms" not in parse(fixture_html, "west").names


def test_unknown_region_and_broken_page(fixture_html):
    with pytest.raises(MenuError):
        parse(fixture_html, "north")
    with pytest.raises(MenuError):
        parse("<html><body>nothing here</body></html>")


def test_freshness(fixture_html):
    m = parse(fixture_html)
    assert is_current(m, dt.date(2026, 11, 9))       # Monday before: this is the new menu
    assert not is_current(m, dt.date(2026, 11, 16))  # following Monday: still last week's
