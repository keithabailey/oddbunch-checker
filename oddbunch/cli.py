"""Command line: init | check | tick | status | options | approve | skip | history | like | dislike | health | install-schedule."""

from __future__ import annotations

import datetime as dt
import os
import sys
from collections import Counter

import click

from . import flow, schedule, window
from .evaluate import Proposal, find_in, normalise
from .menu import Menu
from .store import Store, now_utc


def now() -> dt.datetime:
    """Current time; tests patch this."""
    return now_utc()


def _store() -> Store:
    s = Store()
    s.ensure_dirs()
    return s


def _parse_pair(value: str, what: str) -> tuple[str, str]:
    if "=" not in value:
        raise click.BadParameter(f"expected {what} as A=B, got {value!r}")
    a, b = (x.strip() for x in value.split("=", 1))
    if not a or not b:
        raise click.BadParameter(f"expected {what} as A=B, got {value!r}")
    return a, b


@click.group()
@click.version_option()
def cli():
    """Check the weekly Odd Bunch box and prefill the substitution form."""


@cli.command()
@click.option("--email", help="Account email to store in prefs.yaml (optional).")
def init(email):
    """Create ~/.oddbunch/prefs.yaml from the template."""
    s = _store()
    if s.init_prefs(email):
        click.echo(f"Created {s.prefs_path}. Edit it to set your remove and replace_with lists.")
    else:
        click.echo(f"{s.prefs_path} already exists; left unchanged.")


@cli.command()
@click.option("--no-retry", is_flag=True, help="Fetch once; do not wait for a late menu.")
@click.option("--renotify", is_flag=True, help="Notify again if this week's proposal is still pending.")
def check(no_retry, renotify):
    """Fetch this week's menu, propose a swap and notify."""
    s = _store()
    prefs = s.load_prefs()
    flow.run_check(s, prefs, now(), retries=0 if no_retry else None, force_notify=renotify)


@cli.command()
def tick():
    """Do whatever is due now (launchd runs this at login and at the scheduled times)."""
    s = _store()
    click.echo(f"{now().isoformat(timespec='seconds')} {flow.run_tick(s, s.load_prefs(), now(), clock=now)}")


@cli.command()
def status():
    """Show this week's proposal and what has been submitted."""
    s = _store()
    pending = s.load_pending()
    if not pending:
        click.echo("No proposal yet. Run: oddbunch check")
        return
    title, msg = flow.format_message(pending, s.load_prefs(), now())
    click.echo(f"{title}  [status: {pending['status']}]\n{msg}")
    for sub in pending.get("submissions", []):
        click.echo(f"  submitted: {sub['remove']} -> {sub.get('replace') or '?'} ({sub['result']})")


def _current_pending(s: Store, prefs) -> dict:
    pending = s.load_pending()
    tz = prefs.get("timezone")
    today = now().astimezone(window.ZoneInfo(tz)).date()
    if not pending or dt.date.fromisoformat(pending["week"]) < today:
        raise click.ClickException("No proposal for this week. Run: oddbunch check")
    return pending


def _plan(prefs):
    from . import form as form_mod

    try:
        comps = form_mod.fetch_components()
        return form_mod.plan_for(comps, prefs.get("region"), prefs.get("box"), prefs.get("subscription"))
    except Exception as exc:
        raise click.ClickException(f"Could not read the substitution form: {exc}")


