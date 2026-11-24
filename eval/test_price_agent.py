"""
Phase 2 -- Hand-labeled test set for the price agent's scoring logic.

IMPORTANT SCOPE NOTE: these are hand-labeled synthetic examples that pin
down the *threshold logic* (materiality_from_pct_change, peak premium,
intraday spread) against values a human would label the same way. They are
NOT a substitute for the Phase 5 hand-labeled test set, which per the
execution plan needs 10-15 examples drawn from real pulled historical days
(input -> correct expected output) once Phase 1's price_historical_normalize.py
has actually been run against real IEX exports. Use this file to unit-test
the agent's logic now; extend/replace with real-day examples for Phase 5.

Run: pip install pytest --break-system-packages && pytest eval/test_price_agent.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "ingestion"))
from price_agent import materiality_from_pct_change, peak_premium_pct, intraday_spread_pct

# --- materiality_from_pct_change: 15 hand-labeled cases ---
CASES = [
    (None, 2, "no prior day on record -> moderate default, not a spike claim"),
    (0.0, 1, "flat price -> not newsworthy"),
    (0.5, 1, "trivial move"),
    (1.9, 1, "just under 2% threshold"),
    (2.0, 2, "exactly at 2% threshold"),
    (3.4, 2, "small move"),
    (4.9, 2, "just under 5% threshold"),
    (5.0, 3, "exactly at 5% threshold"),
    (7.2, 3, "moderate move"),
    (9.9, 3, "just under 10% threshold"),
    (10.0, 4, "exactly at 10% threshold"),
    (15.5, 4, "large move"),
    (19.9, 4, "just under 20% threshold"),
    (20.0, 5, "exactly at 20% threshold"),
    (-35.0, 5, "large negative move -- magnitude matters, not direction"),
]


def test_materiality_from_pct_change():
    for pct, expected, label in CASES:
        got = materiality_from_pct_change(pct)
        assert got == expected, f"{label}: pct={pct} expected {expected}, got {got}"


def test_peak_premium_pct():
    summary = {"avg_mcp_rs_per_mwh": 4000, "peak_hours_avg_mcp_rs_per_mwh": 4400}
    assert peak_premium_pct(summary) == 10.0
    assert peak_premium_pct({"avg_mcp_rs_per_mwh": None, "peak_hours_avg_mcp_rs_per_mwh": 4400}) is None


def test_intraday_spread_pct():
    summary = {"avg_mcp_rs_per_mwh": 4000, "max_mcp_rs_per_mwh": 6000, "min_mcp_rs_per_mwh": 2000}
    assert intraday_spread_pct(summary) == 100.0


if __name__ == "__main__":
    test_materiality_from_pct_change()
    test_peak_premium_pct()
    test_intraday_spread_pct()
    print(f"price agent: {len(CASES)} hand-labeled threshold cases passed, plus 2 formula checks.")
