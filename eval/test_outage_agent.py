"""
Phase 2 -- Hand-labeled test set for the outage agent: MW->materiality
thresholds, station->zone mapping, and day-over-day diff logic (new /
ongoing / resolved / capacity-change). Synthetic rows shaped exactly like
outage_historical_pull.py's parsed output. Same scope caveat as the other
test files: extend with real CEA-report days for Phase 5.

Run: pytest eval/test_outage_agent.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "ingestion"))
from outage_agent import materiality_from_mw, zone_for_station, total_mw, process_day
import json
import tempfile

# --- materiality_from_mw: 10 hand-labeled cases ---
MW_CASES = [
    (0.0, 1), (50.0, 1), (99.9, 1),
    (100.0, 2), (250.0, 2), (299.9, 2),
    (300.0, 3), (599.9, 3),
    (600.0, 4), (999.9, 4),
    (1000.0, 5), (1500.0, 5),
]

ZONE_CASES = [
    ("KAIGA", "Uttara Kannada"),
    ("BELLARY TPS", "Ballari"),
    ("RAICHUR TPS", "Raichur"),
    ("TORANGALLU TPS", "Ballari"),
    ("SOME NEW UNMAPPED PLANT", None),
]


def test_materiality_from_mw():
    for mw, expected in MW_CASES:
        got = materiality_from_mw(mw)
        assert got == expected, f"mw={mw}: expected {expected}, got {got}"


def test_zone_for_station():
    for station, expected in ZONE_CASES:
        assert zone_for_station(station) == expected


def test_total_mw():
    row = {"planned_mw": 100.0, "forced_major_mw": 50.0, "forced_minor_mw": 0.0, "others_mw": 0.0}
    assert total_mw(row) == 150.0


def _write(path, rows, as_of_date):
    path.write_text(json.dumps({
        "source": "outage", "region": "Karnataka", "as_of_date": as_of_date,
        "generated_at": as_of_date + "T00:00:00Z",
        "source_pdf": f"https://example.test/{as_of_date}.pdf",
        "rows": rows,
    }))


def test_day_over_day_diff_new_ongoing_resolved():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        day1 = tmp / "2026-07-17.json"
        day2 = tmp / "2026-07-18.json"

        _write(day1, [
            {"station": "RAICHUR TPS", "unit": "1", "planned_mw": 210.0, "forced_major_mw": 0.0,
             "forced_minor_mw": 0.0, "others_mw": 0.0, "start_date": "15/07/2026", "start_time": "06:00",
             "end_date": None, "end_time": None, "reason": "Annual overhaul"},
        ], "2026-07-17")

        _write(day2, [
            {"station": "RAICHUR TPS", "unit": "1", "planned_mw": 210.0, "forced_major_mw": 0.0,
             "forced_minor_mw": 0.0, "others_mw": 0.0, "start_date": "15/07/2026", "start_time": "06:00",
             "end_date": None, "end_time": None, "reason": "Annual overhaul"},
            {"station": "KAIGA", "unit": "3", "planned_mw": 0.0, "forced_major_mw": 220.0,
             "forced_minor_mw": 0.0, "others_mw": 0.0, "start_date": "18/07/2026", "start_time": "02:00",
             "end_date": None, "end_time": None, "reason": "Forced outage - tube leakage"},
        ], "2026-07-18")

        out1, rows1 = process_day(day1, {})
        assert len(out1["items"]) == 1
        assert out1["items"][0]["delta_from_previous"] == "New outage since prior day's report"

        out2, rows2 = process_day(day2, rows1)
        headlines = {i["headline"]: i for i in out2["items"]}
        raichur = [i for i in out2["items"] if "RAICHUR" in i["headline"]][0]
        kaiga = [i for i in out2["items"] if "KAIGA" in i["headline"]][0]
        assert raichur["delta_from_previous"] == "Ongoing, no material change vs prior day"
        assert kaiga["delta_from_previous"] == "New outage since prior day's report"
        assert kaiga["grid_zone"] == "Uttara Kannada"
        assert kaiga["materiality_score"] == 2  # 220 MW falls in the [100,300) band

        # day 3: Raichur resolved
        day3 = tmp / "2026-07-19.json"
        _write(day3, [], "2026-07-19")
        out3, rows3 = process_day(day3, rows2)
        resolved = [i for i in out3["items"] if "resolved" in i["headline"].lower()]
        assert len(resolved) == 2  # both prior rows now absent
        stations_resolved = {r["raw_values"]["station"] for r in resolved}
        assert stations_resolved == {"RAICHUR TPS", "KAIGA"}


def test_unparsed_row_preserved_not_dropped():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        day1 = tmp / "2026-07-20.json"
        _write(day1, [{"unparsed_raw_text": "Karnataka GARBLED ROW TEXT 12.34"}], "2026-07-20")
        out, _ = process_day(day1, {})
        assert len(out["items"]) == 1
        assert out["items"][0]["materiality_score"] == 1
        assert "manual review" in out["items"][0]["headline"].lower()
        assert out["items"][0]["raw_values"]["unparsed_raw_text"] == "Karnataka GARBLED ROW TEXT 12.34"


if __name__ == "__main__":
    test_materiality_from_mw()
    test_zone_for_station()
    test_total_mw()
    test_day_over_day_diff_new_ongoing_resolved()
    test_unparsed_row_preserved_not_dropped()
    n = len(MW_CASES) + len(ZONE_CASES) + 3
    print(f"outage agent: {n}+ hand-labeled/scenario cases passed.")
