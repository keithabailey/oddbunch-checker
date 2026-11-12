from conftest import pt
from oddbunch.window import is_open

TZ = "America/Vancouver"


def test_window_edges():
    assert not is_open(pt(2026, 11, 16, 13, 59), TZ)   # Mon before 2pm
    assert is_open(pt(2026, 11, 16, 14, 0), TZ)        # Mon 2pm opens
    # BC is on permanent UTC-7 from Nov 2026 (tzdata 2026f), so 11:55pm ET is 9:55pm local.
    assert is_open(pt(2026, 11, 17, 21, 54), TZ)
    assert not is_open(pt(2026, 11, 17, 21, 55), TZ)
    assert not is_open(pt(2026, 11, 18, 10, 0), TZ)    # Wednesday
    assert not is_open(pt(2026, 11, 11, 12, 0), TZ)    # today (Wednesday)
