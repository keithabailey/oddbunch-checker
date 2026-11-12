"""Local state: prefs.yaml, log.csv, history snapshots and the pending proposal.

Everything lives under ~/.oddbunch (override with ODDBUNCH_HOME). Nothing personal
is kept in the repository.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
from io import StringIO
from pathlib import Path

from ruamel.yaml import YAML

PREFS_TEMPLATE = """\
# Odd Bunch box check preferences. Edit by hand; the tool also appends to the lists.

# Account email for the substitution form. Can also come from ODDBUNCH_EMAIL or --email.
email: {email}

region: west          # west = BC & AB, east = ON & QC, prairie = SK & MB
box: large-box
timezone: America/Vancouver
free_swaps_only: true # paid ($0.99) swaps are only listed, and need --allow-paid

# Items to swap out, in priority order. The first one found in the box gets the free swap.
remove: []

# Replacements in order of preference. The first one the form offers that week is used.
replace_with: []

# Extra spellings to treat as the same item, e.g. "mandarin oranges: clementines".
aliases: {{}}

# Substitution form field labels, once confirmed on a live Monday (optional).
form:
  email_label:
  remove_label:
  replace_label:

notify:
  channels: [ntfy, macos]
  ntfy_server: https://ntfy.sh
  ntfy_topic:          # pick something long and random; public topics are readable by anyone

check:
  retries: 6           # menu not posted yet: retry this many times...
  retry_minutes: 20    # ...this far apart
"""

DEFAULTS = {
    "email": None,
    "region": "west",
    "box": "large-box",
    "timezone": "America/Vancouver",
    "free_swaps_only": True,
    "remove": [],
    "replace_with": [],
    "aliases": {},
    "form": {},
    "notify": {"channels": ["macos"], "ntfy_server": "https://ntfy.sh", "ntfy_topic": None},
    "check": {"retries": 6, "retry_minutes": 20},
}

LOG_COLUMNS = ["week", "items_count", "flagged", "proposed", "submitted", "result", "notes"]


def default_home() -> Path:
    return Path(os.environ.get("ODDBUNCH_HOME", "~/.oddbunch")).expanduser()


class Prefs:
    def __init__(self, raw):
        self.raw = raw

    def get(self, key, default=None):
        val = self.raw.get(key) if self.raw else None
        if val is None:
            val = DEFAULTS.get(key, default)
        return val

    def section(self, key) -> dict:
        merged = dict(DEFAULTS.get(key) or {})
        merged.update({k: v for k, v in (self.raw.get(key) or {}).items() if v is not None})
        return merged

    @property
    def remove(self) -> list[str]:
        return list(self.get("remove") or [])

    @property
    def replace_with(self) -> list[str]:
        return list(self.get("replace_with") or [])

    @property
    def aliases(self) -> dict[str, str]:
        return dict(self.get("aliases") or {})


class Store:
    def __init__(self, home: Path | str | None = None):
        self.home = Path(home) if home else default_home()
        self.prefs_path = self.home / "prefs.yaml"
        self.log_path = self.home / "log.csv"
        self.pending_path = self.home / "pending.json"
        self.state_path = self.home / "state.json"
        self.history_dir = self.home / "history"
        self.logs_dir = self.home / "logs"
        self.profile_dir = self.home / "profile"
        self._yaml = YAML()
        self._yaml.preserve_quotes = True

    def ensure_dirs(self) -> None:
        for d in (self.home, self.history_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)

    # prefs
    def init_prefs(self, email: str | None = None) -> bool:
        self.ensure_dirs()
        if self.prefs_path.exists():
            return False
        self.prefs_path.write_text(PREFS_TEMPLATE.format(email=email or ""))
        return True

    def load_prefs(self) -> Prefs:
        if not self.prefs_path.exists():
            return Prefs(self._yaml.load(PREFS_TEMPLATE.format(email="")))
        return Prefs(self._yaml.load(self.prefs_path.read_text()) or {})

    def save_prefs(self, prefs: Prefs) -> None:
        self.ensure_dirs()
        buf = StringIO()
        self._yaml.dump(prefs.raw, buf)
        self.prefs_path.write_text(buf.getvalue())

    def add_to_list(self, key: str, item: str, top: bool = False) -> bool:
        """Add item to a prefs list (remove / replace_with). Returns False if already present."""
        from .evaluate import normalise

        if not self.prefs_path.exists():
            self.init_prefs()
        prefs = self.load_prefs()
        lst = prefs.raw.get(key)
        if lst is None:
            prefs.raw[key] = lst = []
        if any(normalise(x) == normalise(item) for x in lst):
            return False
        if top:
            lst.insert(0, item)
        else:
            lst.append(item)
        self.save_prefs(prefs)
        return True

    # pending proposal
    def load_pending(self) -> dict | None:
        if not self.pending_path.exists():
            return None
        return json.loads(self.pending_path.read_text())

    def save_pending(self, pending: dict) -> None:
        self.ensure_dirs()
        self.pending_path.write_text(json.dumps(pending, indent=2))

    # small scheduler state (e.g. which week's "not posted" notice was sent)
    def load_state(self) -> dict:
        return json.loads(self.state_path.read_text()) if self.state_path.exists() else {}

    def save_state(self, state: dict) -> None:
        self.ensure_dirs()
        self.state_path.write_text(json.dumps(state, indent=2))

    # history snapshots
    def save_snapshot(self, menu, box: str) -> Path:
        self.ensure_dirs()
        path = self.history_dir / f"{menu.week_from.isoformat()}_{menu.region}_{box}.json"
        path.write_text(json.dumps(menu.to_dict(), indent=2))
        return path

    def snapshots(self) -> list[dict]:
        if not self.history_dir.exists():
            return []
        return [json.loads(p.read_text()) for p in sorted(self.history_dir.glob("*.json"))]

    # log
    def read_log(self) -> list[dict]:
        if not self.log_path.exists():
            return []
        with self.log_path.open(newline="") as f:
            return list(csv.DictReader(f))

    def upsert_log(self, week: str, **fields) -> None:
        rows = self.read_log()
        row = next((r for r in rows if r["week"] == week), None)
        if row is None:
            row = {c: "" for c in LOG_COLUMNS}
            row["week"] = week
            rows.append(row)
        for k, v in fields.items():
            if k == "notes" and row.get("notes") and v:
                v = f"{row['notes']}; {v}"
            row[k] = v
        self.ensure_dirs()
        with self.log_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
            w.writeheader()
            w.writerows(rows)


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)
