# Energy Trading Briefing Agent — Scope Note (v1.0)

**Project:** PS130 — LLM + Agentic AI for Market Operations
**Region:** Karnataka (Southern grid / IEX Southern region pricing)
**Prepared for:** Jashwanth, Dayananda Sagar University

## What this system does
Produces a daily, structured trading brief for a human trader by fusing four
heterogeneous sources — market prices, weather, outages, and regulatory news —
through a multi-agent pipeline (4 ingestion agents → cross-referencing agent →
synthesis/orchestrator agent → verifier agent). The brief highlights what
changed since the previous day and flags compounded risks across sources
(e.g., an outage in a zone experiencing a heatwave).

## What this system explicitly does NOT do
- **It does not forecast prices or generate trading signals.** No buy/sell/hold
  recommendation, no predicted price direction, no probability of a price move.
- It does not place or simulate trades.
- It does not replace a trader's judgment — it synthesizes and verifies
  information; the trader still decides.

This boundary must stay explicit through every phase, especially Phase 5
(evaluation), where all metrics are information-quality metrics (accuracy,
completeness, grounding, usefulness) — never predictive accuracy.

## In-scope data sources (Karnataka)
- **Prices:** IEX India day-ahead market clearing price (Southern region as
  proxy, since Karnataka has no standalone DAM price).
- **Weather:** OpenWeatherMap (primary), IMD referenced for credibility.
- **Outages:** Karnataka SLDC / Grid-India (SRLDC) generation & grid-event
  bulletins (primary). BESCOM distribution-level outage circulars are
  out-of-scope for v1 (lower market relevance) — noted as future work.
- **Regulatory:** KERC orders/notices (primary), CERC items relevant to
  Karnataka (secondary).

## Success criteria
- High extraction accuracy per source (vs. hand-labeled test set)
- Low hallucination rate (every claim traceable to source JSON)
- High relevance precision (regulatory noise correctly dropped)
- Positive human-usefulness rubric score (clarity, actionability,
  prioritization, length) across 15–20 historical test days

## Repo structure (established)
```
energy-trading-briefing-agent/
├── ingestion/        # 4 ingestion agents + shared output schema
├── agents/           # cross-referencing, synthesis/orchestrator, verifier
├── eval/             # hand-labeled test sets, metrics scripts
├── data/historical/  # prices/ weather/ outages/ regulatory/ (dated folders)
└── briefs/           # generated daily briefs
```

## Open decisions to revisit in Phase 1
- Exact SLDC/Grid-India bulletin URL and whether PDFs are directly downloadable.
- Whether IEX historical bulk data requires registration.
- Final list of Karnataka load-center cities to track for weather (start with
  Bengaluru; consider Hubli-Dharwad or Mangaluru if time allows for
  zone-level compounded-risk detection).
