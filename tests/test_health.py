import json

import pytest

from conftest import FIXTURES, SSR_FIXTURE, SUBS_PAGE_SNIPPET, pt
from oddbunch import flow, health
from oddbunch.health import BROKEN, CHANGED, Report

SSR = SSR_FIXTURE.read_text()
MENU = (FIXTURES / "large_box_2026-11-11.html").read_text()


def levels(report):
    return [(f.level, f.area) for f in report.findings]


def form_report(ssr=SSR):
    r = Report()
    plan = health.check_form(r, ssr, "west", "large-box", None)
    return r, plan


def test_saved_site_is_healthy():
    r = Report()
    health.check_menu(r, MENU, "west")
    health.check_page(r, SUBS_PAGE_SNIPPET)
    plan = health.check_form(r, SSR, "west", "large-box", None)
    assert r.ok, r.text()
    assert plan is not None
    assert r.facts["form.pages"] == 2
    assert "Large Box" in r.facts["form.boxes"] and "All Fruit" in r.facts["form.boxes"]
    assert r.facts["form.extra_required"] == []


def test_menu_layout_change_is_broken():
    r = Report()
    health.check_menu(r, MENU.replace("data-p2m-card", "data-new-card"), "west")
    assert levels(r) == [(BROKEN, "menu")]


def test_form_swapped_on_page_is_broken():
    r = Report()
    health.check_page(r, SUBS_PAGE_SNIPPET.replace("1476dcbf_1758738507", "99999999_1"))
    assert levels(r) == [(BROKEN, "page")] and "99999999_1" in r.findings[0].message
    r = Report()
    health.check_page(r, "<html><body><form id='x'></form></body></html>")
    assert levels(r) == [(BROKEN, "page")]


def test_new_deadline_is_broken_and_quoted():
    r, _ = form_report(SSR.replace("11:55pm Eastern Time on Tuesday", "6:00pm Eastern Time on Tuesday"))
    assert (BROKEN, "rules") in levels(r)
    assert "6:00pm" in next(f.message for f in r.findings if f.area == "rules")


def test_renamed_name_field_is_broken():
    r, _ = form_report(SSR.replace('"firstNameLabel":"Full Name"', '"firstNameLabel":"Your Name"'))
    assert (BROKEN, "form") in levels(r)


def test_extra_page_is_broken():
    extra = '{"type":"step","idx":"zzz_1","componentsList":[]}'
    r, _ = form_report(SSR.replace('{"type":"name"', extra + ',{"type":"name"', 1))
    assert any("3 page" in f.message for f in r.at(BROKEN))


def test_new_required_field_is_broken():
    extra = '{"idx":"new_1","type":"text","label":"Order Number","required":true}'
    r, _ = form_report(SSR.replace('{"type":"name"', extra + ',{"type":"name"', 1))
    assert any("Order Number" in f.message for f in r.at(BROKEN))


def test_required_field_for_another_region_is_ignored():
    r0, plan = form_report()
    east = plan.region["options"][1] if plan.region["options"][0] == "BC / AB" else plan.region["options"][0]
    extra = ('{"idx":"new_2","type":"text","label":"East only","required":true,'
             f'"conditionalElement":"{plan.region["idx"]}","conditionalValue":"{east}"}}')
    r, _ = form_report(SSR.replace('{"type":"name"', extra + ',{"type":"name"', 1))
    assert r.ok, r.text()


def test_lost_region_is_broken():
    r, plan = form_report(SSR.replace('"BC / AB"', '"BC"'))
    assert plan is None and (BROKEN, "form") in levels(r)


def test_changes_against_baseline_are_reported_once(store):
    r, _ = form_report()
    health.record(store, r, pt(2026, 11, 16, 14, 30))
    baseline = json.loads(health.baseline_path(store).read_text())
    baseline["form.boxes"] = [b for b in baseline["form.boxes"] if b != "All Fruit"] + ["Old Box"]
    baseline["rules.text"] = baseline["rules.text"].replace("absolutely free", "free of charge")
    health.baseline_path(store).write_text(json.dumps(baseline))

    r2, _ = form_report()
    health.compare(r2, health.load_baseline(store))
    msgs = "\n".join(f.message for f in r2.at(CHANGED))
    assert "added ['All Fruit']" in msgs and "removed ['Old Box']" in msgs
    assert "rules text changed" in msgs and "absolutely free" in msgs
    health.record(store, r2, pt(2026, 11, 16, 14, 31))   # accepted
    r3, _ = form_report()
    health.compare(r3, health.load_baseline(store))
    assert r3.ok


