"""
Phase 5 -- Extraction Accuracy: AUTOMATED re-derive checker.

Covers price, weather, outage (all 3 have a re-derivable raw source).
Regulatory is NOT covered here -- it's manually curated from web search with
no raw feed to auto-diff against; use extraction_accuracy_template.json /
score_extraction_accuracy.py for regulatory's 3 items instead.

How each source is re-checked:
  price    -- re-reads the same local xlsx/csv in data/historical/prices/raw_xlsx/,
              re-runs price_historical_normalize's own summarize_day(), diffs
              vs. the ingested item's raw_values.
  outage   -- re-reads the same local PDF in data/historical/outages/raw_pdfs/,
              re-runs outage_historical_pull's own extract+parse, diffs vs.
              the ingested item's fields.
  weather  -- re-fetches the SAME date/location from the Open-Meteo archive
              API (fresh network call, not from local cache), diffs vs. the
              ingested item's raw_values.

Important honesty note: price/outage re-use the SAME parsing functions as the
original pull scripts (imported, not reimplemented) -- so this catches
transcription/pipeline drift between data/historical/ and data/ingested/, and
catches a parser bug ONLY if it's nondeterministic or was fixed since the
original run. It does NOT catch a parsing bug that's deterministic and still
present in both runs (e.g. a wrong regex group). Weather is the strongest
check of the three because it's a live independent re-fetch, not a re-parse
of the same static file. For a fully independent check on price/outage, you'd
still want a handful of manual spot-checks -- consider running the manual
template (extraction_accuracy_sample.py) with --n-per-source 2 or 3 just for
those two, as a spot-check on top of this.

Usage (from project root):
    pip install requests openpyxl pdfplumber --break-system-packages
    python eval/extraction_accuracy_auto.py
    python eval/extraction_accuracy_auto.py --dates 2026-07-10 2026-07-11
"""

import argparse
import json
import math
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent / "ingestion"))

try:
    from price_historical_normalize import (
        load_rows_any, summarize_day, parse_ddmmyyyy, RAW_DIR as PRICE_RAW_DIR,
    )
except ImportError:
    load_rows_any = summarize_day = parse_ddmmyyyy = None
    PRICE_RAW_DIR = Path("data/historical/prices/raw_xlsx")

try:
    from outage_historical_pull import extract_text, strip_boilerplate, parse_karnataka_rows
except ImportError:
    extract_text = strip_boilerplate = parse_karnataka_rows = None

OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"
DAILY_VARS = [
    "temperature_2m_max", "temperature_2m_min", "temperature_2m_mean",
    "relative_humidity_2m_mean", "precipitation_sum", "wind_speed_10m_max",
]
WEATHER_FIELD_MAP = {
    "temperature_2m_max_c": "temperature_2m_max",
    "temperature_2m_min_c": "temperature_2m_min",
    "temperature_2m_mean_c": "temperature_2m_mean",
    "relative_humidity_2m_mean_pct": "relative_humidity_2m_mean",
    "precipitation_sum_mm": "precipitation_sum",
    "wind_speed_10m_max_kmh": "wind_speed_10m_max",
}
DEFAULT_LOCATIONS = {"Bengaluru": {"lat": 12.9716, "lon": 77.5946}}

PRICE_FIELDS = ["avg_mcp_rs_per_mwh", "max_mcp_rs_per_mwh", "min_mcp_rs_per_mwh",
                "peak_hours_avg_mcp_rs_per_mwh", "total_scheduled_volume_mwh"]
OUTAGE_FIELDS = ["station", "unit", "planned_mw", "forced_major_mw",
                  "forced_minor_mw", "others_mw", "start_date", "start_time"]

REL_TOL = 0.005  # 0.5% relative tolerance for float rounding drift


def values_match(extracted, truth):
    if extracted is None or truth is None:
        return extracted == truth
    if isinstance(extracted, (int, float)) and isinstance(truth, (int, float)):
        return math.isclose(extracted, truth, rel_tol=REL_TOL, abs_tol=0.01)
    return str(extracted).strip().lower() == str(truth).strip().lower()