@cli.command()
@click.option("--swap", "swap", help='Override the proposal, e.g. --swap "Gooseberries=Apples".')
@click.option("--box", "from_box", help='Take the replacement from this box\'s list only, e.g. "All Fruit".')
@click.option("--name", envvar="ODDBUNCH_NAME", help="Full Name for the form (or ODDBUNCH_NAME, or prefs.yaml).")
@click.option("--email", envvar="ODDBUNCH_EMAIL", help="Account email (or ODDBUNCH_EMAIL, or prefs.yaml).")
@click.option("--field", "fields", multiple=True, help='Any extra text field, as "Label=value". Repeatable.')
@click.option("--submit", "do_submit", is_flag=True, help="Click Submit too. Default: fill only, you submit.")
@click.option("--allow-paid", is_flag=True, help="Allow a paid ($0.99) extra swap.")
@click.option("--yes", is_flag=True, help="Answer yes to preference-update questions.")
def approve(swap, from_box, name, email, fields, do_submit, allow_paid, yes):
    """Fill the substitution form for this week's swap (one swap per run)."""
    s = _store()
    prefs = s.load_prefs()
    tz = prefs.get("timezone")
    t = now()
    if not window.is_open(t, tz):
        opens, closes = window.window_for(t, tz)
        raise click.ClickException(
            "Outside the substitution window, so Odd Bunch would not process it. "
            f"It runs {window.fmt(opens, tz)} to {window.fmt(closes, tz)}."
        )
    pending = _current_pending(s, prefs)
    menu = Menu.from_dict(pending["menu"])
    proposal = Proposal.from_dict(pending["proposal"])
    done = [x["remove"] for x in pending.get("submissions", [])]
    used = [x["replace"] for x in pending.get("submissions", []) if x.get("replace")]
    paid = bool(done)

    if swap:
        a, b = _parse_pair(swap, "--swap")
        remove = find_in(a, menu.names, prefs.aliases)
        if remove is None:
            raise click.ClickException(f"{a!r} is not in this week's box: {', '.join(menu.names)}")
        candidates = [b]
    else:
        queue = ([proposal.free] if proposal.free else []) + proposal.paid
        nxt = next((sw for sw in queue if sw.remove not in done), None)
        if nxt is None:
            raise click.ClickException("Nothing left to submit this week.")
        remove, candidates = nxt.remove, nxt.candidates

    if paid and not allow_paid:
        raise click.ClickException(
            f"The free swap is used. Swapping {remove} costs $0.99; re-run with --allow-paid."
        )

    name = name or prefs.get("name")
    email = email or prefs.get("email")
    if not name or not email:
        raise click.ClickException("The form needs a name and email: pass --name/--email, set "
                                   "ODDBUNCH_NAME/ODDBUNCH_EMAIL, or add them to prefs.yaml.")

    plan = _plan(prefs)
    remove_option = plan.remove_option(remove, prefs.aliases)
    if remove_option is None:
        raise click.ClickException(f"{remove!r} is not in the form's Item to Remove list: "
                                   + ", ".join(plan.remove_options))
    choice = plan.find_replacement(candidates, prefs.get("replacement_boxes"), prefs.aliases,
                                   exclude=used, only_boxes=[from_box] if from_box else None)
    if choice is None:
        own = ", ".join(plan.summary().get(plan.subscription, []))
        raise click.ClickException(
            f"None of {', '.join(candidates) or 'your replacements'} is on the form this week. "
            f"Your box list offers: {own}. Use --swap \"{remove}=<item>\" or see 'oddbunch options'."
        )
    if swap:
        _learn(s, prefs, remove, candidates[0], yes)

    from . import submit as submit_mod

    fill = submit_mod.fill_for(plan, name, email, remove_option, choice,
                               dict(_parse_pair(f, "--field") for f in fields))
    click.echo(f"Opening the form: {remove} -> {choice.name} (from the {choice.box} list)"
               + (" [paid $0.99]" if paid else " [free]"))
    res = submit_mod.run(fill, s.profile_dir, s.history_dir, submit=do_submit)

    record = {"remove": remove, "replace": choice.name, "box": choice.box, "paid": paid,
              "result": res["result"], "at": t.isoformat(), "screenshot": res.get("screenshot")}
    pending.setdefault("submissions", []).append(record)
    pending["status"] = "submitted" if res["result"].startswith("submitted") else res["result"]
    s.save_pending(pending)
    summary = "; ".join(f"{x['remove']} -> {x.get('replace') or '?'}" for x in pending["submissions"])
    s.upsert_log(pending["week"], submitted=summary, result=pending["status"])
    click.echo(f"Recorded: {remove} -> {choice.name} ({res['result']}).")


def _learn(s: Store, prefs, remove: str, replacement: str, yes: bool) -> None:
    if not any(normalise(x, prefs.aliases) == normalise(remove, prefs.aliases) for x in prefs.remove):
        if yes or click.confirm(f"Add {remove} to your remove list?", default=True):
            s.add_to_list("remove", remove)
    if not any(normalise(x, prefs.aliases) == normalise(replacement, prefs.aliases) for x in prefs.replace_with):
        if yes or click.confirm(f"Add {replacement} to your replace_with list?", default=True):
            s.add_to_list("replace_with", replacement)
            click.echo("Added at the end; reorder replace_with in prefs.yaml if it should rank higher.")


