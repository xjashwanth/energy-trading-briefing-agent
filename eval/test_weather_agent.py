"""
Phase 2 -- Hand-labeled test set for the weather agent's impact-language
thresholds. Same scope caveat as test_price_agent.py: synthetic threshold
cases now, replace/extend with real Open-Meteo pulled days for Phase 5.

Run: pytest eval/test_weather_agent.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "ingestion"))
from weather_agent import temp_impact, precip_impact, wind_impact, zone_for

# --- temp_impact: 12 hand-labeled cases (temp_max_c, expected_score_min_label_substr) ---
TEMP_CASES = [
    (None, None, 1),
    (18.0, "Mild", 1),
    (25.0, "Mild", 1),
    (29.9, "Mild", 1),
    (30.0, "Warm", 2),
    (33.5, "Warm", 2),
    (34.9, "Warm", 2),
    (35.0, "Hot", 3),
    (37.9, "Hot", 3),
    (38.0, "Heatwave watch", 4),
    (39.9, "Heatwave watch", 4),
    (40.0, "Severe heatwave", 5),
    (42.0, "Severe heatwave", 5),
]

PRECIP_CASES = [
    (0.0, None, 1),
    (5.0, None, 1),
    (14.9, None, 1),
    (15.0, "Moderate", 2),
    (30.0, "Moderate", 2),
    (49.9, "Moderate", 2),
    (50.0, "Heavy", 4),
    (80.0, "Heavy", 4),
]

WIND_CASES = [
    (10.0, None, 1),
    (29.9, None, 1),
    (30.0, "Elevated", 2),
    (45.0, "Elevated", 2),
]


def test_temp_impact():
    for temp, substr, expected_score in TEMP_CASES:
        label, score = temp_impact(temp)
        assert score == expected_score, f"temp={temp}: expected score {expected_score}, got {score}"
        if substr:
            assert substr in label, f"temp={temp}: expected '{substr}' in label, got {label!r}"
        else:
            assert label is None


def test_precip_impact():
    for precip, substr, expected_score in PRECIP_CASES:
        label, score = precip_impact(precip)
        assert score == expected_score, f"precip={precip}: expected score {expected_score}, got {score}"
        if substr:
            assert substr in label
        else:
            assert label is None


def test_wind_impact():
    for wind, substr, expected_score in WIND_CASES:
        label, score = wind_impact(wind)
        assert score == expected_score
        if substr:
            assert substr in label
        else:
            assert label is None


def test_zone_for():
    assert zone_for("bengaluru.json") == "Bengaluru Urban"
    assert zone_for("unknown_city.json") == "unknown_city.json"


if __name__ == "__main__":
    test_temp_impact()
    test_precip_impact()
    test_wind_impact()
    test_zone_for()
    n = len(TEMP_CASES) + len(PRECIP_CASES) + len(WIND_CASES)
    print(f"weather agent: {n} hand-labeled threshold cases passed.")
