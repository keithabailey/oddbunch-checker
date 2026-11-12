"""The Monday check and the catch-up scheduler tick, kept free of CLI concerns so they can be tested."""

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
    notify_not_posted: bool = True,
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
            if notify_not_posted:
                notifier("Odd Bunch: menu not posted yet",
                         f"The page still shows {m.week_label}. Run 'oddbunch check' again later.",
                         notify_cfg)
            else:
                print(f"Menu still shows {m.week_label}; not posted yet.")
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
        # Reminders already past due are covered by this notification, so a late start sends one message.
        "reminded": [r[0] for r in REMINDERS if now >= _at(now, tz, r[1], r[2])],
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


# (label, weekday Mon=0, local time, urgent)
CHECK_FROM = dt.time(14, 30)
NOT_POSTED_NOTICE_FROM = dt.time(16, 30)
REMINDERS = [
    ("evening", 0, dt.time(19, 30), False),
    ("last-chance", 1, dt.time(18, 0), True),
]


def _at(now: dt.datetime, tz: str, weekday: int, t: dt.time) -> dt.datetime:
    """This week's local datetime for a weekday and time."""
    zone = ZoneInfo(tz)
    local = now.astimezone(zone)
    monday = local.date() - dt.timedelta(days=local.weekday())
    return dt.datetime.combine(monday + dt.timedelta(days=weekday), t, tzinfo=zone)


def run_tick(store: Store, prefs: Prefs, now: dt.datetime, *, fetch=None,
             notifier=notify_mod.notify) -> str:
    """Do whatever is due right now. Runs at login, on wake and every 30 minutes.

    Catches up on a missed check or reminder as soon as the Mac is on, and never
    sends the same message twice in a week. Outside the window it does nothing.
    """
    tz = prefs.get("timezone")
    if not window.is_open(now, tz):
        return "window closed"
    if now < _at(now, tz, 0, CHECK_FROM):
        return "too early"

    pending = store.load_pending()
    today = _local_today(now, tz)
    if not pending or dt.date.fromisoformat(pending["week"]) < today:
        week_key = _at(now, tz, 0, dt.time(0)).date().isoformat()
        state = store.load_state()
        allow_notice = (now >= _at(now, tz, 0, NOT_POSTED_NOTICE_FROM)
                        and state.get("not_posted_notice") != week_key)
        out = run_check(store, prefs, now, fetch=fetch, notifier=notifier, retries=0,
                        notify_not_posted=allow_notice)
        if out is None:
            if allow_notice:
                state["not_posted_notice"] = week_key
                store.save_state(state)
            return "menu not posted"
        return "checked"

    if pending.get("status") != "pending":
        return f"nothing to do ({pending.get('status')})"
    due = [r for r in REMINDERS if now >= _at(now, tz, r[1], r[2])]
    if not due:
        return "waiting"
    label, _, _, urgent = due[-1]
    if label in pending.get("reminded", []):
        return "already reminded"
    notifier(*format_message(pending, prefs, now, last_chance=urgent),
             prefs.section("notify"), urgent=urgent)
    pending["reminded"] = sorted(set(pending.get("reminded", [])) | {r[0] for r in due})
    store.save_pending(pending)
    return f"reminded ({label})"
