"""Site health check: does the Odd Bunch site still look the way this tool expects?

Read with plain GETs (nothing filled in, nothing sent):
  1. the menu page (/products/<box>), which the Monday check parses
  2. the substitutions page, which must still embed the same POWR form
  3. the POWR form config, which supplies every field, option and the rules text
Optionally (on by default for the weekly run):
  4. the form opened headless and filled with dummy details, stopping before Submit,
     to prove the browser path still works

Findings have three levels:
  broken   the check or the form fill will fail, or give a wrong answer (e.g. a new deadline)
  changed  something moved but the tool should still work; reported once, then accepted
  warning  the check itself could not run fully (e.g. the browser would not start)

"Changed" is judged against ~/.oddbunch/baseline.json: structural facts only (field
labels, region/box lists, page layout, the rules text), never the weekly items.
"""

from __future__ import annotations

import datetime as dt
import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from . import form as form_mod
from . import menu as menu_mod

SUBS_PAGE_URL = "https://www.oddbunch.ca/pages/substitutions"

BROKEN, CHANGED, WARNING = "broken", "changed", "warning"

# The rules the tool's window and paid-swap logic are built on (window.py, cli.approve).
EXPECTED_WINDOW = re.compile(
    r"between\s+2:00\s*pm\s+local time.*?on Monday to 11:55\s*pm Eastern Time on Tuesday", re.I | re.S)
FREE_RULE = re.compile(r"one substitution\b[^.]*\bfree", re.I)
PAID_PRICE = "$0.99"
EXPECTED_PAGES = 2
DUMMY_NAME, DUMMY_EMAIL = "Health Check", "health-check@example.com"


class Unreachable(Exception):
    """A transient network problem: not evidence that the site changed."""


@dataclass
class Finding:
    level: str
    area: str
    message: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    browser: str = "not run"

    def add(self, level: str, area: str, message: str) -> None:
        self.findings.append(Finding(level, area, message))

    def at(self, level: str) -> list[Finding]:
        return [f for f in self.findings if f.level == level]

    @property
    def ok(self) -> bool:
        return not self.findings

    def headline(self) -> str:
        if self.at(BROKEN):
            return "Odd Bunch: a site change breaks the tool"
        if self.at(CHANGED):
            return "Odd Bunch: the site changed (tool should still work)"
        if self.at(WARNING):
            return "Odd Bunch: health check could not run fully"
        return "Odd Bunch: site matches what the tool expects"

    def text(self) -> str:
        lines = [self.headline()]
        for level in (BROKEN, CHANGED, WARNING):
            for f in self.at(level):
                lines.append(f"[{level}] {f.area}: {f.message}")
        lines.append(f"Browser fill: {self.browser}")
        return "\n".join(lines)


# --- fetching -----------------------------------------------------------------

def _get(url: str, what: str, timeout: int = 30) -> str:
    """GET a page. 4xx means the page moved (a site change); anything else is transient."""
    try:
        resp = requests.get(url, headers={"User-Agent": menu_mod.USER_AGENT}, timeout=timeout)
    except requests.RequestException as exc:
        raise Unreachable(f"{what}: {exc}") from exc
    if 400 <= resp.status_code < 500:
        return _MOVED + str(resp.status_code)
    if resp.status_code >= 500:
        raise Unreachable(f"{what}: HTTP {resp.status_code}")
    return resp.text


_MOVED = "\x00moved:"


def _moved(report: Report, area: str, html: str, url: str) -> bool:
    if html.startswith(_MOVED):
        report.add(BROKEN, area, f"{url} now returns HTTP {html[len(_MOVED):]}; the page has moved or gone")
        return True
    return False


# --- individual checks (pure: html in, findings and facts out) -----------------

def check_menu(report: Report, html: str, region: str) -> None:
    soup = BeautifulSoup(html, "html.parser")
    report.facts["menu.regions"] = sorted(
        el.get("data-region-list") for el in soup.select(".p2m__list[data-region-list]"))
    report.facts["menu.week_label"] = soup.select_one("[data-p2m-week]") is not None
    try:
        m = menu_mod.parse(html, region)
    except menu_mod.MenuError as exc:
        report.add(BROKEN, "menu", f"the menu page no longer parses ({exc})")
        return
    rows = soup.select(f'.p2m__list[data-region-list="{region}"] .p2m__row')
    present = [part for part in ("name", "qty", "origin")
               if sum(1 for r in rows if r.select_one(f".p2m__{part}")) * 2 >= len(rows)]
    report.facts["menu.row_fields"] = present
    if "qty" not in present:
        report.add(CHANGED, "menu", "item quantities are no longer found on the menu page")
    if len(m.items) < 5:
        report.add(CHANGED, "menu", f"only {len(m.items)} items found for {region}; the list may be incomplete")
    span = (m.week_to - m.week_from).days
    if not 0 <= span <= 7:
        report.add(BROKEN, "menu", f"week dates look wrong ({m.week_from} to {m.week_to}); "
                                   "the 'menu posted yet' test relies on them")


