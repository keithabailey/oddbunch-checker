# oddbunch

A small local tool for an [Odd Bunch](https://www.oddbunch.ca) weekly box subscriber. Every Monday it reads
the public menu, flags items you don't want, proposes a swap from your ranked list of replacements,
and, once you approve, fills in the substitution form in a visible browser.

It is unofficial and has no affiliation with Odd Bunch. It makes no LLM calls, never logs in to the
customer portal, and never solves or bypasses CAPTCHAs.

## How it works

- **Menu:** the product page (`/products/large-box`) contains every region's list in its raw HTML, so a
  plain HTTP fetch is enough. No browser and no login.
- **Preferences:** two independent ranked lists. `remove` holds items you don't want, in priority order;
  the first one in the box gets the free swap. `replace_with` holds what you'd like instead, in order of
  preference; the first one the form offers that week is used.
- **Form:** the substitution page hosts a two-page POWR form (name, email, region, subscription, item
  to remove; then which box's list to pick from, and the item to add). POWR serves the whole form,
  every conditional list included, as one page, so the tool reads the options with a plain fetch
  and only opens a browser to fill it in.
- **Replacements across boxes:** the form lets you take the replacement from any box's list. Your own
  box is searched first, then the others, for each preference in order. For example, Asian Pears may
  be missing from the Large Box list but offered on the All Fruit list.
- **Window:** the form is always viewable, but Odd Bunch only processes requests sent between Monday
  2pm local and Tuesday 11:55pm Eastern, so the tool refuses to fill it outside that window. The
  deadline is calculated from your timezone.
- **Swaps:** one swap per box is free. Each extra costs $0.99, needs `--allow-paid` and is a separate
  form submission (a separate `approve` run).

## Install (macOS)

Keep the checkout outside any cloud-synced folder (iCloud Drive, OneDrive, Dropbox). launchd jobs
cannot reliably read files there.

```sh
git clone https://github.com/keithabailey/oddbunch-checker.git ~/dev/oddbunch && cd ~/dev/oddbunch
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/playwright install chromium     # only needed for approve and the browser tests
.venv/bin/oddbunch init --email you@example.com
$EDITOR ~/.oddbunch/prefs.yaml            # set remove, replace_with, notify
.venv/bin/oddbunch install-schedule       # one launchd agent; see Scheduling
```

## Scheduling

`install-schedule` writes one launchd agent that runs `oddbunch tick`. There is no polling timer. It
starts at login or boot and at three times a week:

| When | Action |
|---|---|
| Monday 14:30 | Check the menu and send the proposal |
| Monday 19:30 | Reminder, if you haven't approved or skipped |
| Tuesday 15:00 | Last-chance reminder |

- **Missed times:** a time missed while the Mac was asleep fires once when it wakes; if it was shut
  down, the login run catches up. A late start sends one message, not every missed one.
- **Retries:** if the check fails (menu not posted yet, or the site unreachable), the same run retries
  every 20 minutes until it succeeds or the window closes, with one "not posted yet" notice from 16:30.
- **Otherwise idle:** outside Monday 14:30 to Tuesday 11:55pm ET, or once the week's check has
  succeeded, `tick` exits immediately without touching the network.

Logs go to `~/.oddbunch/logs/`.

## Personal data stays out of the repo

Everything personal lives in `~/.oddbunch/` (override with `ODDBUNCH_HOME`): `prefs.yaml`, `log.csv`,
`history/`, `pending.json`, `logs/` and the browser profile. The repo only has `prefs.example.yaml`.

Anything the form needs can be given at run time instead of being stored:

| Need | Options (first wins) |
|---|---|
| Full name | `--name`, `ODDBUNCH_NAME`, `name:` in prefs |
| Account email | `--email`, `ODDBUNCH_EMAIL`, `email:` in prefs |
| Any new text field the form adds | `--field "Label=value"` (repeatable) |
| ntfy topic / token | `ODDBUNCH_NTFY_TOPIC`, `ODDBUNCH_NTFY_TOKEN`, or `notify:` in prefs |

## Commands

| Command | What it does |
|---|---|
| `oddbunch check` | Fetch this week's menu (retries if not yet posted), propose a swap, notify. Safe to re-run. |
| `oddbunch tick` | Do whatever is due now. launchd runs this; you rarely need to. |
| `oddbunch check --renotify` | Run the check by hand, or resend this week's proposal. |
| `oddbunch status` | Show this week's proposal and submissions. |
| `oddbunch options` | What the form offers this week, by box, and where each of your replacements is. |
| `oddbunch approve` | Fill the form for the next swap. You review and click Submit. |
| `oddbunch approve --submit` | Also click Submit. Stops and hands over if a CAPTCHA challenge appears. |
| `oddbunch approve --swap "A=B"` | Override this week's swap; offers to add A and B to your lists. |
| `oddbunch approve --box "All Fruit"` | Take the replacement from that box's list only. |
| `oddbunch approve --allow-paid` | Allow a paid extra swap (one per run). |
| `oddbunch skip` | No swap this week. |
| `oddbunch history` | Recent weeks, and how often each unwanted item turned up. |
| `oddbunch like ITEM [--top]` / `dislike ITEM [--top]` | Add to `replace_with` / `remove`. |

## Status

Menu parsing, form reading, matching, the window and scheduling are covered by tests. The filler is
tested against a local mock built from the real form configuration, and a prefill-only run against
the live form (dummy details, not submitted) reached the Submit button with the right selections. The
first real submission is the remaining unknown: whether the invisible reCAPTCHA accepts it and what
the confirmation looks like. The form sends a confirmation email, which is the simplest proof.

## Tests

```sh
.venv/bin/pip install -e '.[dev]' && .venv/bin/pytest
```

`tests/fixtures/` holds a trimmed copy of the public menu block and the form configuration from the
week of 11 November 2026. `tests/test_submit.py` drives the filler in headless Chromium against a mock
form built from that configuration (run `playwright install chromium` first).
