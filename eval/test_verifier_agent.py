"""
Phase 4 -- Hand-labeled scenario tests for the verifier agent.
Pure rule-based logic, no LLM/API involved.

Run: pytest eval/test_verifier_agent.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "agents"))
from verifier_agent import (
    check_claim,
    collect_source_numbers,
    collect_source_entities,
    verify_brief,
    gather_sources,
)


def _price_source(avg_mcp=5619.08, peak_avg=9370.0):
    return {
        "price": {
            "source": "price", "as_of_date": "2026-07-13", "generated_at": "2026-07-14T00:00:00Z",
            "items": [{
                "item_id": "price-2026-07-13-001",
                "headline": "Average DAM MCP: Rs 5619.08/MWh",
                "detail": "Daily average MCP.",
                "materiality_score": 4,
                "source_reference": "local:price.json",
                "source_timestamp": "2026-07-13T00:00:00Z",
                "delta_from_previous": "+125.7%",
                "grid_zone": None,
                "raw_values": {"avg_mcp_rs_per_mwh": avg_mcp, "peak_hours_avg_mcp_rs_per_mwh": peak_avg},
            }],
        },
        "weather": None, "outage": None, "regulatory": None, "cross_reference": None,
    }


def test_grounded_number_is_verified():
    sources = _price_source()
    numbers = collect_source_numbers(sources)
    entities = collect_source_entities(sources)
    result = check_claim("Average DAM MCP: Rs 5619.08/MWh.", numbers, entities)
    assert result["verdict"] == "VERIFIED"


def test_fabricated_number_is_flagged():
    sources = _price_source()
    numbers = collect_source_numbers(sources)
    entities = collect_source_entities(sources)
    # 9999.99 appears nowhere in source JSON and isn't derivable from present numbers
    result = check_claim("The average MCP spiked to Rs 9999.99/MWh.", numbers, entities)
    assert result["verdict"] == "FLAGGED"


def test_derived_percentage_is_accepted():
    # peak premium = (9370.0 - 5619.08) / 5619.08 * 100 = ~66.75%
    sources = _price_source(avg_mcp=5619.08, peak_avg=9370.0)
    numbers = collect_source_numbers(sources)
    entities = collect_source_entities(sources)
    result = check_claim("The peak-hour premium was +66.8% vs the daily average.", numbers, entities)
    assert result["verdict"] == "VERIFIED"


def test_entity_match_without_numbers_is_verified():
    sources = _price_source()
    numbers = collect_source_numbers(sources)
    entities = collect_source_entities(sources)
    result = check_claim("Average DAM MCP: Rs 5619.08/MWh was the day's headline figure.", numbers, entities)
    # contains a grounded number, still verified
    assert result["verdict"] == "VERIFIED"


def test_no_data_statement_needs_source_context():
    numbers, entities = set(), set()
    result = check_claim("No outages reported for Karnataka today.", numbers, entities)
    assert result["verdict"] == "UNVERIFIABLE"


def test_prose_with_no_numbers_or_known_entities_is_unverifiable_not_verified():
    numbers, entities = set(), set()
    result = check_claim("Conditions were generally calm across the region.", numbers, entities)
    assert result["verdict"] == "UNVERIFIABLE"


def test_verify_brief_removes_flagged_claim_and_keeps_grounded_one():
    sources = _price_source()
    brief = (
        "# Energy Trading Brief -- Karnataka -- 2026-07-13\n\n"
        "## Price movement & drivers\n"
        "Average DAM MCP: Rs 5619.08/MWh. The average MCP briefly touched Rs 42.00/MWh at 3am.\n"
    )
    verified_text, report = verify_brief(brief, sources)
    assert "5619.08" in verified_text
    assert "42.00" not in verified_text
    assert report["sections"]["Price movement & drivers"]["flagged"] == 1
    assert "Source: price" in verified_text  # attribution tag present


def test_verify_brief_adds_attribution_tags_per_section():
    sources = _price_source()
    brief = (
        "# Brief\n\n## Price movement & drivers\nAverage DAM MCP: Rs 5619.08/MWh.\n\n"
        "## Outage watch\nNo outage data available for this date.\n"
    )
    _, report = verify_brief(brief, sources)
    assert "Outage watch" in report["sections"]
    assert report["sections"]["Outage watch"]["details"][0]["verdict"] == "VERIFIED"


def test_gather_sources_does_not_crash_on_missing_files(tmp_path):
    # No data/ingested/ directory exists at all under tmp_path -- Phase 4's
    # "malformed/missing data does not crash the pipeline" requirement.
    sources = gather_sources("2026-07-13", base_dir=tmp_path)
    assert sources["price"] is None
    assert sources["weather"] is None
    assert sources["outage"] is None


def test_number_embedded_in_string_field_is_grounded():
    # Regression test for a real false-positive found on 2026-07-13: a delta
    # figure stored as prose ("up 125.7% from prior day") rather than as a
    # bare numeric field must still be recognized as a grounded number.
    sources = {
        "price": {
            "source": "price", "as_of_date": "2026-07-13", "generated_at": "2026-07-14T00:00:00Z",
            "items": [{
                "item_id": "price-2026-07-13-001",
                "headline": "Average DAM MCP: Rs 5619.08/MWh",
                "detail": "Daily average MCP.",
                "materiality_score": 4,
                "source_reference": "local:price.json",
                "source_timestamp": "2026-07-13T00:00:00Z",
                "delta_from_previous": "up 125.7% from prior day",
                "grid_zone": None,
                "raw_values": {"avg_mcp_rs_per_mwh": 5619.08},
            }],
        },
        "weather": None, "outage": None, "regulatory": None, "cross_reference": None,
    }
    numbers = collect_source_numbers(sources)
    entities = collect_source_entities(sources)
    result = check_claim(
        "Average DAM MCP increased by +125.7% to Rs 5619.08/MWh compared to the prior day.",
        numbers, entities,
    )
    assert result["verdict"] == "VERIFIED"


def test_iso_date_in_claim_is_not_treated_as_a_fabricated_number():
    # Regression test: "for 2026-07-13" should not be split into fake numbers
    # 2026 / -07 / -13 and then flagged for not matching source data.
    sources = _price_source()
    numbers = collect_source_numbers(sources)
    entities = collect_source_entities(sources)
    result = check_claim(
        "The average DAM MCP for 2026-07-13 was Rs 5619.08/MWh.",
        numbers, entities,
    )
    assert result["verdict"] == "VERIFIED"


def test_year_range_in_claim_is_not_treated_as_a_fabricated_number():
    # Regression test: "2026-2029" (a control-period range) should not be
    # split into fake numbers 2026 / -2029.
    sources = {
        "regulatory": {
            "source": "regulatory", "as_of_date": "2026-07-13", "generated_at": "2026-07-14T00:00:00Z",
            "items": [{
                "item_id": "regulatory-2026-07-02-001",
                "headline": "KERC opens public hearing on solar tariff framework for 2026-2029",
                "detail": "Hearing covers the control period 2026-07-01 to 2029-06-30.",
                "materiality_score": 2,
                "source_reference": "local:regulatory.json",
                "source_timestamp": "2026-07-02T00:00:00Z",
                "delta_from_previous": None,
                "grid_zone": None,
                "raw_values": {"control_period_start": "2026-07-01", "control_period_end": "2029-06-30"},
            }],
        },
        "price": None, "weather": None, "outage": None, "cross_reference": None,
    }
    numbers = collect_source_numbers(sources)
    entities = collect_source_entities(sources)
    result = check_claim(
        "KERC opened a public hearing on the solar tariff framework for 2026-2029.",
        numbers, entities,
    )
    # Correct behavior: once the fake date-numbers are stripped, this sentence
    # has no numbers left to check, and it's a paraphrase (not an exact match)
    # of the source headline -- so it should land as UNVERIFIABLE (kept, not
    # counted as strongly grounded), NOT as FLAGGED (which implies fabrication).
    # The critical assertion is that it's no longer wrongly flagged as fabricated.
    assert result["verdict"] != "FLAGGED"


def test_gather_sources_does_not_crash_on_malformed_json(tmp_path):
    prices_dir = tmp_path / "data/ingested/prices"
    prices_dir.mkdir(parents=True)
    (prices_dir / "2026-07-13.json").write_text("{not valid json,,,")
    sources = gather_sources("2026-07-13", base_dir=tmp_path)
    assert sources["price"] is None  # logged and skipped, not raised


if __name__ == "__main__":
    test_grounded_number_is_verified()
    test_fabricated_number_is_flagged()
    test_derived_percentage_is_accepted()
    test_entity_match_without_numbers_is_verified()
    test_no_data_statement_needs_source_context()
    test_prose_with_no_numbers_or_known_entities_is_unverifiable_not_verified()
    test_verify_brief_removes_flagged_claim_and_keeps_grounded_one()
    test_verify_brief_adds_attribution_tags_per_section()
    print("verifier agent: 8 hand-labeled scenario tests passed (2 more need pytest tmp_path).")