def check_page(report: Report, html: str) -> None:
    soup = BeautifulSoup(html, "html.parser")
    embeds = soup.select("powr-form-builder")
    if not embeds:
        report.add(BROKEN, "page", "the substitutions page no longer embeds a POWR form; "
                                   "the form may have moved to another provider")
        return
    ids = [e.get("u") for e in embeds]
    report.facts["page.form_ids"] = ids
    if form_mod.FORM_UNIQUE_LABEL not in ids:
        report.add(BROKEN, "page", f"the page now embeds POWR form {', '.join(map(str, ids))}, but the tool "
                                   f"reads {form_mod.FORM_UNIQUE_LABEL}; update FORM_UNIQUE_LABEL in form.py")
    elif len(ids) > 1:
        report.add(CHANGED, "page", f"the page embeds {len(ids)} POWR forms; the tool uses only one")


def _rules_text(ssr_html: str) -> str:
    soup = BeautifulSoup(ssr_html, "html.parser")
    el = soup.select_one("#form-description-edit-on-click") or soup
    return " ".join(el.get_text(" ", strip=True).split())


def check_form(report: Report, ssr_html: str, region: str, box: str, subscription: str | None):
    """Returns the FormPlan when the fields the tool relies on are all there, else None."""
    try:
        comps = form_mod.parse_components(ssr_html)
    except form_mod.FormError as exc:
        report.add(BROKEN, "form", str(exc))
        return None

    steps = sum(1 for c in comps if c["type"] == "step")
    report.facts["form.pages"] = steps
    if steps != EXPECTED_PAGES:
        report.add(BROKEN, "form", f"the form now has {steps} page(s); the filler expects {EXPECTED_PAGES} "
                                   "(it clicks Next once)")
    names = [c for c in comps if c["type"] == "name"]
    name_labels = [c.get("firstNameLabel") for c in names]
    email_labels = [c.get("label") for c in comps if c["type"] == "email"]
    report.facts["form.name_labels"] = name_labels
    report.facts["form.email_labels"] = email_labels
    if form_mod.NAME_LABEL not in name_labels:
        report.add(BROKEN, "form", f"no '{form_mod.NAME_LABEL}' field (name fields: {name_labels or 'none'})")
    if form_mod.EMAIL_LABEL not in email_labels:
        report.add(BROKEN, "form", f"no '{form_mod.EMAIL_LABEL}' field (email fields: {email_labels or 'none'})")

    rules = _rules_text(ssr_html)
    report.facts["rules.text"] = rules
    if not EXPECTED_WINDOW.search(rules):
        said = next((s for s in re.split(r"(?<=[.!?])\s+", rules) if re.search(r"submit", s, re.I)
                     and re.search(r"monday|tuesday|pm|am\b", s, re.I)), "no window sentence found")
        report.add(BROKEN, "rules", "the submission window wording changed, so the deadline the tool uses "
                                    f"(Mon 2pm local to Tue 11:55pm ET) may be wrong. It now says: {said}")
    if not FREE_RULE.search(rules) or PAID_PRICE not in rules:
        report.add(CHANGED, "rules", "the free-swap or $0.99 pricing wording changed; check the paid-swap logic")

    try:
        plan = form_mod.plan_for(comps, region, box, subscription)
    except form_mod.FormError as exc:
        report.add(BROKEN, "form", f"the region/subscription/item chain no longer resolves ({exc})")
        return None
    report.facts["form.regions"] = [o for o in plan.region["options"] if o and o.strip()]
    report.facts["form.subscriptions"] = [o for o in plan.subscription_field["options"] if o and o.strip()]
    report.facts["form.boxes"] = [o for o in plan.box_field["options"] if o and o.strip()]
    report.facts["form.chain_labels"] = [plan.region["label"], plan.subscription_field["label"],
                                         plan.remove_field["label"], plan.box_field["label"]]
    if not plan.remove_options:
        report.add(BROKEN, "form", f"the Item to Remove list for {plan.subscription} is empty")
    if not any(plan.add_options(b) for b in plan.add_fields):
        report.add(BROKEN, "form", "no box has any Item to Add options")
    elif plan.subscription not in plan.add_fields:
        report.add(CHANGED, "form", f"{plan.subscription} no longer has its own Item to Add list")

    # Any other required field that applies to this region and box would block the fill.
    known = {plan.region["idx"], plan.subscription_field["idx"], plan.remove_field["idx"],
             plan.box_field["idx"], *(c["idx"] for c in plan.add_fields.values())}
    scope = {None: None, plan.region["idx"]: plan.region_value,
             plan.subscription_field["idx"]: plan.subscription}
    extra = []
    for c in comps:
        if not c.get("required") or c["type"] in ("name", "email", "step") or c["idx"] in known:
            continue
        parent = c.get("conditionalElement")
        if parent in scope and (parent is None or c.get("conditionalValue") == scope[parent]):
            extra.append(c.get("label") or c["type"])
    for c in names:
        if c.get("lastNameRequired"):
            extra.append(c.get("lastNameLabel") or "Last Name")
    report.facts["form.extra_required"] = sorted(extra)
    if extra:
        report.add(BROKEN, "form", "new required field(s): " + ", ".join(sorted(extra))
                   + ". Text fields can be passed with --field \"Label=value\"; anything else needs a code change")
    return plan


