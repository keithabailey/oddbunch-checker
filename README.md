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
- **Window:** substitutions open Monday 2pm local and close Tuesday 11:55pm Eastern. The deadline is
  calculated from your timezone, so it stays right if your province changes its clocks.
- **Swaps:** one swap per box is free. Each extra costs $0.99, needs `--allow-paid` and is a separate
  form submission (a separate `approve` run).

## Install (macOS)

Keep the checkout outside any cloud-synced folder (iCloud Drive, OneDrive, Dropbox). launchd jobs
cannot reliably read files there.

```sh
git clone https://github.com/keithabailey/oddbunch-checker.git ~/dev/oddbunch && cd ~/dev/oddbunch
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/playwright install chromium     # only needed for approve / inspect-form
.venv/bin/oddbunch init --email you@example.com
$EDITOR ~/.oddbunch/prefs.yaml            # set remove, replace_with, notify
.venv/bin/oddbunch install-schedule       # check Mon 14:30; remind Mon 19:30 and Tue 18:00
```

## Personal data stays out of the repo

Everything personal lives in `~/.oddbunch/` (override with `ODDBUNCH_HOME`): `prefs.yaml`, `log.csv`,
`history/`, `pending.json`, `logs/` and the browser profile. The repo only has `prefs.example.yaml`.

Anything the form needs can be given at run time instead of being stored:

| Need | Options (first wins) |
|---|---|
| Account email | `--email`, `ODDBUNCH_EMAIL`, `email:` in prefs |
| Any other form field | `--field "Label=value"` (repeatable) |
| Field labels, if detection misses | `--remove-label`, `--replace-label`, or `form:` in prefs |
| ntfy topic / token | `ODDBUNCH_NTFY_TOPIC`, `ODDBUNCH_NTFY_TOKEN`, or `notify:` in prefs |

## Commands

| Command | What it does |
|---|---|
| `oddbunch check` | Fetch this week's menu (retries if not yet posted), propose a swap, notify. Safe to re-run. |
| `oddbunch remind` | Re-notify if the proposal is unanswered; runs the check if it never ran. |
| `oddbunch status` | Show this week's proposal and submissions. |
| `oddbunch approve` | Fill the form for the next swap. You review and click Submit. |
| `oddbunch approve --submit` | Also click Submit. Stops and hands over if a CAPTCHA challenge appears. |
| `oddbunch approve --swap "A=B"` | Override this week's swap; offers to add A and B to your lists. |
| `oddbunch approve --allow-paid` | Allow a paid extra swap (one per run). |
| `oddbunch skip` | No swap this week. |
| `oddbunch history` | Recent weeks, and how often each unwanted item turned up. |
| `oddbunch like ITEM [--top]` / `dislike ITEM [--top]` | Add to `replace_with` / `remove`. |
| `oddbunch inspect-form` | List the substitution form's fields and options (use on the first open Monday). |

## Status

The menu parser, matching, window logic and scheduling are tested. The substitution form only renders
during the window and has not been seen live yet, so field detection in `submit.py` is heuristic. On the
first open Monday, run `oddbunch inspect-form`, record the labels under `form:` in prefs.yaml, and use the
default fill-only mode.

## Tests

```sh
.venv/bin/pip install -e '.[dev]' && .venv/bin/pytest
```

`tests/fixtures/` holds a trimmed copy of the public menu block from the week of 11 November 2026.
