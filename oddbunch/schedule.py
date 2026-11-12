"""Write launchd user agents for the Monday check and the reminders."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

AGENTS_DIR = Path("~/Library/LaunchAgents").expanduser()
JOBS = {
    "check": [{"Weekday": 1, "Hour": 14, "Minute": 30}],
    "remind": [{"Weekday": 1, "Hour": 19, "Minute": 30}, {"Weekday": 2, "Hour": 18, "Minute": 0}],
}


def plist_for(job: str, label_prefix: str, logs_dir: Path) -> dict:
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"}
    if os.environ.get("ODDBUNCH_HOME"):
        env["ODDBUNCH_HOME"] = os.environ["ODDBUNCH_HOME"]
    return {
        "Label": f"{label_prefix}.{job}",
        "ProgramArguments": [sys.executable, "-m", "oddbunch", job],
        "StartCalendarInterval": JOBS[job],
        "StandardOutPath": str(logs_dir / f"{job}.log"),
        "StandardErrorPath": str(logs_dir / f"{job}.err.log"),
        "EnvironmentVariables": env,
    }


def install(logs_dir: Path, label_prefix: str = "local.oddbunch", load: bool = False) -> list[Path]:
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for job in JOBS:
        data = plist_for(job, label_prefix, logs_dir)
        path = AGENTS_DIR / f"{data['Label']}.plist"
        with path.open("wb") as f:
            plistlib.dump(data, f)
        paths.append(path)
        if load:
            domain = f"gui/{os.getuid()}"
            subprocess.run(["launchctl", "bootout", domain, str(path)], capture_output=True)
            subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True)
    return paths
