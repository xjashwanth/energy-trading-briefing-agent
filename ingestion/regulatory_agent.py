"""
Phase 2 -- Regulatory Ingestion Agent

Input:  a candidate-items JSON file in the same shape as
        2026-04-19_to_2026-07-17.json (i.e. output of the manual
        web-search-and-curate workflow described in
        regulatory_collection_notes.md -- KERC's own site blocks scraping,
        so there is no automated raw feed to ingest here; a human curates
        candidates weekly/bi-weekly, and THIS agent applies a consistent,
        repeatable relevance/materiality pass on top of that curation).
Output: data/ingested/regulatory/<window_start>_to_<window_end>.json
        (schema.json-conformant)

Per the execution plan: "filter for bid-relevant items only; drop
routine/non-material filings." This agent does that with a keyword-based
wholesale-relevance classifier rather than trusting the curator's
materiality_score verbatim, so scoring is consistent across
weeks/curators. Dropped items are NOT silently discarded -- they're logged
to `dropped_items` in the output's raw metadata (visible if you inspect the
run, e.g. for a false-negative audit) even though they don't appear in
`items`.

Rule-based now, LLM-ready later: swap `classify_relevance` for a Claude API
call with a fixed prompt template if keyword matching proves too brittle on
real filings -- keep the drop/keep DECISION auditable either way (Phase 4's
verifier agent and Phase 5's precision metric both depend on being able to
explain why an item was kept or dropped).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from schema_utils import build_item, build_output, make_item_id, write_output

WHOLESALE_KEYWORDS = [
    "generation", "merit order", "power purchase agreement", "ppa",
    "open access", "banking", "wheeling", "cross subsidy surcharge",
    "must run", "curtailment", "grid code", "scheduling",
    "deviation settlement", "dsm", "wholesale", "day-ahead", "dam",
    "bulk supply", "transmission",
]
RETAIL_ONLY_KEYWORDS = [
    "true-up", "true up", "retail tariff", "distribution code", "metering",
    "billing", "consumer", "connection standard",
]
ROUTINE_KEYWORDS = [
    "staff appointment", "administrative circular", "rti notice",
    "office order", "holiday notice",
]
# Filings still in a pre-decision stage aren't yet a market signal, regardless
# of subject matter -- a hearing notice about generation tariffs is not the
# same market event as the resulting order. Cap materiality until it resolves
# to an actual order/circular.
PROCEDURAL_STAGE_KEYWORDS = [
    "hearing", "public notice", "draft amendment", "draft regulation",
    "consultation", "invites comments", "notice announcing",
]


def classify_relevance(item: dict) -> tuple[int, str, bool]:
    """Returns (materiality_score, reason, keep). Independent of whatever
    materiality_score the curator originally assigned, so scoring is
    reproducible run to run."""
    text = " ".join([
        item.get("headline", ""),
        item.get("detail", ""),
        str(item.get("raw_values", {}).get("filing_type", "")),
        str(item.get("raw_values", {}).get("scope", "")),
    ]).lower()

    if any(k in text for k in ROUTINE_KEYWORDS):
        return 1, "Routine/administrative filing, no market relevance -- dropped", False

    has_wholesale = any(k in text for k in WHOLESALE_KEYWORDS)
    has_retail_only = any(k in text for k in RETAIL_ONLY_KEYWORDS)
    is_procedural = any(k in text for k in PROCEDURAL_STAGE_KEYWORDS)

    if is_procedural:
        return 2, "Procedural/consultative stage (hearing/draft/notice), not yet a binding order -- kept as low-priority watch item regardless of subject matter", True
    if has_wholesale:
        return 4, "Contains wholesale/generation/market-structure keywords", True
    if has_retail_only:
        return 2, "Retail/consumer-facing tariff or code change -- kept as demand-side context, not a wholesale signal", True
    # Procedural/consultative items with neither signal: keep at low materiality
    # as background rather than dropping outright, since these can develop into
    # binding orders (e.g. a hearing notice preceding a future tariff order).
    return 2, "Procedural/consultative stage, no clear wholesale signal yet -- kept as low-priority watch item", True


def process_window(candidates_path: Path) -> dict:
    raw = json.loads(candidates_path.read_text(encoding="utf-8-sig"))
    window_start = raw.get("window_start", "unknown")
    window_end = raw.get("window_end", "unknown")
    as_of_date = window_end  # regulatory operates on a window cadence, not daily; see collection notes

    items = []
    dropped = []
    seq = 1
    for cand in raw.get("items", []):
        score, reason, keep = classify_relevance(cand)
        if not keep:
            dropped.append({"item_id": cand.get("item_id"), "headline": cand.get("headline"), "reason": reason})
            continue
        items.append(build_item(
            item_id=make_item_id("regulatory", as_of_date, seq),
            headline=cand["headline"],
            detail=cand["detail"],
            materiality_score=score,
            source_reference=cand["source_reference"],
            source_timestamp=cand["source_timestamp"],
            delta_from_previous=cand.get("delta_from_previous"),
            grid_zone=cand.get("grid_zone"),
            raw_values={
                **cand.get("raw_values", {}),
                "note_for_trader_relevance": cand.get("note_for_trader_relevance"),
                "relevance_classification_reason": reason,
                "curator_original_materiality_score": cand.get("materiality_score"),
            },
        ))
        seq += 1

    out = build_output("regulatory", as_of_date, items)
    out["window_start"] = window_start
    out["window_end"] = window_end
    out["collection_method"] = raw.get("collection_method")
    out["dropped_items"] = dropped  # extra field, not in schema.json's required set, kept for audit
    return out


def main():
    candidates_path = Path("data/historical/regulatory") / "candidates.json"
    out_dir = Path("data/ingested/regulatory")
    if not candidates_path.exists():
        print(f"No candidates file at {candidates_path}. Point this at your curated "
              f"regulatory JSON (same shape as 2026-04-19_to_2026-07-17.json).")
        return
    out = process_window(candidates_path)
    filename = f"{out['window_start']}_to_{out['window_end']}.json"
    out_path = write_output(out, out_dir, filename=filename)
    print(f"{out['window_start']} to {out['window_end']}: "
          f"{len(out['items'])} kept, {len(out['dropped_items'])} dropped -> {out_path}")


if __name__ == "__main__":
    main()
