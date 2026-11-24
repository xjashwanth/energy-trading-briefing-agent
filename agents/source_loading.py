"""Shared date-aware source loading for synthesis and verification."""
import json
from datetime import date
from pathlib import Path


def load_json(path):
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict) or not isinstance(value.get("items"), list):
            raise ValueError("expected an object with an items array")
        return value
    except (ValueError, UnicodeError) as exc:
        print(f"WARNING: could not parse {path}: {exc}")
        return None


def gather_sources(as_of_date, base_dir=Path(".")):
    date.fromisoformat(as_of_date)
    root = base_dir / "data/ingested"
    sources = {}
    for key, folder in (("price", "prices"), ("weather", "weather"),
                        ("outage", "outages"), ("cross_reference", "cross_reference")):
        data = load_json(root / folder / f"{as_of_date}.json")
        sources[key] = data if data and data.get("as_of_date") == as_of_date else None
    # A retrospectively curated window can contain earlier publications. Filter
    # each publication by date; never feed future or undated filings to the model.
    items = {}
    found = False
    for path in sorted((root / "regulatory").glob("*.json")):
        data = load_json(path)
        if data is None:
            continue
        found = True
        for item in data["items"]:
            stamp = str(item.get("source_timestamp", ""))[:10]
            try:
                published = date.fromisoformat(stamp)
            except ValueError:
                continue
            if published <= date.fromisoformat(as_of_date):
                key = (item.get("source_reference"), stamp, item.get("headline"))
                items[key] = item
    sources["regulatory"] = ({"source": "regulatory", "region": "Karnataka",
        "as_of_date": as_of_date, "items": list(items.values()),
        "selection_note": "Filtered by publication date; retrospective collection is not proof of availability at market open."}
        if found else None)
    return sources
