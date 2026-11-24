"""
Phase 1 — Historical price collection for the Energy Trading Briefing Agent.

Source: IEX (Indian Energy Exchange) Day-Ahead Market Snapshot
https://www.iexindia.com/market-data/day-ahead-market/market-snapshot

Why manual export instead of scraping: the Market Snapshot page is a JS-rendered
app with no documented public API, and IEX's own disclaimer restricts reuse of the
published data ("Only correct quotation with attribution is permitted"). Rather
than reverse-engineer an undocumented endpoint against a live production site,
this script normalizes the Excel (.xlsx) files you export yourself using the
page's "Export" button — a one-time bootstrap task, consistent with Phase 1
being historical/offline collection.

How to get the raw files:
  1. Go to https://www.iexindia.com/market-data/day-ahead-market/market-snapshot
  2. Set Interval to "15-Min-Block", set Delivery Period to the date/range you want
  3. Click "Export" — save the .xlsx file
  4. Repeat for your ~90-day window (the page may support multi-day ranges —
     check "Delivery Period" options; if it's single-day only, batch a few weeks
     at a time rather than 90 individual clicks)
  5. Drop all exported .xlsx files into data/historical/prices/raw_xlsx/

This script does NOT hit the network at all — it only reads local files — so it
has no scraping/ToS concerns. CSV files are also supported if you get a mix of
formats (auto-detected by extension) — just put them in the same folder.

Usage (in Colab, after mounting Drive and cd-ing into the project root):
    pip install openpyxl
    python ingestion/price_historical_normalize.py

Output: one JSON file per day at data/historical/prices/<date>.json
"""

import csv
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

TIME_BLOCK_PATTERN = re.compile(r'^\d{2}:\d{2}\s*-\s*\d{2}:\d{2}$')

try:
    import openpyxl
except ImportError:
    openpyxl = None  # only needed if .xlsx files are present

RAW_DIR = Path("data/historical/prices/raw_xlsx")
OUT_DIR = Path("data/historical/prices")

# Column names as seen in IEX's export; kept flexible (case-insensitive, fuzzy
# match) since export headers can vary slightly by page version.
COLUMN_ALIASES = {
    "date": ["date"],
    "hour": ["hour"],
    "time_block": ["time block", "timeblock"],
    "purchase_bid_mw": ["purchase bid (mw)", "purchase bid"],
    "sell_bid_mw": ["sell bid (mw)", "sell bid"],
    "mcv_mw": ["mcv (mw)", "mcv"],
    "final_scheduled_volume_mw": ["final scheduled volume (mw)", "final scheduled volume"],
    "mcp_rs_per_mwh": ["mcp (rs/mwh)", "mcp (rs./mwh)", "mcp"],
}

PEAK_HOURS = set(range(18, 24))  # 18:00-23:59, per IEX's own "Peak" definition


def find_column(headers: list[str], aliases: list[str]) -> str | None:
    lower_headers = {h.lower().strip().rstrip("*").strip(): h for h in headers}
    for alias in aliases:
        if alias in lower_headers:
            return lower_headers[alias]
    return None


def load_csv_rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        headers = next(reader, [])
        colmap = {key: find_column(headers, aliases) for key, aliases in COLUMN_ALIASES.items()}

        if not (colmap["date"] and colmap["mcp_rs_per_mwh"]):
            raise ValueError(f"{path.name}: could not find Date/MCP columns. "
                              f"Found headers: {headers}")

        rows = []
        for raw_row in reader:
            d = _row_to_dict(colmap, headers, raw_row)
            if d is not None:
                rows.append(d)
        return rows


def load_rows_any(path: Path) -> list[dict]:
    """Dispatch to the right loader based on file extension."""
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xls"):
        return load_excel_rows(path)
    elif suffix == ".csv":
        return load_csv_rows(path)
    else:
        raise ValueError(f"{path.name}: unsupported file type '{suffix}'")


def _row_to_dict(colmap: dict, headers: list[str], values: list) -> dict | None:
    """Shared record-building logic for both CSV and Excel rows."""
    raw = dict(zip(headers, values))
    try:
        time_block = str(raw[colmap["time_block"]]).strip() if colmap["time_block"] else ""
        # IEX exports append a summary block below the 96 real rows (Total/Max/Min/Avg).
        # Those rows share the same Date/MCP columns but have labels like "Avg (MW)"
        # instead of a real "HH:MM - HH:MM" time block — reject them here so they
        # don't silently corrupt the daily average.
        if not TIME_BLOCK_PATTERN.match(time_block):
            return None
        return {
            "date": str(raw[colmap["date"]]).strip(),
            "time_block": time_block,
            "purchase_bid_mw": float(raw[colmap["purchase_bid_mw"]] or 0),
            "sell_bid_mw": float(raw[colmap["sell_bid_mw"]] or 0),
            "mcv_mw": float(raw[colmap["mcv_mw"]] or 0),
            "final_scheduled_volume_mw": float(raw[colmap["final_scheduled_volume_mw"]] or 0),
            "mcp_rs_per_mwh": float(raw[colmap["mcp_rs_per_mwh"]] or 0),
        }
    except (ValueError, KeyError, TypeError):
        return None  # skip blank/malformed rows


