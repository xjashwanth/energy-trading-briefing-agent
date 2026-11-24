"""
Phase 1 — Historical outage bulletin collection for the Energy Trading Briefing Agent.

Source: Central Electricity Authority (CEA) Daily Maintenance Report (Coal, Lignite
and Nuclear) — "Sub-Report-10" — published daily on the National Power Portal.
https://npp.gov.in/publishedReports

Why this source and not Karnataka SLDC (kptclsldc.in): the SLDC site's robots.txt
disallows automated access. CEA's national daily report is fetchable and, usefully,
already breaks generation-unit outages down by state — including a Karnataka section
(covers KAIGA, BELLARY TPS, RAICHUR TPS, TORANGALLU TPS as of mid-2026). This is a
generation-level outage feed (the kind that actually matters for trading), not a
distribution-level consumer outage feed like BESCOM's.

Caveat to note in your report: this only covers coal/lignite/nuclear units. Hydro
outages are reported separately by CEA (Sub-Report-7, "Daily Outage Report (Hydro
Units)") but that PDF was showing as unavailable ("N/A") as of this writing — check
the live page before assuming it's missing permanently. Karnataka draws meaningfully
from hydro, so flag this as a known gap in your data-source-access-note.

Usage (in Colab, after mounting Drive and cd-ing into the project root):
    pip install requests pdfplumber
    python ingestion/outage_historical_pull.py

Output:
  - Raw PDFs saved to data/historical/outages/raw_pdfs/<date>.pdf (for auditability —
    Phase 4's verifier agent should be able to trace any claim back to the source PDF)
  - Parsed Karnataka rows saved to data/historical/outages/<date>.json
"""

import json
import re
import time
from datetime import date, timedelta
from pathlib import Path

import requests

try:
    import pdfplumber
except ImportError:
    pdfplumber = None  # falls back to pypdf-only text extraction below

from pypdf import PdfReader

# ---- Config ----
START_DATE = date.today() - timedelta(days=90)
END_DATE = date.today() - timedelta(days=1)

RAW_DIR = Path("data/historical/outages/raw_pdfs")
OUT_DIR = Path("data/historical/outages")

BASE_URL = "https://npp.gov.in/public-reports/cea/daily/dgr/{ddmmyyyy}/dgr10-{yyyymmdd}.pdf"

# Every row in the source PDF repeats its state name, so we can split on these
# as row boundaries. Southern-region states are listed first since that's our
# region of interest, but the full list guards against a Karnataka row's
# "reason" text accidentally bleeding into the next region's rows.
STATE_MARKERS = [
    "Andhra Pradesh", "Karnataka", "Kerala", "Tamil Nadu", "Telangana", "Puducherry",
    "Haryana", "Punjab", "Rajasthan", "Uttar Pradesh", "Delhi", "Himachal Pradesh",
    "Jammu", "Uttarakhand", "Chandigarh",
    "Chhatisgarh", "Chhattisgarh", "Gujarat", "Madhya Pradesh", "Maharashtra", "Goa",
    "Bihar", "Jharkhand", "Odisha", "West Bengal", "Sikkim",
    "Assam", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Tripura",
    "Total Northern", "Total Western", "Total Southern", "Total Eastern", "Total",
]

ROW_PATTERN = re.compile(
    r'^Karnataka\s+(?P<station>.+?)\s+(?P<unit>\S+)\s+'
    r'(?P<planned_mw>[\d,]+\.\d{2})\s+(?P<forced_major_mw>[\d,]+\.\d{2})\s+'
    r'(?P<forced_minor_mw>[\d,]+\.\d{2})\s+(?P<others_mw>[\d,]+\.\d{2})\s+'
    r'(?P<start_date>\d{2}/\d{2}/\d{4})\s+(?P<start_time>\d{2}:\d{2})\s*'
    r'(?:(?P<end_date>\d{2}/\d{2}/\d{4})\s+(?P<end_time>\d{2}:\d{2})\s*)?'
    r'(?P<reason>.+)$'
)


