"""
Phase 3 -- Cross-Referencing Agent

Input:  today's 4 ingested outputs (data/ingested/{prices,weather,outages}/<date>.json
        + data/ingested/regulatory/<window>.json) + yesterday's cross-reference output
        (for delta-only reporting)
Output: data/ingested/cross_reference/<date>.json  (schema.json-shaped)

Rule-based, no LLM call -- correlation detection is pure comparison logic
across already-computed materiality scores and grid zones, so there's no
fabrication risk here. This agent never invents a link between sources; it
only flags a correlation when BOTH sides independently already cleared a
materiality threshold in their own ingestion agent.

Per the execution plan: "compare outputs across sources for the same grid
zone/date and flag compounded risks (e.g., outage + heatwave together)."

Known limitation carried over from weather_agent.py: only Bengaluru is
weather-tracked by default, so zone-matched correlations (outage + heatwave
in the SAME zone) are only possible when the outage is also in/near
Bengaluru. For outages elsewhere (Raichur, Kaiga, Ballari), the agent still
flags same-day co-occurrence but explicitly labels it "region-wide,
zone-match not confirmable" rather than implying a zone match that isn't
actually verified.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "ingestion"))
from schema_utils import build_item, build_output, make_item_id, write_output

HEATWAVE_THRESHOLD = 4       # weather item materiality (Heatwave watch or worse)
OUTAGE_THRESHOLD = 3         # outage item materiality (>=300MW band)
PRICE_SPIKE_THRESHOLD = 4    # price item materiality (>=10% day-over-day move)


def _load(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8-sig"))


def find_compounded_risks(price_out, weather_out, outage_out, as_of_date):
    items = []
    seq = 1

    weather_items = (weather_out or {}).get("items", [])
    outage_items = (outage_out or {}).get("items", [])
    price_items = (price_out or {}).get("items", [])

    heat_items = [w for w in weather_items if w["materiality_score"] >= HEATWAVE_THRESHOLD
                  and "heatwave" in w.get("headline", "").lower()]
    major_outages = [o for o in outage_items if o["materiality_score"] >= OUTAGE_THRESHOLD]
    price_spikes = [p for p in price_items if p["materiality_score"] >= PRICE_SPIKE_THRESHOLD
                    and "average" in p["headline"].lower()]

    # --- Rule 1: outage + heatwave, same grid_zone (highest-confidence flag) ---
    for heat in heat_items:
        for outage in major_outages:
            if heat.get("grid_zone") and heat.get("grid_zone") == outage.get("grid_zone"):
                items.append(build_item(
                    item_id=make_item_id("cross_reference", as_of_date, seq),
                    headline=f"Compounded risk in {heat['grid_zone']}: heatwave + major generation outage",
                    detail=(f"{heat['headline']} coincides with {outage['headline']} in the same zone "
                             f"({heat['grid_zone']}) on {as_of_date}. Peak demand pressure from heat "
                             f"combined with reduced generation capacity in the same zone is a higher-"
                             f"confidence price-risk signal than either factor alone."),
                    materiality_score=5,
                    source_reference=f"{heat['source_reference']} + {outage['source_reference']}",
                    source_timestamp=outage["source_timestamp"],
                    delta_from_previous=None,
                    grid_zone=heat["grid_zone"],
                    raw_values={"risk_type": "outage_plus_heatwave_same_zone",
                                "linked_item_ids": [heat["item_id"], outage["item_id"]]},
                ))
                seq += 1

    # --- Rule 2: outage + heatwave, same day, zone not confirmable ---
    if heat_items and major_outages:
        zone_matched_outage_ids = {
            o["item_id"] for heat in heat_items for o in major_outages
            if heat.get("grid_zone") == o.get("grid_zone") and heat.get("grid_zone")
        }
        unmatched_outages = [o for o in major_outages if o["item_id"] not in zone_matched_outage_ids]
        if unmatched_outages:
            worst_heat = max(heat_items, key=lambda h: h["materiality_score"])
            outage_list = "; ".join(o["headline"] for o in unmatched_outages)
            items.append(build_item(
                item_id=make_item_id("cross_reference", as_of_date, seq),
                headline="Same-day heatwave + generation outage(s) -- zone match not confirmable",
                detail=(f"{worst_heat['headline']} on {as_of_date}, same day as: {outage_list}. "
                         f"Weather is only tracked for {worst_heat.get('grid_zone','the tracked location')}; "
                         f"these outages are in a different/unconfirmed zone, so this is a region-wide "
                         f"co-occurrence flag, not a confirmed zone-level compounded risk."),
                materiality_score=3,
                source_reference=f"{worst_heat['source_reference']} + multiple outage records",
                source_timestamp=as_of_date + "T00:00:00Z",
                delta_from_previous=None,
                grid_zone=None,
                raw_values={"risk_type": "outage_plus_heatwave_unconfirmed_zone",
                            "linked_item_ids": [worst_heat["item_id"]] + [o["item_id"] for o in unmatched_outages]},
            ))
            seq += 1

    # --- Rule 3: price spike + major outage, same day ---
    if price_spikes and major_outages:
        total_outage_mw = sum(o["raw_values"].get("planned_mw", 0) + o["raw_values"].get("forced_major_mw", 0)
                               + o["raw_values"].get("forced_minor_mw", 0) + o["raw_values"].get("others_mw", 0)
                               for o in major_outages if "raw_values" in o)
        outage_list = "; ".join(o["headline"] for o in major_outages)
        items.append(build_item(
            item_id=make_item_id("cross_reference", as_of_date, seq),
            headline=f"Price move coincides with {len(major_outages)} major outage(s) same day",
            detail=(f"{price_spikes[0]['headline']} on {as_of_date}, same day as: {outage_list} "
                     f"(~{total_outage_mw:.0f} MW total affected capacity). Reduced supply is a plausible "
                     f"contributing driver, alongside demand-side factors -- not asserted as the sole cause."),
            materiality_score=4,
            source_reference=f"{price_spikes[0]['source_reference']} + outage records",
            source_timestamp=as_of_date + "T00:00:00Z",
            delta_from_previous=None,
            grid_zone=None,
            raw_values={"risk_type": "price_spike_plus_outage",
                        "linked_item_ids": [price_spikes[0]["item_id"]] + [o["item_id"] for o in major_outages]},
        ))
        seq += 1

    # --- Rule 4: price spike + heatwave, same day (demand-driven pressure) ---
    if price_spikes and heat_items:
        worst_heat = max(heat_items, key=lambda h: h["materiality_score"])
        items.append(build_item(
            item_id=make_item_id("cross_reference", as_of_date, seq),
            headline="Price move coincides with heatwave -- demand-driven pressure plausible",
            detail=(f"{price_spikes[0]['headline']} on {as_of_date}, same day as {worst_heat['headline']}. "
                     f"Elevated cooling demand is a plausible contributing driver, alongside supply-side "
                     f"factors -- not asserted as the sole cause."),
            materiality_score=4,
            source_reference=f"{price_spikes[0]['source_reference']} + {worst_heat['source_reference']}",
            source_timestamp=as_of_date + "T00:00:00Z",
            delta_from_previous=None,
            grid_zone=worst_heat.get("grid_zone"),
            raw_values={"risk_type": "price_spike_plus_heatwave",
                        "linked_item_ids": [price_spikes[0]["item_id"], worst_heat["item_id"]]},
        ))
        seq += 1

    return items


def diff_vs_yesterday(items, prev_output):
    """Delta-only reporting: mark which flags are new vs. a repeat of yesterday's
    same risk_type + linked zone, so the synthesis agent doesn't re-announce an
    already-known compounded risk as if it were breaking news."""
    prev_risk_types = set()
    if prev_output:
        for it in prev_output.get("items", []):
            prev_risk_types.add((it["raw_values"].get("risk_type"), it.get("grid_zone")))
    for it in items:
        key = (it["raw_values"].get("risk_type"), it.get("grid_zone"))
        it["delta_from_previous"] = "New today" if key not in prev_risk_types else "Persisting from prior day"
    return items


def process_day(as_of_date: str, base_dir: Path = Path(".")) -> dict:
    price_out = _load(base_dir / "data/ingested/prices" / f"{as_of_date}.json")
    weather_out = _load(base_dir / "data/ingested/weather" / f"{as_of_date}.json")
    outage_out = _load(base_dir / "data/ingested/outages" / f"{as_of_date}.json")

    items = find_compounded_risks(price_out, weather_out, outage_out, as_of_date)

    prev_output = None
    cr_dir = base_dir / "data/ingested/cross_reference"
    if cr_dir.exists():
        prior_files = sorted([f for f in cr_dir.glob("*.json") if f.stem < as_of_date])
        if prior_files:
            prev_output = _load(prior_files[-1])
    items = diff_vs_yesterday(items, prev_output)

    return build_output("cross_reference", as_of_date, items)


def main():
    prices_dir = Path("data/ingested/prices")
    if not prices_dir.exists():
        print("No ingested price data found -- run the 4 Phase 2 agents first.")
        return
    dates = sorted(p.stem for p in prices_dir.glob("*.json"))
    out_dir = Path("data/ingested/cross_reference")
    for as_of_date in dates:
        out = process_day(as_of_date)
        out_path = write_output(out, out_dir)
        print(f"{as_of_date}: {len(out['items'])} compounded-risk flag(s) -> {out_path}")


if __name__ == "__main__":
    main()
