# Hand-Labeled Test Sets — Phase 2 Deliverable

Per execution plan Phase 2: build 10–15 examples per source now (input → correct
expected output), so Phase 5 has ground truth to measure extraction accuracy
against. Build these *while* the raw source data is fresh in front of you —
much slower to reconstruct later.

## Format (one JSON file per source)

`eval/test_sets/price_test_set.json`, `weather_test_set.json`,
`outage_test_set.json`, `regulatory_test_set.json` — each an array of:

```json
{
  "case_id": "price-case-001",
  "input_file": "data/historical/prices/2026-05-14.json",
  "expected_output": {
    "headline_contains": ["MCP", "up"],
    "materiality_score": 4,
    "delta_direction": "up"
  },
  "notes": "Sharp price spike day — good test for materiality=4/5 threshold boundary."
}
```

Keep `expected_output` as loose match criteria (substrings, score, direction)
rather than exact-string match — exact-string comparison breaks on trivial
rewording and isn't what Phase 5's "extraction accuracy" metric needs; it
needs to verify the *fields*, not the prose.

## What to pick for each source (10–15 examples, mix of cases)

- **Price**: a few normal days, one sharp-move day (tests materiality=5), one
  flat/no-change day (tests materiality=1), one day with peak/day-avg
  divergence (tests the second-item logic).
- **Weather**: one heatwave day, one heavy-rain day, one unremarkable day, one
  high-wind day — one example per classification bucket in `weather_agent.py`.
- **Outage**: a real parsed row, a multi-unit-outage day, a no-outage day
  (tests empty-list-not-fabricated behavior), and — if you find one in the 90
  day pull — an unparsed/malformed row (tests the visible-failure path).
- **Regulatory**: cover all 3 items already curated
  (`2026-04-19_to_2026-07-17.json`) plus, if the window gets widened later
  (per `regulatory_collection_notes.md`'s Phase 5 suggestion), any new items —
  make sure at least one bid-relevant=false and one bid-relevant=true case
  are represented, since that's the precision metric Phase 5 measures.

## Next step

Once `data/historical/` is populated (run the three pull scripts + regulatory
curation), pick concrete dates for each bucket above and fill in
`eval/test_sets/*.json`. This file is the template/checklist, not the data
itself — the data has to come from what actually shows up in your 90-day pull.
