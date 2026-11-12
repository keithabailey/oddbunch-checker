"""Command line: init | check | tick | approve | skip | status | history | like | dislike | inspect-form | install-schedule."""

from __future__ import annotations

import datetime as dt
import os
import sys
from collections import Counter

import click

from . import flow, schedule, window
from .evaluate import Proposal, find_in, normalise, rank_candidates
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


@cli.command()
@click.option("--swap", "swap", help='Override the proposal, e.g. --swap "Gooseberries=Apples".')
@click.option("--email", envvar="ODDBUNCH_EMAIL", help="Account email (or ODDBUNCH_EMAIL, or prefs.yaml).")
@click.option("--field", "fields", multiple=True, help='Any other form field, as "Label=value". Repeatable.')
@click.option("--remove-label", help="Exact label of the item-to-remove field, if detection misses it.")
@click.option("--replace-label", help="Exact label of the replacement field, if detection misses it.")
@click.option("--submit", "do_submit", is_flag=True, help="Click Submit too. Default: fill only, you submit.")
@click.option("--allow-paid", is_flag=True, help="Allow a paid ($0.99) extra swap.")
@click.option("--yes", is_flag=True, help="Answer yes to preference-update questions.")
def approve(swap, email, fields, remove_label, replace_label, do_submit, allow_paid, yes):
    """Fill the substitution form for this week's swap (one swap per run)."""
    s = _store()
    prefs = s.load_prefs()
    tz = prefs.get("timezone")
    t = now()
    if not window.is_open(t, tz):
        opens, closes = window.window_for(t, tz)
        raise click.ClickException(
            f"Substitution window is closed. It runs {window.fmt(opens, tz)} to {window.fmt(closes, tz)}."
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
        remove = nxt.remove
        candidates = rank_candidates(nxt.candidates, exclude=used, aliases=prefs.aliases)

    if paid and not allow_paid:
        raise click.ClickException(
            f"The free swap is used. Swapping {remove} costs $0.99; re-run with --allow-paid."
        )

    email = email or prefs.get("email")
    if not email:
        raise click.ClickException("No email: pass --email, set ODDBUNCH_EMAIL, or add it to prefs.yaml.")
    if swap:
        _learn(s, prefs, remove, candidates[0], yes)

    extra = dict(_parse_pair(f, "--field") for f in fields)
    labels = dict(prefs.section("form"))
    if remove_label:
        labels["remove_label"] = remove_label
    if replace_label:
        labels["replace_label"] = replace_label

    from . import submit as submit_mod

    fill = submit_mod.FormFill(email=email, remove=remove, candidates=candidates, labels=labels, extra=extra)
    click.echo(f"Opening the form: {remove} -> {candidates[0] if candidates else '(choose on the form)'}"
               + (" [paid $0.99]" if paid else " [free]"))
    try:
        res = submit_mod.run(fill, s.profile_dir, s.history_dir, submit=do_submit)
    except submit_mod.SubmitError as exc:
        raise click.ClickException(str(exc))

    if res.get("form_options"):
        click.echo("Replacement options on the form: " + ", ".join(res["form_options"]))
    record = {"remove": remove, "replace": res.get("replace"), "paid": paid,
              "result": res["result"], "at": t.isoformat(), "screenshot": res.get("screenshot")}
    pending.setdefault("submissions", []).append(record)
    pending["status"] = "submitted" if res["result"].startswith("submitted") else res["result"]
    s.save_pending(pending)
    summary = "; ".join(f"{x['remove']} -> {x.get('replace') or '?'}" for x in pending["submissions"])
    s.upsert_log(pending["week"], submitted=summary, result=pending["status"])
    click.echo(f"Recorded: {remove} -> {res.get('replace') or '?'} ({res['result']}).")


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


@cli.command("inspect-form")
def inspect_form():
    """Open the substitution form and list its fields (use on the first open Monday)."""
    from . import submit as submit_mod

    s = _store()
    try:
        fields = submit_mod.inspect_form(s.profile_dir)
    except submit_mod.SubmitError as exc:
        raise click.ClickException(str(exc))
    for f in fields:
        opts = f"  options: {', '.join(f['options'])}" if f["options"] else ""
        click.echo(f"{f['tag']}[{f['type']}] name={f['name']!r} label={f['label']!r}{opts}")


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