def check_browser(report: Report, plan, home: Path) -> None:
    """Fill both pages headless with dummy details. Never clicks Submit."""
    from . import submit as submit_mod

    box = next((b for b in plan.box_order() if plan.add_options(b)), None)
    if box is None or not plan.remove_options:
        report.browser = "skipped (no options to choose)"
        return
    choice = form_mod.Choice(box=box, option=plan.add_options(box)[0], label=plan.add_fields[box]["label"])
    fill = submit_mod.fill_for(plan, DUMMY_NAME, DUMMY_EMAIL, plan.remove_options[0], choice)
    try:
        res = submit_mod.run(fill, home / "health-profile", home / "health", submit=False,
                             headless=True, wait=False, quiet=True)
    except Exception as exc:
        report.browser = "could not start"
        report.add(WARNING, "browser", f"the browser check could not run ({str(exc).splitlines()[0]})")
        return
    if res["result"] == "prefilled":
        report.browser = "both pages filled, stopped before Submit"
    else:
        report.browser = "failed"
        report.add(BROKEN, "browser", f"the form could not be filled in a browser: {res.get('error')}")


# --- baseline comparison ----------------------------------------------------------

def _describe(key: str, old, new) -> str:
    if key == "rules.text":
        split = lambda t: [s for s in re.split(r"(?<=[.!?])\s+", t or "") if s]
        diff = [d for d in difflib.ndiff(split(old), split(new)) if d[:1] in "+-"]
        return "the rules text changed:\n  " + "\n  ".join(d.replace("- ", "removed: ", 1).replace(
            "+ ", "added: ", 1) for d in diff[:8])
    if isinstance(old, list) and isinstance(new, list):
        gone, added = [x for x in old if x not in new], [x for x in new if x not in old]
        parts = ([f"added {added}"] if added else []) + ([f"removed {gone}"] if gone else [])
        return f"{key}: " + ("; ".join(parts) if parts else f"order changed to {new}")
    return f"{key}: {old!r} -> {new!r}"


def compare(report: Report, baseline: dict | None) -> None:
    if not baseline:
        return
    for key, new in report.facts.items():
        if key in baseline and baseline[key] != new:
            report.add(CHANGED, key.split(".")[0], _describe(key, baseline[key], new))
    for key in baseline:
        if key not in report.facts and not report.at(BROKEN):
            report.add(CHANGED, key.split(".")[0], f"{key} could no longer be read")


# --- orchestration ----------------------------------------------------------------

def run(store, prefs, *, browser: bool = True, get=_get) -> Report:
    """Run every check. Raises Unreachable on a transient network failure."""
    region, box = prefs.get("region"), prefs.get("box")
    report = Report()

    url = menu_mod.MENU_URL.format(box=box)
    html = get(url, "menu page")
    if not _moved(report, "menu", html, url):
        check_menu(report, html, region)

    html = get(SUBS_PAGE_URL, "substitutions page")
    if not _moved(report, "page", html, SUBS_PAGE_URL):
        check_page(report, html)

    url = form_mod.SSR_URL.format(label=form_mod.FORM_UNIQUE_LABEL)
    html = get(url, "POWR form")
    plan = None
    if not _moved(report, "form", html, url):
        plan = check_form(report, html, region, box, prefs.get("subscription"))

    if browser and plan is not None and not report.at(BROKEN):
        check_browser(report, plan, store.home)

    compare(report, load_baseline(store))
    return report


def baseline_path(store) -> Path:
    return store.home / "baseline.json"


def load_baseline(store) -> dict | None:
    p = baseline_path(store)
    return json.loads(p.read_text()) if p.exists() else None


def record(store, report: Report, now: dt.datetime) -> Path:
    """Save the report; accept the current facts as the new baseline unless something is broken."""
    out = store.home / "health"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{now.astimezone():%Y-%m-%d_%H%M}.txt"
    path.write_text(report.text() + "\n")
    if not report.at(BROKEN):
        baseline_path(store).write_text(json.dumps(report.facts, indent=2))
    return path


def weekly(store, prefs, now: dt.datetime, week_key: str, notifier, *, browser: bool = True) -> str:
    """Once per week, inside the window: run the checks and notify about anything found."""
    state = store.load_state()
    if state.get("health_week") == week_key:
        return "health: already checked this week"
    try:
        report = run(store, prefs, browser=browser)
    except Unreachable as exc:
        return f"health: site unreachable ({exc}); will try at the next run"
    path = record(store, report, now)
    if not report.ok:
        body = "\n".join(f"[{f.level}] {f.message}" for f in report.findings[:6])
        notifier(report.headline(), f"{body}\nFull report: {path}", prefs.section("notify"),
                 urgent=bool(report.at(BROKEN)))
    state["health_week"] = week_key
    store.save_state(state)
    return f"health: {len(report.at(BROKEN))} broken, {len(report.at(CHANGED))} changed"
