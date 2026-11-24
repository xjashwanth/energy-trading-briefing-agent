"""The readiness runner must stay offline and reject invalid notebooks."""
import json

from check_project import notebook_check, rehearsal_synthesis
from agents.verifier_agent import verify_brief


def test_notebook_rejects_saved_outputs(tmp_path):
    path = tmp_path / "demo.ipynb"
    path.write_text(json.dumps({"cells": [{"cell_type": "code", "source": "pass",
        "outputs": [{"text": "private output"}], "execution_count": 1}]}))
    try:
        notebook_check(path)
        raise AssertionError("saved outputs accepted")
    except ValueError as exc:
        assert "Clear" in str(exc)


def test_offline_selection_is_labeled_and_source_grounded():
    sources = {"price": {"items": [{"headline": "Average MCP: Rs 5000/MWh", "item_id": "p1"}]},
               "weather": None, "outage": {"items": []}, "regulatory": None, "cross_reference": {"items": []}}
    draft, audit = rehearsal_synthesis("2026-07-13", sources)
    assert audit["provider"] == "offline_rehearsal"
    assert audit["model"] is None
    _, report = verify_brief("# Rehearsal\n\n" + draft, sources)
    assert report["summary"]["total_flagged"] == 0
    assert len(report["sections"]) == 6
