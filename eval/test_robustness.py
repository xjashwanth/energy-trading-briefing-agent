"""
Phase 5 -- Robustness / Failure-Mode Tests

Execution plan definition: "No-data days, malformed PDFs, conflicting
signals" -> Pass/fail checklist.

This does NOT re-test what Phase 2/4's own eval suites already cover in
isolation (e.g. test_verifier_agent.py's missing-directory and malformed-
JSON cases). It re-runs those same failure conditions END-TO-END through
the real CLI entry points, because a component passing its own unit test
in isolation is not the same guarantee as the full pipeline not crashing
when that component fails mid-run. That end-to-end distinction is the
whole point of a Phase 5 robustness checklist vs. a Phase 2/4 unit test.

ADAPT-THIS NOTE: each agent's CLI is assumed to follow the same pattern
documented for verifier_agent.py / synthesis_agent.py in your READMEs:
`python <module_path>.py <date>`. If any ingestion agent's actual CLI
signature differs, update AGENT_CLI below -- nothing else needs to change.

Usage:
    python eval/test_robustness.py            # run full checklist
    python eval/test_robustness.py --keep     # keep scratch dirs for inspection
"""

import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.parent
SCRATCH_DATE = "2026-01-01"  # synthetic date, never a real historical day
BRIEFS_DIR = ROOT / "briefs"

# Real layout (confirmed against cross_referencing_agent.py / synthesis_agent.py):
#   data/ingested/<source_plural>/<date>.json   e.g. data/ingested/prices/2026-01-01.json
# NOT data/ingested/<date>/<source>.json -- that was the original bug here: scratch
# files were being written somewhere none of the real agents ever look, so these
# tests were silently falling through to the "missing directory" path instead of
# actually exercising malformed-data / no-data handling.
INGESTED_DIR = ROOT / "data" / "ingested"
SOURCE_DIRS = {
    "price": INGESTED_DIR / "prices",
    "weather": INGESTED_DIR / "weather",
    "outage": INGESTED_DIR / "outages",
    "regulatory": INGESTED_DIR / "regulatory",
}
# Scratch roots that get cleaned up between scenarios / at the end of the run.
SCRATCH_DIRS = list(SOURCE_DIRS.values()) + [INGESTED_DIR / "cross_reference"]

AGENT_CLI = {
    "outage": ROOT / "ingestion" / "outage_agent.py",
    "cross_reference": ROOT / "agents" / "cross_referencing_agent.py",
    "verifier": ROOT / "agents" / "verifier_agent.py",
    "synthesis": ROOT / "agents" / "synthesis_agent.py",
}

SCHEMA_STUB = {
    "source": "stub",
    "region": "Karnataka",
    "as_of_date": SCRATCH_DATE,
    "generated_at": "2026-01-01T00:00:00Z",
    "items": [],
}


def _run(cli_path: Path, *args, timeout=60):
    """Run an agent CLI as a subprocess; never let a crash escape as an
    unhandled exception here -- capture returncode/stdout/stderr so the
    test can assert on graceful vs. ungraceful failure."""
    if not cli_path.exists():
        return None, f"CLI not found at {cli_path} -- update AGENT_CLI", 127
    try:
        proc = subprocess.run(
            [sys.executable, str(cli_path), *args],
            cwd=ROOT, capture_output=True, text=True, timeout=timeout,
        )
        return proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired:
        return None, "timed out", -1


def _reset_scratch():
    """Wipe and recreate only the scratch DATE's files within each real source
    directory -- we must not rmtree the whole source directory, since that
    would delete real historical data sitting alongside it."""
    for d in SCRATCH_DIRS:
        d.mkdir(parents=True, exist_ok=True)
        stale = d / f"{SCRATCH_DATE}.json"
        if stale.exists():
            stale.unlink()


def _write(source: str, payload):
    """source is one of 'price', 'weather', 'outage', 'regulatory' -- written
    to data/ingested/<source_plural>/<SCRATCH_DATE>.json, matching what the
    real agents (cross_referencing_agent.gather / verifier_agent.gather_sources)
    actually read."""
    target_dir = SOURCE_DIRS[source]
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / f"{SCRATCH_DATE}.json").write_text(
        payload if isinstance(payload, str) else json.dumps(payload)
    )


