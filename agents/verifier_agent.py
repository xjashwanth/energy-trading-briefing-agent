"""Conservative release verifier.
Only complete statements copied from dated source items are released. This
preserves entity, unit and date bindings but does not independently establish
source truth. Unsupported paraphrases and calculations are removed. Legacy
numeric helpers remain for diagnostic evaluation, never release approval.
"""

import json
import re
import sys
from pathlib import Path
from typing import Optional

# ---- Config ----
NUMBER_PATTERN = re.compile(r'[-+]?\d[\d,]*\.?\d*')
# ISO dates ("2026-07-13") and year ranges ("2026-2029") must be stripped before
# number extraction -- otherwise the hyphen gets read as a minus sign and a date
# gets shredded into fake "numbers" (2026, -07, -13) that will never match
# anything, since dates live in source JSON as date-strings, not numeric fields.
DATE_PATTERN = re.compile(r'\b\d{4}-\d{2}-\d{2}\b|\b\d{4}\s*-\s*\d{4}\b')
REL_TOLERANCE = 0.01     # 1% relative tolerance, absorbs rounding drift (e.g. brief
                          # says "125.7%", source stores 125.68234...)
ABS_TOLERANCE = 0.05     # small absolute tolerance for near-zero values where a
                          # relative tolerance would be meaningless
DERIVED_TOLERANCE = 0.6  # looser tolerance when checking whether a number is a
                          # plausible arithmetic combination of two present numbers

SECTION_SOURCE_MAP = {
    "Price movement & drivers": "price",
    "Weather-demand linkage": "weather",
    "Outage watch": "outage",
    "Regulatory flags": "regulatory",
    "Compounded risk flags": "cross_reference",
    # TL;DR deliberately excluded -- it draws from multiple sources at once,
    # so no single attribution tag applies; each of its claims is still
    # checked as a claim, just not tagged with one section source.
}

NEGATIVE_PHRASES = (
    "no outages", "no items", "none reported", "not reported", "no cross-reference",
    "no regulatory", "no data", "no weather", "no price data",
)


# ---------------------------------------------------------------------------
# Source loading -- independent re-read, mirrors synthesis_agent.gather_sources
# but must never crash on missing/malformed files (Phase 4 requirement:
# "malformed PDF/file -> pipeline logs and skips, does not crash").
# ---------------------------------------------------------------------------

def _load(path: Optional[Path]):
    if not path or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"WARNING: could not parse {path} ({e}) -- treating as no data for this source.")
        return None


try:
    from .source_loading import gather_sources
except ImportError:
    from source_loading import gather_sources


# ---------------------------------------------------------------------------
# Ground-truth extraction from source JSON
# ---------------------------------------------------------------------------

def _normalize_number(x) -> Optional[float]:
    try:
        return round(float(x), 4)
    except (TypeError, ValueError):
        return None


def _numbers_in_text(text: str) -> list:
    """Extract numeric values from free text, ignoring date-like substrings
    (ISO dates, year ranges) so e.g. '2026-07-13' or '2026-2029' don't get
    shredded into fake numbers like 2026, -07, -13."""
    stripped = DATE_PATTERN.sub(' ', text)
    out = []
    for m in NUMBER_PATTERN.findall(stripped):
        if m in ('', '-', '+', '.'):
            continue
        try:
            out.append(float(m.replace(',', '')))
        except ValueError:
            continue
    return out


def collect_source_numbers(sources: dict) -> set:
    """Recursively walk every source's JSON, collecting every numeric value --
    both fields that are typed as int/float AND numbers embedded in string
    fields (e.g. a `delta_from_previous` field like "up 125.7% from prior
    day" -- a real figure the ingestion agent already computed, just stored
    as prose rather than a bare number). This is the full set of numbers the
    synthesis agent could possibly have seen; any number in the brief must
    trace back to something in here."""
    numbers = set()

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, (int, float)) and not isinstance(node, bool):
            n = _normalize_number(node)
            if n is not None:
                numbers.add(n)
        elif isinstance(node, str):
            for n in _numbers_in_text(node):
                norm = _normalize_number(n)
                if norm is not None:
                    numbers.add(norm)

    for src_data in sources.values():
        if src_data:
            walk(src_data)
    return numbers


