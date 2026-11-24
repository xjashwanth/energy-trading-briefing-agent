"""
Phase 3 -- Hand-labeled scenario tests for the cross-referencing agent.
Pure rule-based logic, no LLM/API involved, so these are proper
input->expected-output tests, not threshold approximations.

Run: pytest eval/test_cross_referencing_agent.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "agents"))
sys.path.insert(0, str(Path(__file__).parent.parent / "ingestion"))
from cross_referencing_agent import find_compounded_risks, diff_vs_yesterday


def _weather_item(zone, score, iid="weather-x-001"):
    return {"item_id": iid, "headline": f"{zone}: Heatwave watch (max 39C)",
            "materiality_score": score, "grid_zone": zone,
            "source_reference": "local:weather.json", "source_timestamp": "2026-07-18T00:00:00Z"}


def _outage_item(zone, score, mw=250, iid="outage-x-001"):
    return {"item_id": iid, "headline": f"RAICHUR TPS unit 1: {mw} MW affected",
            "materiality_score": score, "grid_zone": zone,
            "source_reference": "local:outage.json", "source_timestamp": "2026-07-18T00:00:00Z",
            "raw_values": {"planned_mw": mw, "forced_major_mw": 0, "forced_minor_mw": 0, "others_mw": 0}}


def _price_item(score, iid="price-x-001"):
    return {"item_id": iid, "headline": "Average DAM (Southern proxy) MCP: Rs 6000/MWh",
            "materiality_score": score,
            "source_reference": "local:price.json", "source_timestamp": "2026-07-18T00:00:00Z"}


def test_zone_matched_outage_plus_heatwave_flags_high():
    price_out = {"items": []}
    weather_out = {"items": [_weather_item("Bengaluru Urban", 4)]}
    outage_out = {"items": [_outage_item("Bengaluru Urban", 3)]}
    items = find_compounded_risks(price_out, weather_out, outage_out, "2026-07-18")
    zone_matches = [i for i in items if i["raw_values"]["risk_type"] == "outage_plus_heatwave_same_zone"]
    assert len(zone_matches) == 1
    assert zone_matches[0]["materiality_score"] == 5
    assert zone_matches[0]["grid_zone"] == "Bengaluru Urban"


def test_no_zone_match_still_flags_but_lower_confidence():
    price_out = {"items": []}
    weather_out = {"items": [_weather_item("Bengaluru Urban", 4)]}
    outage_out = {"items": [_outage_item("Raichur", 3)]}  # different zone than weather
    items = find_compounded_risks(price_out, weather_out, outage_out, "2026-07-18")
    unmatched = [i for i in items if i["raw_values"]["risk_type"] == "outage_plus_heatwave_unconfirmed_zone"]
    zone_matched = [i for i in items if i["raw_values"]["risk_type"] == "outage_plus_heatwave_same_zone"]
    assert len(unmatched) == 1
    assert len(zone_matched) == 0
    assert unmatched[0]["materiality_score"] == 3  # lower confidence than zone-matched (5)


def test_below_threshold_no_flag():
    price_out = {"items": []}
    weather_out = {"items": [_weather_item("Bengaluru Urban", 2)]}   # below HEATWAVE_THRESHOLD=4
    outage_out = {"items": [_outage_item("Bengaluru Urban", 1)]}     # below OUTAGE_THRESHOLD=3
    items = find_compounded_risks(price_out, weather_out, outage_out, "2026-07-18")
    assert items == []


def test_no_data_no_flag():
    items = find_compounded_risks(None, None, None, "2026-07-18")
    assert items == []


def test_price_spike_plus_outage():
    price_out = {"items": [_price_item(4)]}
    weather_out = {"items": []}
    outage_out = {"items": [_outage_item("Raichur", 3)]}
    items = find_compounded_risks(price_out, weather_out, outage_out, "2026-07-18")
    flagged = [i for i in items if i["raw_values"]["risk_type"] == "price_spike_plus_outage"]
    assert len(flagged) == 1
    assert flagged[0]["materiality_score"] == 4


def test_price_spike_plus_heatwave():
    price_out = {"items": [_price_item(5)]}
    weather_out = {"items": [_weather_item("Bengaluru Urban", 5)]}
    outage_out = {"items": []}
    items = find_compounded_risks(price_out, weather_out, outage_out, "2026-07-18")
    flagged = [i for i in items if i["raw_values"]["risk_type"] == "price_spike_plus_heatwave"]
    assert len(flagged) == 1
    assert flagged[0]["grid_zone"] == "Bengaluru Urban"


def test_price_below_threshold_no_spike_flags():
    price_out = {"items": [_price_item(2)]}  # below PRICE_SPIKE_THRESHOLD=4
    weather_out = {"items": [_weather_item("Bengaluru Urban", 5)]}
    outage_out = {"items": [_outage_item("Bengaluru Urban", 4)]}
    items = find_compounded_risks(price_out, weather_out, outage_out, "2026-07-18")
    price_flags = [i for i in items if "price" in i["raw_values"]["risk_type"]]
    assert price_flags == []
    # the zone-matched outage+heatwave flag should still fire independently
    assert any(i["raw_values"]["risk_type"] == "outage_plus_heatwave_same_zone" for i in items)


def test_delta_new_vs_persisting():
    items_day1 = [{"raw_values": {"risk_type": "outage_plus_heatwave_same_zone"}, "grid_zone": "Bengaluru Urban"}]
    result_day1 = diff_vs_yesterday(items_day1, None)
    assert result_day1[0]["delta_from_previous"] == "New today"

    prev_output = {"items": items_day1}
    items_day2 = [{"raw_values": {"risk_type": "outage_plus_heatwave_same_zone"}, "grid_zone": "Bengaluru Urban"}]
    result_day2 = diff_vs_yesterday(items_day2, prev_output)
    assert result_day2[0]["delta_from_previous"] == "Persisting from prior day"

    items_day3_different_zone = [{"raw_values": {"risk_type": "outage_plus_heatwave_same_zone"}, "grid_zone": "Raichur"}]
    result_day3 = diff_vs_yesterday(items_day3_different_zone, prev_output)
    assert result_day3[0]["delta_from_previous"] == "New today"  # different zone = different risk instance


if __name__ == "__main__":
    test_zone_matched_outage_plus_heatwave_flags_high()
    test_no_zone_match_still_flags_but_lower_confidence()
    test_below_threshold_no_flag()
    test_no_data_no_flag()
    test_price_spike_plus_outage()
    test_price_spike_plus_heatwave()
    test_price_below_threshold_no_spike_flags()
    test_delta_new_vs_persisting()
    print("cross-referencing agent: 8 hand-labeled scenario tests passed.")
