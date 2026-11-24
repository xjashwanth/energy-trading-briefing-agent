# Energy Trading Briefing Agent (PS130)

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Multi-Agent Architecture](https://img.shields.io/badge/architecture-multi--agent-green.svg)]()
[![Fact Grounded](https://img.shields.io/badge/verifier-deterministic--grounding-orange.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An end-to-end multi-agent AI system designed to produce **daily, factual, and verified market briefs** for energy market participants operating in **Karnataka (Southern Electricity Grid, India)**.

The system ingests and fuses data from **4 heterogeneous domains** (Market Prices, Weather, Power Outages, and Regulatory Filings), cross-references them for compounded market risks, synthesizes them into structured daily briefs using NVIDIA NIM, and matches release statements against source JSON using a deterministic verifier.

---

## Key Features

* **7 Specialized Pipeline Modules**:
  * **Rule-Based Ingestion Agents**: `price_agent`, `weather_agent`, `outage_agent`, `regulatory_agent`.
  * **Cross-Referencing Agent**: Rule-based compound risk detector.
  * **Synthesis Agent**: LLM orchestrator producing 6-section daily trading briefs.
  * **Verifier Agent**: Conservative verifier matching complete statements to dated source items.
* **Interactive Web Dashboard (`app.py`)**: Dark slate corporate style browser interface to browse briefs, inspect verification accuracy metrics, and manage pipeline runs.
* **Publication-Ready PDF Reports (`pdf_generator.py`)**: Instant corporate PDF report generation formatted for energy traders.
* **Automated Email PDF Dispatch (`email_service.py`)**: Send verified briefing PDF reports directly to any specified email address.
* **Source-grounding guardrails**: Only complete source statements are released; unsupported paraphrases and calculations are removed. This checks source fidelity, not whether the source itself is correct.

---

## Quickstart Guide

### 1. Clone & Install Dependencies

```bash
# Clone the repository
git clone https://github.com/your-username/energy-trading-briefing-agent.git
cd energy-trading-briefing-agent

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment (Optional for Offline Testing)

Copy `.env.example` to `.env` and add your API keys:

```bash
cp .env.example .env
```

```env
NVIDIA_API_KEY=nvapi-your-free-key-here
SMTP_SERVER=smtp.gmail.com
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-app-password
```

> **Note**: Generating a new brief requires `NVIDIA_API_KEY`. Without it, the run fails clearly and preserves existing briefs. Saved briefs can still be browsed, and unit tests run offline. `.env` is loaded by the dashboard/pipeline when python-dotenv is installed.

---

## Usage

### Option A: Interactive Web Dashboard (Recommended)

Launch the corporate styled web portal:

```bash
streamlit run app.py
```

Open your browser at `http://localhost:8501` to:
1. Browse daily briefs by business date.
2. Inspect claim grounding accuracy badges.
3. Download one-click PDF reports.
4. Send PDF reports directly to your email address.

---

### Option B: Master Pipeline CLI

Run the full multi-agent pipeline for a single date:

```bash
python run_pipeline.py --date 2026-07-13
```

Generate a PDF report and send it to an email address:

```bash
python run_pipeline.py --date 2026-07-13 --pdf --email trader@example.com
```

Process all historical dates:

```bash
python run_pipeline.py --all
```

---

## Multi-Agent Pipeline Architecture

```
                               ┌────────────────────────────────┐
                               │     4 Heterogeneous Sources    │
                               │ Prices, Weather, Outages, Regs │
                               └───────────────┬────────────────┘
                                               │
                                               ▼
                               ┌────────────────────────────────┐
                               │   Phase 2: Ingestion Agents    │
                               │   Deterministic & Schema JSON  │
                               └───────────────┬────────────────┘
                                               │
                                               ▼
                               ┌────────────────────────────────┐
                               │ Phase 3: Cross-Ref & Synthesis  │
                               │ Compound Risk + NVIDIA NIM     │
                               └───────────────┬────────────────┘
                                               │
                                               ▼
                               ┌────────────────────────────────┐
                               │  Phase 4: Verifier Guardrails  │
                               │  Strips Hallucinations + PDF   │
                               └───────────────┬────────────────┘
                                               │
                                               ▼
                               ┌────────────────────────────────┐
                               │ Web Dashboard & Email Dispatch │
                               │ PDF Briefs Delivered to Email  │
                               └────────────────────────────────┘
```

---

## Evaluation & Testing

See [the Phase 5 evaluation](PHASE5_README.md) for the historical Colab results, the baseline comparison, and the source extraction checks. Rescore the saved artifacts without making another model request:

```bash
python eval/phase5_audit.py
python eval/phase5_inputs.py
```

Run individual agent test suites:

```bash
python eval/test_price_agent.py
python eval/test_weather_agent.py
python eval/test_outage_agent.py
python eval/test_regulatory_agent.py
python eval/test_cross_referencing_agent.py
python eval/test_verifier_agent.py
python eval/test_robustness.py
```

---

## Explicit Trade Boundary & Disclaimer

> **This system explicitly DOES NOT forecast prices or generate trading signals.**
> - No price direction predictions or probability estimates.
> - No buy / sell / hold execution recommendations.
> - Purpose: Information synthesis, data verifiability, and risk awareness for human market operators.

---

## License

Distributed under the MIT License. See `LICENSE` for details.


## Reliability and validation

- Single-date runs ingest only that date, using the previous calendar day's
  weather/outage records when available. Existing normalized inputs may be reused.
- Future and undated regulatory publications are excluded. Retrospectively
  collected records are not proof of what was available at market open.
- Cross-reference deltas use only an earlier saved date. Heatwave flags require
  a heatwave weather headline, not merely a high materiality score.
- Synthesis selects and orders complete source statements. Unsupported paraphrases,
  new arithmetic, and unsupported absence claims are removed. This deliberately
  trades prose flexibility for auditable source fidelity.
- Dashboard/email percentages describe the share of draft claims matched to sources.
  Historical reports retain their legacy verification method until regenerated.
- Missing credentials, provider errors, verification failure, PDF failure, and
  email failure return failure status. A missing SMTP configuration is not delivery.
  Draft/report/PDF files are staged before publication; an operating-system lock prevents concurrent pipeline writers.
- Existing evaluation reports include small/synthetic samples and must not be
  presented as evidence of overall real-world accuracy.

Run the isolated offline regression suite without replacing saved briefs or reports:

```bash
python -m pytest eval/test_price_agent.py eval/test_weather_agent.py eval/test_outage_agent.py eval/test_regulatory_agent.py eval/test_cross_referencing_agent.py eval/test_verifier_agent.py eval/test_regressions.py eval/test_pipeline_status.py -q
```

Use `--no-pdf` to omit PDF generation. Batch runs return a nonzero exit status
if any date fails. No historical briefs are automatically regenerated by these fixes.


## Run and inspect a briefing

Check saved coverage and credentials without changing data:

```powershell
python run_pipeline.py --date 2026-07-13 --check
```

Set `NVIDIA_API_KEY` in your local `.env`, then generate and open the dashboard:

```powershell
python run_pipeline.py --date 2026-07-13 --require-complete --open
```

`--require-complete` stops before the provider call if a daily input is missing
or the regulatory collection window is more than seven days old relative to the
selected business date. Empty items are distinct from missing files. Retrospective
coverage does not prove availability at market open. Checks do not collect new data.

Each attempt writes `runs/<date>_<run-id>.json` with stage status, elapsed time,
coverage, verification summary, and published artifact path when available.
Records and logs are local and ignored by Git. A global OS lock prevents concurrent
pipeline writers and releases on process exit. A forced termination can leave an
old record marked running, but does not retain the execution lock.

In the dashboard, inspect **Source coverage**, click **Generate briefing**, then
**Refresh status**. Generation runs in a background process. Browsing does not
initiate generation. After a failed run, any displayed brief is labeled as an
older published version. Worker output is in `runs/<date>_worker.log`.

`--open` launches a local-only Streamlit server on an available port and opens the
selected date. The CLI prints its PID; this server stays running after generation.
This workflow is intended for a single local operator, not public deployment.


## Google Colab

Use `Energy_Trading_Briefing_Agent_Colab.ipynb` and follow `COLAB_DEMO_GUIDE.md`.
It loads your existing Colab secret, checks a genuine historical date, runs the
pipeline on local runtime disk, displays results inline, and saves outputs to
Drive. Upload the updated project files to Drive first. Use notebook downloads
instead of the desktop `--open` option.


## Offline readiness check

```powershell
python check_project.py --date 2026-07-13 --rehearse
```

Checks dependencies, Python/notebook syntax, source coverage, and isolated tests.
The rehearsal runs real ingestion, verification, and PDF generation in a temporary
workspace using a clearly labeled deterministic substitute for the LLM. It makes
no API calls or emails and preserves production briefs and data. Inspect
`output/readiness/2026-07-13/readiness.md` and the `rehearsal` subfolder.
These checks do not test live model output, Colab Secrets, or Drive persistence.
The Colab notebook repeats them before live generation. Wait for folder sync
to finish before starting Colab; no ZIP is required.