def load_excel_rows(path: Path) -> list[dict]:
    """IEX's export often has a title/banner row (or a merged-cell header) above
    the real column headers. Scan the first several rows for the one that looks
    like a header (contains recognizable column names) instead of assuming row 1."""
    if openpyxl is None:
        raise RuntimeError("openpyxl is not installed — run: pip install openpyxl")

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    all_rows = list(ws.iter_rows(values_only=True))

    header_row_idx = None
    colmap = None
    for i, row in enumerate(all_rows[:10]):
        headers = [str(c) if c is not None else "" for c in row]
        candidate_colmap = {key: find_column(headers, aliases) for key, aliases in COLUMN_ALIASES.items()}
        # Require at minimum date + mcp columns found to accept this as the header row
        if candidate_colmap["date"] and candidate_colmap["mcp_rs_per_mwh"]:
            header_row_idx = i
            colmap = candidate_colmap
            headers_found = headers
            break

    if header_row_idx is None:
        raise ValueError(f"{path.name}: couldn't locate a header row with Date/MCP columns "
                          f"in the first 10 rows — check the export format.")

    rows = []
    for row in all_rows[header_row_idx + 1:]:
        d = _row_to_dict(colmap, headers_found, list(row))
        if d is not None:
            rows.append(d)
    return rows


def parse_ddmmyyyy(s: str) -> str:
    """IEX dates come as DD-MM-YYYY; normalize to ISO YYYY-MM-DD."""
    return datetime.strptime(s, "%d-%m-%Y").date().isoformat()


def block_hour(time_block: str) -> int | None:
    """'18:00 - 18:15' -> 18"""
    try:
        return int(time_block.split(":")[0])
    except (ValueError, IndexError):
        return None


def summarize_day(rows: list[dict]) -> dict:
    mcps = [r["mcp_rs_per_mwh"] for r in rows if r["mcp_rs_per_mwh"] > 0]
    peak_mcps = [r["mcp_rs_per_mwh"] for r in rows
                 if r["mcp_rs_per_mwh"] > 0 and block_hour(r["time_block"]) in PEAK_HOURS]
    total_volume = sum(r["final_scheduled_volume_mw"] for r in rows) / 4  # 15-min blocks -> MWh

    return {
        "avg_mcp_rs_per_mwh": round(sum(mcps) / len(mcps), 2) if mcps else None,
        "max_mcp_rs_per_mwh": round(max(mcps), 2) if mcps else None,
        "min_mcp_rs_per_mwh": round(min(mcps), 2) if mcps else None,
        "peak_hours_avg_mcp_rs_per_mwh": round(sum(peak_mcps) / len(peak_mcps), 2) if peak_mcps else None,
        "total_scheduled_volume_mwh": round(total_volume, 2),
        "n_blocks": len(rows),
    }


def main():
    input_files = sorted(
        list(RAW_DIR.glob("*.xlsx")) + list(RAW_DIR.glob("*.xls")) + list(RAW_DIR.glob("*.csv"))
    ) if RAW_DIR.exists() else []

    if not input_files:
        print(f"No .xlsx/.xls/.csv files found in {RAW_DIR}/. Export them from IEX first — "
              f"see the instructions in this script's docstring — then re-run.")
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Group rows by date across all files (in case one file covers multiple days,
    # or multiple files cover the same day from overlapping exports)
    by_date: dict[str, list[dict]] = {}

    for file_path in input_files:
        try:
            rows = load_rows_any(file_path)
        except (ValueError, RuntimeError) as e:
            print(f"SKIPPED {file_path.name}: {e}")
            continue

        for r in rows:
            try:
                iso_date = parse_ddmmyyyy(r["date"])
            except ValueError:
                continue
            by_date.setdefault(iso_date, []).append(r)

    dates_sorted = sorted(by_date.keys())
    prev_summary = None

    for iso_date in dates_sorted:
        rows = by_date[iso_date]
        summary = summarize_day(rows)

        delta = None
        if prev_summary and prev_summary.get("avg_mcp_rs_per_mwh") and summary.get("avg_mcp_rs_per_mwh"):
            change = summary["avg_mcp_rs_per_mwh"] - prev_summary["avg_mcp_rs_per_mwh"]
            pct = (change / prev_summary["avg_mcp_rs_per_mwh"]) * 100
            delta = {
                "avg_mcp_change_rs_per_mwh": round(change, 2),
                "avg_mcp_change_pct": round(pct, 1),
            }

        record = {
            "source": "price",
            "region": "Southern (IEX area price, used as Karnataka proxy)",
            "as_of_date": iso_date,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "summary": summary,
            "delta_from_previous_day": delta,
            "blocks": rows,
        }
        (OUT_DIR / f"{iso_date}.json").write_text(json.dumps(record, indent=2))
        print(f"{iso_date}: avg MCP {summary['avg_mcp_rs_per_mwh']} Rs/MWh "
              f"({summary['n_blocks']} blocks)"
              + (f", delta {delta['avg_mcp_change_pct']:+.1f}%" if delta else ""))

        prev_summary = summary

    print(f"\nDone. {len(dates_sorted)} days written to {OUT_DIR}/")
    if not dates_sorted:
        print("No valid rows parsed — check that your CSVs match IEX's export format.")


if __name__ == "__main__":
    main()
