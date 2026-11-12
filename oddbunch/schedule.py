"""Write the launchd user agent that runs `oddbunch tick`."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

AGENTS_DIR = Path("~/Library/LaunchAgents").expanduser()
# One agent running `oddbunch tick`, which decides what is due:
#   RunAtLoad             at login / boot (covers a Mac that was shut down); exits at once
#                         outside the Monday-Tuesday window
#   StartCalendarInterval Mon 14:30 check, Mon 19:30 and Tue 18:00 reminders; a time missed
#                         while the Mac was asleep fires once on wake
# No polling interval: a failed check retries inside the same run until the window closes,
# and launchd never starts a second copy while one is running.
TIMES = [
    {"Weekday": 1, "Hour": 14, "Minute": 30},
    {"Weekday": 1, "Hour": 19, "Minute": 30},
    {"Weekday": 2, "Hour": 18, "Minute": 0},
]
LEGACY_JOBS = ("check", "remind")  # v0.1 installed one agent per job


def plist_for(label_prefix: str, logs_dir: Path) -> dict:
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"}
    if os.environ.get("ODDBUNCH_HOME"):
        env["ODDBUNCH_HOME"] = os.environ["ODDBUNCH_HOME"]
    return {
        "Label": f"{label_prefix}.tick",
        "ProgramArguments": [sys.executable, "-m", "oddbunch", "tick"],
        "RunAtLoad": True,
        "StartCalendarInterval": TIMES,
        "StandardOutPath": str(logs_dir / "tick.log"),
        "StandardErrorPath": str(logs_dir / "tick.err.log"),
        "EnvironmentVariables": env,
    }


def install(logs_dir: Path, label_prefix: str = "local.oddbunch", load: bool = False) -> Path:
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    for job in LEGACY_JOBS:
        old = AGENTS_DIR / f"{label_prefix}.{job}.plist"
        if old.exists():
            subprocess.run(["launchctl", "bootout", domain, str(old)], capture_output=True)
            old.unlink()
            print(f"Removed old agent {old.name}")
    data = plist_for(label_prefix, logs_dir)
    path = AGENTS_DIR / f"{data['Label']}.plist"
    with path.open("wb") as f:
        plistlib.dump(data, f)
    if load:
        subprocess.run(["launchctl", "bootout", domain, str(path)], capture_output=True)
        subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True)
    return path
