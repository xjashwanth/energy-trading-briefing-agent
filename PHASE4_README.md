# Phase 4 — Verifier Agent & Guardrails

Deliverable per the execution plan: **verifier module + source-attribution
trail integrated into every generated brief.** Done.

## What's in here

```
agents/
  verifier_agent.py           rule-based, no LLM -- claim grounding + attribution
eval/
  test_verifier_agent.py      10 hand-labeled scenario tests
briefs/
  <date>_verified.md          release-ready brief (flagged claims removed,
                               source-attribution tag on every section)
  <date>_verification_report.json   full audit trail: every claim, its verdict, why
```

## Why rule-based, not another LLM call

Checking "does this claim trace to a value in the source JSON" is a
grounding/lookup problem, not a generation problem. Using an LLM to verify
an LLM's output means the verifier can hallucinate its own verdicts — the
same failure mode you're trying to guard against. So `verifier_agent.py`
never calls the Anthropic API. It:

1. Re-loads the same source JSON the synthesis agent saw — independently,
   straight from `data/ingested/`, not by re-parsing the audit trail string.
2. Collects every number and named entity (headlines, plant names, grid
   zones) that appears anywhere in that source JSON.
3. Splits the draft brief into sentences per section, extracts every number
   in each sentence, and checks it against that ground-truth set (with a 1%
   relative tolerance for rounding drift, plus a looser check for numbers
   that are legitimate arithmetic on two present values — e.g. a peak-hour
   premium computed from two present averages).
4. Any sentence with a number that can't be traced is **removed**, not
   rewritten — rewriting risks quietly introducing a different unverified
   claim.
5. Every section gets an explicit `_Source: X — N item(s), as-of DATE,
   generated TIMESTAMP._` tag appended.

## Verified

```
$ pytest eval/test_verifier_agent.py -v
10 passed
```

Covers: grounded numbers pass, fabricated numbers get flagged and stripped
from the release brief, a derived percentage (computed from two real
numbers) is correctly accepted rather than false-flagged, explicit "no
data" sentences are verified rather than penalized, prose with no checkable
content is marked `UNVERIFIABLE` (kept, but visibly not counted as
grounded), and — the two Phase 4 execution-plan graceful-failure
requirements — a missing `data/ingested/` directory and a malformed/corrupt
JSON file are both logged and skipped rather than crashing the pipeline.

## Known limitation (by design, not hidden)

A **correct** number attached to the **wrong** entity (right MW figure,
wrong plant name) isn't caught in v1 — that needs number-to-entity
co-occurrence checking, which is a reasonable stretch goal but adds real
complexity. Flag this explicitly in your Phase 5 write-up as a scoping
decision rather than an oversight.

## How to run in Colab

```python
!python agents/verifier_agent.py 2026-07-13   # one day
!python agents/verifier_agent.py --all        # every day with a draft brief
```

Output per day, e.g.:
```
2026-07-13: verified brief -> briefs/2026-07-13_verified.md  (14 claims checked, 2 flagged, 85.7% grounded)
```

## Feeding Phase 5

`briefs/<date>_verification_report.json`'s `summary.grounding_rate_pct`
field is a direct input to Phase 5's **faithfulness / hallucination-rate**
metric ("% claims grounded") — you can aggregate this across your 15–20
historical test days without re-deriving anything by hand. The
`sections.*.details` array (every claim + verdict + reason) is also useful
raw material for the human-usefulness rubric scoring, since it lets you see
exactly what got cut before a human ever reads the release brief.

## Next steps

- **Phase 5**: run `verifier_agent.py --all` across your full historical
  batch, aggregate `grounding_rate_pct` per day into the metrics table, and
  spot-check a handful of `FLAGGED` claims manually to confirm the verifier
  itself isn't over- or under-flagging (a false-flag on a legitimate derived
  stat would understate your grounding rate; a missed fabrication would
  overstate it).
- Consider whether to loosen `DERIVED_TOLERANCE` in `verifier_agent.py` once
  you see real flagged output — v1 tolerance was picked to pass the example
  brief you shared, not tuned against your full dataset yet.
