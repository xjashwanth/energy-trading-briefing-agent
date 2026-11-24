"""Offline readiness checks and an isolated source-based rehearsal. No API calls."""
import argparse
import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
TESTS = [f"eval/test_{name}.py" for name in (
    "price_agent", "weather_agent", "outage_agent", "regulatory_agent",
    "cross_referencing_agent", "verifier_agent", "regressions", "pipeline_status", "readiness")]


def notebook_check(path):
    notebook = json.loads(path.read_text(encoding="utf-8"))
    count = 0
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            source = cell["source"]
            compile("".join(source) if isinstance(source, list) else source, str(path), "exec")
            if cell.get("outputs") or cell.get("execution_count") is not None:
                raise ValueError("Clear notebook execution outputs before distributing")
            count += 1
    return count


def rehearsal_synthesis(target, sources):
    """A test double, not an LLM result or a production generation fallback."""
    from agents.verifier_agent import SECTION_SOURCE_MAP
    ranked = sorted([item for src in sources.values() if src for item in src.get("items", [])],
                    key=lambda item: item.get("materiality_score", 0), reverse=True)
    lines = ["## TL;DR", *["- " + item["headline"] for item in ranked[:3]]]
    for heading, key in SECTION_SOURCE_MAP.items():
        lines.extend(["", "## " + heading])
        data = sources.get(key)
        if data is None:
            lines.append(f"No {key} data available for this date.")
        elif not data.get("items"):
            lines.append(f"No {key} items reported for this date.")
        else:
            lines.extend("- " + item["headline"] for item in data["items"][:3])
    return "\n".join(lines), {"provider": "offline_rehearsal", "model": None,
        "notice": "Deterministic source selection for execution testing; no LLM was called."}


def rehearse(root, target, destination):
    import run_pipeline as pipeline
    with tempfile.TemporaryDirectory(prefix="briefing-rehearsal-") as folder:
        workspace = Path(folder)
        shutil.copytree(root / "data", workspace / "data",
                        ignore=shutil.ignore_patterns("raw_pdfs", "raw_xlsx", "*.pdf", "*.xlsx"))
        with patch.object(pipeline, "ROOT", workspace), \
             patch.dict(os.environ, {"NVIDIA_API_KEY": "offline-test-placeholder"}), \
             patch.object(pipeline, "call_synthesis", side_effect=rehearsal_synthesis):
            if not pipeline.process_single_date(target, generate_pdf=True, require_complete=True):
                raise RuntimeError("Offline rehearsal failed; inspect console output")
        report = json.loads((workspace / f"briefs/{target}_verification_report.json").read_text())
        if report["summary"]["total_flagged"]:
            raise ValueError("Verifier rejected copied source statements")
        # Every delivered artifact is explicitly identified as an offline rehearsal.
        destination.mkdir(parents=True, exist_ok=True)
        draft = workspace / f"briefs/{target}_verified.md"
        text = draft.read_text(encoding="utf-8").replace("# Energy Trading Brief", "# OFFLINE REHEARSAL - Energy Trading Brief", 1)
        draft.write_text(text, encoding="utf-8")
        from pdf_generator import generate_pdf_from_markdown
        generate_pdf_from_markdown(text, str(workspace / f"briefs/{target}_verified.pdf"))
        for path in (workspace / "briefs").iterdir():
            shutil.copy2(path, destination / path.name)
        for path in (workspace / "runs").glob("*.json"):
            record = json.loads(path.read_text())
            record["mode"] = "offline_rehearsal"
            record["artifact"] = str(destination / f"{target}_verified.pdf")
            (destination / path.name).write_text(json.dumps(record, indent=2), encoding="utf-8")
        return report["summary"]



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default="2026-07-13")
    parser.add_argument("--rehearse", action="store_true", help="Exercise real ingestion, verification, and PDF with deterministic test synthesis")
    args = parser.parse_args()
    date.fromisoformat(args.date)
    output = ROOT / "output/readiness" / args.date
    output.mkdir(parents=True, exist_ok=True)
    results = []

    def check(name, action):
        try:
            detail = action()
            results.append({"check": name, "status": "PASS", "detail": detail})
        except Exception as exc:
            results.append({"check": name, "status": "FAIL", "detail": str(exc)})
        print(f"[{results[-1]['status']}] {name}")

    def dependencies():
        names = ("openai", "dotenv", "reportlab", "streamlit", "pytest", "pandas", "openpyxl")
        missing = [name for name in names if importlib.util.find_spec(name) is None]
        if missing:
            raise RuntimeError("Install requirements.txt; missing: " + ", ".join(missing))
        return list(names)

    def syntax():
        files = list(ROOT.glob("*.py"))
        for folder in ("agents", "ingestion", "eval"):
            files += list((ROOT / folder).rglob("*.py"))
        for path in files:
            ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        return f"{len(files)} Python files parsed"

    def data_check():
        from agents.source_loading import gather_sources
        from ingestion.schema_utils import validate_output
        from pipeline_status import coverage
        sources = gather_sources(args.date, ROOT)
        rows = coverage(sources, args.date, ROOT)
        for key in ("price", "weather", "outage"):
            data = sources.get(key)
            if data is None:
                raise ValueError(f"Missing or malformed {key} data for {args.date}")
            problems = validate_output(data)
            if problems:
                raise ValueError(f"{key}: {problems}")
        if any(row["status"] in ("missing", "stale") for row in rows):
            raise ValueError(f"Incomplete coverage: {rows}")
        return rows

    def tests():
        result = subprocess.run([sys.executable, "-m", "pytest", *TESTS, "-q"], cwd=ROOT,
                                capture_output=True, text=True, timeout=180)
        (output / "tests.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        if result.returncode:
            raise RuntimeError(f"Tests failed; inspect {output / 'tests.log'}")
        return result.stdout.strip()

    check("Dependencies", dependencies)
    check("Python syntax", syntax)
    check("Notebook syntax and cleared outputs", lambda: notebook_check(ROOT / "Energy_Trading_Briefing_Agent_Colab.ipynb"))
    check("Selected-date data and coverage", data_check)
    check("Isolated regression suite", tests)
    if args.rehearse:
        check("Offline pipeline rehearsal (not a live LLM test)", lambda: rehearse(ROOT, args.date, output / "rehearsal"))
    report = {"date": args.date, "checks": results,
              "live_checks_pending": ["Colab Secrets access", "NVIDIA authentication and model output", "Google Drive persistence and browser download"]}
    (output / "readiness.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = [f"# Readiness review - {args.date}", "", "Offline checks only; no API call or email was made.", ""]
    for item in results:
        lines += [f"- {item['status']}: {item['check']}"]
    lines += ["", "## Still requires Colab", *[f"- {x}" for x in report["live_checks_pending"]]]
    (output / "readiness.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Review: {output / 'readiness.md'}")
    return int(any(result["status"] == "FAIL" for result in results))


if __name__ == "__main__":
    sys.exit(main())
