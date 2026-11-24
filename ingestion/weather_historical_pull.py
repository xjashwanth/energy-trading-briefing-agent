"""
Phase 1 — Historical weather collection for the Energy Trading Briefing Agent.

Source: Open-Meteo Historical Weather API (https://open-meteo.com/en/docs/historical-weather-api)
Why Open-Meteo instead of OpenWeatherMap: no API key/card required, straightforward
free historical archive, good enough for this project's needs (temp, humidity,
precipitation, wind — the drivers of demand/supply impact language in Phase 2).

Usage (in Colab, after mounting Drive and cd-ing into the project root):
    python ingestion/weather_historical_pull.py

Output: one JSON file per day at data/historical/weather/<date>.json
"""

import json
import time
from datetime import date, timedelta
from pathlib import Path

import requests

# ---- Config ----
LOCATIONS = {
    "Bengaluru": {"lat": 12.9716, "lon": 77.5946},
    # Add more Karnataka load centers here later if you expand zone coverage, e.g.:
    # "Hubli-Dharwad": {"lat": 15.3647, "lon": 75.1240},
    # "Mangaluru": {"lat": 12.9141, "lon": 74.8560},
}

START_DATE = date.today() - timedelta(days=90)   # ~3 months back, per Phase 1 spec
END_DATE = date.today() - timedelta(days=1)       # yesterday (archive API needs a completed day)

OUTPUT_DIR = Path("data/historical/weather")
BASE_URL = "https://archive-api.open-meteo.com/v1/archive"

DAILY_VARS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "relative_humidity_2m_mean",
    "precipitation_sum",
    "wind_speed_10m_max",
]


def fetch_range(lat: float, lon: float, start: date, end: date) -> dict:
    """Single API call covering the whole date range for one location — Open-Meteo
    supports date ranges natively, so we don't need to loop per day."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": ",".join(DAILY_VARS),
        "timezone": "Asia/Kolkata",
    }
    resp = requests.get(BASE_URL, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def split_and_save(location_name: str, data: dict) -> int:
    """Open-Meteo returns one blob for the whole range; split into per-day files
    so Phase 2's weather agent can load 'just today's data' the same way it will
    for the other 3 sources."""
    daily = data["daily"]
    dates = daily["time"]
    count = 0
    for i, day_str in enumerate(dates):
        day_record = {
            "location": location_name,
            "latitude": data["latitude"],
            "longitude": data["longitude"],
            "date": day_str,
            "temperature_2m_max_c": daily["temperature_2m_max"][i],
            "temperature_2m_min_c": daily["temperature_2m_min"][i],
            "temperature_2m_mean_c": daily["temperature_2m_mean"][i],
            "relative_humidity_2m_mean_pct": daily["relative_humidity_2m_mean"][i],
            "precipitation_sum_mm": daily["precipitation_sum"][i],
            "wind_speed_10m_max_kmh": daily["wind_speed_10m_max"][i],
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        day_dir = OUTPUT_DIR / day_str
        day_dir.mkdir(parents=True, exist_ok=True)
        out_path = day_dir / f"{location_name.lower().replace(' ', '_')}.json"
        out_path.write_text(json.dumps(day_record, indent=2))
        count += 1
    return count


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    total_files = 0

    for name, coords in LOCATIONS.items():
        print(f"Fetching {name} ({START_DATE} to {END_DATE})...")
        try:
            data = fetch_range(coords["lat"], coords["lon"], START_DATE, END_DATE)
        except requests.RequestException as e:
            print(f"  FAILED for {name}: {e}")
            continue

        n = split_and_save(name, data)
        total_files += n
        print(f"  Saved {n} daily files.")

    print(f"\nDone. {total_files} total daily weather files written under {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
