"""Operational checks use temporary workspaces and mocked providers."""
import json
from pathlib import Path
from unittest.mock import patch

import run_pipeline as pipeline
from pipeline_status import RunRecord, coverage, execution_lock, latest_run


def test_lock_blocks_overlap_and_releases(tmp_path):
    with execution_lock(tmp_path):
        try:
            with execution_lock(tmp_path):
                raise AssertionError("second writer acquired lock")
        except RuntimeError as error:
            assert "active" in str(error)
    with execution_lock(tmp_path):
        pass


def test_stage_failure_is_persisted_without_exception_payload(tmp_path):
    run = RunRecord(tmp_path, "2026-07-13")
    try:
        with run.stage("synthesis"):
            raise ValueError("sensitive provider response")
    except ValueError:
        run.finish("failed", "synthesis: ValueError")
    result = latest_run(tmp_path, "2026-07-13")
    assert result["stages"][0]["status"] == "failed"
    assert result["stages"][0]["duration_seconds"] >= 0
    assert "sensitive" not in run.path.read_text()


def test_stale_regulatory_and_missing_daily_inputs(tmp_path):
    folder = tmp_path / "data/ingested/regulatory"
    folder.mkdir(parents=True)
    (folder / "window.json").write_text(json.dumps({"window_end": "2026-07-01", "items": []}))
    rows = coverage({"regulatory": {"items": []}}, "2026-07-13", tmp_path)
    assert rows[0]["status"] == "missing"
    assert rows[-1]["status"] == "stale"
    assert rows[-1]["age_days"] == 12


def test_required_coverage_stops_before_provider(tmp_path):
    with patch.object(pipeline, "ROOT", tmp_path), \
         patch.dict("os.environ", {"NVIDIA_API_KEY": "test"}), \
         patch.object(pipeline, "ingest_date"), \
         patch.object(pipeline, "gather_sources", return_value={"price": {"items": [1]}}), \
         patch.object(pipeline, "call_synthesis") as provider:
        assert not pipeline.process_single_date("2026-07-13", False, require_complete=True)
        provider.assert_not_called()
        result = latest_run(tmp_path, "2026-07-13")
        assert result["status"] == "failed"
        assert result["stages"][-1]["name"] == "coverage"


def test_future_regulatory_window_does_not_make_old_data_fresh(tmp_path):
    folder = tmp_path / "data/ingested/regulatory"
    folder.mkdir(parents=True)
    for name, start, end in (("old", "2026-06-01", "2026-06-30"),
                             ("future", "2026-08-01", "2026-08-30")):
        (folder / f"{name}.json").write_text(json.dumps({"window_start": start, "window_end": end}))
    row = coverage({"regulatory": {"items": []}}, "2026-07-13", tmp_path)[-1]
    assert row["status"] == "stale"
    assert row["latest_saved_date"] == "2026-06-30"


def test_preflight_never_ingests_or_calls_provider(tmp_path):
    with patch.object(pipeline, "ROOT", tmp_path), \
         patch.object(pipeline, "ingest_date") as ingest, \
         patch.object(pipeline, "call_synthesis") as provider:
        result = pipeline.preflight("2026-07-13")
        assert result["coverage"][0]["status"] == "missing"
        ingest.assert_not_called()
        provider.assert_not_called()
        assert not (tmp_path / "runs").exists()
