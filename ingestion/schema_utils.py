"""
Phase 2 — Shared helpers for the 4 ingestion agents.

Every ingestion agent must emit an object matching schema.json:
    { source, region, as_of_date, generated_at, items: [ ... ] }

This module centralizes the boring, error-prone parts (item_id formatting,
timestamp formatting, materiality clamping, and a schema conformance check)
so each source agent can focus on its own domain logic and all 4 stay
consistent with each other and with Phase 4's verifier agent expectations.
"""

import json
import time
from pathlib import Path

SCHEMA_REGION = "Karnataka"  # schema.json pins region to this const at the top level


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def clamp_materiality(score: int) -> int:
    """schema.json requires materiality_score in [1,5]."""
    return max(1, min(5, int(round(score))))


def make_item_id(source: str, as_of_date: str, seq: int) -> str:
    return f"{source}-{as_of_date}-{seq:03d}"


def build_item(
    item_id: str,
    headline: str,
    detail: str,
    materiality_score: int,
    source_reference: str,
    source_timestamp: str,
    delta_from_previous=None,
    grid_zone=None,
    raw_values=None,
) -> dict:
    return {
        "item_id": item_id,
        "headline": headline,
        "detail": detail,
        "materiality_score": clamp_materiality(materiality_score),
        "source_reference": source_reference,
        "source_timestamp": source_timestamp,
        "delta_from_previous": delta_from_previous,
        "grid_zone": grid_zone,
        "raw_values": raw_values or {},
    }


def build_output(source: str, as_of_date: str, items: list) -> dict:
    return {
        "source": source,
        "region": SCHEMA_REGION,
        "as_of_date": as_of_date,
        "generated_at": now_iso(),
        "items": items,
    }


REQUIRED_ITEM_FIELDS = [
    "item_id", "headline", "detail", "materiality_score",
    "source_reference", "source_timestamp", "delta_from_previous",
]
REQUIRED_TOP_FIELDS = ["source", "region", "as_of_date", "generated_at", "items"]


def validate_output(obj: dict) -> list:
    """Lightweight conformance check against schema.json (no jsonschema dependency
    required — Colab-friendly). Returns a list of problems; empty list = OK.
    Phase 4's verifier agent should still be the authority on claim-level
    grounding; this only checks structural shape."""
    problems = []
    for f in REQUIRED_TOP_FIELDS:
        if f not in obj:
            problems.append(f"missing top-level field: {f}")
    if obj.get("region") != SCHEMA_REGION:
        problems.append(f"region must be '{SCHEMA_REGION}', got {obj.get('region')!r}")
    for i, item in enumerate(obj.get("items", [])):
        for f in REQUIRED_ITEM_FIELDS:
            if f not in item:
                problems.append(f"item[{i}] missing field: {f}")
        ms = item.get("materiality_score")
        if not isinstance(ms, int) or not (1 <= ms <= 5):
            problems.append(f"item[{i}] materiality_score out of range: {ms!r}")
    return problems


def write_output(obj: dict, out_dir: Path, filename: str = None) -> Path:
    problems = validate_output(obj)
    if problems:
        raise ValueError(f"Refusing to write non-conformant output: {problems}")
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = filename or f"{obj['as_of_date']}.json"
    out_path = out_dir / filename
    out_path.write_text(json.dumps(obj, indent=2))
    return out_path