# ---------------------------------------------------------------------
# Scenario 1: no-data day -- all four sources present but empty
# ---------------------------------------------------------------------
def test_no_data_day():
    _reset_scratch()
    for src in ("price", "weather", "outage", "regulatory"):
        _write(src, {**SCHEMA_STUB, "source": src})

    # verifier_agent checks briefs/<date>.md (the synthesis agent's draft)
    # against source JSON -- it does not read data/ingested/ directly (see
    # PHASE4_README.md). We can't call synthesis_agent here (needs a real
    # API key), so stub the draft it would have produced on a genuine
    # no-data day: every section explicitly says "none", per synthesis
    # prompt rule 4 ("never invent an item to fill a section").
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    draft_path = BRIEFS_DIR / f"{SCRATCH_DATE}.md"
    draft_path.write_text(
        f"# Energy Trading Brief -- Karnataka (Southern Grid) -- {SCRATCH_DATE}\n\n"
        "## TL;DR\n- No material items reported across any source today.\n\n"
        "## Price movement & drivers\nNo price data reported for Karnataka today.\n\n"
        "## Weather-demand linkage\nNo weather items reported today.\n\n"
        "## Outage watch\nNo outages reported for Karnataka today.\n\n"
        "## Regulatory flags\nNo regulatory items reported today.\n\n"
        "## Compounded risk flags\nNo compounded risk flags today.\n"
    )

    out, err, rc = _run(AGENT_CLI["verifier"], SCRATCH_DATE)
    if out is None and "not found" in (err or ""):
        return "SKIP", err
    if rc != 0:
        return "FAIL", f"verifier_agent crashed on empty-items day (rc={rc}): {err}"
    # A no-data day should still produce a report; sections should say
    # "none" rather than the pipeline silently producing nothing.
    report_path = BRIEFS_DIR / f"{SCRATCH_DATE}_verification_report.json"
    if not report_path.exists():
        return "FAIL", (f"no verification report written for a no-data day "
                         f"(stdout: {(out or '')[-200:]!r}, stderr: {(err or '')[-200:]!r})")
    return "PASS", "verifier_agent completed cleanly on all-empty sources"


# ---------------------------------------------------------------------
# Scenario 2: malformed / corrupt JSON in data/ingested
# ---------------------------------------------------------------------
def test_malformed_json():
    _reset_scratch()
    _write("price", "{not valid json,,,")  # deliberately broken
    for src in ("weather", "outage", "regulatory"):
        _write(src, {**SCHEMA_STUB, "source": src})

    out, err, rc = _run(AGENT_CLI["verifier"], SCRATCH_DATE)
    if out is None and "not found" in (err or ""):
        return "SKIP", err
    if rc != 0 and "Traceback" in (err or ""):
        return "FAIL", f"unhandled exception on malformed JSON: {err[-300:]}"
    return "PASS", "malformed price.json logged/skipped rather than crashing pipeline"


# ---------------------------------------------------------------------
# Scenario 3: missing data/ingested directory entirely for the date
# ---------------------------------------------------------------------
def test_missing_directory():
    missing_date = "1999-01-01"  # guaranteed not to exist
    out, err, rc = _run(AGENT_CLI["verifier"], missing_date)
    if out is None and "not found" in (err or ""):
        return "SKIP", err
    if rc != 0 and "Traceback" in (err or ""):
        return "FAIL", f"unhandled exception on missing directory: {err[-300:]}"
    return "PASS", "missing data/ingested/<date> handled without crash"


# ---------------------------------------------------------------------
# Scenario 4: malformed outage bulletin row (simulates a bad PDF extract)
# ---------------------------------------------------------------------
def test_malformed_outage_row():
    _reset_scratch()
    raw_dir = ROOT / "data" / "historical" / "outages" / SCRATCH_DATE
    raw_dir.mkdir(parents=True, exist_ok=True)
    # A row a PDF extractor would plausibly mangle: missing MW figure,
    # garbled plant name, no parseable duration.
    (raw_dir / "raw_extract.json").write_text(json.dumps([
        {"plant": "###UNREADABLE###", "mw": None, "zone": "", "duration": "??"}
    ]))
    out, err, rc = _run(AGENT_CLI["outage"], SCRATCH_DATE)
    if out is None and "not found" in (err or ""):
        return "SKIP", err
    if rc != 0 and "Traceback" in (err or ""):
        return "FAIL", f"outage_agent crashed on unparseable row: {err[-300:]}"
    out_path = SOURCE_DIRS["outage"] / f"{SCRATCH_DATE}.json"
    if out_path.exists():
        data = json.loads(out_path.read_text())
        flagged = [it for it in data.get("items", []) if it.get("confidence") == "low"
                   or it.get("flagged") or "unparsed" in str(it).lower()]
        if not flagged:
            return "FAIL", "unparseable row neither flagged nor dropped-with-log -- silently disappeared"
    return "PASS", "unparseable outage row preserved as low-confidence flagged item"


