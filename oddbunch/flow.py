"""The Monday check and the reminder, kept free of CLI concerns so they can be tested."""

from __future__ import annotations

import datetime as dt
import time
from zoneinfo import ZoneInfo

from . import menu as menu_mod
from . import notify as notify_mod
from . import window
from .evaluate import Proposal, evaluate
from .store import Prefs, Store


def _local_today(now: dt.datetime, tz: str) -> dt.date:
    return now.astimezone(ZoneInfo(tz)).date()


def format_message(pending: dict, prefs: Prefs, now: dt.datetime, last_chance: bool = False):
    tz = prefs.get("timezone")
    m = menu_mod.Menu.from_dict(pending["menu"])
    p = Proposal.from_dict(pending["proposal"])
    _, closes = window.window_for(now, tz)
    title = f"Odd Bunch {m.week_label}"
    if last_chance:
        title = f"Last chance: {title}"
    lines = [f"Box ({len(m.items)}): " + ", ".join(m.names)]
    if not p.free:
        lines.append("Box is fine, no action needed.")
        return title, "\n".join(lines)
    lines.append("Flagged: " + ", ".join(p.flagged))
    if p.free.candidates:
        first, rest = p.free.candidates[0], p.free.candidates[1:4]
        tail = f" (then {', '.join(rest)})" if rest else ""
        lines.append(f"Free swap: {p.free.remove} -> {first}{tail}")
    else:
        lines.append(f"Free swap: {p.free.remove} -> no replacement preference set; pick one on the form")
    if p.paid:
        lines.append("Optional paid swaps ($0.99 each): " + ", ".join(s.remove for s in p.paid))
    lines.append(f"Deadline: {window.fmt(closes, tz)}")
    lines.append("Run: oddbunch approve  |  oddbunch skip")
    return title, "\n".join(lines)


def run_check(
    store: Store,
    prefs: Prefs,
    now: dt.datetime,
    *,
    fetch=None,
    sleep=time.sleep,
    notifier=notify_mod.notify,
    retries: int | None = None,
    force_notify: bool = False,
) -> dict | None:
    """Fetch this week's menu, build a proposal, notify. Safe to run more than once a week."""
    tz = prefs.get("timezone")
    box, region = prefs.get("box"), prefs.get("region")
    fetch = fetch or (lambda: menu_mod.fetch_menu(box, region))
    check_cfg = prefs.section("check")
    retries = check_cfg["retries"] if retries is None else retries
    notify_cfg = prefs.section("notify")
    today = _local_today(now, tz)

    attempt = 0
    while True:
        m = fetch()
        if menu_mod.is_current(m, today):
            break
        if attempt >= retries:
            notifier("Odd Bunch: menu not posted yet",
                     f"The page still shows {m.week_label}. Run 'oddbunch check' again later.",
                     notify_cfg)
            return None
        attempt += 1
        print(f"Menu still shows {m.week_label}; retry {attempt}/{retries} in {check_cfg['retry_minutes']} min")
        sleep(check_cfg["retry_minutes"] * 60)
        now = now + dt.timedelta(minutes=check_cfg["retry_minutes"])

    week = m.week_from.isoformat()
    store.save_snapshot(m, box)
    existing = store.load_pending()
    if existing and existing.get("week") == week:
        if existing.get("status") == "pending" and force_notify:
            notifier(*format_message(existing, prefs, now), notify_cfg)
        else:
            print(f"Proposal for {m.week_label} already exists (status: {existing.get('status')}).")
        return existing

    if not window.is_open(now, tz):
        print(f"Substitution window for {m.week_label} is closed; logging as missed.")
        store.upsert_log(week, items_count=len(m.items), result="window missed")
        return None

    proposal = evaluate(m.names, prefs.remove, prefs.replace_with, prefs.aliases)
    pending = {
        "week": week,
        "menu": m.to_dict(),
        "proposal": proposal.to_dict(),
        "status": "pending" if proposal.free else "no-action",
        "submissions": [],
        "created_at": now.isoformat(),
    }
    store.save_pending(pending)
    proposed = ""
    if proposal.free:
        first = proposal.free.candidates[0] if proposal.free.candidates else "?"
        proposed = f"{proposal.free.remove} -> {first}"
    store.upsert_log(
        week,
        items_count=len(m.items),
        flagged="; ".join(proposal.flagged),
        proposed=proposed,
        result="pending" if proposal.free else "box is fine",
    )
    notifier(*format_message(pending, prefs, now), notify_cfg)
    return pending


def run_remind(store: Store, prefs: Prefs, now: dt.datetime, **check_kwargs) -> str:
    """Re-notify if this week's proposal is still pending; run the check if it never ran."""
    tz = prefs.get("timezone")
    if not window.is_open(now, tz):
        return "window closed"
    pending = store.load_pending()
    today = _local_today(now, tz)
    if not pending or dt.date.fromisoformat(pending["week"]) < today:
        run_check(store, prefs, now, **check_kwargs)
        return "ran check"
    if pending.get("status") != "pending":
        return f"nothing to do ({pending.get('status')})"
    last_chance = now.astimezone(ZoneInfo(tz)).weekday() == 1
    notifier = check_kwargs.get("notifier", notify_mod.notify)
    notifier(*format_message(pending, prefs, now, last_chance=last_chance),
             prefs.section("notify"), urgent=last_chance)
    return "reminded"
