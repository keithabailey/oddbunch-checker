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
    def run(fill, profile_dir, shots_dir, submit=False, **kw):
        calls.append(fill)
        return {"result": "prefilled", "screenshot": None, "error": None}
    return run


def test_approve_outside_window_refuses(store, fixture_html, monkeypatch):
    _seed_pending(store, fixture_html)
    monkeypatch.setattr(cli, "now", lambda: pt(2026, 11, 17, 22, 0))
    res = CliRunner().invoke(cli.cli, ["approve"])
    assert res.exit_code != 0 and "Outside the substitution window" in res.output


def test_approve_uses_ranked_fallbacks_and_email_arg(store, fixture_html, monkeypatch):
    _seed_pending(store, fixture_html)
    calls = []
    monkeypatch.setattr(cli, "now", lambda: pt(2026, 11, 16, 16, 0))
    monkeypatch.setattr(submit, "run", _fake_run(calls))
    res = CliRunner().invoke(cli.cli, ["approve", "--email", "arg@example.com", "--name", "Arg Name"])
    assert res.exit_code == 0, res.output
    fill = calls[0]
    assert (fill.name, fill.email) == ("Arg Name", "arg@example.com")
    assert (fill.region, fill.subscription) == ("BC / AB", "Large Box")
    assert fill.remove_option == "Fresh Gooseberries - 1 clamshell"
    assert (fill.box, fill.add_option) == ("All Fruit", "Asian Pears - 2pieces")
    assert fill.add_label == "Item to Add (All Fruit Box - BC / AB)"
    pending = store.load_pending()
    assert pending["status"] == "prefilled"
    assert pending["submissions"][0]["replace"] == "Asian Pears"


def test_check_proposal_names_the_form_choice(store, fixture_html):
    _seed_pending(store, fixture_html)
    form = store.load_pending()["form"]["free"]
    assert (form["name"], form["box"]) == ("Asian Pears", "All Fruit")


def test_name_and_email_can_come_from_env(store, fixture_html, monkeypatch):
    _seed_pending(store, fixture_html)
    prefs = store.prefs_path.read_text().replace("name: Test Person\n", "").replace("email: someone@example.com\n", "")
    store.prefs_path.write_text(prefs)
    calls = []
    monkeypatch.setattr(cli, "now", lambda: pt(2026, 11, 16, 16, 0))
    monkeypatch.setattr(submit, "run", _fake_run(calls))
    assert CliRunner().invoke(cli.cli, ["approve"]).exit_code != 0
    res = CliRunner().invoke(cli.cli, ["approve"], env={"ODDBUNCH_NAME": "Env Name", "ODDBUNCH_EMAIL": "env@example.com"})
    assert res.exit_code == 0, res.output
    assert (calls[0].name, calls[0].email) == ("Env Name", "env@example.com")


def test_unavailable_replacement_lists_what_is_offered(store, fixture_html, monkeypatch):
    _seed_pending(store, fixture_html)
    monkeypatch.setattr(cli, "now", lambda: pt(2026, 11, 16, 16, 0))
    res = CliRunner().invoke(cli.cli, ["approve", "--swap", "Gooseberries=Satsumas", "--yes"])
    assert res.exit_code != 0 and "Your box list offers" in res.output


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
    res = CliRunner().invoke(cli.cli, ["approve", "--swap", "Gooseberries=Limes"], input="y\n")
    assert res.exit_code == 0, res.output
    assert calls[0].add_option == "Fresh Limes - 3pieces"
    prefs = store.load_prefs()
    assert prefs.replace_with == ["Asian Pears", "Apples", "Satsumas", "Limes"]
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