def collect_source_entities(sources: dict) -> set:
    """Collect known names/phrases (headlines, plant/zone names, filing types,
    detail text) that non-numeric claims can be grounded against."""
    entities = set()

    def add_text(t):
        if isinstance(t, str) and t.strip():
            entities.add(t.strip().lower())

    for src_data in sources.values():
        if not src_data:
            continue
        for item in src_data.get("items", []):
            add_text(item.get("headline"))
            add_text(item.get("grid_zone"))
            add_text(item.get("detail"))
            for v in (item.get("raw_values") or {}).values():
                if isinstance(v, str):
                    add_text(v)
    return entities


def has_any_items(sources: dict, source_key: str) -> bool:
    data = sources.get(source_key)
    return bool(data and data.get("items"))


# ---------------------------------------------------------------------------
# Number grounding logic
# ---------------------------------------------------------------------------

def number_is_grounded(n: float, source_numbers: set) -> bool:
    if n in source_numbers:
        return True
    for sn in source_numbers:
        if abs(n - sn) <= ABS_TOLERANCE:
            return True
        if sn != 0 and abs(n - sn) / abs(sn) <= REL_TOLERANCE:
            return True
    return False


def derived_from_two(n: float, source_numbers: set) -> bool:
    """Permissive check: is n plausibly computed from two numbers that ARE
    present (a-b, (a-b)/b*100, a/b*100)? Exists so legitimate arithmetic on
    given facts -- e.g. a peak-hour premium computed from two present
    averages -- isn't flagged as fabrication just because the % itself isn't
    a literal field in the JSON. Only covers pairwise combinations; anything
    beyond that is out of scope for v1 and will be (correctly) flagged."""
    nums = list(source_numbers)
    for a in nums:
        for b in nums:
            if a == b:
                continue
            if abs((a - b) - n) <= DERIVED_TOLERANCE:
                return True
            if b != 0:
                if abs((a - b) / b * 100 - n) <= DERIVED_TOLERANCE:
                    return True
                if abs(a / b * 100 - n) <= DERIVED_TOLERANCE:
                    return True
    return False


# ---------------------------------------------------------------------------
# Brief parsing
# ---------------------------------------------------------------------------

def split_sections(brief_text: str) -> dict:
    """Split the brief markdown into its required sections by ## headers."""
    parts = re.split(r'\n(?=## )', brief_text)
    sections = {}
    for part in parts:
        m = re.match(r'##\s*(.+?)\n', part)
        if m:
            sections[m.group(1).strip()] = part[m.end():].strip()
    return sections


def split_sentences(text: str) -> list:
    sentences = []
    for line in text.splitlines():
        line = re.sub(r'^\s*[\*\-]\s*', '', line).strip()
        if line and not line.startswith('_Source:'):
            sentences.extend(re.split(r'(?<=[.!?])\s+(?=[A-Z0-9*])', line))
    return [s.strip() for s in sentences if s.strip()]


# ---------------------------------------------------------------------------
# Claim checking
# ---------------------------------------------------------------------------

def check_claim(sentence: str, source_numbers: set, source_entities: set) -> dict:
    """Legacy numeric diagnostic only; release verification uses source statements."""
    numbers = _numbers_in_text(sentence)

    if not numbers:
        lower = sentence.lower()
        if any(p in lower for p in NEGATIVE_PHRASES):
            return {"verdict": "UNVERIFIABLE", "reason": "absence requires source-aware verification"}
        if any(len(e) > 6 and (e in lower or lower in e) for e in source_entities):
            return {"verdict": "VERIFIED", "reason": "matches a known source entity/phrase"}
        return {"verdict": "UNVERIFIABLE", "reason": "no numeric or recognizable entity content to check"}

    unverified = []
    for n in numbers:
        if number_is_grounded(n, source_numbers) or derived_from_two(n, source_numbers):
            continue
        unverified.append(n)

    if unverified:
        return {"verdict": "FLAGGED", "reason": f"number(s) not found in source JSON: {unverified}"}
    return {"verdict": "VERIFIED", "reason": "all numbers grounded in source JSON"}


