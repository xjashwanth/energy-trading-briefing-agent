# Phase 2 — Ingestion Agent Development

Deliverable per the execution plan: **4 working ingestion agents + hand-labeled
ground-truth test sets (10–15 examples each).** Both are done, rule-based
(no LLM calls), and verified — see "Verified" below.

## What's in here

```
ingestion/
  schema_utils.py       shared helpers: item_id, materiality clamping,
                         schema.json conformance check, write_output()
  price_agent.py         Phase 1 normalized prices -> schema items
  weather_agent.py        Phase 1 Open-Meteo daily files -> schema items
  outage_agent.py         Phase 1 CEA outage rows -> schema items
  regulatory_agent.py     Phase 1 curated regulatory candidates -> schema items
eval/
  test_price_agent.py      15 hand-labeled threshold cases
  test_weather_agent.py    25 hand-labeled threshold cases
  test_outage_agent.py     10 MW→materiality cases + 5 zone cases + 2 scenario tests
  test_regulatory_agent.py 6 hand-labeled cases (3 real curated items + 3 marked synthetic fixtures)
```

## How each agent works (rule-based now, LLM-ready later)

All four read Phase 1's historical-pull output and emit `schema.json`-conformant
JSON (`{source, region: "Karnataka", as_of_date, generated_at, items[]}`).
Materiality scoring and numeric claims are **deterministic thresholds**, not
LLM judgment calls — kept that way on purpose, since Phase 4's verifier agent
needs every claim traceable to a source value, and a threshold function is
auditable in a way "the model decided 4/5" isn't.

- **price_agent.py** — daily avg price item (always), peak-hour premium item
  (if ≥5% divergence from daily avg), intraday volatility item (if spread
  ≥40% of avg). Materiality scales with day-over-day % change.
- **weather_agent.py** — translates temp/precip/wind into demand/supply
  impact language rather than raw numbers, per the execution plan's explicit
  instruction. Only emits precip/wind items when they clear a materiality
  threshold — a light drizzle isn't a brief-worthy item.
- **outage_agent.py** — per-row plant/MW/zone/duration extraction, plus
  day-over-day diffing (New / Ongoing / capacity-changed / Resolved).
  Unparsed rows from Phase 1's PDF extraction are preserved as
  low-confidence flagged items, never dropped or guessed at.
- **regulatory_agent.py** — applies a keyword-based wholesale-relevance
  classifier on top of the manually-curated candidate list (KERC's site
  blocks scraping, so there's no raw feed to run this against automatically —
  see `regulatory_collection_notes.md`). Dropped items are logged in
  `dropped_items`, not silently discarded.

**Where an LLM would slot in later:** if you want richer narrative prose in
`detail` fields, or the regulatory classifier proves too brittle on real
filings, swap the relevant function for a Claude API call with a fixed
prompt template. Keep the *materiality number and keep/drop decision*
deterministic even then — that's what Phase 4 and Phase 5's precision metric
depend on being able to explain.

## Hand-labeled test sets — important scope note

The `eval/test_*.py` files hand-label the **threshold logic** (e.g., "a 9.9%
price move should score 3, a 10.0% move should score 4") against values a
human would label the same way. This is what Phase 2 needs right now to
verify the agents are correct.

This is **not yet** the Phase 5 test set. Phase 5 asks for 10–15 examples
*per source drawn from real historical days* (input → correct expected
output) to measure extraction accuracy against. That requires Phase 1's
pull scripts to actually run against live data (Colab, network access) —
not available in this environment. Once you've run
`price_historical_normalize.py` / `weather_historical_pull.py` /
`outage_historical_pull.py` for real, sample 10–15 real days per source and
hand-label the *expected items* for those days; that becomes your Phase 5
ground truth. The regulatory source is the exception — its 3 real curated
items are already genuine and already used in the test set.

One regulatory test file also includes 3 items explicitly marked
`[TEST FIXTURE]` / `SYNTHETIC-TEST-FIXTURE` — invented only to exercise the
"drop routine filings" branch that the real 3-item dataset doesn't cover.
They can't be mistaken for real KERC filings even out of context.

## Verified

```
$ pytest eval/ -v
13 passed
```

Also smoke-tested end-to-end: ran `regulatory_agent.py` against the real
`2026-04-19_to_2026-07-17.json`, and ran `price_agent.py` / `weather_agent.py`
/ `outage_agent.py` against one synthetic day each, then validated every
output against `schema.json`'s required fields, region constant, and
materiality range — all passed.

## Next steps (per the execution plan)

1. Run the real Phase 1 pull scripts to populate `data/historical/*` with
   real 90-day data, then run these 4 agents over it.
2. Build the Phase 5 real-data hand-labeled set as described above.
3. Move to Phase 3: cross-referencing agent (compounded risk across sources)
   + synthesis/orchestrator agent (final brief draft).
