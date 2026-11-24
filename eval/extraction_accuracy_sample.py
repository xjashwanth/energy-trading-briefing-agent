"""
Phase 5 -- Extraction Accuracy: sampling script.

Metric def: "did the ingestion agent correctly pull the right value from the
raw source (PDF/API/xlsx) into the item's fields?" This is NOT about the LLM
(ingestion agents 1-4 are non-LLM extractors) -- it's a data-pipeline QA check.

What this does:
  1. Walks data/ingested/{price,weather,outage,regulatory}/*.json
  2. Stratified-samples N items per source (default 8/source = ~32 total,
     enough for a 15-20 day window's worth of spot checks without making you
     hand-check every single row)
  3. Writes eval/extraction_accuracy_template.json -- one row per sampled
     item, with the extracted fields + a pointer back to source_reference
     (URL / local PDF / xlsx path) so you can open the ORIGINAL and compare.
  4. You fill in "ground_truth_value" and "correct" (true/false) per row by
     checking against the original source. Leave "correct" null if unsure.

Usage:
    python eval/extraction_accuracy_sample.py --n-per-source 8
    python eval/extraction_accuracy_sample.py --n-per-source 8 --seed 42
"""

import argparse
import json
import random
from pathlib import Path

SOURCES = ["price", "weather", "outage", "regulatory"]

# Directory names on disk don't all match the singular "source" label used
# inside each item's JSON -- prices/outages are plural dirs, weather/regulatory
# aren't. Keep the item-level "source" tag singular (matches schema.json) but
# look in the right folder.
SOURCE_DIR_NAMES = {
    "price": "prices",
    "weather": "weather",
    "outage": "outages",
    "regulatory": "regulatory",
}

# Which raw_values keys matter most per source -- these are what actually get
# checked against the original PDF/API/xlsx (headline/detail are derived text,
# less useful to hand-verify than the underlying numbers).
KEY_FIELDS = {
    "price": ["avg_mcp_rs_per_mwh", "max_mcp_rs_per_mwh", "min_mcp_rs_per_mwh",
              "peak_hours_avg_mcp_rs_per_mwh", "total_scheduled_volume_mwh"],
    "weather": ["temperature_2m_max_c", "temperature_2m_min_c", "temperature_2m_mean_c",
                "relative_humidity_2m_mean_pct", "precipitation_sum_mm", "wind_speed_10m_max_kmh"],
    "outage": ["station", "unit", "planned_mw", "forced_major_mw", "forced_minor_mw",
               "others_mw", "start_date", "start_time"],
    "regulatory": ["filing_type", "commission", "effective_from", "effective_to"],
}


def load_all_items(base_dir: Path, source: str) -> list[dict]:
    """Flatten every item across every date file for a source into one list,
    tagging each with its source file for traceability."""
    src_dir = base_dir / "data" / "ingested" / SOURCE_DIR_NAMES.get(source, source)
    items = []
    if not src_dir.exists():
        print(f"  [skip] {src_dir} not found")
        return items

    for f in sorted(src_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            print(f"  [skip] {f} -- malformed JSON")
            continue

        # price/outage historical-pull scripts use "rows" instead of the
        # common "items" schema -- normalize both shapes here.
        raw_items = data.get("items")
        if raw_items is None:
            raw_items = data.get("rows", [])

        for it in raw_items:
            items.append({
                "source": source,
                "source_file": str(f),
                "source_reference": (
                    it.get("source_reference")
                    or data.get("source_pdf")
                    or data.get("collection_method")
                    or "see source_file"
                ),
                "as_of_date": data.get("as_of_date", data.get("date", "unknown")),
                "item": it,
            })
    return items


def build_row(sampled: dict) -> dict:
    source = sampled["source"]
    it = sampled["item"]
    raw_values = it.get("raw_values", it)  # outage rows are flat, no raw_values wrapper

    fields_to_check = {}
    for key in KEY_FIELDS.get(source, []):
        if key in raw_values:
            fields_to_check[key] = {
                "extracted_value": raw_values[key],
                "ground_truth_value": None,   # <-- fill in by checking original source
                "correct": None,              # <-- true / false / null (unsure)
            }

    return {
        "source": source,
        "as_of_date": sampled["as_of_date"],
        "item_id": it.get("item_id", it.get("station", "unknown")),
        "headline": it.get("headline"),
        "source_reference": sampled["source_reference"],
        "source_file": sampled["source_file"],
        "fields_to_check": fields_to_check,
        "reviewer_notes": "",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-per-source", type=int, default=8)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--base-dir", type=Path, default=Path("."))
    ap.add_argument("--out", type=Path, default=Path("eval/extraction_accuracy_template.json"))
    args = ap.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    rows = []
    for source in SOURCES:
        pool = load_all_items(args.base_dir, source)
        if not pool:
            continue
        # Only keep items that actually have checkable numeric/key fields --
        # skip e.g. regulatory items with no raw_values at all.
        pool = [p for p in pool if any(k in (p["item"].get("raw_values", p["item"]) or {})
                                        for k in KEY_FIELDS.get(source, []))]
        n = min(args.n_per_source, len(pool))
        sample = random.sample(pool, n) if n > 0 else []
        print(f"{source}: sampled {n} of {len(pool)} eligible items")
        rows.extend(build_row(s) for s in sample)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "instructions": (
            "For each row, open source_reference (or source_file for locally-saved "
            "PDFs/xlsx) and check each field under fields_to_check against the ORIGINAL "
            "raw source. Fill ground_truth_value with what the source actually says, and "
            "set correct = true/false. Leave correct = null if you can't verify. Save this "
            "file, then run: python eval/score_extraction_accuracy.py"
        ),
        "rows": rows,
    }, indent=2))

    total_fields = sum(len(r["fields_to_check"]) for r in rows)
    print(f"\nWrote {len(rows)} items ({total_fields} checkable fields) -> {args.out}")
    if not rows:
        print("No items sampled -- check that data/ingested/<source>/ exists and has files.")


if __name__ == "__main__":
    main()