def download_pdf(day: date) -> bytes | None:
    url = BASE_URL.format(ddmmyyyy=day.strftime("%d-%m-%Y"), yyyymmdd=day.strftime("%Y-%m-%d"))
    resp = requests.get(url, timeout=30)
    if resp.status_code != 200 or not resp.content.startswith(b"%PDF"):
        return None
    return resp.content


def extract_text(pdf_bytes: bytes) -> str:
    """Prefer pdfplumber (layout-aware); fall back to pypdf if unavailable."""
    tmp_path = Path("/tmp/_outage_tmp.pdf")
    tmp_path.write_bytes(pdf_bytes)

    if pdfplumber is not None:
        text_parts = []
        with pdfplumber.open(tmp_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text_parts.append(t)
        return "\n".join(text_parts)

    reader = PdfReader(tmp_path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def strip_boilerplate(text: str) -> str:
    """Remove repeated headers/footers that appear on every page."""
    lines = text.splitlines()
    keep = []
    for line in lines:
        if line.strip().startswith("https://npp.gov.in"):
            continue
        if "DAILY MAINTENANCE REPORT" in line:
            continue
        if re.match(r'^(State/System|Power Station|Unit No\.?|Planned|Forced|Others|Reasons|DD/MM/YYYY|Expected|HH24|1|2|3|4|5|6|7|8|9|10)$', line.strip()):
            continue
        keep.append(line)
    return "\n".join(keep)


def parse_karnataka_rows(text: str) -> list[dict]:
    flat = re.sub(r'\s+', ' ', text).strip()

    stop_pattern = '|'.join(re.escape(m) for m in STATE_MARKERS)
    chunks = re.split(rf'(?=(?:{stop_pattern})\s)', flat)
    karnataka_chunks = [c.strip() for c in chunks if c.startswith('Karnataka ')]

    rows = []
    for chunk in karnataka_chunks:
        m = ROW_PATTERN.match(chunk)
        if m:
            d = m.groupdict()
            # numeric cleanup
            for k in ("planned_mw", "forced_major_mw", "forced_minor_mw", "others_mw"):
                d[k] = float(d[k].replace(",", ""))
            rows.append(d)
        else:
            # Keep unparsed chunks visible rather than silently dropping data —
            # per Phase 4's "no hallucinated fallback" principle, we surface
            # extraction failures instead of guessing.
            rows.append({"unparsed_raw_text": chunk})
    return rows


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    day = START_DATE
    ok_count, missing_count, unparsed_count = 0, 0, 0

    while day <= END_DATE:
        day_str = day.isoformat()
        pdf_bytes = download_pdf(day)

        if pdf_bytes is None:
            missing_count += 1
            print(f"{day_str}: no PDF available (skipped)")
            day += timedelta(days=1)
            continue

        (RAW_DIR / f"{day_str}.pdf").write_bytes(pdf_bytes)

        raw_text = extract_text(pdf_bytes)
        clean_text = strip_boilerplate(raw_text)
        rows = parse_karnataka_rows(clean_text)

        n_unparsed = sum(1 for r in rows if "unparsed_raw_text" in r)
        unparsed_count += n_unparsed

        record = {
            "source": "outage",
            "region": "Karnataka",
            "as_of_date": day_str,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source_pdf": f"https://npp.gov.in/public-reports/cea/daily/dgr/{day.strftime('%d-%m-%Y')}/dgr10-{day.strftime('%Y-%m-%d')}.pdf",
            "raw_pdf_local_copy": str(RAW_DIR / f"{day_str}.pdf"),
            "rows": rows,
        }
        (OUT_DIR / f"{day_str}.json").write_text(json.dumps(record, indent=2))

        ok_count += 1
        status = f"{len(rows)} Karnataka rows"
        if n_unparsed:
            status += f" ({n_unparsed} unparsed — check manually)"
        print(f"{day_str}: {status}")

        time.sleep(0.3)  # be polite to the government server
        day += timedelta(days=1)

    print(f"\nDone. {ok_count} days pulled, {missing_count} missing, "
          f"{unparsed_count} rows flagged unparsed across the run.")
    if unparsed_count:
        print("Review unparsed rows manually — they're kept (not dropped) in the JSON "
              "so nothing silently disappears from the hand-labeled test set.")


if __name__ == "__main__":
    main()
