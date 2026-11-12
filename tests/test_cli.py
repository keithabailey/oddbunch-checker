import dataclasses
import datetime as dt

from click.testing import CliRunner

from conftest import pt
from oddbunch import cli, flow, submit
from oddbunch.menu import parse


def _seed_pending(store, html):
    m = dataclasses.replace(parse(html), week_from=dt.date(2026, 11, 18), week_to=dt.date(2026, 11, 21))
    flow.run_check(store, store.load_prefs(), pt(2026, 11, 16, 14, 30),
                   fetch=lambda: m, notifier=lambda *a, **k: None)


def _fake_run(calls):
    def run(fill, profile_dir, shots_dir, submit=False, timeout_s=45):
        calls.append(fill)
        return {"replace": fill.candidates[0] if fill.candidates else None, "result": "prefilled",
                "screenshot": None, "missing": []}
    return run


def test_approve_outside_window_refuses(store, fixture_html, monkeypatch):
    _seed_pending(store, fixture_html)
    monkeypatch.setattr(cli, "now", lambda: pt(2026, 11, 17, 22, 0))
    res = CliRunner().invoke(cli.cli, ["approve"])
    assert res.exit_code != 0 and "window is closed" in res.output


def test_approve_uses_ranked_fallbacks_and_email_arg(store, fixture_html, monkeypatch):
    _seed_pending(store, fixture_html)
    calls = []
    monkeypatch.setattr(cli, "now", lambda: pt(2026, 11, 16, 16, 0))
    monkeypatch.setattr(submit, "run", _fake_run(calls))
    res = CliRunner().invoke(cli.cli, ["approve", "--email", "arg@example.com", "--field", "Order number=123"])
    assert res.exit_code == 0, res.output
    fill = calls[0]
    assert fill.email == "arg@example.com"
    assert fill.remove == "Gooseberries"
    assert fill.candidates == ["Asian Pears", "Apples", "Satsumas"]
    assert fill.extra == {"Order number": "123"}
    assert store.load_pending()["status"] == "prefilled"


def test_second_swap_needs_allow_paid(store, fixture_html, monkeypatch):
    _seed_pending(store, fixture_html)
    monkeypatch.setattr(cli, "now", lambda: pt(2026, 11, 16, 16, 0))
    monkeypatch.setattr(submit, "run", _fake_run([]))
    r = CliRunner()
    assert r.invoke(cli.cli, ["approve"]).exit_code == 0
    res = r.invoke(cli.cli, ["approve", "--swap", "Kale=Apples"])
    assert res.exit_code != 0 and "--allow-paid" in res.output


def test_swap_override_learns_on_yes(store, fixture_html, monkeypatch):
    _seed_pending(store, fixture_html)
    calls = []
    monkeypatch.setattr(cli, "now", lambda: pt(2026, 11, 16, 16, 0))
    monkeypatch.setattr(submit, "run", _fake_run(calls))
    res = CliRunner().invoke(cli.cli, ["approve", "--swap", "Gooseberries=Kiwi"], input="y\n")
    assert res.exit_code == 0, res.output
    assert calls[0].candidates == ["Kiwi"]
    prefs = store.load_prefs()
    assert prefs.replace_with == ["Asian Pears", "Apples", "Satsumas", "Kiwi"]
    assert "# ranked fallbacks" in store.prefs_path.read_text()  # comments survive


def test_swap_item_must_be_in_box(store, fixture_html, monkeypatch):
    _seed_pending(store, fixture_html)
    monkeypatch.setattr(cli, "now", lambda: pt(2026, 11, 16, 16, 0))
    res = CliRunner().invoke(cli.cli, ["approve", "--swap", "Durian=Kiwi"])
    assert res.exit_code != 0 and "not in this week's box" in res.output


def test_like_and_dislike(store):
    r = CliRunner()
    r.invoke(cli.cli, ["like", "Pears", "--top"])
    r.invoke(cli.cli, ["dislike", "Fresh Gooseberries"])  # already present under another spelling
    prefs = store.load_prefs()
    assert prefs.replace_with[0] == "Pears"
    assert prefs.remove == ["Gooseberries", "Seafood Mushrooms"]
