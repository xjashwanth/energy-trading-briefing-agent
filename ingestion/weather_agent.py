"""
Phase 2 -- Weather Ingestion Agent

Input:  data/historical/weather/<date>/<location>.json
        (output of ingestion/weather_historical_pull.py -- Open-Meteo daily
        aggregates: temp max/min/mean, humidity, precip, wind)
Output: data/ingested/weather/<date>.json  (schema.json-conformant)

Per the execution plan, this agent's job is to translate raw numbers into
demand/supply impact language -- NOT to just re-report Celsius and mm.
Thresholds below are heuristic starting points for Karnataka's Southern
grid load centers; recalibrate against the hand-labeled test set once you
have real distribution of values across the 90-day pull (Phase 5).

Rule-based now, LLM-ready later: swap `translate_temp_impact` etc. for a
prompt-templated Claude API call if you want richer/more nuanced impact
narratives -- keep the *thresholds and materiality* deterministic even then,
since Phase 4's verifier agent needs numeric claims to be traceable, not
model-judgment calls that can drift day to day.

Known gap (see scope-note.md / data-source-access-note.md): only Bengaluru
is tracked by default. Wind-resource-relevant zones (Chitradurga, Gadag) and
hydro-catchment zones aren't covered -- the wind item below is intentionally
conservative/context-only rather than claiming Karnataka-wide generation
impact from a single-city wind reading.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from schema_utils import build_item, build_output, make_item_id, write_output

LOCATION_TO_ZONE = {
    "bengaluru": "Bengaluru Urban",
    "hubli_dharwad": "Hubli-Dharwad",
    "mangaluru": "Mangaluru / Dakshina Kannada",
}


def zone_for(location_filename: str) -> str:
    key = location_filename.lower().replace(".json", "")
    return LOCATION_TO_ZONE.get(key, location_filename)


def temp_impact(temp_max_c: float):
    """Returns (label, materiality). Heat drives AC/cooling demand up, which
    is the dominant summer demand driver for Southern-region peak pricing."""
    if temp_max_c is None:
        return None, 1
    if temp_max_c >= 40:
        return "Severe heatwave conditions -- sharp peak cooling-demand rise likely", 5
    if temp_max_c >= 38:
        return "Heatwave watch -- elevated peak cooling-demand likely", 4
    if temp_max_c >= 35:
        return "Hot conditions -- above-normal cooling demand likely", 3
    if temp_max_c >= 30:
        return "Warm conditions -- modest cooling-demand pressure", 2
    return "Mild conditions -- limited cooling-demand impact", 1


def precip_impact(precip_mm: float):
    if precip_mm is None:
        return None, 1
    if precip_mm >= 50:
        return "Heavy rainfall -- likely hydro inflow boost and dampened evening cooling demand", 4
    if precip_mm >= 15:
        return "Moderate rainfall -- some hydro inflow benefit, mild demand dampening", 2
    return None, 1  # negligible rain isn't worth an item


def wind_impact(wind_kmh: float):
    if wind_kmh is None:
        return None, 1
    if wind_kmh >= 30:
        return ("Elevated wind speeds at Bengaluru reading -- context only; Karnataka's "
                 "wind generation is concentrated in Chitradurga/Gadag, not directly "
                 "measured here, so treat as a directional signal, not a zone-specific one"), 2
    return None, 1


def process_day(day_dir: Path, prev_day_dir: Path | None) -> dict:
    files = sorted(day_dir.glob("*.json"))
    if not files:
        return None
    as_of_date = None
    items = []
    seq = 1

    for f in files:
        rec = json.loads(f.read_text())
        as_of_date = rec["date"]
        zone = zone_for(f.name)
        ts = rec.get("fetched_at", as_of_date + "T00:00:00Z")
        source_ref = f"local:{f}"

        prev_rec = None
        if prev_day_dir and (prev_day_dir / f.name).exists():
            prev_rec = json.loads((prev_day_dir / f.name).read_text())

        # --- temperature / demand item ---
        label, score = temp_impact(rec.get("temperature_2m_max_c"))
        if label:
            delta_text = None
            if prev_rec and prev_rec.get("temperature_2m_max_c") is not None:
                d = rec["temperature_2m_max_c"] - prev_rec["temperature_2m_max_c"]
                delta_text = f"{d:+.1f}C vs prior day max"
                if abs(d) >= 3:
                    score = min(5, score + 1)  # sharp day-over-day jump bumps materiality
            items.append(build_item(
                item_id=make_item_id("weather", as_of_date, seq),
                headline=f"{zone}: {label} (max {rec.get('temperature_2m_max_c')}C)",
                detail=(f"Max temperature {rec.get('temperature_2m_max_c')}C, "
                         f"mean {rec.get('temperature_2m_mean_c')}C, "
                         f"humidity {rec.get('relative_humidity_2m_mean_pct')}%."),
                materiality_score=score,
                source_reference=source_ref,
                source_timestamp=ts,
                delta_from_previous=delta_text,
                grid_zone=zone,
                raw_values=rec,
            ))
            seq += 1

        # --- precipitation / hydro-supply item (only if notable) ---
        label, score = precip_impact(rec.get("precipitation_sum_mm"))
        if label:
            items.append(build_item(
                item_id=make_item_id("weather", as_of_date, seq),
                headline=f"{zone}: {label} ({rec.get('precipitation_sum_mm')}mm)",
                detail=f"Precipitation sum {rec.get('precipitation_sum_mm')}mm for {as_of_date}.",
                materiality_score=score,
                source_reference=source_ref,
                source_timestamp=ts,
                delta_from_previous=None,
                grid_zone=zone,
                raw_values=rec,
            ))
            seq += 1

        # --- wind item (only if notable, context-only per known gap) ---
        label, score = wind_impact(rec.get("wind_speed_10m_max_kmh"))
        if label:
            items.append(build_item(
                item_id=make_item_id("weather", as_of_date, seq),
                headline=f"{zone}: elevated wind speeds ({rec.get('wind_speed_10m_max_kmh')} km/h max)",
                detail=label,
                materiality_score=score,
                source_reference=source_ref,
                source_timestamp=ts,
                delta_from_previous=None,
                grid_zone=zone,
                raw_values=rec,
            ))
            seq += 1

    if as_of_date is None:
        return None
    return build_output("weather", as_of_date, items)


def main():
    in_root = Path("data/historical/weather")
    out_dir = Path("data/ingested/weather")
    if not in_root.exists():
        print(f"No weather data found in {in_root}/. Run weather_historical_pull.py first.")
        return
    day_dirs = sorted([d for d in in_root.iterdir() if d.is_dir()])
    prev_dir = None
    for d in day_dirs:
        out = process_day(d, prev_dir)
        if out:
            out_path = write_output(out, out_dir)
            print(f"{out['as_of_date']}: {len(out['items'])} weather item(s) -> {out_path}")
        prev_dir = d


if __name__ == "__main__":
    main()
