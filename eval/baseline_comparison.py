"""
Phase 5 -- Baseline Comparison

Execution plan definition: "Multi-agent pipeline vs. naive single-prompt
summarizer on same days" -> Qualitative + rubric delta.

This adds a QUANTITATIVE half to that "qualitative + rubric delta": it
computes a grounding rate for the baseline brief using the same rule
Phase 4's verifier_agent applies to the real pipeline (every number in
the brief must trace to a value in that day's source JSON, 1% tolerance),
so you get a real number-vs-number faithfulness comparison, not just a
side-by-side read. This does not replace the human usefulness rubric --
it's a second, independent signal alongside it.

NOTE: this reimplements Phase 4's grounding rule at a basic level rather
than importing verifier_agent's internals directly, since the baseline
brief has a different filename convention (<date>_baseline.md) than what
verifier_agent.py's CLI expects (<date>.md) and its internal claim/number
extraction functions aren't part of the documented public interface.
If verifier_agent.py exposes a reusable `check_claim`/`extract_numbers`
function, swap this out for it directly -- that guarantees byte-for-byte
identical grounding logic on both sides of the comparison.

CLI:
    python eval/baseline_comparison.py 2026-07-13
    python eval/baseline_comparison.py --all
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "agents"))
from verifier_agent import gather_sources

ROOT = Path(__file__).parent.parent
BRIEFS_DIR = ROOT / "briefs"
NUMBER_RE = re.compile(r'-?\d[\d,]*\.?\d*')
TOLERANCE = 0.01


def _ground_truth_numbers(sources: dict) -> set:
    values = set()
    for data in sources.values():
        for item in (data or {}).get("items", []):
            for v in (item.get("raw_values") or {}).values():
                if isinstance(v, (int, float)):
                    values.add(round(float(v), 2))
            for m in NUMBER_RE.findall(json.dumps(item)):
                try:
                    values.add(round(float(m.replace(",", "")), 2))
                except ValueError:
                    pass
    return values


def _is_grounded(num: float, truth: set) -> bool:
    if num in truth:
        return True
    return any(t != 0 and abs(num - t) / abs(t) <= TOLERANCE for t in truth)


def grounding_rate(brief_text: str, sources: dict):
    sentences = re.split(r'(?<=[.!?])\s+', brief_text)
    truth = _ground_truth_numbers(sources)
    checked, grounded = 0, 0
    flagged_examples = []
    for sent in sentences:
        nums = NUMBER_RE.findall(sent)
        for n in nums:
            try:
                val = round(float(n.replace(",", "")), 2)
            except ValueError:
                continue
            checked += 1
            if _is_grounded(val, truth):
                grounded += 1
            else:
                flagged_examples.append(sent.strip()[:140])
    pct = round(100 * grounded / checked, 1) if checked else None
    return {"numbers_checked": checked, "grounded": grounded, "grounding_rate_pct": pct,
            "flagged_examples": flagged_examples[:5]}


def compare_day(as_of_date: str):
    sources = gather_sources(as_of_date, ROOT)

    baseline_path = BRIEFS_DIR / f"{as_of_date}_baseline.md"
    pipeline_path = BRIEFS_DIR / f"{as_of_date}_verified.md"
    pipeline_report_path = BRIEFS_DIR / f"{as_of_date}_verification_report.json"

    result = {"date": as_of_date}

    if baseline_path.exists():
        result["baseline"] = grounding_rate(baseline_path.read_text(encoding="utf-8-sig"), sources)
    else:
        result["baseline"] = None

    if pipeline_report_path.exists():
        # Prefer the pipeline's own reported number -- it's the authoritative
        # source, computed by the actual verifier rather than this reimplementation.
        # NOTE: verifier_agent.py's real summary key is `verified_rate_pct`
        # (confirmed against actual output), not `grounding_rate_pct` as
        # PHASE4_README.md's prose implied -- fixed here after the original
        # lookup silently returned None instead of erroring.
        pr = json.loads(pipeline_report_path.read_text())
        result["pipeline"] = {"grounding_rate_pct": pr.get("summary", {}).get("verified_rate_pct")}
    elif pipeline_path.exists():
        result["pipeline"] = grounding_rate(pipeline_path.read_text(encoding="utf-8-sig"), sources)
    else:
        result["pipeline"] = None

    result["word_count"] = {
        "baseline": len(baseline_path.read_text(encoding="utf-8-sig").split()) if baseline_path.exists() else None,
        "pipeline": len(pipeline_path.read_text(encoding="utf-8-sig").split()) if pipeline_path.exists() else None,
    }
    return result


def main():
    args = sys.argv[1:]
    if args and args[0] != "--all":
        dates = [args[0]]
    else:
        dates = sorted(
            p.stem.replace("_baseline", "") for p in BRIEFS_DIR.glob("*_baseline.md")
        ) if BRIEFS_DIR.exists() else []

    if not dates:
        print("No baseline briefs found -- run agents/baseline_agent.py first.")
        return

    results = [compare_day(d) for d in dates]

    def _avg(key_path):
        vals = [r[key_path[0]][key_path[1]] for r in results
                if r.get(key_path[0]) and r[key_path[0]].get(key_path[1]) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    summary = {
        "days_compared": len(results),
        "avg_grounding_pct": {
            "baseline": _avg(("baseline", "grounding_rate_pct")),
            "pipeline": _avg(("pipeline", "grounding_rate_pct")),
        },
        "avg_word_count": {
            "baseline": _avg(("word_count", "baseline")),
            "pipeline": _avg(("word_count", "pipeline")),
        },
        "note": "Grounding % is the quantitative half of this metric. Qualitative "
                "read (compounded-risk detection, delta-awareness, section structure) "
                "still needs a manual side-by-side skim -- this script doesn't score that.",
    }

    report = {"summary": summary, "per_day": results}
    out_path = ROOT / "eval" / "baseline_comparison_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))

    print(f"Compared {len(results)} day(s) -> {out_path}\n")
    print(f"  Avg grounding: baseline {summary['avg_grounding_pct']['baseline']}% "
          f"vs pipeline {summary['avg_grounding_pct']['pipeline']}%")
    print(f"  Avg length:   baseline {summary['avg_word_count']['baseline']} words "
          f"vs pipeline {summary['avg_word_count']['pipeline']} words")
    print("  (Qualitative comparison -- compounded-risk detection, delta-awareness, "
          "structure -- needs a manual read; not scored here.)")


if __name__ == "__main__":
    main()
