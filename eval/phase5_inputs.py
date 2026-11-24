"""Check Phase 5 input extraction against saved reference data."""
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean

from openpyxl import load_workbook

from phase5_audit import ROOT, prompt_sources, read_json

OUT = ROOT / "output/phase5"
RAW = ROOT / "data/historical"
MW_FIELDS = ("planned_mw", "forced_major_mw", "forced_minor_mw", "others_mw")
WEATHER_FIELDS = ("temperature_2m_max_c", "temperature_2m_mean_c",
                  "relative_humidity_2m_mean_pct", "precipitation_sum_mm",
                  "wind_speed_10m_max_kmh")


def load_exchange_blocks():
    days = defaultdict(list)
    for path in sorted((RAW / "prices/raw_xlsx").glob("*.xlsx")):
        book = load_workbook(path, data_only=True, read_only=True)
        try:
            for date_cell, _, time, _, _, _, mw, price in book.active.iter_rows(
                min_row=6, max_col=8, values_only=True
            ):
                if isinstance(time, str) and re.fullmatch(r"\d\d:\d\d - \d\d:\d\d", time):
                    day = datetime.strptime(date_cell, "%d-%m-%Y").date().isoformat()
                    days[day].append((time, float(mw), float(price)))
        finally:
            book.close()
    return days


def price_reference(blocks):
    # Exports in this cohort have 96 entries per day. Preserve the pipeline's
    # positive-price selection and report excluded blocks separately.
    prices = [price for _, _, price in blocks if price > 0]
    peak = [price for time, _, price in blocks if time[:2] >= "18" and price > 0]
    return {
        "avg_mcp_rs_per_mwh": round(mean(prices), 2),
        "min_mcp_rs_per_mwh": min(prices),
        "max_mcp_rs_per_mwh": max(prices),
        "peak_hours_avg_mcp_rs_per_mwh": round(mean(peak), 2),
        "total_scheduled_volume_mwh": round(sum(mw * 0.25 for _, mw, _ in blocks), 2),
        "n_blocks": len(blocks),
    }


def main():
    exchanges = load_exchange_blocks()
    candidates = read_json(RAW / "regulatory/candidates.json")["items"]
    references = {r["source_reference"]: r for r in candidates}
    checks, review, seen_regulatory = [], [], set()

    def compare(source, day, record, field, actual, expected):
        checks.append({"source": source, "date": day, "record": record, "field": field,
                       "expected": expected, "actual": actual, "match": actual == expected})

    for path in sorted((OUT / "lightning-paired-v1").glob("*_pipeline.json")):
        result = read_json(path)
        day = result["date"]
        sources = prompt_sources(result)
        if any(price <= 0 for _, _, price in exchanges[day]):
            review.append({"date": day, "issue": "Nonpositive MCP blocks excluded by the price rule"})
        price_items = sources["price"]["items"]
        primary = next((i for i in price_items if i["headline"].startswith("Average DAM")), {})
        for field, expected in price_reference(exchanges[day]).items():
            compare("price", day, "daily summary", field, primary.get("raw_values", {}).get(field), expected)

        weather_items = sources["weather"]["items"]
        for weather_file in sorted((RAW / "weather" / day).glob("*.json")):
            expected = read_json(weather_file)
            temperature = next((i for i in weather_items if "(max " in i["headline"] and
                                i.get("raw_values", {}).get("location") == expected["location"]), {})
            for field in WEATHER_FIELDS:
                compare("weather", day, expected["location"], field,
                        temperature.get("raw_values", {}).get(field), expected[field])

        outage_items = sources["outage"]["items"]
        raw_rows = read_json(RAW / "outages" / f"{day}.json")["rows"]
        for row in raw_rows:
            if "unparsed_raw_text" in row:
                compare("outage", day, "unparsed row", "preserved",
                        any(i.get("raw_values") == row for i in outage_items), True)
                continue
            unit = f"{row['station']} unit {row['unit']}"
            group = [i for i in outage_items if i.get("raw_values") == row
                     and i["headline"].endswith("MW affected")]
            compare("outage", day, unit, "matching bulletin rows", len(group), 1)
            expected = f"{sum(row.get(field) or 0 for field in MW_FIELDS):.1f}"
            observed = group[0]["headline"].split(": ")[-1].split()[0] if group else None
            compare("outage", day, unit, "affected MW", observed, expected)
        for item in outage_items:
            if item["headline"].endswith("outage resolved"):
                review.append({"date": day, "issue": "Resolution inferred from absence in the current bulletin",
                               "item": item["headline"]})

        for item in (sources["regulatory"] or {}).get("items", []):
            ref = item["source_reference"]
            if ref in seen_regulatory:
                continue
            seen_regulatory.add(ref)
            candidate = references[ref]
            for field in ("headline", "source_timestamp"):
                compare("regulatory", day, ref, field, item[field], candidate[field])
            for field, expected in candidate["raw_values"].items():
                compare("regulatory", day, ref, field, item["raw_values"].get(field), expected)

    summary = {}
    for source in sorted({r["source"] for r in checks}):
        rows = [r for r in checks if r["source"] == source]
        summary[source] = {"records": len({(r["date"], r["record"]) for r in rows}),
                           "fields_checked": len(rows), "matches": sum(r["match"] for r in rows)}
    report = {"reference": {"price": "IEX XLSX time blocks",
                            "weather": "saved Bengaluru weather records",
                            "outage": "normalized bulletin rows",
                            "regulatory": "saved curated candidates, deduplicated by source URL"},
              "summary": summary, "checks": checks, "review": review}
    (OUT / "extraction_checks.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print("Review observations:", len(review))


if __name__ == "__main__":
    main()
