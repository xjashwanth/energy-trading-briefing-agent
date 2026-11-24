"""
Phase 5 -- Regulatory relevance-filtering precision: sampler.

Scope note's success criterion: "High relevance precision (regulatory noise
correctly dropped)." Note the wording -- precision here is about the KEEP
decision being correct (not keeping noise), which is the inverse framing of
"noise correctly dropped."

Why this can't just reuse test_regulatory_agent.py's 6 cases: those are hand-
picked to exercise each branch of classify_relevance (3 real + 3 clearly-
marked synthetic), which is unit-test coverage, not a precision SAMPLE. A
precision metric needs an unbiased-ish set of real candidate items scored by
someone who didn't already know the expected answer -- right now that's only
the 3 items in candidates.json, which is too small a sample size to report a
meaningful percentage from (regulatory_collection_notes.md already flags
this). This script is written to be re-run against a WIDER window once you
have more real candidates -- point --candidates at any file in the same
shape (window_start/window_end/items).

What this does:
  1. Loads a candidates-shaped JSON (real curated items, NOT the synthetic
     test fixtures -- those stay in test_regulatory_agent.py where they
     belong).
  2. Runs classify_relevance on every item (the actual agent logic, imported
     directly -- not reimplemented).
  3. Writes eval/regulatory_precision_template.json: one row per item with
     the agent's predicted keep/drop + score + reason, plus blank fields for
     you to fill in your own independent judgment call.
  4. You fill in `your_verdict_keep` (true/false) and `your_verdict_score`
     (1-5) per row based on your own reading of the filing -- ideally without
     re-reading classify_relevance's reason string first, to keep the
     judgment independent.

Usage:
    python eval/regulatory_precision_sample.py
    python eval/regulatory_precision_sample.py --candidates data/historical/regulatory/candidates_widened.json
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "ingestion"))
from regulatory_agent import classify_relevance


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", type=Path,
                     default=Path("data/historical/regulatory/candidates.json"))
    ap.add_argument("--out", type=Path,
                     default=Path("eval/regulatory_precision_template.json"))
    args = ap.parse_args()

    if not args.candidates.exists():
        print(f"{args.candidates} not found.")
        return

    raw = json.loads(args.candidates.read_text(encoding="utf-8-sig"))
    cand_items = raw.get("items", [])

    rows = []
    for cand in cand_items:
        score, reason, keep = classify_relevance(cand)
        rows.append({
            "item_id": cand.get("item_id"),
            "headline": cand.get("headline"),
            "detail": cand.get("detail"),
            "source_reference": cand.get("source_reference"),
            "agent_predicted_keep": keep,
            "agent_predicted_score": score,
            "agent_reason": reason,
            "curator_original_score": cand.get("materiality_score"),
            "curator_note_for_trader_relevance": cand.get("note_for_trader_relevance"),
            "your_verdict_keep": None,    # <-- fill: should this have been KEPT? true/false
            "your_verdict_score": None,   # <-- fill: what materiality score (1-5) would you give it?
            "your_notes": "",
        })

    n_kept = sum(1 for r in rows if r["agent_predicted_keep"])
    n_dropped = len(rows) - n_kept

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "instructions": (
            "For each row, read headline/detail/source_reference yourself and decide "
            "independently: should this item have been KEPT (bid-relevant / worth a "
            "trader's attention) or DROPPED (noise)? Fill your_verdict_keep (true/false) "
            "and your_verdict_score (1-5). Try not to just copy agent_reason -- the point "
            "is an independent check. Then run: python eval/score_regulatory_precision.py"
        ),
        "sample_size_warning": (
            f"This sample has only {len(rows)} item(s). A precision % from a sample this "
            f"small is not statistically meaningful on its own -- treat any resulting "
            f"number as illustrative, and note the small-n caveat explicitly if you cite "
            f"it in your Phase 5 report. Widen the regulatory collection window "
            f"(regulatory_collection_notes.md already suggests this) and re-run this "
            f"script against the wider file for a more defensible number."
        ) if len(rows) < 15 else None,
        "rows": rows,
    }, indent=2))

    print(f"{len(rows)} real candidate item(s) scored by the agent "
          f"({n_kept} kept, {n_dropped} dropped) -> {args.out}")
    if len(rows) < 15:
        print(f"WARNING: n={len(rows)} is too small for a meaningful precision % -- "
              f"see sample_size_warning in the output file. Consider widening your "
              f"regulatory collection window before citing this in Phase 5.")


if __name__ == "__main__":
    main()
