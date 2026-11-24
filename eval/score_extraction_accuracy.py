"""
Phase 5 -- Extraction Accuracy: scoring script.

Reads eval/extraction_accuracy_template.json AFTER you've filled in
ground_truth_value + correct (true/false) per field, and computes:
  - overall extraction accuracy %
  - per-source breakdown
  - list of flagged (incorrect) fields for your write-up appendix

Usage:
    python eval/score_extraction_accuracy.py
    python eval/score_extraction_accuracy.py --in eval/extraction_accuracy_template.json
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", type=Path,
                     default=Path("eval/extraction_accuracy_template.json"))
    args = ap.parse_args()

    if not args.in_path.exists():
        print(f"{args.in_path} not found -- run extraction_accuracy_sample.py first.")
        return

    data = json.loads(args.in_path.read_text(encoding="utf-8-sig"))
    rows = data.get("rows", [])

    per_source = defaultdict(lambda: {"checked": 0, "correct": 0, "unsure": 0})
    flagged = []

    for row in rows:
        source = row["source"]
        for field_name, f in row.get("fields_to_check", {}).items():
            verdict = f.get("correct")
            if verdict is None:
                per_source[source]["unsure"] += 1
                continue
            per_source[source]["checked"] += 1
            if verdict is True:
                per_source[source]["correct"] += 1
            else:
                flagged.append({
                    "source": source, "as_of_date": row["as_of_date"],
                    "item_id": row["item_id"], "field": field_name,
                    "extracted_value": f["extracted_value"],
                    "ground_truth_value": f.get("ground_truth_value"),
                    "notes": row.get("reviewer_notes", ""),
                })

    print("Extraction accuracy by source")
    print("-" * 50)
    total_checked, total_correct, total_unsure = 0, 0, 0
    for source, stats in per_source.items():
        checked, correct, unsure = stats["checked"], stats["correct"], stats["unsure"]
        pct = round(100 * correct / checked, 1) if checked else None
        print(f"  {source:12s}  {correct}/{checked} correct"
              + (f"  ({pct}%)" if pct is not None else "  (nothing scored)")
              + (f"   [{unsure} unsure/unlabeled]" if unsure else ""))
        total_checked += checked
        total_correct += correct
        total_unsure += unsure

    print("-" * 50)
    overall_pct = round(100 * total_correct / total_checked, 1) if total_checked else None
    print(f"  {'OVERALL':12s}  {total_correct}/{total_checked} correct"
          + (f"  ({overall_pct}%)" if overall_pct is not None else "  (nothing scored yet)")
          + (f"   [{total_unsure} unsure/unlabeled]" if total_unsure else ""))

    if flagged:
        print(f"\n{len(flagged)} flagged (incorrect) field(s):")
        for f in flagged:
            print(f"  - {f['source']} {f['as_of_date']} {f['item_id']} :: {f['field']} "
                  f"extracted={f['extracted_value']!r} truth={f['ground_truth_value']!r}")

    out_path = args.in_path.parent / "extraction_accuracy_report.json"
    out_path.write_text(json.dumps({
        "summary": {
            "overall_checked": total_checked,
            "overall_correct": total_correct,
            "overall_accuracy_pct": overall_pct,
            "unsure_or_unlabeled": total_unsure,
            "per_source": {k: dict(v) for k, v in per_source.items()},
        },
        "flagged_fields": flagged,
    }, indent=2))
    print(f"\nWrote {out_path}")

    if total_unsure:
        print(f"\nNote: {total_unsure} field(s) still have correct=null -- "
              f"finish labeling those before citing this number in your Phase 5 report.")


if __name__ == "__main__":
    main()
