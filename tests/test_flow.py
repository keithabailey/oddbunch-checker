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


def _tick(store, html, when, note):
    return flow.run_tick(store, store.load_prefs(), when, fetch=lambda: fresh_menu(html), notifier=note)


def test_tick_quiet_outside_window_and_before_check_time(store, fixture_html):
    note = Recorder()
    assert _tick(store, fixture_html, pt(2026, 11, 18, 9, 0), note) == "window closed"
    assert _tick(store, fixture_html, pt(2026, 11, 16, 14, 10), note) == "too early"
    assert note.calls == []


def test_tick_normal_week_one_message_per_step(store, fixture_html):
    note = Recorder()
    assert _tick(store, fixture_html, pt(2026, 11, 16, 14, 30), note) == "checked"
    assert _tick(store, fixture_html, pt(2026, 11, 16, 15, 0), note) == "waiting"
    assert _tick(store, fixture_html, pt(2026, 11, 16, 19, 30), note) == "reminded (evening)"
    assert _tick(store, fixture_html, pt(2026, 11, 16, 20, 0), note) == "already reminded"
    assert _tick(store, fixture_html, pt(2026, 11, 17, 18, 0), note) == "reminded (last-chance)"
    assert len(note.calls) == 3
    assert note.calls[-1][0].startswith("Last chance") and note.calls[-1][2]


def test_tick_catches_up_when_mac_comes_on_late(store, fixture_html):
    # Mac off all Monday; switched on Tuesday 7pm: one proposal message, no reminder pile-up.
    note = Recorder()
    assert _tick(store, fixture_html, pt(2026, 11, 17, 19, 0), note) == "checked"
    assert _tick(store, fixture_html, pt(2026, 11, 17, 19, 30), note) == "already reminded"
    assert len(note.calls) == 1


def test_tick_menu_not_posted_notice_once(store, fixture_html):
    note = Recorder()
    stale = lambda: parse(fixture_html)
    run = lambda when: flow.run_tick(store, store.load_prefs(), when, fetch=stale, notifier=note)
    assert run(pt(2026, 11, 16, 14, 30)) == "menu not posted"
    assert note.calls == []                       # too early to worry
    assert run(pt(2026, 11, 16, 16, 30)) == "menu not posted"
    assert run(pt(2026, 11, 16, 17, 0)) == "menu not posted"
    assert len(note.calls) == 1 and "not posted" in note.calls[0][0]


def test_tick_stops_after_approve_or_skip(store, fixture_html):
    note = Recorder()
    _tick(store, fixture_html, pt(2026, 11, 16, 14, 30), note)
    pending = store.load_pending()
    pending["status"] = "skipped"
    store.save_pending(pending)
    assert _tick(store, fixture_html, pt(2026, 11, 16, 19, 30), note) == "nothing to do (skipped)"
    assert len(note.calls) == 1
