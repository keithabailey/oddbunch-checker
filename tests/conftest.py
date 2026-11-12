import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from oddbunch.store import Store

FIXTURES = Path(__file__).parent / "fixtures"
PT = ZoneInfo("America/Vancouver")

SEED_PREFS = """\
name: Test Person
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


def form_components() -> list[dict]:
    return json.loads((FIXTURES / "powr_form_2026-11-11.json").read_text())


@pytest.fixture(autouse=True)
def offline_form(monkeypatch):
    """Never hit POWR in tests: serve the saved form configuration instead."""
    from oddbunch import form

    monkeypatch.setattr(form, "fetch_components", lambda *a, **k: form_components())


SSR_FIXTURE = FIXTURES / "powr_ssr_2026-11-12.html"
SUBS_PAGE_SNIPPET = ('<html><body><h1>Substitutions</h1><powr-form-builder u="1476dcbf_1758738507" '
                     'data-design-mode="false"></powr-form-builder></body></html>')


@pytest.fixture(autouse=True)
def offline_health(monkeypatch):
    """The scheduler's weekly health check is tested on its own; elsewhere it is a no-op."""
    from oddbunch import health

    calls = []
    monkeypatch.setattr(health, "weekly", lambda *a, **k: calls.append((a, k)) or "health: stubbed")
    return calls
