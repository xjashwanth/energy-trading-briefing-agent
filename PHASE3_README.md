# Phase 3 — Cross-Referencing & Synthesis Agent

Deliverable per the execution plan: **end-to-end pipeline producing a
first-draft daily brief in the required structure.** Done.

## What's in here

```
agents/
  cross_referencing_agent.py   rule-based, no LLM -- correlation detection
  synthesis_agent.py            real Claude API call -- drafts the brief
  prompts/synthesis_prompt.md   the exact fixed system prompt sent to the API
eval/
  test_cross_referencing_agent.py   8 hand-labeled scenario tests
briefs/                         (created on first run) generated .md briefs
                                 + _prompt_and_response.json audit trail per day
```

## Cross-referencing agent (rule-based, zero fabrication risk)

Compares today's already-scored items across sources and flags compounded
risk only when BOTH sides independently already cleared their own
materiality threshold in Phase 2 -- it never invents a correlation.

4 rules:
1. Outage + heatwave, **same grid_zone** -> materiality 5 (highest confidence)
2. Outage + heatwave, same day, zone not confirmable -> materiality 3,
   explicitly labeled "zone match not confirmable" (since only Bengaluru is
   weather-tracked, most outage zones can't be zone-matched -- see the known
   gap noted in weather_agent.py)
3. Price spike + major outage, same day -> materiality 4, framed as
   "plausible contributing driver," never asserted as sole cause
4. Price spike + heatwave, same day -> materiality 4, same careful framing

Delta-only: each flag is tagged "New today" or "Persisting from prior day"
by comparing risk_type + grid_zone against yesterday's cross-reference
output, so the brief doesn't re-announce a known risk as breaking news.

**Verified**: ran end-to-end against your real 91-day ingested price/weather/
outage data sitting in this environment already -- correctly produced 0
flags on quiet days and, on a constructed same-day heatwave+outage+price-
spike scenario, correctly caught that the outage's zone (Raichur) didn't
match the tracked weather zone (Bengaluru Urban) and labeled it
"unconfirmed zone" rather than falsely claiming a zone match.

## Synthesis agent (real Claude API call, per the execution plan)

This is the one agent in the whole pipeline that calls a real LLM. It:
- Loads the fixed prompt from `agents/prompts/synthesis_prompt.md`
- Gathers today's 5 upstream JSON outputs (price/weather/outage/regulatory/
  cross_reference)
- Sends them to the Claude API with hard rules: use only facts in the JSON,
  never predict prices/suggest trades, lead with deltas, say "none" rather
  than invent an empty section, follow the exact 6-section structure
- Writes `briefs/<date>.md` (the brief) and
  `briefs/<date>_prompt_and_response.json` (full audit trail: exact prompt
  sent, exact raw response, token usage) -- this audit file is what Phase 4's
  verifier agent will check every sentence against

**It does not fall back to templated/fabricated text if no API key is set
-- it raises and refuses to run.** Verified above: without
`ANTHROPIC_API_KEY`, `process_day()` raises `RuntimeError` rather than
producing any output.

## How to run in Colab

```python
!pip install anthropic --quiet

import os
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-..."   # or use Colab secrets

!python agents/cross_referencing_agent.py     # rule-based, run first, no key needed
!python agents/synthesis_agent.py 2026-07-13   # one day
!python agents/synthesis_agent.py --all        # every day you have ingested data for
```

## Model

Defaults to `claude-sonnet-5`. Override with:
```python
os.environ["SYNTHESIS_MODEL"] = "claude-opus-4-8"
```

## Verified

```
$ pytest eval/ -v
21 passed   (13 from Phase 2 + 8 new cross-referencing tests)
```

Plus the two manual checks above (real correlation detection on your real
data, and confirmed hard-refusal without an API key).

## Next steps

- **Phase 4**: verifier agent -- check every sentence in `briefs/<date>.md`
  traces back to a value in the `*_prompt_and_response.json` source JSON.
  That audit file's `user_message` field already contains the exact source
  data the model saw, so Phase 4 has everything it needs.
- Read a few real generated briefs yourself once you've run this with a
  real key -- cheapest way to catch prompt issues before building the
  verifier around them.