@cli.command()
@click.option("--note", default="", help="Optional reason, saved in log.csv.")
def skip(note):
    """No swap this week."""
    s = _store()
    pending = _current_pending(s, s.load_prefs())
    pending["status"] = "skipped"
    s.save_pending(pending)
    s.upsert_log(pending["week"], result="skipped", notes=note)
    click.echo("Skipped this week.")


@cli.command()
@click.option("--weeks", "-n", default=8, show_default=True)
def history(weeks):
    """Recent weeks, and how often your unwanted items turn up."""
    s = _store()
    prefs = s.load_prefs()
    for r in s.read_log()[-weeks:]:
        click.echo(f"{r['week']}  flagged: {r['flagged'] or '-':<30} submitted: {r['submitted'] or '-':<30} {r['result']}")
    snaps = s.snapshots()[-weeks:]
    if not snaps:
        return
    counts = Counter()
    for snap in snaps:
        keys = {normalise(i["name"], prefs.aliases) for i in snap["items"]}
        for item in prefs.remove:
            if normalise(item, prefs.aliases) in keys:
                counts[item] += 1
    click.echo(f"\nUnwanted items over the last {len(snaps)} week(s):")
    for item in prefs.remove:
        click.echo(f"  {item}: {counts[item]}")


@cli.command()
@click.argument("item")
@click.option("--top", is_flag=True, help="Put it first (highest priority).")
def dislike(item, top):
    """Add an item to the remove list."""
    added = _store().add_to_list("remove", item, top=top)
    click.echo(f"Added {item} to remove." if added else f"{item} is already in remove.")


@cli.command()
@click.argument("item")
@click.option("--top", is_flag=True, help="Make it the first-choice replacement.")
def like(item, top):
    """Add an item to the replace_with list."""
    added = _store().add_to_list("replace_with", item, top=top)
    click.echo(f"Added {item} to replace_with." if added else f"{item} is already in replace_with.")


@cli.command()
def options():
    """Show what the substitution form offers this week, and where your replacements are."""
    s = _store()
    prefs = s.load_prefs()
    plan = _plan(prefs)
    from .form import item_name

    click.echo(f"Item to Remove ({plan.subscription} - {plan.region_value}): "
               + ", ".join(item_name(o) for o in plan.remove_options))
    for box, items in plan.summary().items():
        mark = " (your box)" if box == plan.subscription else ""
        click.echo(f"Add from {box}{mark}: {', '.join(items)}")
    click.echo("")
    for want in prefs.replace_with:
        hit = plan.find_replacement([want], prefs.get("replacement_boxes"), prefs.aliases)
        click.echo(f"  {want}: " + (f"available from the {hit.box} list" if hit else "not offered this week"))


@cli.command()
@click.option("--browser/--no-browser", default=True, show_default=True,
              help="Also fill the form headless with dummy details (never submitted).")
def health(browser):
    """Check the menu page and substitution form still look the way this tool expects."""
    from . import health as health_mod

    s = _store()
    try:
        report = health_mod.run(s, s.load_prefs(), browser=browser)
    except health_mod.Unreachable as exc:
        raise click.ClickException(f"Could not reach the site ({exc}). Nothing was concluded.")
    path = health_mod.record(s, report, now())
    click.echo(report.text())
    click.echo(f"Saved to {path}" + ("" if report.at(health_mod.BROKEN) else "; baseline updated"))
    if report.at(health_mod.BROKEN):
        sys.exit(1)


@cli.command("install-schedule")
@click.option("--load/--no-load", default=True, help="Load the agents into launchd now.")
@click.option("--label-prefix", default="local.oddbunch", show_default=True)
def install_schedule(load, label_prefix):
    """Install the launchd agent: `tick` at login, Mon 14:30, Mon 19:30 and Tue 15:00."""
    s = _store()
    click.echo(f"Wrote {schedule.install(s.logs_dir, label_prefix=label_prefix, load=load)}")


def main():
    cli(prog_name="oddbunch")


if __name__ == "__main__":
    sys.exit(main())