def check_source_claim(sentence, sources, source_key=None):
    """Conservative extractive verification, preserving entity/unit/date bindings.

    A full source statement must match, not an arbitrary collection of numbers.
    Source truth and causal interpretations are outside this check's scope.
    """
    def canonical(text):
        text = re.sub(r'\[([\w-]+)\]', '', text)
        return ' '.join(text.replace('**', '').strip().rstrip('.').split()).casefold()

    claim = canonical(sentence)
    domains = {source_key: sources.get(source_key)} if source_key else sources
    for key, data in domains.items():
        no_data = canonical(f"No {key} data available for this date.")
        no_items = canonical(f"No {key} items reported for this date.")
        if claim == no_data and data is None:
            return {"verdict": "VERIFIED", "reason": "source is unavailable", "source": key}
        if claim == no_items and data is not None and not data.get('items'):
            return {"verdict": "VERIFIED", "reason": "source items array is empty", "source": key}
        for item in (data or {}).get('items', []):
            citations = re.findall(r'\[([\w-]+)\]', sentence)
            if citations and citations != [item.get('item_id')]:
                continue
            for field in ('headline', 'detail', 'delta_from_previous'):
                evidence = item.get(field)
                if not isinstance(evidence, str):
                    continue
                statements = [evidence, *split_sentences(evidence)]
                if any(claim == canonical(s) for s in statements):
                    return {"verdict": "VERIFIED", "reason": "matches a complete source statement",
                            "source": key, "item_id": item.get('item_id'), "field": field,
                            "source_reference": item.get('source_reference')}
    return {"verdict": "FLAGGED", "reason": "no matching source statement with the same entity, units and dates"}


