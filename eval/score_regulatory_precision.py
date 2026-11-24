"""
Phase 5 -- Regulatory relevance-filtering precision: scorer.

Reads eval/regulatory_precision_template.json AFTER you've filled in
your_verdict_keep / your_verdict_score, and computes:
  - Precision: of items the agent KEPT, what fraction did you also say
    should be kept? (agent keeping noise = false positive, hurts this)
  - Recall-ish: of items the agent DROPPED, what fraction did you agree
    should be dropped? (agent dropping something relevant = false negative --
    this is the dangerous direction, since a missed relevant filing never
    reaches the trader at all)
  - Score agreement: how often your materiality score matches the agent's
    (within 1 point), separate from the binary keep/drop question.

Usage:
    python eval/score_regulatory_precision.py
"""

import argparse
import json
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", type=Path,
                     default=Path("eval/regulatory_precision_template.json"))
    args = ap.parse_args()

    if not args.in_path.exists():
        print(f"{args.in_path} not found -- run regulatory_precision_sample.py first.")
        return

    data = json.loads(args.in_path.read_text(encoding="utf-8-sig"))
    rows = data.get("rows", [])

    labeled = [r for r in rows if r.get("your_verdict_keep") is not None]
    unlabeled = len(rows) - len(labeled)

    agent_kept = [r for r in labeled if r["agent_predicted_keep"]]
    agent_dropped = [r for r in labeled if not r["agent_predicted_keep"]]

    # Precision: of what the agent kept, how much did the human also want kept?
    true_positives = sum(1 for r in agent_kept if r["your_verdict_keep"] is True)
    false_positives = sum(1 for r in agent_kept if r["your_verdict_keep"] is False)
    precision = round(100 * true_positives / len(agent_kept), 1) if agent_kept else None

    # "Recall" direction: of what the agent dropped, how much did the human
    # agree was correctly droppable (i.e. NOT a missed relevant item)?
    true_negatives = sum(1 for r in agent_dropped if r["your_verdict_keep"] is False)
    false_negatives = sum(1 for r in agent_dropped if r["your_verdict_keep"] is True)
    drop_accuracy = round(100 * true_negatives / len(agent_dropped), 1) if agent_dropped else None

    score_matches = [r for r in labeled if r.get("your_verdict_score") is not None]
    score_agree = sum(1 for r in score_matches
                        if abs(r["your_verdict_score"] - r["agent_predicted_score"]) <= 1)
    score_agreement_pct = round(100 * score_agree / len(score_matches), 1) if score_matches else None

    print(f"Labeled: {len(labeled)} / {len(rows)}  ({unlabeled} still unlabeled)")
    print("-" * 60)
    print(f"Precision (agent KEPT, human agrees):  {true_positives}/{len(agent_kept)}"
          + (f"  ({precision}%)" if precision is not None else "  (no kept items)"))
    print(f"Drop accuracy (agent DROPPED, human agrees):  {true_negatives}/{len(agent_dropped)}"
          + (f"  ({drop_accuracy}%)" if drop_accuracy is not None else "  (no dropped items)"))
    print(f"Materiality score agreement (within 1 point):  {score_agree}/{len(score_matches)}"
          + (f"  ({score_agreement_pct}%)" if score_agreement_pct is not None else "  (no scores labeled)"))

    if false_positives:
        print(f"\n{false_positives} FALSE POSITIVE(S) -- agent kept noise:")
        for r in agent_kept:
            if r["your_verdict_keep"] is False:
                print(f"  - {r['item_id']}: {r['headline']}  (your notes: {r.get('your_notes','')})")

    if false_negatives:
        print(f"\n{false_negatives} FALSE NEGATIVE(S) -- agent dropped something relevant "
              f"(the dangerous direction -- these never reached the trader):")
        for r in agent_dropped:
            if r["your_verdict_keep"] is True:
                print(f"  - {r['item_id']}: {r['headline']}  (your notes: {r.get('your_notes','')})")

    n_total = len(rows)
    if n_total < 15:
        print(f"\nSample-size caveat: n={n_total}. Treat these percentages as illustrative "
              f"only -- widen your regulatory collection window before citing a precision "
              f"number in the Phase 5 report.")

    out_path = args.in_path.parent / "regulatory_precision_report.json"
    out_path.write_text(json.dumps({
        "n_total": n_total, "n_labeled": len(labeled), "n_unlabeled": unlabeled,
        "precision_pct": precision, "drop_accuracy_pct": drop_accuracy,
        "score_agreement_pct": score_agreement_pct,
        "false_positives": false_positives, "false_negatives": false_negatives,
        "sample_size_adequate": n_total >= 15,
    }, indent=2))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