def test_broken_run_keeps_old_baseline(store):
    health.baseline_path(store).write_text(json.dumps({"form.pages": 2}))
    r, _ = form_report(SSR.replace('"firstNameLabel":"Full Name"', '"firstNameLabel":"Your Name"'))
    health.record(store, r, pt(2026, 11, 16, 14, 30))
    assert json.loads(health.baseline_path(store).read_text()) == {"form.pages": 2}


def fake_get(pages):
    def get(url, what):
        for key, html in pages.items():
            if key in url:
                return html
        raise AssertionError(url)
    return get


GOOD = {"products/large-box": MENU, "pages/substitutions": SUBS_PAGE_SNIPPET, "powr.io": SSR}


def test_run_end_to_end_without_browser(store):
    r = health.run(store, store.load_prefs(), browser=False, get=fake_get(GOOD))
    assert r.ok, r.text()


def test_moved_page_is_broken(store):
    pages = dict(GOOD, **{"pages/substitutions": health._MOVED + "404"})
    r = health.run(store, store.load_prefs(), browser=False, get=fake_get(pages))
    assert levels(r) == [(BROKEN, "page")] and "404" in r.findings[0].message


class Note:
    def __init__(self):
        self.calls = []

    def __call__(self, title, message, cfg, urgent=False):
        self.calls.append((title, message, urgent))


ORIGINAL_RUN = health.run
ORIGINAL_WEEKLY = health.weekly


@pytest.fixture
def weekly_with(monkeypatch):
    def make(pages):
        monkeypatch.setattr(health, "run", lambda store, prefs, browser=True:
                            ORIGINAL_RUN(store, prefs, browser=False, get=fake_get(pages)))
        return ORIGINAL_WEEKLY
    return make


def test_weekly_notifies_broken_once_per_week(store, weekly_with):
    weekly = weekly_with(dict(GOOD, **{"powr.io": SSR.replace("11:55pm Eastern", "6:00pm Eastern")}))
    note, prefs, t = Note(), store.load_prefs(), pt(2026, 11, 16, 14, 30)
    out = weekly(store, prefs, t, "2026-11-16", note)
    assert "1 broken" in out
    assert len(note.calls) == 1 and "breaks the tool" in note.calls[0][0] and note.calls[0][2] is True
    assert "already checked" in weekly(store, prefs, t, "2026-11-16", note)
    assert len(note.calls) == 1


def test_weekly_is_silent_when_healthy(store, weekly_with):
    weekly = weekly_with(GOOD)
    note = Note()
    weekly(store, store.load_prefs(), pt(2026, 11, 16, 14, 30), "2026-11-16", note)
    assert note.calls == [] and health.baseline_path(store).exists()


def test_weekly_retries_after_network_failure(store, monkeypatch):
    def down(*a, **k):
        raise health.Unreachable("menu page: timed out")
    monkeypatch.setattr(health, "run", down)
    out = ORIGINAL_WEEKLY(store, store.load_prefs(), pt(2026, 11, 16, 14, 30), "2026-11-16", Note())
    assert "unreachable" in out and "health_week" not in store.load_state()


def test_tick_runs_health_inside_window_only(store, fixture_html, offline_health):
    from oddbunch.menu import parse
    fetch = lambda: parse(fixture_html)
    flow.run_tick(store, store.load_prefs(), pt(2026, 11, 13, 10, 0), fetch=fetch, notifier=Note())
    assert offline_health == []
    flow.run_tick(store, store.load_prefs(), pt(2026, 11, 16, 14, 30), fetch=fetch, notifier=Note(),
                  sleep=lambda s: None, clock=lambda: pt(2026, 11, 17, 23, 0))
    assert len(offline_health) == 1 and offline_health[0][0][3] == "2026-11-16"
