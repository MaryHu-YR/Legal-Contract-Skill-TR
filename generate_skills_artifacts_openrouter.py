#!/usr/bin/env python3
"""Generate contract-writing mistake guide and review rubrics via OpenRouter.

Uses the combined contract-review skills in this repo as source policy,
calls anthropic/claude-opus-4.6 (or OPENROUTER_MODEL), and writes two markdown files.

Usage:
  export OPENROUTER_API_KEY="your-key"
  python3 generate_skills_artifacts_openrouter.py
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import error, request

try:
    import certifi
except ImportError:
    certifi = None

# Reuse skill loading from review script
from review_contracts_openrouter import PRESETS, build_system_prompt, SKILL_REGISTRY

ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL = os.environ.get("OPENROUTER_MODEL", "anthropic/claude-opus-4.6")
DEFAULT_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
DEFAULT_OUTPUT_DIR = ROOT / "contract-review-skills-summary"

MISTAKES_OUTPUT = DEFAULT_OUTPUT_DIR / "contract-writing-common-mistakes.md"
RUBRICS_OUTPUT = DEFAULT_OUTPUT_DIR / "contract-review-rubrics.md"


def call_openrouter(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str,
    api_key: str,
    base_url: str,
    temperature: float,
    max_tokens: int,
    max_retries: int = 5,
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "HTTP-Referer": "https://github.com/local-contract-review",
        "X-Title": "Contract skills artifact generator",
    }
    ssl_context = ssl.create_default_context(cafile=certifi.where()) if certifi else None
    retryable = (
        error.HTTPError,
        error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        ConnectionResetError,
        ConnectionError,
        BrokenPipeError,
        OSError,
    )
    last_error: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        req = request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=300, context=ssl_context) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            choices = data.get("choices") or []
            if not choices:
                raise ValueError("No choices in response")
            content = choices[0].get("message", {}).get("content")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("Empty model content")
            return content.strip() + "\n"
        except retryable as exc:
            last_error = exc
            if attempt == max_retries:
                break
            wait = min(2**attempt, 15)
            print(f"  Retry {attempt}/{max_retries} after {type(exc).__name__}, wait {wait}s...", flush=True)
            time.sleep(wait)

    raise RuntimeError(f"OpenRouter failed after {max_retries} attempts: {last_error}")


MISTAKES_USER_PROMPT = """Based ONLY on the contract-review skills provided in the system message, produce a comprehensive markdown document titled:

# Common Contract Drafting Mistakes

Audience: lawyers, contract managers, and business drafters who want to avoid issues that reviewers flag.

Requirements:
1. Derive mistakes directly from what these review skills look for (red flags, missing protections, hidden risks, market benchmarks, negotiability, CUAD categories, freelancer traps, etc.).
2. Organize by category (e.g., Definitions, Term & Termination, Liability, IP, Payment, Compliance, Structure/Consistency).
3. For each mistake include:
   - **Mistake name**
   - **What drafters do wrong** (1–2 sentences)
   - **Why reviewers flag it** (link to review logic)
   - **Typical bad example** (short quoted clause pattern, not a full contract)
   - **Better drafting practice** (actionable fix)
   - **Severity**: Critical / Important / Minor
4. Include at least 35 distinct mistakes across multiple contract types (NDA, SaaS/MSA, employment, M&A, freelancer, vendor).
5. Add a final section: **Top 15 mistakes to catch before sending for signature**.
6. Write in clear English. Output markdown only — no JSON, no outer code fences wrapping the whole document.
7. Add a short disclaimer that this is educational, not legal advice.
"""


RUBRICS_USER_PROMPT = """Based ONLY on the contract-review skills provided in the system message, produce a reusable markdown rubric document titled:

# Contract Review Rubrics (Reusable Across Agreements)

Purpose: These rubrics generalize the combined review skills so they can be applied to many contracts (not one specific deal).

Requirements:
1. Create **scoring rubrics** reviewers can reuse. Use a consistent scale:
   - **Score 0–2** per criterion (0 = absent/high risk, 1 = partial/needs work, 2 = acceptable/market standard)
   - Optional **N/A** where criterion does not apply
