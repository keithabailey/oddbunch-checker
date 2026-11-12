"""Notifications: ntfy push (phone) and macOS notification centre."""

from __future__ import annotations

import os
import subprocess
import sys

import requests


def _ntfy(title: str, message: str, cfg: dict, urgent: bool) -> None:
    topic = os.environ.get("ODDBUNCH_NTFY_TOPIC") or cfg.get("ntfy_topic")
    if not topic:
        raise RuntimeError("ntfy channel enabled but no ntfy_topic set")
    server = (cfg.get("ntfy_server") or "https://ntfy.sh").rstrip("/")
    headers = {
        "Title": title.encode("ascii", "replace").decode(),
        "Priority": "high" if urgent else "default",
        "Tags": "carrot",
    }
    token = os.environ.get("ODDBUNCH_NTFY_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.post(f"{server}/{topic}", data=message.encode(), headers=headers, timeout=15)
    resp.raise_for_status()


def _macos(title: str, message: str, cfg: dict, urgent: bool) -> None:
    if sys.platform != "darwin":
        raise RuntimeError("macOS notifications only work on macOS")
    script = [
        "-e", "on run argv",
        "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
        "-e", "end run",
    ]
    subprocess.run(["osascript", *script, title, message], check=True, capture_output=True)


CHANNELS = {"ntfy": _ntfy, "macos": _macos}


def notify(title: str, message: str, cfg: dict, urgent: bool = False) -> list[str]:
    """Send on every configured channel; returns the channels that succeeded."""
    sent = []
    for name in cfg.get("channels") or ["macos"]:
        fn = CHANNELS.get(name)
        if fn is None:
            print(f"[notify] unknown channel {name!r}", file=sys.stderr)
            continue
        try:
            fn(title, message, cfg, urgent)
            sent.append(name)
        except Exception as exc:  # one channel failing must not stop the others
            print(f"[notify] {name} failed: {exc}", file=sys.stderr)
    print(f"{title}\n{message}")
    return sent
