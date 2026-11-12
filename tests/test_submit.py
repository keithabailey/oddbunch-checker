"""Drives the real filler against a local mock of the POWR form in headless Chromium."""

from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from conftest import form_components  # noqa: E402
from oddbunch import submit  # noqa: E402
from oddbunch.form import plan_for  # noqa: E402
from powr_mock import build  # noqa: E402


@pytest.fixture
def mock_url(tmp_path) -> str:
    page = tmp_path / "subs.html"
    page.write_text(build(form_components()))
    return page.as_uri()


def _fill(**over):
    p = plan_for(form_components(), "west", "large-box")
    choice = p.find_replacement(["Asian Pears"])
    base = dict(name="Test Person", email="someone@example.com",
                region_label=p.region["label"], region=p.region_value,
                subscription_label=p.subscription_field["label"], subscription=p.subscription,
                remove_label=p.remove_field["label"], remove_option=p.remove_option("Gooseberries"),
                box_label=p.box_field["label"], box=choice.box,
                add_label=choice.label, add_option=choice.option)
    base.update(over)
    return submit.FormFill(**base)


def _run(tmp_path: Path, url: str, fill, **kw):
    return submit.run(fill, tmp_path / "profile", tmp_path / "shots", url=url, headless=True,
                      wait=False, timeout_s=10, **kw)


def test_prefill_stops_before_submit(tmp_path, mock_url):
    res = _run(tmp_path, mock_url, _fill())
    assert res == {"result": "prefilled", "screenshot": None, "error": None}


def test_submit_mode_completes_both_pages(tmp_path, mock_url):
    res = _run(tmp_path, mock_url, _fill(), submit=True)
    assert res["result"] == "submitted"
    assert Path(res["screenshot"]).exists()


def test_bad_option_hands_over_and_never_submits(tmp_path, mock_url):
    res = _run(tmp_path, mock_url, _fill(add_option="Durian - 1 whole"), submit=True)
    assert res["result"] == "incomplete, handed over"
    assert "Item to Add" in res["error"]


def test_powr_confirmation_response():
    url = "https://responsesapi.powr.io/api/form/responses"
    assert submit.is_confirmation("POST", url, 200, '{"success":true}')
    assert submit.is_confirmation("POST", url, 200, '{"success": true}')
    assert not submit.is_confirmation("POST", url, 200, '{"success":false}')
    assert not submit.is_confirmation("POST", url, 422, '{"success":true}')
    assert not submit.is_confirmation("GET", url, 200, '{"success":true}')
    assert not submit.is_confirmation("POST", "https://analytics-api.powr.io/register", 200, '{"success":true}')