def _load(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return None


# ---------- PRICE ----------

def recheck_price(as_of_date: str, base_dir: Path) -> dict | None:
    if load_rows_any is None:
        return None
    raw_dir = base_dir / PRICE_RAW_DIR if not str(PRICE_RAW_DIR).startswith("/") else PRICE_RAW_DIR
    files = list(raw_dir.glob("*.xlsx")) + list(raw_dir.glob("*.xls")) + list(raw_dir.glob("*.csv"))
    day_rows = []
    for f in files:
        try:
            for r in load_rows_any(f):
                if parse_ddmmyyyy(r["date"]) == as_of_date:
                    day_rows.append(r)
        except (ValueError, RuntimeError):
            continue
    if not day_rows:
        return None
    return summarize_day(day_rows)


# ---------- OUTAGE ----------

def recheck_outage(as_of_date: str, base_dir: Path) -> list[dict] | None:
    if extract_text is None:
        return None
    pdf_path = base_dir / "data/historical/outages/raw_pdfs" / f"{as_of_date}.pdf"
    if not pdf_path.exists():
        return None
    import re
    raw_text = extract_text(pdf_path.read_bytes())
    clean_text = strip_boilerplate(raw_text)
    rows = parse_karnataka_rows(clean_text)
    return [r for r in rows if "unparsed_raw_text" not in r]


# ---------- WEATHER ----------

def recheck_weather(as_of_date: str, lat: float, lon: float) -> dict | None:
    params = {
        "latitude": lat, "longitude": lon,
        "start_date": as_of_date, "end_date": as_of_date,
        "daily": ",".join(DAILY_VARS), "timezone": "Asia/Kolkata",
    }
    try:
        resp = requests.get(OPEN_METEO_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"  [weather fetch failed] {as_of_date}: {e}")
        return None
    daily = data.get("daily", {})
    if not daily.get("time"):
        return None
    return {k: daily[v][0] for k, v in WEATHER_FIELD_MAP.items() if v in daily and daily[v]}


# ---------- driver ----------

def check_source(source: str, dir_name: str, base_dir: Path, dates: list[str] | None,
                  recheck_fn, key_fields: list[str]) -> tuple[list, list]:
    src_dir = base_dir / "data" / "ingested" / dir_name
    checked, flagged = [], []
    if not src_dir.exists():
        print(f"[skip] {src_dir} not found")
        return checked, flagged

    date_files = sorted(src_dir.glob("*.json"))
    if dates:
        date_files = [f for f in date_files if f.stem in dates]

    for f in date_files:
        data = _load(f)
        if not data:
            continue
        as_of_date = data.get("as_of_date", f.stem)
        items = data.get("items", data.get("rows", []))

        if source == "weather":
            for it in items:
                rv = it.get("raw_values", it)
                lat = rv.get("latitude") or DEFAULT_LOCATIONS["Bengaluru"]["lat"]
                lon = rv.get("longitude") or DEFAULT_LOCATIONS["Bengaluru"]["lon"]
                truth = recheck_weather(as_of_date, lat, lon)
                if truth is None:
                    continue
                for field in key_fields:
                    if field not in rv:
                        continue
                    checked.append(1)
                    if not values_match(rv[field], truth.get(field)):
                        flagged.append({"source": source, "date": as_of_date,
                                         "item_id": it.get("item_id"), "field": field,
                                         "extracted": rv[field], "recomputed": truth.get(field)})

        elif source == "price":
            truth = recheck_price(as_of_date, base_dir)
            if truth is None:
                continue
            for it in items:
                rv = it.get("raw_values", it)
                for field in key_fields:
                    if field not in rv:
                        continue
                    checked.append(1)
                    if not values_match(rv[field], truth.get(field)):
                        flagged.append({"source": source, "date": as_of_date,
                                         "item_id": it.get("item_id"), "field": field,
                                         "extracted": rv[field], "recomputed": truth.get(field)})

        elif source == "outage":
            truth_rows = recheck_outage(as_of_date, base_dir)
            if truth_rows is None:
                continue
            for it in items:
                rv = it.get("raw_values", it)
                station = rv.get("station")
                unit = rv.get("unit")
                # Match on station + unit together -- a station can have multiple
                # units/outages listed in the same day's PDF, so station alone
                # is not a unique key and grabs the wrong row.
                candidates = [r for r in truth_rows if r.get("station") == station]
                match = next((r for r in candidates if str(r.get("unit")) == str(unit)), None)
                if match is None and len(candidates) == 1:
                    # Only one row for this station and unit didn't match exactly
                    # (e.g. minor formatting difference) -- still worth comparing,
                    # flagged separately below with a note rather than silently picked.
                    match = candidates[0]
                    match["_unit_mismatch"] = True
                if match is None:
                    flagged.append({"source": source, "date": as_of_date,
                                     "item_id": it.get("item_id"), "field": "station+unit",
                                     "extracted": f"{station} unit {unit}",
                                     "recomputed": f"NOT FOUND ({len(candidates)} station matches, "
                                                    f"none matched unit)"})
                    checked.append(1)
                    continue
                if match.get("_unit_mismatch"):
                    flagged.append({"source": source, "date": as_of_date,
                                     "item_id": it.get("item_id"), "field": "unit",
                                     "extracted": unit, "recomputed": match.get("unit"),
                                     "note": "only 1 station match but unit differed -- check manually"})
                    checked.append(1)
                for field in key_fields:
                    if field == "unit":
                        continue  # already checked above as part of the match key
                    if field not in rv:
                        continue
                    checked.append(1)
                    if not values_match(rv[field], match.get(field)):
                        flagged.append({"source": source, "date": as_of_date,
                                         "item_id": it.get("item_id"), "field": field,
                                         "extracted": rv[field], "recomputed": match.get(field)})

    return checked, flagged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", nargs="*", default=None,
                     help="Restrict to specific YYYY-MM-DD dates (default: all)")
    ap.add_argument("--base-dir", type=Path, default=Path("."))
    args = ap.parse_args()

    sources = [
        ("price", "prices", PRICE_FIELDS),
        ("weather", "weather", list(WEATHER_FIELD_MAP.keys())),
        ("outage", "outages", OUTAGE_FIELDS),
    ]

    report = {}
    print("Automated extraction accuracy (price/weather/outage)")
    print("-" * 60)
    total_checked, total_flagged = 0, []

    for source, dir_name, fields in sources:
        checked, flagged = check_source(source, dir_name, args.base_dir, args.dates,
                                          None, fields)
        n_checked = len(checked)
        n_flagged = len(flagged)
        pct = round(100 * (n_checked - n_flagged) / n_checked, 1) if n_checked else None
        print(f"  {source:10s}  {n_checked - n_flagged}/{n_checked} correct"
              + (f"  ({pct}%)" if pct is not None else "  (no re-derivable data found)"))
        report[source] = {"checked": n_checked, "correct": n_checked - n_flagged,
                           "accuracy_pct": pct, "flagged": flagged}
        total_checked += n_checked
        total_flagged.extend(flagged)

    print("-" * 60)
    overall_pct = round(100 * (total_checked - len(total_flagged)) / total_checked, 1) if total_checked else None
    print(f"  {'OVERALL':10s}  {total_checked - len(total_flagged)}/{total_checked} correct"
          + (f"  ({overall_pct}%)" if overall_pct is not None else "  (nothing checked)"))
    print("\n  regulatory: NOT auto-checkable -- use extraction_accuracy_template.json "
          "(only 8 fields / 3 items, manual)")

    if total_flagged:
        print(f"\n{len(total_flagged)} flagged mismatch(es):")
        for f in total_flagged[:30]:
            print(f"  - {f['source']} {f['date']} {f.get('item_id')} :: {f['field']} "
                  f"extracted={f['extracted']!r} recomputed={f['recomputed']!r}")
        if len(total_flagged) > 30:
            print(f"  ... and {len(total_flagged) - 30} more, see report JSON")

    out_path = Path("eval/extraction_accuracy_auto_report.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "summary": {"overall_checked": total_checked,
                     "overall_correct": total_checked - len(total_flagged),
                     "overall_accuracy_pct": overall_pct},
        "per_source": report,
    }, indent=2))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
