"""
Phase 2 -- Price Ingestion Agent

Input:  data/historical/prices/<date>.json
        (output of ingestion/price_historical_normalize.py -- has `summary`
        and `delta_from_previous_day` already computed from IEX blocks)
Output: data/ingested/prices/<date>.json  (schema.json-conformant)

Logic (rule-based, no LLM call -- swap in an LLM later for the `detail` prose
if you want richer narrative; the numbers/materiality below are deterministic
either way and should stay that way per Phase 4's grounding requirement):

  1. Daily average price item -- always emitted. Materiality scales with the
     size of the day-over-day % change (a 1% move isn't news; a 20% move is).
  2. Peak-hour premium item -- emitted only if peak-hour avg MCP diverges
     materially from the day's overall avg (peak bidding behavior).
  3. Intraday volatility item -- emitted only if max/min spread is unusually
     wide relative to the average (matters for block-level bidding strategy).

Region caveat: Karnataka has no standalone DAM price, so all price items
carry `raw_values.region_note` explaining the Southern-region-as-proxy
framing (per scope-note.md) rather than silently presenting it as
Karnataka-specific.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from schema_utils import build_item, build_output, make_item_id, write_output

REGION_NOTE = ("Karnataka has no standalone day-ahead price; this uses the IEX "
               "Southern-region area clearing price as a proxy (see scope-note.md).")


def materiality_from_pct_change(pct):
    if pct is None:
        return 2  # first day on record, nothing to compare -- moderate default, not a spike claim
    p = abs(pct)
    if p < 2:
        return 1
    if p < 5:
        return 2
    if p < 10:
        return 3
    if p < 20:
        return 4
    return 5


def peak_premium_pct(summary):
    avg = summary.get("avg_mcp_rs_per_mwh")
    peak = summary.get("peak_hours_avg_mcp_rs_per_mwh")
    if not avg or not peak:
        return None
    return round((peak - avg) / avg * 100, 1)


def intraday_spread_pct(summary):
    avg = summary.get("avg_mcp_rs_per_mwh")
    mx = summary.get("max_mcp_rs_per_mwh")
    mn = summary.get("min_mcp_rs_per_mwh")
    if not avg or mx is None or mn is None:
        return None
    return round((mx - mn) / avg * 100, 1)


def process_day(normalized_path):
    raw = json.loads(normalized_path.read_text())
    as_of_date = raw["as_of_date"]
    summary = raw.get("summary", {})
    delta = raw.get("delta_from_previous_day")
    source_ref = f"local:{normalized_path}"  # traces back to the exported IEX xlsx/csv, per Phase1 script
    ts = raw.get("generated_at", as_of_date + "T00:00:00Z")

    items = []
    seq = 1

    # --- Item 1: daily average price ---
    pct = delta.get("avg_mcp_change_pct") if delta else None
    headline = f"Average DAM (Southern proxy) MCP: Rs {summary.get('avg_mcp_rs_per_mwh')}/MWh"
    detail = (
        f"Day-ahead market average clearing price for {as_of_date} was "
        f"Rs {summary.get('avg_mcp_rs_per_mwh')}/MWh across {summary.get('n_blocks')} "
        f"15-min blocks (range Rs {summary.get('min_mcp_rs_per_mwh')}-"
        f"{summary.get('max_mcp_rs_per_mwh')}/MWh)."
    )
    delta_text = None
    if delta:
        delta_text = (f"{delta.get('avg_mcp_change_rs_per_mwh'):+} Rs/MWh "
                       f"({delta.get('avg_mcp_change_pct'):+.1f}%) vs prior day")
    items.append(build_item(
        item_id=make_item_id("price", as_of_date, seq),
        headline=headline,
        detail=detail,
        materiality_score=materiality_from_pct_change(pct),
        source_reference=source_ref,
        source_timestamp=ts,
        delta_from_previous=delta_text,
        grid_zone=None,
        raw_values={**summary, "region_note": REGION_NOTE},
    ))
    seq += 1

    # --- Item 2: peak-hour premium (only if notable) ---
    premium = peak_premium_pct(summary)
    if premium is not None and abs(premium) >= 5:
        items.append(build_item(
            item_id=make_item_id("price", as_of_date, seq),
            headline=f"Peak-hour (18:00-24:00) MCP premium: {premium:+.1f}% vs daily avg",
            detail=(f"Peak-hour average MCP was Rs {summary.get('peak_hours_avg_mcp_rs_per_mwh')}/MWh "
                     f"vs the day's overall average of Rs {summary.get('avg_mcp_rs_per_mwh')}/MWh, "
                     f"a {premium:+.1f}% divergence."),
            materiality_score=3 if abs(premium) < 15 else 4,
            source_reference=source_ref,
            source_timestamp=ts,
            delta_from_previous=None,
            grid_zone=None,
            raw_values={**summary, "region_note": REGION_NOTE},
        ))
        seq += 1

    # --- Item 3: intraday volatility (only if wide spread) ---
    spread = intraday_spread_pct(summary)
    if spread is not None and spread >= 40:
        items.append(build_item(
            item_id=make_item_id("price", as_of_date, seq),
            headline=f"Wide intraday MCP spread: {spread:.0f}% of daily average",
            detail=(f"Max-min spread of Rs {summary.get('max_mcp_rs_per_mwh')} - "
                     f"Rs {summary.get('min_mcp_rs_per_mwh')} = "
                     f"{spread:.0f}% of the daily average, indicating high intraday volatility."),
            materiality_score=3 if spread < 80 else 4,
            source_reference=source_ref,
            source_timestamp=ts,
            delta_from_previous=None,
            grid_zone=None,
            raw_values={**summary, "region_note": REGION_NOTE},
        ))
        seq += 1

    return build_output("price", as_of_date, items)


def main():
    in_dir = Path("data/historical/prices")
    out_dir = Path("data/ingested/prices")
    files = sorted(in_dir.glob("*.json")) if in_dir.exists() else []
    if not files:
        print(f"No normalized price files found in {in_dir}/. Run price_historical_normalize.py first.")
        return
    for f in files:
        out = process_day(f)
        out_path = write_output(out, out_dir)
        print(f"{out['as_of_date']}: {len(out['items'])} price item(s) -> {out_path}")


if __name__ == "__main__":
    main()
