"""
Phase 5 -- Baseline Agent (naive single-prompt summarizer)

Execution plan definition: "Multi-agent pipeline vs. naive single-prompt
summarizer on same days" -> Qualitative + rubric delta.

This deliberately reproduces what a NON-agentic approach would do, as the
comparison point for your report:
  - No materiality scoring, no threshold filtering -- dumps every raw item.
  - No cross-referencing agent -- no compounded-risk detection at all.
  - No verifier agent -- whatever the model writes is what gets released.
  - No delta-vs-yesterday logic -- every day looks like the first day.
  - One shot, one prompt, no fixed prompt template refinement.

It reuses the SAME raw per-source JSON your real pipeline reads (via
gather_sources), so the input DATA is identical between baseline and
pipeline -- the only variable being measured is architecture, not data
quality. This is what makes the comparison fair/attributable.

Output: briefs/<date>_baseline.md + briefs/<date>_baseline_prompt_and_response.json
(mirrors the real synthesis agent's audit-trail convention so the verifier
agent can be pointed at either one interchangeably.)

CLI:
    python agents/baseline_agent.py 2026-07-13
    python agents/baseline_agent.py --all
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from verifier_agent import gather_sources  # same independent loader as the real pipeline

ROOT = Path(__file__).parent.parent
BRIEFS_DIR = ROOT / "briefs"

# Same LLM_PROVIDER switch as synthesis_agent.py -- keeping both agents on the
# same provider for a given day is what makes the baseline-vs-pipeline
# comparison fair (architecture is the only variable, not model/vendor).
PROVIDER = os.environ.get("LLM_PROVIDER", "anthropic").lower()

ANTHROPIC_MODEL = os.environ.get("SYNTHESIS_MODEL", "claude-sonnet-5")
NVIDIA_MODEL = os.environ.get("SYNTHESIS_MODEL", "meta/llama-3.3-70b-instruct")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

NAIVE_PROMPT = """You are a trading assistant. Below is today's raw data on \
electricity prices, weather, outages, and regulatory news for Karnataka. \
Write a summary brief for a trader covering anything notable.

{raw_json}
"""


def _call_anthropic(prompt: str):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set -- baseline_agent refuses to run without it. "
                            "Set it, or switch LLM_PROVIDER=nvidia_nim for the free-tier option.")
    import urllib.request
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps({
            "model": ANTHROPIC_MODEL,
            "max_tokens": 1500,
            "messages": [{"role": "user", "content": prompt}],
        }).encode(),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    text_blocks = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    brief_text = "\n".join(text_blocks).strip()
    usage = data.get("usage", {})
    return brief_text, ANTHROPIC_MODEL, usage, data


def _call_nvidia_nim(prompt: str):
    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        raise RuntimeError("NVIDIA_API_KEY not set. Get a free key at build.nvidia.com -> API keys, "
                            "then: os.environ['NVIDIA_API_KEY'] = 'nvapi-...'. baseline_agent refuses "
                            "to run without a real key -- no templated fallback.")
    import urllib.request
    req = urllib.request.Request(
        f"{NVIDIA_BASE_URL}/chat/completions",
        data=json.dumps({
            "model": NVIDIA_MODEL,
            "max_tokens": 1500,
            "messages": [{"role": "user", "content": prompt}],
        }).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    brief_text = (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    usage = {
        "input_tokens": data.get("usage", {}).get("prompt_tokens"),
        "output_tokens": data.get("usage", {}).get("completion_tokens"),
    }
    return brief_text, NVIDIA_MODEL, usage, data


def process_day(as_of_date: str, base_dir: Path = ROOT):
    """Mirrors synthesis_agent.process_day's contract: raises RuntimeError
    if no API key is set rather than silently degrading to templated text --
    the baseline should fail the same honest way the real pipeline does, so
    the comparison isn't confounded by one of them having a safety net the
    other lacks."""
    sources = gather_sources(as_of_date, base_dir)
    raw_json = json.dumps(sources, indent=2)
    prompt = NAIVE_PROMPT.format(raw_json=raw_json)

    if PROVIDER == "nvidia_nim":
        brief_text, model_used, usage, raw_response = _call_nvidia_nim(prompt)
    elif PROVIDER == "anthropic":
        brief_text, model_used, usage, raw_response = _call_anthropic(prompt)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER={PROVIDER!r} -- use 'anthropic' or 'nvidia_nim'")

    if not brief_text:
        raise RuntimeError(f"baseline_agent got no text content back: {raw_response}")

    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    (BRIEFS_DIR / f"{as_of_date}_baseline.md").write_text(brief_text)
    (BRIEFS_DIR / f"{as_of_date}_baseline_prompt_and_response.json").write_text(json.dumps({
        "as_of_date": as_of_date,
        "provider": PROVIDER,
        "model": model_used,
        "user_message": prompt,
        "raw_response": raw_response,
        "usage": usage,
    }, indent=2))
    return brief_text


def main():
    args = sys.argv[1:]
    if args and args[0] == "--all":
        # NOTE: data/ingested/* are source directories (prices/, weather/, outages/,
        # regulatory/, cross_reference/), not per-date directories -- globbing that
        # level and treating names as dates would silently feed "prices", "weather"
        # etc. into process_day() as bogus dates. Dates live as filenames one level
        # down, inside data/ingested/prices/<date>.json (mirrors synthesis_agent.py's
        # own --all logic, which uses this same directory as its date source).
        dates = sorted(p.stem for p in (ROOT / "data" / "ingested" / "prices").glob("*.json"))
    elif args:
        dates = [args[0]]
    else:
        print("Usage: python agents/baseline_agent.py <date> | --all")
        return

    for d in dates:
        try:
            process_day(d)
            print(f"{d}: baseline brief -> briefs/{d}_baseline.md")
        except RuntimeError as e:
            print(f"{d}: FAILED -- {e}")


if __name__ == "__main__":
    main()
