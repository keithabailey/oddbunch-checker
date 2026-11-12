import dataclasses
import datetime as dt

from conftest import pt
from oddbunch import flow
from oddbunch.menu import parse


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, title, message, cfg, urgent=False):
        self.calls.append((title, message, urgent))


def fresh_menu(html):
    m = parse(html)
    return dataclasses.replace(m, week_from=dt.date(2026, 11, 18), week_to=dt.date(2026, 11, 21),
                               week_label="NOV 18 - NOV 21")


def test_stale_menu_retries_then_notifies(store, fixture_html):
    sleeps, note = [], Recorder()
    out = flow.run_check(store, store.load_prefs(), pt(2026, 11, 16, 14, 30),
                         fetch=lambda: parse(fixture_html), sleep=sleeps.append, notifier=note)
    assert out is None
    assert sleeps == [1200] * 4
    assert len(note.calls) == 1 and "not posted" in note.calls[0][0]


def test_fresh_menu_proposes_and_is_idempotent(store, fixture_html):
    note = Recorder()
    kw = dict(fetch=lambda: fresh_menu(fixture_html), sleep=lambda s: None, notifier=note)
    pending = flow.run_check(store, store.load_prefs(), pt(2026, 11, 16, 14, 30), **kw)
    assert pending["status"] == "pending"
    assert pending["proposal"]["free"]["remove"] == "Gooseberries"
    assert len(note.calls) == 1
    title, msg, _ = note.calls[0]
    assert "NOV 18 - NOV 21" in title
    assert "Gooseberries -> Asian Pears (then Apples, Satsumas)" in msg
    assert "Tue 17 Nov 9:55pm" in msg
    assert (store.history_dir / "2026-11-18_west_large-box.json").exists()
    assert store.read_log()[0]["proposed"] == "Gooseberries -> Asian Pears"

    again = flow.run_check(store, store.load_prefs(), pt(2026, 11, 16, 15, 0), **kw)
    assert again["created_at"] == pending["created_at"]
    assert len(note.calls) == 1  # no duplicate notification


def test_missed_window_is_logged(store, fixture_html):
    note = Recorder()
    out = flow.run_check(store, store.load_prefs(), pt(2026, 11, 18, 9, 0),
                         fetch=lambda: fresh_menu(fixture_html), notifier=note)
    assert out is None and note.calls == []
    assert store.read_log()[0]["result"] == "window missed"


def test_remind_tuesday_is_last_chance(store, fixture_html):
    note = Recorder()
    kw = dict(fetch=lambda: fresh_menu(fixture_html), sleep=lambda s: None, notifier=note)
    flow.run_check(store, store.load_prefs(), pt(2026, 11, 16, 14, 30), **kw)
    assert flow.run_remind(store, store.load_prefs(), pt(2026, 11, 17, 18, 0), **kw) == "reminded"
    title, _, urgent = note.calls[-1]
    assert title.startswith("Last chance") and urgent


def test_remind_runs_check_when_it_never_ran(store, fixture_html):
    note = Recorder()
    kw = dict(fetch=lambda: fresh_menu(fixture_html), sleep=lambda s: None, notifier=note)
    assert flow.run_remind(store, store.load_prefs(), pt(2026, 11, 16, 19, 30), **kw) == "ran check"
    assert store.load_pending()["status"] == "pending"
