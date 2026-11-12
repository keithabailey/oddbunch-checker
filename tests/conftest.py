import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from oddbunch.store import Store

FIXTURES = Path(__file__).parent / "fixtures"
PT = ZoneInfo("America/Vancouver")

SEED_PREFS = """\
email: someone@example.com
region: west
box: large-box
timezone: America/Vancouver
remove:
  - Gooseberries
  - Seafood Mushrooms
replace_with:   # ranked fallbacks
  - Asian Pears
  - Apples
  - Satsumas
aliases: {}
notify:
  channels: []
check:
  retries: 4
  retry_minutes: 20
"""


def pt(*args) -> dt.datetime:
    return dt.datetime(*args, tzinfo=PT)


@pytest.fixture
def fixture_html() -> str:
    return (FIXTURES / "large_box_2026-11-11.html").read_text()


@pytest.fixture
def store(tmp_path, monkeypatch) -> Store:
    monkeypatch.setenv("ODDBUNCH_HOME", str(tmp_path))
    s = Store(tmp_path)
    s.ensure_dirs()
    s.prefs_path.write_text(SEED_PREFS)
    return s
