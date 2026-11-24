"""Date-scoped briefing pipeline. Failures never publish an older artifact."""
import argparse
import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from pipeline_status import RunRecord, coverage, execution_lock
from pdf_generator import generate_pdf_from_markdown
from email_service import send_brief_email
from agents.synthesis_agent import call_synthesis
from agents.source_loading import gather_sources
from agents.verifier_agent import verify_brief
from agents.cross_referencing_agent import process_day as cross_reference
from ingestion.schema_utils import write_output
from ingestion.price_agent import process_day as ingest_price
from ingestion.weather_agent import process_day as ingest_weather
from ingestion.outage_agent import process_day as ingest_outage, row_key
from ingestion.regulatory_agent import process_window


def discover_dates():
    return sorted({p.stem for folder in ("historical", "ingested")
                   for p in (ROOT / "data" / folder / "prices").glob("*.json")
                   if _valid_date(p.stem)})


def _valid_date(value):
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def ingest_date(as_of_date):
    previous = (date.fromisoformat(as_of_date) - timedelta(days=1)).isoformat()
    historical = ROOT / "data/historical"
    outputs = ROOT / "data/ingested"
    price = historical / "prices" / f"{as_of_date}.json"
    if price.exists():
        write_output(ingest_price(price), outputs / "prices")
    weather = historical / "weather" / as_of_date
    if weather.exists():
        result = ingest_weather(weather, historical / "weather" / previous)
        if result is not None:
            write_output(result, outputs / "weather")
    outage = historical / "outages" / f"{as_of_date}.json"
    prior = historical / "outages" / f"{previous}.json"
    if outage.exists():
        rows = json.loads(prior.read_text(encoding="utf-8-sig")).get("rows", []) if prior.exists() else []
        previous_rows = {row_key(r): r for r in rows if "unparsed_raw_text" not in r}
        result, _ = ingest_outage(outage, previous_rows)
        write_output(result, outputs / "outages")
    candidates = historical / "regulatory/candidates.json"
    if candidates.exists():
        result = process_window(candidates)
        write_output(result, outputs / "regulatory",
                     f"{result['window_start']}_to_{result['window_end']}.json")
    write_output(cross_reference(as_of_date, ROOT), outputs / "cross_reference")


def publish(as_of_date, draft, verified, report, audit, generate_pdf):
    output = ROOT / "briefs"
    output.mkdir(parents=True, exist_ok=True)
    # Stage the complete run before replacing any published brief.
    with tempfile.TemporaryDirectory(dir=output) as staging:
        staging = Path(staging)
        artifacts = {
            f"{as_of_date}.md": draft,
            f"{as_of_date}_verified.md": verified,
            f"{as_of_date}_verification_report.json": json.dumps(report, indent=2),
            f"{as_of_date}_prompt_and_response.json": json.dumps(audit, indent=2),
        }
        for name, content in artifacts.items():
            (staging / name).write_text(content, encoding="utf-8")
        pdf_name = f"{as_of_date}_verified.pdf"
        if generate_pdf:
            generated = generate_pdf_from_markdown(verified, str(staging / pdf_name))
            if not generated or not (staging / pdf_name).exists():
                raise RuntimeError("PDF generation did not produce a PDF")
        for artifact in staging.iterdir():
            artifact.replace(output / artifact.name)
        if not generate_pdf:
            (output / pdf_name).unlink(missing_ok=True)
    return output / (pdf_name if generate_pdf else f"{as_of_date}_verified.md")


def preflight(as_of_date):
    if not _valid_date(as_of_date):
        raise ValueError("Expected a date in YYYY-MM-DD format")
    sources = gather_sources(as_of_date, ROOT)
    return {"date": as_of_date, "api_key_configured": bool(os.environ.get("NVIDIA_API_KEY")),
            "coverage": coverage(sources, as_of_date, ROOT),
            "note": "Coverage describes saved normalized inputs; collection is a separate step."}


def process_single_date(as_of_date, generate_pdf=True, email=None, require_complete=False):
    if not _valid_date(as_of_date):
        print("[FAILED] Expected a date in YYYY-MM-DD format.")
        return False
    run = RunRecord(ROOT, as_of_date)
    try:
        with execution_lock(ROOT):
            with run.stage("credentials"):
                if not os.environ.get("NVIDIA_API_KEY"):
                    raise RuntimeError("NVIDIA_API_KEY is required. Existing briefs were preserved.")
            with run.stage("ingestion"):
                ingest_date(as_of_date)
            with run.stage("coverage"):
                sources = gather_sources(as_of_date, ROOT)
                run.data["coverage"] = coverage(sources, as_of_date, ROOT)
                run.save()
                if not sources["price"] or not sources["price"]["items"]:
                    raise ValueError("No price items for the requested date")
                gaps = [r["source"] for r in run.data["coverage"] if r["status"] in ("missing", "stale")]
                if gaps:
                    print("[COVERAGE] Missing or stale: " + ", ".join(gaps))
                    if require_complete:
                        raise ValueError("Complete coverage required; update missing or stale inputs first")
            with run.stage("synthesis"):
                draft, audit = call_synthesis(as_of_date, sources)
                draft = f"# Energy Trading Brief -- Karnataka -- {as_of_date}\n\n{draft}\n"
            with run.stage("verification"):
                verified, report = verify_brief(draft, sources)
                if not report["summary"]["total_verified"]:
                    raise ValueError("No source-grounded claims survived verification")
                report.update(as_of_date=as_of_date, run_id=run.data["run_id"], coverage=run.data["coverage"])
                audit["source_snapshot"] = sources
                audit["run_id"] = run.data["run_id"]
                run.data["verification"] = report["summary"]
            with run.stage("publication"):
                artifact = publish(as_of_date, draft, verified, report, audit, generate_pdf)
                run.data["artifact"] = str(artifact)
            if email:
                with run.stage("email"):
                    if not send_brief_email(email, as_of_date, verified, str(artifact),
                            verification_summary=report["summary"]):
                        raise RuntimeError("Brief generated, but email was not sent")
        run.finish("success")
        print(f"[SUCCESS] Brief generated for {as_of_date}. Run record: {run.path}")
        return True
    except Exception as exc:
        # Record exception type only: provider exceptions may contain sensitive request data.
        stage = run.data["stages"][-1]["name"] if run.data["stages"] else "lock"
        run.finish("failed", f"{stage}: {type(exc).__name__}")
        print(f"[FAILED] {as_of_date}: {exc}")
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    dates = parser.add_mutually_exclusive_group()
    dates.add_argument("--date")
    dates.add_argument("--all", action="store_true")
    parser.add_argument("--pdf", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--email")
    parser.add_argument("--check", action="store_true", help="Inspect saved coverage and credentials without running generation")
    parser.add_argument("--require-complete", action="store_true", help="Fail before synthesis if a source is missing or stale")
    parser.add_argument("--open", action="store_true", help="Open the local dashboard after successful generation")
    args = parser.parse_args()
    available = discover_dates()
    targets = available if args.all else [args.date or (available[-1] if available else "")]
    if not targets or targets == [""]:
        parser.error("No dates available")
    if args.check:
        for target in targets:
            print(json.dumps(preflight(target), indent=2))
        return 0
    results = [process_single_date(d, args.pdf, args.email, args.require_complete) for d in targets]
    if args.open and all(results):
        from dashboard_launcher import open_dashboard
        open_dashboard(ROOT, targets[-1])
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
