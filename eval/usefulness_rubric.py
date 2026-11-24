"""
Phase 5 -- Human Usefulness Rubric

Execution plan definition: "Score ~15-20 historical briefs on clarity,
actionability, prioritization, length" -> 1-5 average score.

This is a TEMPLATE + AGGREGATOR, not a scorer -- the execution plan is
explicit that this metric is a human judgment call, and fabricating scores
here would defeat the point of the metric. What this script does:

  1. `--generate`: scans briefs/*_verified.md and writes a CSV with one row
     per day, brief text truncated inline for quick reference, and empty
     1-5 columns for you to fill in by hand (in Excel/Sheets/a text editor).
  2. `--aggregate`: reads the filled-in CSV back and computes per-criterion
     and overall averages -- the number that goes in your Phase 5 table.

Rubric definitions (put these at the top of your scoring session so scores
stay consistent across all 15-20 days -- drifting criteria partway through
is the most common way this metric ends up not meaning anything):
  Clarity         - Could a trader understand each flagged item in one read,
                    no re-reading required?
  Actionability    - Does each item say clearly enough what changed / why it
                    matters, without you having to infer it yourself?
  Prioritization   - Are the highest-materiality / compounded-risk items
                    actually surfaced first, not buried?
  Length           - Is it a genuine "2-minute document" (per the Phase 3
                    synthesis prompt), or padded / clipped?

CLI:
    python eval/usefulness_rubric.py --generate
    python eval/usefulness_rubric.py --aggregate
"""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
BRIEFS_DIR = ROOT / "briefs"
CSV_PATH = ROOT / "eval" / "usefulness_scores.csv"
CRITERIA = ["clarity", "actionability", "prioritization", "length"]


def generate():
    verified = sorted(BRIEFS_DIR.glob("*_verified.md"))
    if not verified:
        print("No *_verified.md briefs found in briefs/ -- run verifier_agent.py first.")
        return

    rows = []
    for path in verified:
        as_of_date = path.stem.replace("_verified", "")
        text = path.read_text(encoding="utf-8-sig")
        preview = " ".join(text.split())[:200]
        rows.append({
            "date": as_of_date,
            "preview": preview,
            **{c: "" for c in CRITERIA},
            "notes": "",
        })

    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "preview", *CRITERIA, "notes"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Generated scoring template -> {CSV_PATH}  ({len(rows)} brief(s))")
    print(f"Fill in {CRITERIA} as 1-5 integers, one row per day, then run --aggregate.")
    if len(rows) < 15:
        print(f"Note: execution plan asks for ~15-20 days; you currently have {len(rows)} "
              f"verified brief(s) -- generate more before this metric is representative.")


def aggregate():
    if not CSV_PATH.exists():
        print(f"{CSV_PATH} not found -- run --generate first, then fill in scores.")
        return

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    scored_rows = [r for r in rows if all(r.get(c, "").strip() for c in CRITERIA)]
    if not scored_rows:
        print(f"No fully-scored rows yet in {CSV_PATH} -- fill in {CRITERIA} for at least some days.")
        return

    per_criterion = {}
    for c in CRITERIA:
        vals = []
        for r in scored_rows:
            try:
                v = int(r[c])
                if not 1 <= v <= 5:
                    raise ValueError
                vals.append(v)
            except ValueError:
                print(f"Warning: non-1-5 value '{r[c]}' for {r['date']}/{c} -- skipped")
        per_criterion[c] = round(sum(vals) / len(vals), 2) if vals else None

    overall_vals = [per_criterion[c] for c in CRITERIA if per_criterion[c] is not None]
    overall = round(sum(overall_vals) / len(overall_vals), 2) if overall_vals else None

    print(f"Scored {len(scored_rows)}/{len(rows)} day(s):\n")
    for c in CRITERIA:
        print(f"  {c:15s}: {per_criterion[c]}")
    print(f"  {'overall':15s}: {overall}")

    if len(scored_rows) < 15:
        print(f"\nNote: only {len(scored_rows)} day(s) scored; execution plan asks for ~15-20 "
              f"for this average to be defensible in your report.")


def main():
    args = sys.argv[1:]
    if "--generate" in args:
        generate()
    elif "--aggregate" in args:
        aggregate()
    else:
        print("Usage: python eval/usefulness_rubric.py --generate | --aggregate")


if __name__ == "__main__":
    main()
