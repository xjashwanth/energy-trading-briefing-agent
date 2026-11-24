"""
Phase 5 -- Smoke Test

Exercises all 4 Phase 5 scripts (usefulness_rubric, baseline_comparison,
baseline_agent, test_robustness) against synthetic data in the REAL
data/ingested/<source_plural>/<date>.json layout -- no ANTHROPIC_API_KEY
needed, since it stubs the LLM-dependent outputs (baseline/verified briefs)
directly instead of calling baseline_agent.process_day().

This is NOT a replacement for test_robustness.py (that's the actual Phase 5
failure-mode checklist deliverable) -- this checks that the Phase 5 TOOLING
itself runs cleanly against your real repo layout: right paths, right
imports, no crashes.

Usage:
    python eval/test_phase5_pipeline.py            # run, clean up after
    python eval/test_phase5_pipeline.py --keep      # leave scratch files for inspection
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SCRATCH_DATE = "2026-01-02"  # different from test_robustness.py's 2026-01-01, avoid collision
BRIEFS_DIR = ROOT / "briefs"
INGESTED_DIR = ROOT / "data" / "ingested"

SOURCE_DIRS = {
    "prices": INGESTED_DIR / "prices",
    "weather": INGESTED_DIR / "weather",
    "outages": INGESTED_DIR / "outages",
    "regulatory": INGESTED_DIR / "regulatory",
    "cross_reference": INGESTED_DIR / "cross_reference",
}

SCHEMA_STUB = {
    "source": "stub",
    "region": "Karnataka",
    "as_of_date": SCRATCH_DATE,
    "generated_at": "2026-01-02T00:00:00Z",
    "items": [
        {
            "item_id": "stub-2026-01-02-001",
            "headline": "Test item",
            "detail": "Synthetic item for smoke test.",
            "materiality_score": 3,
            "source_reference": "smoke_test",
            "source_timestamp": "2026-01-02T00:00:00Z",
            "delta_from_previous": None,
            "grid_zone": "Bengaluru Urban",
            "raw_values": {"test_value_mw": 123.4},
        }
    ],
}

results = []


def _record(label, ok, detail):
    results.append({"step": label, "status": "PASS" if ok else "FAIL", "detail": detail})
    mark = "[PASS]" if ok else "[FAIL]"
    print(f"{mark} {label}: {detail}")


def setup():
    for name, d in SOURCE_DIRS.items():
        d.mkdir(parents=True, exist_ok=True)
        stub = {**SCHEMA_STUB, "source": name.rstrip("s") if name != "cross_reference" else "cross_reference"}
        (d / f"{SCRATCH_DATE}.json").write_text(json.dumps(stub, indent=2))

    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    # Stub in place of real LLM output -- lets downstream tooling be tested
    # without an API key. Numbers below intentionally include one grounded
    # (123.4, matches raw_values above) and one NOT grounded (9999), so the
    # grounding-rate step below actually exercises both pass/fail branches.
    (BRIEFS_DIR / f"{SCRATCH_DATE}_verified.md").write_text(
        f"# Energy Trading Brief -- {SCRATCH_DATE}\n\n"
        f"## TL;DR\n- Test item observed at 123.4 MW.\n\n"
        f"_Source: prices -- 1 item(s), as-of {SCRATCH_DATE}._\n"
    )
    (BRIEFS_DIR / f"{SCRATCH_DATE}_baseline.md").write_text(
        f"Test item observed at 123.4 MW. Unrelated fabricated figure: 9999 MW.\n"
    )


def teardown():
    for d in SOURCE_DIRS.values():
        f = d / f"{SCRATCH_DATE}.json"
        if f.exists():
            f.unlink()
    if BRIEFS_DIR.exists():
        for f in BRIEFS_DIR.glob(f"{SCRATCH_DATE}*"):
            f.unlink()
    scores_csv = ROOT / "eval" / "usefulness_scores.csv"
    # leave usefulness_scores.csv -- it may contain real scored days, don't nuke it


def step_usefulness_rubric():
    sys.path.insert(0, str(ROOT / "eval"))
    try:
        import importlib
        mod_path = ROOT / "eval" / "usefulness_rubric.py"
        if not mod_path.exists():
            _record("usefulness_rubric.py --generate", False, f"not found at {mod_path}")
            return
        proc = subprocess.run(
            [sys.executable, str(mod_path), "--generate"],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        ok = proc.returncode == 0 and (ROOT / "eval" / "usefulness_scores.csv").exists()
        _record("usefulness_rubric.py --generate", ok,
                 proc.stdout.strip().splitlines()[-1] if ok else proc.stderr[-300:])
    except Exception as e:
        _record("usefulness_rubric.py --generate", False, repr(e))


def step_baseline_comparison():
    mod_path = ROOT / "eval" / "baseline_comparison.py"
    if not mod_path.exists():
        _record("baseline_comparison.py", False, f"not found at {mod_path}")
        return
    proc = subprocess.run(
        [sys.executable, str(mod_path), SCRATCH_DATE],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    report_path = ROOT / "eval" / "baseline_comparison_report.json"
    if proc.returncode != 0:
        _record("baseline_comparison.py <date>", False, proc.stderr[-500:])
        return
    if not report_path.exists():
        _record("baseline_comparison.py <date>", False, "ran clean but no report written")
        return
    report = json.loads(report_path.read_text())
    baseline_grounding = report["summary"]["avg_grounding_pct"]["baseline"]
    # With our stub (1 grounded number, 1 fabricated), expect ~50%, not 100% --
    # if this comes back 100%, the reimplemented grounding check is too loose.
    ok = baseline_grounding is not None and baseline_grounding < 100
    _record("baseline_comparison.py <date>", ok,
             f"baseline grounding {baseline_grounding}% (expected <100%, has 1 fabricated number)")


def step_test_robustness():
    mod_path = ROOT / "eval" / "test_robustness.py"
    if not mod_path.exists():
        _record("test_robustness.py", False, f"not found at {mod_path}")
        return
    proc = subprocess.run(
        [sys.executable, str(mod_path)],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    # rc 1 is expected if any scenario legitimately FAILs -- that's still the
    # script working correctly. rc != 0/1, or a raw traceback, means the
    # harness itself broke.
    ok = proc.returncode in (0, 1) and "Traceback" not in proc.stderr
    _record("test_robustness.py (full checklist)", ok,
             proc.stdout.strip().splitlines()[0] if proc.stdout else proc.stderr[-300:])


def step_baseline_agent_dates_only():
    """Doesn't call the API -- just checks --all's date-discovery logic
    finds SCRATCH_DATE and nothing bogus like 'prices'/'weather'."""
    mod_path = ROOT / "agents" / "baseline_agent.py"
    if not mod_path.exists():
        _record("baseline_agent.py date discovery", False, f"not found at {mod_path}")
        return
    text = mod_path.read_text()
    if 'glob("*.json")' in text and '"data" / "ingested" / "prices"' in text:
        dates = sorted(p.stem for p in SOURCE_DIRS["prices"].glob("*.json"))
        ok = SCRATCH_DATE in dates and all(d.count("-") == 2 for d in dates)
        _record("baseline_agent.py date discovery", ok, f"discovered dates: {dates}")
    else:
        _record("baseline_agent.py date discovery", False,
                 "--all still globs data/ingested/* directories, not prices/*.json -- unpatched version?")


def main():
    keep = "--keep" in sys.argv[1:]
    setup()
    try:
        step_usefulness_rubric()
        step_baseline_comparison()
        step_baseline_agent_dates_only()
        step_test_robustness()
    finally:
        if not keep:
            teardown()

    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    print(f"\nPhase 5 smoke test: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
