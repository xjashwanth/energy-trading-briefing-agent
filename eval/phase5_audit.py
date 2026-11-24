"""Evaluate saved drafts against the inputs they actually received.

Run from any directory with python eval/phase5_audit.py.
Outputs are written under output/phase5, separate from published briefs.
"""

import csv
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.verifier_agent import SECTION_SOURCE_MAP, split_sections, verify_brief

SUFFIX = "_prompt_and_response.json"
SECTIONS = {"TL;DR", *SECTION_SOURCE_MAP}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def prompt_sources(audit):
    if "source_snapshot" in audit:
        return audit["source_snapshot"]
    return json.loads(audit["user_message"].split("```json", 1)[1].split("```", 1)[0])


def score_draft(day, draft, sources):
    # Add a controlled title so a draft beginning with ## is parsed consistently.
    text = f"# Historical briefing for {day}\n\n{draft.strip()}\n"
    headings = re.findall(r"^## (.+?)\s*$", draft, flags=re.MULTILINE)
    sections = split_sections(text)
    missing = sorted(SECTIONS - sections.keys())
    unexpected = sorted(sections.keys() - SECTIONS)
    if not sections or unexpected:
        return {"status": "invalid_structure", "missing_sections": missing,
                "unexpected_sections": unexpected}, None
    release, details = verify_brief(text, sources)
    rows = [r for s in details["sections"].values() for r in s["details"]]
    eligible = {i["item_id"] for data in sources.values()
                for i in (data or {}).get("items", [])
                if i.get("item_id") and i.get("materiality_score", 0) >= 4}
    retained = {r.get("item_id") for r in rows if r["verdict"] == "VERIFIED"}
    summary = details["summary"]
    summary.update(
        status="scored",
        missing_sections=missing,
        duplicate_headings=len(headings) - len(set(headings)),
        sections_without_verified_claims=[h for h, s in details["sections"].items()
                                          if s["verified"] == 0],
        draft_words=len(draft.split()),
        release_words=len(release.split()),
        high_materiality_items=len(eligible),
        high_materiality_items_retained=len(eligible & retained),
        high_materiality_items_omitted=sorted(eligible - retained),
        rejected_examples=list(dict.fromkeys(
            r["claim"] for r in rows if r["verdict"] == "FLAGGED"
        ))[:3],
    )
    return summary, release


def summarize(rows):
    totals = {k: sum(r.get(k, 0) for r in rows) for k in (
        "total_claims_checked", "total_verified", "total_flagged",
        "high_materiality_items", "high_materiality_items_retained",
    )}
    checked = totals["total_claims_checked"]
    return {
        "dates": len(rows),
        "scorable_dates": sum(r["status"] == "scored" for r in rows),
        "six_section_drafts": sum(r["status"] == "scored" and not r["missing_sections"]
                                  and not r["duplicate_headings"] for r in rows),
        **totals,
        "source_match_pct": round(100 * totals["total_verified"] / checked, 1) if checked else None,
    }


def evaluate_live(directory):
    if not directory.is_dir():
        return None
    results = {arm: {} for arm in ("pipeline", "baseline")}
    for arm, days in results.items():
        for path in sorted(directory.glob(f"*_{arm}.json")):
            record = read_json(path)
            day = record["date"]
            sources = prompt_sources(record)
            score, _ = score_draft(day, record["draft"], sources)
            saved = (record.get("report") or {}).get("summary", {})
            for key in ("total_claims_checked", "total_verified", "total_flagged"):
                if score.get(key) != saved.get(key):
                    raise ValueError(f"{path.name}: saved and replayed {key} differ")
            days[day] = {"file": path.name, "model": record["model"],
                         "input_sha256": hashlib.sha256(record["user_message"].encode()).hexdigest(),
                         "latency_seconds": record["latency_seconds"], "usage": record["usage"],
                         "finish_reason": record["finish_reason"], **score}

    paired_dates = sorted(results["pipeline"].keys() & results["baseline"].keys())
    for day in paired_dates:
        pipeline, baseline = (results[arm][day] for arm in results)
        if any(pipeline[k] != baseline[k] for k in ("model", "input_sha256")):
            raise ValueError(f"{day}: model or input differs between arms")

    cohort_summary = {}
    for arm, days in results.items():
        rows = [days[d] for d in paired_dates]
        cohort_summary[arm] = {
            **summarize(rows),
            "nonempty_verified_briefs": sum(r.get("total_verified", 0) > 0 for r in rows),
            "median_latency_seconds": round(median(r["latency_seconds"] for r in rows), 2) if rows else None,
            "input_tokens": sum((r["usage"] or {}).get("prompt_tokens") or 0 for r in rows),
            "output_tokens": sum((r["usage"] or {}).get("completion_tokens") or 0 for r in rows),
        }
    return {
        "paired_dates": paired_dates,
        "unpaired_dates": sorted(results["pipeline"].keys() ^ results["baseline"].keys()),
        "cohorts": cohort_summary,
        "per_arm": results,
    }


