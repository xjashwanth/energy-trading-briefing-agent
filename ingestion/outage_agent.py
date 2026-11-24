"""
Phase 2 -- Outage Ingestion Agent

Input:  data/historical/outages/<date>.json
        (output of ingestion/outage_historical_pull.py -- parsed Karnataka
        rows from the CEA daily maintenance report, including any rows the
        Phase 1 parser couldn't confidently parse, kept as `unparsed_raw_text`)
Output: data/ingested/outages/<date>.json  (schema.json-conformant)

Logic (rule-based):
  - Each successfully-parsed row -> one item with plant name, MW affected,
    approximate grid zone (station-name lookup table below), and duration.
  - Unparsed rows are NOT dropped and NOT guessed at -- they become a
    low-confidence item flagged for manual review, per the "no hallucinated
    fallback" principle already established in the Phase 1 script.
  - Day-over-day diffing (by station+unit) produces delta_from_previous:
    "New", "Ongoing", or a capacity-change note -- and separately reports
    any outages present yesterday but absent today as "Resolved".
  - Materiality scales with total affected MW (planned + forced_major +
    forced_minor + others), since a 30MW unit trip is a very different
    signal than an 800MW one for wholesale price impact.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from schema_utils import build_item, build_output, make_item_id, write_output

# Approximate station -> grid zone mapping. Extend as new stations appear in
# the CEA report; unmapped stations still get an item, just with zone=None
# rather than a guessed location (per the no-hallucination principle).
STATION_TO_ZONE = {
    "KAIGA": "Uttara Kannada",
    "BELLARY TPS": "Ballari",
    "RAICHUR TPS": "Raichur",
    "TORANGALLU TPS": "Ballari",
}


def zone_for_station(station: str):
    if not station:
        return None
    key = station.strip().upper()
    for k, v in STATION_TO_ZONE.items():
        if k in key:
            return v
    return None


def total_mw(row: dict) -> float:
    return sum(row.get(k, 0.0) or 0.0 for k in
               ("planned_mw", "forced_major_mw", "forced_minor_mw", "others_mw"))


def materiality_from_mw(mw: float) -> int:
    if mw < 100:
        return 1
    if mw < 300:
        return 2
    if mw < 600:
        return 3
    if mw < 1000:
        return 4
    return 5


def row_key(row: dict):
    return (row.get("station", "").strip().upper(), row.get("unit", "").strip().upper())


def process_day(day_path: Path, prev_rows_by_key: dict) -> tuple[dict, dict]:
    raw = json.loads(day_path.read_text())
    as_of_date = raw["as_of_date"]
    source_ref = raw.get("source_pdf", f"local:{day_path}")
    ts = raw.get("generated_at", as_of_date + "T00:00:00Z")
    rows = raw.get("rows", [])

    items = []
    seq = 1
    current_rows_by_key = {}

    for row in rows:
        if "unparsed_raw_text" in row:
            items.append(build_item(
                item_id=make_item_id("outage", as_of_date, seq),
                headline="Unparsed outage bulletin entry -- manual review required",
                detail=("Phase 1 PDF extraction could not confidently parse this row "
                         "from the CEA daily report. Raw text preserved verbatim below "
                         "rather than guessing at values."),
                materiality_score=1,
                source_reference=source_ref,
                source_timestamp=ts,
                delta_from_previous=None,
                grid_zone=None,
                raw_values={"unparsed_raw_text": row["unparsed_raw_text"]},
            ))
            seq += 1
            continue

        key = row_key(row)
        current_rows_by_key[key] = row
        mw = total_mw(row)
        zone = zone_for_station(row.get("station"))
        ongoing = row.get("end_date") in (None, "")

        delta_text = None
        if key not in prev_rows_by_key:
            delta_text = "New outage since prior day's report"
        else:
            prev_mw = total_mw(prev_rows_by_key[key])
            if abs(prev_mw - mw) > 0.5:
                delta_text = f"Affected capacity changed {prev_mw:.1f} -> {mw:.1f} MW vs prior day"
            else:
                delta_text = "Ongoing, no material change vs prior day"

        duration_text = row.get("start_date", "?")
        if row.get("end_date"):
            duration_text += f" to {row.get('end_date')}"
        else:
            duration_text += " (ongoing, no end date reported)"

        items.append(build_item(
            item_id=make_item_id("outage", as_of_date, seq),
            headline=f"{row.get('station','Unknown station')} unit {row.get('unit','?')}: {mw:.1f} MW affected",
            detail=(f"Outage at {row.get('station')} unit {row.get('unit')}, "
                     f"{mw:.1f} MW total affected capacity "
                     f"(planned {row.get('planned_mw',0)}, forced-major {row.get('forced_major_mw',0)}, "
                     f"forced-minor {row.get('forced_minor_mw',0)}, others {row.get('others_mw',0)}). "
                     f"Period: {duration_text}. Reason: {row.get('reason','not stated')}."),
            materiality_score=materiality_from_mw(mw),
            source_reference=source_ref,
            source_timestamp=ts,
            delta_from_previous=delta_text,
            grid_zone=zone,
            raw_values=row,
        ))
        seq += 1

    # Resolved outages: present yesterday, absent today
    for key, prev_row in prev_rows_by_key.items():
        if key not in current_rows_by_key:
            zone = zone_for_station(prev_row.get("station"))
            items.append(build_item(
                item_id=make_item_id("outage", as_of_date, seq),
                headline=f"{prev_row.get('station','Unknown station')} unit {prev_row.get('unit','?')}: outage resolved",
                detail=(f"Outage at {prev_row.get('station')} unit {prev_row.get('unit')} "
                         f"present in the prior day's report is no longer listed -- "
                         f"treated as resolved/returned to service."),
                materiality_score=2,
                source_reference=source_ref,
                source_timestamp=ts,
                delta_from_previous="Resolved since prior day's report",
                grid_zone=zone,
                raw_values=prev_row,
            ))
            seq += 1

    return build_output("outage", as_of_date, items), current_rows_by_key


def main():
    in_dir = Path("data/historical/outages")
    out_dir = Path("data/ingested/outages")
    files = sorted(in_dir.glob("*.json")) if in_dir.exists() else []
    if not files:
        print(f"No outage data found in {in_dir}/. Run outage_historical_pull.py first.")
        return

    prev_rows_by_key = {}
    for f in files:
        out, prev_rows_by_key = process_day(f, prev_rows_by_key)
        out_path = write_output(out, out_dir)
        print(f"{out['as_of_date']}: {len(out['items'])} outage item(s) -> {out_path}")


if __name__ == "__main__":
    main()