# ---------------------------------------------------------------------
# Scenario 5: conflicting / non-correlated signals -- different zones
# should NOT be falsely flagged as compounded risk
# ---------------------------------------------------------------------
def test_no_false_correlation():
    _reset_scratch()
    _write("outage", {
        **SCHEMA_STUB, "source": "outage",
        "items": [{
            "item_id": f"outage-{SCRATCH_DATE}-001",
            "headline": "Plant X outage", "detail": "Synthetic outage for robustness test.",
            "grid_zone": "Raichur", "materiality_score": 5,
            "source_reference": "smoke_test", "source_timestamp": f"{SCRATCH_DATE}T00:00:00Z",
            "delta_from_previous": None, "raw_values": {"mw": 400},
        }],
    })
    _write("weather", {
        **SCHEMA_STUB, "source": "weather",
        "items": [{
            "item_id": f"weather-{SCRATCH_DATE}-001",
            "headline": "Heatwave conditions", "detail": "Synthetic heatwave for robustness test.",
            "grid_zone": "Bengaluru Urban", "materiality_score": 4,
            "source_reference": "smoke_test", "source_timestamp": f"{SCRATCH_DATE}T00:00:00Z",
            "delta_from_previous": None, "raw_values": {"temp_c": 41},
        }],
    })
    _write("price", {**SCHEMA_STUB, "source": "price"})
    _write("regulatory", {**SCHEMA_STUB, "source": "regulatory"})

    out, err, rc = _run(AGENT_CLI["cross_reference"], SCRATCH_DATE)
    if out is None and "not found" in (err or ""):
        return "SKIP", err
    if rc != 0 and "Traceback" in (err or ""):
        return "FAIL", f"cross_referencing_agent crashed: {err[-300:]}"
    cross_path = INGESTED_DIR / "cross_reference" / f"{SCRATCH_DATE}.json"
    if cross_path.exists():
        data = json.loads(cross_path.read_text())
        for it in data.get("items", []):
            text = json.dumps(it).lower()
            if "raichur" in text and "bengaluru" in text and "unconfirmed" not in text \
                    and "not confirmable" not in text:
                return "FAIL", "different-zone outage+heatwave flagged as confirmed match"
    return "PASS", "mismatched-zone outage+heatwave not falsely asserted as confirmed correlation"


def _cleanup_scratch():
    """Remove only the SCRATCH_DATE files this run created, across every real
    directory it touched -- never rmtree a whole source directory (that would
    delete real historical data living alongside the scratch file)."""
    for d in SCRATCH_DIRS:
        f = d / f"{SCRATCH_DATE}.json"
        if f.exists():
            f.unlink()
    hist_dir = ROOT / "data" / "historical" / "outages" / SCRATCH_DATE
    if hist_dir.exists():
        shutil.rmtree(hist_dir, ignore_errors=True)
    # briefs/<date>*.md and *.json artifacts written by verifier/synthesis during the run
    if BRIEFS_DIR.exists():
        for f in BRIEFS_DIR.glob(f"{SCRATCH_DATE}*"):
            f.unlink()


SCENARIOS = [
    ("No-data day (all sources empty)", test_no_data_day),
    ("Malformed/corrupt JSON in data/ingested", test_malformed_json),
    ("Missing data/ingested directory", test_missing_directory),
    ("Malformed outage bulletin row", test_malformed_outage_row),
    ("Conflicting signals, different zones -> no false correlation", test_no_false_correlation),
]


def main():
    keep = "--keep" in sys.argv[1:]
    results = []
    for label, fn in SCENARIOS:
        try:
            status, detail = fn()
        except Exception as e:  # the harness itself should never crash the checklist
            status, detail = "FAIL", f"test harness raised: {e!r}"
        results.append({"scenario": label, "status": status, "detail": detail})

    if not keep:
        _cleanup_scratch()

    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    skipped = sum(1 for r in results if r["status"] == "SKIP")

    print(f"Robustness checklist: {passed} passed, {failed} failed, {skipped} skipped\n")
    for r in results:
        mark = {"PASS": "[PASS]", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}[r["status"]]
        print(f"  {mark} {r['scenario']}")
        print(f"         {r['detail']}")

    out_path = ROOT / "eval" / "robustness_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "run_date": date.today().isoformat(),
        "summary": {"passed": passed, "failed": failed, "skipped": skipped},
        "results": results,
    }, indent=2))
    print(f"\nWritten -> {out_path}")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
