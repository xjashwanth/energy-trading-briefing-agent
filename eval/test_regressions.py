"""Offline regressions for release integrity; no live APIs or project data writes."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_pipeline as pipeline
from agents.source_loading import gather_sources
from agents.verifier_agent import check_source_claim, verify_brief
from agents.cross_referencing_agent import find_compounded_risks, process_day
from email_service import send_brief_email


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def source():
    return {"outage": {"as_of_date": "2026-07-13", "items": [{
        "item_id": "outage-2026-07-13-001", "headline": "Raichur: 800 MW affected",
        "source_reference": "local:outage", "source_timestamp": "2026-07-13",
        "detail": "Raichur outage started on 2026-07-12.", "raw_values": {"planned_mw": 800}}]}}


class ReleaseRegressions(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root_patch = patch.object(pipeline, "ROOT", Path(self.workspace.name))
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def test_reject_wrong_entity_unit_date_and_absence(self):
        for claim in ("Kaiga: 800 MW affected", "Raichur: 800 C affected",
                      "Raichur outage started on 2026-07-11.", "No outages reported.",
                      "Raichur: 400 MW affected", "No outage items reported for this date."):
            with self.subTest(claim=claim):
                self.assertEqual(check_source_claim(claim, source())["verdict"], "FLAGGED")

    def test_valid_statement_has_item_provenance(self):
        result = check_source_claim("Raichur: 800 MW affected [outage-2026-07-13-001]", source())
        self.assertEqual(result["verdict"], "VERIFIED")
        self.assertEqual(result["source_reference"], "local:outage")
        self.assertEqual(check_source_claim("Raichur: 800 MW affected [wrong-id]", source())["verdict"], "FLAGGED")

    def test_missing_is_not_empty(self):
        claim = "No outage items reported for this date."
        self.assertEqual(check_source_claim(claim, {"outage": None})["verdict"], "FLAGGED")
        self.assertEqual(check_source_claim(claim, {"outage": {"items": []}})["verdict"], "VERIFIED")

    def test_sections_and_unsupported_prose(self):
        text, report = verify_brief("# Brief\n\n## Weather-demand linkage\nRaichur: 800 MW affected\nConditions are calm.", source())
        self.assertNotIn("800", text)
        self.assertNotIn("Conditions are calm", text)
        self.assertEqual(report["summary"]["total_flagged"], 2)

    def test_regulatory_publication_cutoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root / "data/ingested/regulatory/window.json", {"items": [
                {"headline": "Earlier", "source_timestamp": "2026-04-18T00:00:00Z"},
                {"headline": "Future", "source_timestamp": "2026-07-03T00:00:00Z"},
                {"headline": "Undated"}]})
            items = gather_sources("2026-04-20", root)["regulatory"]["items"]
            self.assertEqual([i["headline"] for i in items], ["Earlier"])

    def test_rain_is_not_heat(self):
        weather = {"items": [{"headline": "Heavy rainfall", "materiality_score": 4}]}
        outage = {"items": [{"headline": "Major outage", "materiality_score": 4}]}
        self.assertEqual(find_compounded_risks(None, weather, outage, "2026-07-13"), [])

    def test_cross_reference_never_reads_current_or_future(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for day in ("2026-07-12", "2026-07-13", "2026-07-14"):
                write(root / f"data/ingested/cross_reference/{day}.json", {"items": [], "as_of_date": day})
            with patch("agents.cross_referencing_agent.diff_vs_yesterday", return_value=[]) as diff:
                process_day("2026-07-13", root)
                self.assertEqual(diff.call_args.args[1]["as_of_date"], "2026-07-12")

    def test_missing_key_preserves_existing_brief(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(pipeline, "ingest_date") as ingest:
            self.assertFalse(pipeline.process_single_date("2026-07-13", False))
            ingest.assert_not_called()

    def test_failed_synthesis_cannot_publish_stale_success(self):
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "test"}), \
             patch.object(pipeline, "ingest_date"), \
             patch.object(pipeline, "gather_sources", return_value={"price": {"items": [1]}}), \
             patch.object(pipeline, "call_synthesis", side_effect=RuntimeError("provider failed")), \
             patch.object(pipeline, "generate_pdf_from_markdown") as pdf:
            self.assertFalse(pipeline.process_single_date("2026-07-13"))
            pdf.assert_not_called()

    def test_successful_staged_run_and_pdf_failure_preserves_brief(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = {"price": {"items": [{"headline": "Average MCP: Rs 5000/MWh", "item_id": "price-1"}]}}
            draft = "## Price movement & drivers\nAverage MCP: Rs 5000/MWh"
            with patch.object(pipeline, "ROOT", root), patch.dict(os.environ, {"NVIDIA_API_KEY": "test"}), \
                 patch.object(pipeline, "ingest_date"), patch.object(pipeline, "gather_sources", return_value=sources), \
                 patch.object(pipeline, "call_synthesis", return_value=(draft, {})):
                self.assertTrue(pipeline.process_single_date("2026-07-13", False))
                path = root / "briefs/2026-07-13_verified.md"
                original = path.read_bytes()
                with patch.object(pipeline, "generate_pdf_from_markdown", side_effect=RuntimeError("PDF failed")):
                    self.assertFalse(pipeline.process_single_date("2026-07-13", True))
                self.assertEqual(path.read_bytes(), original)

    def test_unconfigured_email_is_not_success(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(send_brief_email("test@example.com", "2026-07-13", "brief"))


if __name__ == "__main__":
    unittest.main()
