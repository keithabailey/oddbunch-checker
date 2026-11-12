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
    assert "Gooseberries -> Asian Pears (from the All Fruit list)" in msg
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


def _tick(store, html, when, note, **kw):
    kw.setdefault("sleep", lambda s: None)
    return flow.run_tick(store, store.load_prefs(), when, fetch=lambda: fresh_menu(html), notifier=note, **kw)


def test_tick_quiet_outside_window_and_before_check_time(store, fixture_html):
    note, fetches = Recorder(), []

    def fetch():
        fetches.append(1)
        return fresh_menu(fixture_html)

    for when in (pt(2026, 11, 18, 9, 0), pt(2026, 11, 21, 10, 0), pt(2026, 11, 16, 14, 10)):
        flow.run_tick(store, store.load_prefs(), when, fetch=fetch, notifier=note)
    assert fetches == [] and note.calls == []  # no network outside the window


def test_tick_normal_week_one_message_per_step(store, fixture_html):
    note = Recorder()
    assert _tick(store, fixture_html, pt(2026, 11, 16, 14, 30), note) == "checked"
    assert _tick(store, fixture_html, pt(2026, 11, 16, 15, 0), note).startswith("done for now")
    assert _tick(store, fixture_html, pt(2026, 11, 16, 19, 30), note) == "reminded (evening)"
    assert _tick(store, fixture_html, pt(2026, 11, 16, 20, 0), note) == "already reminded"
    assert _tick(store, fixture_html, pt(2026, 11, 17, 15, 0), note) == "reminded (last-chance)"
    assert len(note.calls) == 3
    assert note.calls[-1][0].startswith("Last chance") and note.calls[-1][2]


def test_tick_after_success_does_not_fetch_again(store, fixture_html):
    note, fetches = Recorder(), []

    def fetch():
        fetches.append(1)
        return fresh_menu(fixture_html)

    flow.run_tick(store, store.load_prefs(), pt(2026, 11, 16, 14, 30), fetch=fetch, notifier=note)
    flow.run_tick(store, store.load_prefs(), pt(2026, 11, 16, 19, 30), fetch=fetch, notifier=note)
    flow.run_tick(store, store.load_prefs(), pt(2026, 11, 17, 9, 0), fetch=fetch, notifier=note)
    assert len(fetches) == 1


def test_tick_catches_up_when_mac_comes_on_late(store, fixture_html):
    # Mac off all Monday; switched on Tuesday 7pm: one proposal message, no reminder pile-up.
    note = Recorder()
    assert _tick(store, fixture_html, pt(2026, 11, 17, 19, 0), note) == "checked"
    assert _tick(store, fixture_html, pt(2026, 11, 17, 19, 30), note) == "already reminded"
    assert len(note.calls) == 1


def test_tick_retries_in_process_until_menu_posted(store, fixture_html):
    note, sleeps = Recorder(), []
    answers = iter([parse(fixture_html)] * 8 + [fresh_menu(fixture_html)])
    out = flow.run_tick(store, store.load_prefs(), pt(2026, 11, 16, 14, 30),
                        fetch=lambda: next(answers), notifier=note, sleep=sleeps.append)
    assert out == "checked"
    assert sleeps == [1200] * 8                 # 14:30 .. 17:10, posted on the 9th try
    titles = [c[0] for c in note.calls]
    assert titles.count("Odd Bunch: menu not posted yet") == 1   # one notice, from 16:30
    assert titles[-1].startswith("Odd Bunch NOV 18")


def test_tick_retries_network_errors(store, fixture_html):
    import requests

    note, calls = Recorder(), []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise requests.ConnectionError("offline")
        return fresh_menu(fixture_html)

    out = flow.run_tick(store, store.load_prefs(), pt(2026, 11, 16, 14, 30),
                        fetch=flaky, notifier=note, sleep=lambda s: None)
    assert out == "checked" and len(calls) == 3


def test_tick_gives_up_when_window_closes(store, fixture_html):
    note, sleeps = Recorder(), []
    out = flow.run_tick(store, store.load_prefs(), pt(2026, 11, 17, 21, 0),
                        fetch=lambda: parse(fixture_html), notifier=note, sleep=sleeps.append)
    assert "giving up" in out
    assert len(sleeps) == 2                     # 21:00, 21:20, 21:40; close is 21:55


def test_tick_stops_after_approve_or_skip(store, fixture_html):
    note = Recorder()
    _tick(store, fixture_html, pt(2026, 11, 16, 14, 30), note)
    pending = store.load_pending()
    pending["status"] = "skipped"
    store.save_pending(pending)
    assert _tick(store, fixture_html, pt(2026, 11, 16, 19, 30), note) == "done for this week (skipped)"
    assert len(note.calls) == 1