def attribution_tag(source_key: str, sources: dict) -> str:
    data = sources.get(source_key)
    if not data:
        return f"_Source: {source_key} -- no data available for this date._"
    as_of = data.get("as_of_date") or data.get("window_start", "unknown date")
    generated = data.get("generated_at", "unknown time")
    n_items = len(data.get("items", []))
    return f"_Source: {source_key} -- {n_items} item(s), as-of {as_of}, generated {generated}._"


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def verify_brief(brief_text: str, sources: dict) -> tuple:
    """Pure function: brief text + sources in, (rebuilt_brief_text, report) out.
    Kept separate from file I/O so it's directly unit-testable."""
    sections = split_sections(brief_text)
    if not sections:
        raise ValueError("Brief has no Markdown sections to verify")

    # Never copy arbitrary unverified prose from the preamble into the release.
    title_line = brief_text.splitlines()[0] if brief_text.startswith("# ") else "# Source-grounded briefing"
    rebuilt = [title_line, ""]
    report = {"sections": {}}

    for header, body in sections.items():
        if header not in SECTION_SOURCE_MAP and header != "TL;DR":
            raise ValueError(f"Unexpected brief section: {header}")
        sentences = split_sentences(body)
        kept, section_report = [], []

        for s in sentences:
            verdict = check_source_claim(s, sources, SECTION_SOURCE_MAP.get(header))
            section_report.append({"claim": s, **verdict})
            if verdict["verdict"] == "VERIFIED":
                kept.append(s)
            # FLAGGED claims are dropped from the release brief, never rewritten
            # silently -- rewriting risks introducing a different unverified claim.

        flagged = sum(1 for r in section_report if r["verdict"] == "FLAGGED")
        verified = sum(1 for r in section_report if r["verdict"] == "VERIFIED")
        unverifiable = sum(1 for r in section_report if r["verdict"] == "UNVERIFIABLE")
        report["sections"][header] = {
            "claims_checked": len(section_report),
            "verified": verified,
            "unverifiable": unverifiable,
            "flagged": flagged,
            "details": section_report,
        }

        rebuilt.append(f"## {header}")
        rebuilt.append("\n".join("- " + claim for claim in kept) if kept else "No verifiable items for this section.")
        src_key = SECTION_SOURCE_MAP.get(header)
        if src_key:
            rebuilt.append(attribution_tag(src_key, sources))
        if flagged:
            rebuilt.append(
                f"_Verifier note: {flagged} claim(s) removed -- could not be traced to "
                f"source JSON. See verification report for details._"
            )
        rebuilt.append("")

    total_claims = sum(v["claims_checked"] for v in report["sections"].values())
    total_verified = sum(v["verified"] for v in report["sections"].values())
    total_unverifiable = sum(v["unverifiable"] for v in report["sections"].values())
    total_flagged = sum(v["flagged"] for v in report["sections"].values())
    report["summary"] = {
        "verification_method": "exact_source_statement_v1",
        "total_claims_checked": total_claims,
        "total_verified": total_verified,
        "total_unverifiable": total_unverifiable,
        "total_flagged": total_flagged,
        # Strict: only claims we actually confirmed against source JSON.
        "verified_rate_pct": (
            round(100 * total_verified / total_claims, 1) if total_claims else None
        ),
        # Loose: everything that wasn't caught as fabricated -- includes
        # UNVERIFIABLE prose we simply had nothing to check. Kept for
        # backward compatibility with earlier runs; prefer verified_rate_pct
        # when reporting faithfulness in Phase 5.
        "not_flagged_rate_pct": (
            round(100 * (total_claims - total_flagged) / total_claims, 1) if total_claims else None
        ),
    }

    return "\n".join(rebuilt), report


def process_day(as_of_date: str, base_dir: Path = Path(".")) -> Path:
    brief_path = base_dir / "briefs" / f"{as_of_date}.md"
    if not brief_path.exists():
        raise FileNotFoundError(f"No draft brief found at {brief_path} -- run Phase 3 first.")

    brief_text = brief_path.read_text(encoding="utf-8-sig")
    sources = gather_sources(as_of_date, base_dir)

    verified_text, report = verify_brief(brief_text, sources)
    report["as_of_date"] = as_of_date

    verified_path = base_dir / "briefs" / f"{as_of_date}_verified.md"
    verified_path.write_text(verified_text, encoding="utf-8")

    report_path = base_dir / "briefs" / f"{as_of_date}_verification_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return verified_path


def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: python agents/verifier_agent.py <YYYY-MM-DD>  |  --all")
        return

    if args[0] == "--all":
        briefs_dir = Path("briefs")
        dates = sorted(
            p.stem for p in briefs_dir.glob("*.md")
            if not p.stem.endswith("_verified")
        ) if briefs_dir.exists() else []
    else:
        dates = [args[0]]

    failed = False
    for d in dates:
        try:
            path = process_day(d)
            report_path = Path("briefs") / f"{d}_verification_report.json"
            report = json.loads(report_path.read_text())
            summary = report["summary"]
            print(
                f"{d}: verified brief -> {path}  "
                f"({summary['total_claims_checked']} claims: "
                f"{summary['total_verified']} verified, "
                f"{summary['total_unverifiable']} unverifiable, "
                f"{summary['total_flagged']} flagged -- "
                f"{summary['verified_rate_pct']}% strictly grounded, "
                f"{summary['not_flagged_rate_pct']}% not flagged)"
            )
        except FileNotFoundError as e:
            failed = True
            print(f"{d}: SKIPPED -- {e}")
        except Exception as e:
            failed = True
            print(f"{d}: FAILED -- {e}")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
