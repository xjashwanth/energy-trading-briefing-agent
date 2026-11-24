"""
Phase 3 -- Synthesis / Orchestrator Agent

Input:  today's ingested outputs from all 5 upstream agents (4 ingestion +
        cross-reference)
Output: briefs/<date>.md  (the actual daily trading brief, prose)
        briefs/<date>_prompt_and_response.json  (audit trail: exact prompt
        sent + exact raw response -- Phase 4's verifier agent needs this to
        check every sentence against source JSON, and it's also just good
        practice for an LLM-in-the-loop system: never let the only record
        of what was sent/received be a terminal scrollback)

This is the one agent in the pipeline that calls a real LLM (per the
execution plan: "Orchestrator/synthesis agent: take all 4 structured
outputs + correlation flags, and draft the brief using an LLM with a fixed
prompt template.") It requires a REAL API call -- it does NOT fall back to
fabricated/templated prose without one, because that would defeat the point
of having a verifier agent in Phase 4 check LLM output against source JSON.

Backend: NVIDIA NIM only (build.nvidia.com, free tier, OpenAI-compatible,
hosts open models -- Llama, Nemotron, etc.). Needs NVIDIA_API_KEY.

Which model produced a given brief is recorded in the audit JSON
(`briefs/<date>_prompt_and_response.json` -> "model" field) so Phase 4 and
Phase 5 can note it if model choice ever affects grounding quality.

Usage:
    export NVIDIA_API_KEY=nvapi-...
    python agents/synthesis_agent.py 2026-07-13

    python agents/synthesis_agent.py --all           # every date with price data,
                                                       # SKIPS dates that already
                                                       # have a brief written
    python agents/synthesis_agent.py --all --force    # regenerate every date,
                                                       # even ones already done
"""

import json
import os
import sys
from pathlib import Path

try:
    import openai
except ImportError:
    openai = None

# Validated with hosted NVIDIA NIM in Colab; override via SYNTHESIS_MODEL.
NVIDIA_MODEL = os.environ.get("SYNTHESIS_MODEL", "nvidia/nemotron-3-super-120b-a12b")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

PROMPT_PATH = Path(__file__).parent / "prompts" / "synthesis_prompt.md"


def _load(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_system_prompt() -> str:
    text = PROMPT_PATH.read_text(encoding="utf-8-sig")
    # Everything after the "---" divider is the actual prompt; the header
    # above it is documentation for humans reading the .md file, not for the model.

    return text.split("---", 1)[1].strip()


try:
    from .source_loading import gather_sources
except ImportError:
    from source_loading import gather_sources


def build_user_message(as_of_date: str, sources: dict) -> str:
    return (
        f"Target date: {as_of_date}\n\n"
        f"Source data (JSON):\n```json\n{json.dumps(sources, indent=2)}\n```\n\n"
        f"Draft today's brief following the required structure exactly."
    )


def _call_nvidia_nim(system_prompt: str, user_message: str):
    if openai is None:
        raise RuntimeError("pip install openai --break-system-packages  (NVIDIA NIM uses the OpenAI-compatible client)")
    if not os.environ.get("NVIDIA_API_KEY"):
        raise RuntimeError(
            "NVIDIA_API_KEY not set. Get a free key at build.nvidia.com -> API keys, "
            "then: os.environ['NVIDIA_API_KEY'] = 'nvapi-...'. This agent will not "
            "fabricate a brief without a real key."
        )
    client = openai.OpenAI(base_url=NVIDIA_BASE_URL, api_key=os.environ["NVIDIA_API_KEY"], timeout=90.0)
    
    candidate_models = [
        NVIDIA_MODEL,
        "meta/llama-3.1-8b-instruct",
        "meta/llama-3.3-70b-instruct",
        "meta/llama-3.1-70b-instruct",
        "nvidia/nemotron-4-340b-instruct"
    ]
    # Deduplicate while preserving order
    seen = set()
    models_to_try = [m for m in candidate_models if not (m in seen or seen.add(m))]
    
    last_err = None
    for model_name in models_to_try:
        try:
            print(f"Connecting to NVIDIA NIM model: {model_name}...")
            response = client.chat.completions.create(
                model=model_name,
                max_tokens=2048,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}} if model_name.startswith("nvidia/nemotron-3-") else {},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            )
            text = response.choices[0].message.content
            usage = {"input_tokens": response.usage.prompt_tokens, "output_tokens": response.usage.completion_tokens} \
                if getattr(response, "usage", None) else {"input_tokens": None, "output_tokens": None}
            print(f"âœ… Success with {model_name}!")
            return text, model_name, usage
        except Exception as e:
            print(f"  Failed {model_name}: {str(e)[:100]}")
            last_err = e
            continue
            
    raise last_err


def call_synthesis(as_of_date: str, sources: dict) -> tuple:
    system_prompt = load_system_prompt()
    user_message = build_user_message(as_of_date, sources)

    text, model_used, usage = _call_nvidia_nim(system_prompt, user_message)

    audit = {
        "provider": "nvidia_nim", "model": model_used,
        "system_prompt": system_prompt, "user_message": user_message,
        "raw_response_text": text, "usage": usage,
    }
    return text, audit


def process_day(as_of_date: str, base_dir: Path = Path(".")) -> Path:
    sources = gather_sources(as_of_date, base_dir)
    if not sources["price"]:
        raise FileNotFoundError(f"No ingested price data for {as_of_date} -- run Phase 2 agents first.")

    brief_text, audit = call_synthesis(as_of_date, sources)

    briefs_dir = base_dir / "briefs"
    briefs_dir.mkdir(parents=True, exist_ok=True)

    brief_path = briefs_dir / f"{as_of_date}.md"
    brief_path.write_text(f"# Energy Trading Brief -- Karnataka (Southern Grid) -- {as_of_date}\n\n{brief_text}\n", encoding="utf-8")

    audit_path = briefs_dir / f"{as_of_date}_prompt_and_response.json"
    audit_path.write_text(json.dumps(audit, indent=2))

    return brief_path


def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: python agents/synthesis_agent.py <YYYY-MM-DD>  |  --all [--force]")
        return

    force = "--force" in args
    args = [a for a in args if a != "--force"]

    if args[0] == "--all":
        dates = sorted(p.stem for p in Path("data/ingested/prices").glob("*.json"))
    else:
        dates = [args[0]]
        # An explicit single-date call always (re)generates that date, --force
        # or not -- skip-if-exists only applies to batch (--all) runs, where
        # the point is resuming an interrupted long run without re-paying for
        # days you already have.
        force = True

    briefs_dir = Path("briefs")
    skipped = 0

    failed = False
    for d in dates:
        existing = briefs_dir / f"{d}.md"
        if not force and existing.exists():
            skipped += 1
            continue
        try:
            path = process_day(d)
            print(f"{d}: brief written -> {path}")
        except Exception as e:
            failed = True
            print(f"{d}: FAILED -- {e}")

    if skipped:
        print(f"\n{skipped} date(s) skipped -- already had a brief. "
              f"Re-run with --force to regenerate everything.")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
