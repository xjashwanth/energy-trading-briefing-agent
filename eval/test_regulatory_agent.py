"""
Phase 2 -- Hand-labeled test set for the regulatory agent's relevance
classifier (classify_relevance).

Cases 1-3 use the REAL curated items from 2026-04-19_to_2026-07-17.json
(the actual Phase 1 regulatory dataset). Cases 4-6 are clearly-marked
SYNTHETIC TEST FIXTURES -- invented only to exercise the "drop routine/
non-material filings" branch, which the real 3-item dataset doesn't
otherwise cover. They use an obviously fake source_reference
("SYNTHETIC-TEST-FIXTURE") and a "[TEST FIXTURE]" headline prefix so
they can never be mistaken for real KERC filings if this file is read
out of context.

Run: pytest eval/test_regulatory_agent.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "ingestion"))
from regulatory_agent import classify_relevance

REAL_TRUE_UP_ITEM = {
    "headline": "KERC true-up order: BESCOM tariff up 56 paise/unit from May 2026",
    "detail": "KERC issued a true-up tariff order covering revenue shortfalls...",
    "raw_values": {"filing_type": "true-up tariff order", "commission": "KERC"},
}
REAL_SOLAR_HEARING_ITEM = {
    "headline": "KERC opens public hearing on solar tariff framework for 2026-2029",
    "detail": "Public notice issued announcing a hearing to determine tariff and regulatory framework...",
    "raw_values": {"filing_type": "public notice / hearing announcement", "scope": "solar generation tariff, including DSPV"},
}
REAL_DISTRIBUTION_CODE_ITEM = {
    "headline": "KERC issues draft amendment to Karnataka Electricity Distribution Code",
    "detail": "Draft First Amendment to the Karnataka Electricity Distribution Code (KEDC) 2026...",
    "raw_values": {"filing_type": "draft regulation amendment", "scope": "Distribution Code: metering, billing, technical connection standards, grid integration"},
}

# --- SYNTHETIC TEST FIXTURES (not real KERC filings) ---
SYNTHETIC_ROUTINE_ITEM = {
    "headline": "[TEST FIXTURE] KERC office holiday notice",
    "detail": "[TEST FIXTURE] Administrative circular announcing office closure for a public holiday.",
    "raw_values": {"filing_type": "administrative circular"},
    "source_reference": "SYNTHETIC-TEST-FIXTURE",
}
SYNTHETIC_STAFF_ITEM = {
    "headline": "[TEST FIXTURE] KERC staff appointment notice",
    "detail": "[TEST FIXTURE] Internal staff appointment order, no market or consumer-facing content.",
    "raw_values": {"filing_type": "staff appointment"},
    "source_reference": "SYNTHETIC-TEST-FIXTURE",
}
SYNTHETIC_WHOLESALE_ITEM = {
    "headline": "[TEST FIXTURE] KERC order on open access banking and wheeling charges",
    "detail": "[TEST FIXTURE] Hypothetical order revising open access banking and wheeling charges affecting generation scheduling.",
    "raw_values": {"filing_type": "order", "scope": "open access, banking, wheeling"},
    "source_reference": "SYNTHETIC-TEST-FIXTURE",
}

CASES = [
    (REAL_TRUE_UP_ITEM, True, 2, "retail true-up order: kept as context, not wholesale"),
    (REAL_SOLAR_HEARING_ITEM, True, 2, "procedural hearing notice: kept as low-priority watch item"),
    (REAL_DISTRIBUTION_CODE_ITEM, True, 2, "distribution/metering code: retail-only keywords"),
    (SYNTHETIC_ROUTINE_ITEM, False, 1, "[TEST FIXTURE] routine administrative circular: dropped"),
    (SYNTHETIC_STAFF_ITEM, False, 1, "[TEST FIXTURE] staff appointment: dropped"),
    (SYNTHETIC_WHOLESALE_ITEM, True, 4, "[TEST FIXTURE] wholesale keywords (open access/banking/wheeling): kept, high materiality"),
]


def test_classify_relevance():
    for item, expected_keep, expected_score, label in CASES:
        score, reason, keep = classify_relevance(item)
        assert keep == expected_keep, f"{label}: expected keep={expected_keep}, got {keep} ({reason})"
        assert score == expected_score, f"{label}: expected score={expected_score}, got {score}"


if __name__ == "__main__":
    test_classify_relevance()
    print(f"regulatory agent: {len(CASES)} hand-labeled cases passed "
          f"(3 real curated items + 3 clearly-marked synthetic drop/keep fixtures).")