def evaluate_selection(directory, paired_directory):
    rows = []
    for path in sorted(directory.glob("*_selection.json")):
        record = read_json(path)
        day = record["date"]
        row = {"date": day, "status": record["status"], "issues": record["issues"],
               "model": record["model"], "latency_seconds": record["latency_seconds"],
               "usage": record["usage"], "audit": path.name}
        if record.get("draft"):
            paired = read_json(paired_directory / f"{day}_pipeline.json")
            score, _ = score_draft(day, record["draft"], prompt_sources(paired))
            if score["total_verified"] != record["report"]["summary"]["total_verified"]:
                raise ValueError(f"{path.name}: saved and replayed matches differ")
            row["verification"] = score
        rows.append(row)
    return {
        "dates_attempted": len(rows),
        "outcomes": dict(Counter(row["status"] for row in rows)),
        "invalid_sections": dict(Counter(issue for row in rows for issue in row["issues"])),
        "generated_briefs": summarize([r["verification"] for r in rows if "verification" in r]),
        "per_date": rows,
    }


def main():
    out = ROOT / "output/phase5"
    out.mkdir(parents=True, exist_ok=True)
    briefs = ROOT / "briefs"
    rows, comparisons, legacy_baselines, skipped = [], [], [], []
    for path in sorted(briefs.glob(f"*{SUFFIX}")):
        day = path.name[:10]
        if path.name != f"{day}{SUFFIX}":
            legacy_baselines.append(path.name)
            continue
        try:
            audit = read_json(path)
            sources = prompt_sources(audit)
            summary, release = score_draft(day, audit["raw_response_text"], sources)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            skipped.append({"file": path.name, "error": type(exc).__name__})
            continue
        rows.append({
            "date": day, "model": audit.get("model"), "provider": audit.get("provider"),
            "source_evidence": "source_snapshot" if "source_snapshot" in audit else "saved_prompt",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "audit": path.name,
            "usage": audit.get("usage"), **summary,
        })
        baseline = briefs / f"{day}_baseline{SUFFIX}"
        if baseline.exists():
            saved = read_json(baseline)
            baseline_sources = json.loads(saved["user_message"].split("\n\n", 1)[1])
            draft = saved["raw_response"]["choices"][0]["message"]["content"]
            score, _ = score_draft(day, draft, baseline_sources)
            comparisons.append({"date": day, "archived_baseline": score,
                                "archived_pipeline_draft": summary,
                                "same_saved_inputs": baseline_sources == sources})
        if day == "2026-07-13" and release:
            recorded = read_json(briefs / f"{day}_verification_report.json")["summary"]
            assert all(summary[k] == recorded[k] for k in (
                "total_claims_checked", "total_verified", "total_flagged"))

    cohorts = {model: summarize([r for r in rows if r["model"] == model])
               for model in sorted({r["model"] for r in rows if r["model"]})}
    with (ROOT / "eval/usefulness_scores.csv").open(encoding="utf-8-sig", newline="") as stream:
        scores = list(csv.DictReader(stream))
    fields = ("clarity", "actionability", "prioritization", "length")
    scored_dates = sorted({r["date"] for r in scores
                           if all(r.get(k, "").strip() in {"1", "2", "3", "4", "5"}
                                  for k in fields)})
    report = {
        "method": "Exact statement match against each draft's saved input. Occurrences include repeats.",
        "cohorts": cohorts,
        "legacy_baseline_audits": legacy_baselines,
        "skipped_audits": skipped,
        "human_usefulness_scored_dates": scored_dates,
        "archived_baseline_comparisons": comparisons,
        "baseline_limit": ("The three legacy pairs share saved inputs, but the freeform baseline did not use "
                           "the required headings. See the controlled live comparison for matched format instructions."),
        "per_date": rows,
    }
    (out / "archived_evaluation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"cohorts": cohorts, "statuses": Counter(r["status"] for r in rows),
                      "archives_skipped": skipped, "human_scored_dates": len(scored_dates),
                      "baseline_pairs": len(comparisons)}, indent=2))
    live = evaluate_live(out / "lightning-paired-v1")
    if live is not None:
        live["source_selection"] = evaluate_selection(out / "lightning-selection-v1",
                                                      out / "lightning-paired-v1")
        (out / "live_evaluation.json").write_text(json.dumps(live, indent=2), encoding="utf-8")
        print(json.dumps({"live_pairs": len(live["paired_dates"]), **live["cohorts"]}, indent=2))
        print("Source selection outcomes:", live["source_selection"]["outcomes"])


if __name__ == "__main__":
    main()