2. Structure rubrics into these sections (align with the skills):
   - A. Document completeness & structure
   - B. Parties, scope & definitions
   - C. Term, termination & survival
   - D. Financial & commercial terms
   - E. Liability, indemnity & remedies
   - F. IP, confidentiality & restrictions
   - G. Compliance, governance & dispute resolution
   - H. Negotiation readiness (redlines, missing clauses, priorities)
3. For each rubric section provide:
   - **Criteria table**: ID | Criterion | What to check | Score (0–2) | Notes
   - **Red-flag triggers** (auto-fail or cap score)
   - **Market benchmark hints** where the skills mention standards
4. Add a **Master scoring sheet**:
   - Weighted categories (suggest weights that sum to 100%)
   - Formula for overall Contract Health Score (0–100)
   - Grade bands (A/B/C/D/F) matching the legal-review skill spirit
5. Add **Quick scan checklist** (1-page style, checkbox format) derived from red-flag quick scan in the skills.
6. Add **Document-type overlays**: short add-on rubric rows for NDA, SaaS/MSA, Employment, M&A, Freelancer/Contractor (5–8 extra criteria each).
7. Make rubrics practical for batch review of many contracts — criteria must be observable from text, not require external facts unless noted.
8. Write in clear English. Output markdown only — no JSON, no outer code fences wrapping the whole document.
9. Add a short disclaimer that this is educational, not legal advice.
"""


def write_with_metadata(path: Path, body: str, *, model: str, task: str, skills: List[str]) -> None:
    header = (
        f"<!-- Generated by generate_skills_artifacts_openrouter.py -->\n"
        f"<!-- Task: {task} | Model: {model} | Skills: {', '.join(skills)} -->\n"
        f"<!-- Generated at: {datetime.now(timezone.utc).isoformat()} -->\n\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + body, encoding="utf-8")
    print(f"Wrote {path} ({path.stat().st_size:,} bytes)", flush=True)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate mistakes guide and rubrics via OpenRouter.")
    parser.add_argument("--preset", choices=sorted(PRESETS), default="full", help="Skills to load")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenRouter model (default: anthropic/claude-opus-4.6)")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--api-key", default=os.environ.get("OPENROUTER_API_KEY"))
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument("--max-tokens", type=int, default=16000, help="Max tokens per artifact")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--mistakes-only", action="store_true")
    parser.add_argument("--rubrics-only", action="store_true")
    args = parser.parse_args(argv)

    if not args.api_key:
        print("ERROR: Set OPENROUTER_API_KEY or pass --api-key.", file=sys.stderr)
        return 2

    skill_ids = PRESETS[args.preset]
    system_prompt = build_system_prompt(
        skill_ids,
        max_chars_per_skill=None,
    )
    system_prompt += (
        "\n\nYou are now acting as a legal drafting educator and rubric designer, "
        "not reviewing a specific contract. Synthesize patterns from the skills above."
    )

    mistakes_path = args.output_dir / "contract-writing-common-mistakes.md"
    rubrics_path = args.output_dir / "contract-review-rubrics.md"

    run_mistakes = not args.rubrics_only
    run_rubrics = not args.mistakes_only

    if run_mistakes:
        print(f"Generating common drafting mistakes with {args.model}...", flush=True)
        mistakes_body = call_openrouter(
            system_prompt,
            MISTAKES_USER_PROMPT,
            model=args.model,
            api_key=args.api_key,
            base_url=args.base_url,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )
        write_with_metadata(
            mistakes_path,
            mistakes_body,
            model=args.model,
            task="common_mistakes",
            skills=skill_ids,
        )
        time.sleep(2)

    if run_rubrics:
        print(f"Generating review rubrics with {args.model}...", flush=True)
        rubrics_body = call_openrouter(
            system_prompt,
            RUBRICS_USER_PROMPT,
            model=args.model,
            api_key=args.api_key,
            base_url=args.base_url,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )
        write_with_metadata(
            rubrics_path,
            rubrics_body,
            model=args.model,
            task="rubrics",
            skills=skill_ids,
        )

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "preset": args.preset,
        "skills": skill_ids,
        "outputs": {
            "mistakes": str(mistakes_path) if run_mistakes else None,
            "rubrics": str(rubrics_path) if run_rubrics else None,
        },
    }
    meta_path = args.output_dir / "artifacts_generation_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Meta: {meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
