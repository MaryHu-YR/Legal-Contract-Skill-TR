#!/usr/bin/env python3
"""Review contracts from contracts_2020_sample.json using OpenRouter.

Loads contract-review skills from this repo (see contract-review-skills-summary/),
sends each contract to the OpenRouter Chat Completions API, and writes markdown
reports plus a summary JSON file.

Usage:
  export OPENROUTER_API_KEY="your-key"
  python review_contracts_openrouter.py --limit 3

  # Minimal prompt (faster / cheaper):
  python review_contracts_openrouter.py --preset minimal

  # Full multi-skill workflow:
  python review_contracts_openrouter.py --preset workflow
"""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import error, request

try:
    import certifi
except ImportError:
    certifi = None


ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = ROOT / "contracts_2020_sample.json"
DEFAULT_OUTPUT_DIR = ROOT / "contracts_2020_reviews"
SKILLS_SUMMARY = ROOT / "contract-review-skills-summary" / "CONTRACT-REVIEW-SKILLS.md"

# DEFAULT_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4-turbo")
DEFAULT_MODEL = os.environ.get("OPENROUTER_MODEL", "minimax/minimax-m3")
DEFAULT_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# Skill id -> path relative to ROOT
SKILL_REGISTRY: Dict[str, Path] = {
    "summary": SKILLS_SUMMARY,
    "contract-review": ROOT / "claude-legal-skill-main" / "skill.md",
    "legal-review": ROOT / "ai-legal-claude-main" / "skills" / "legal-review" / "SKILL.md",
    "legal-risks": ROOT / "ai-legal-claude-main" / "skills" / "legal-risks" / "SKILL.md",
    "legal-missing": ROOT / "ai-legal-claude-main" / "skills" / "legal-missing" / "SKILL.md",
    "legal-negotiate": ROOT / "ai-legal-claude-main" / "skills" / "legal-negotiate" / "SKILL.md",
    "legal-plain": ROOT / "ai-legal-claude-main" / "skills" / "legal-plain" / "SKILL.md",
    "legal-compare": ROOT / "ai-legal-claude-main" / "skills" / "legal-compare" / "SKILL.md",
    "legal-freelancer": ROOT / "ai-legal-claude-main" / "skills" / "legal-freelancer" / "SKILL.md",
}

PRESETS: Dict[str, List[str]] = {
    "minimal": ["contract-review"],
    "workflow": ["summary", "contract-review", "legal-review", "legal-risks", "legal-missing"],
    "full": [
        "summary",
        "contract-review",
        "legal-review",
        "legal-risks",
        "legal-missing",
        "legal-negotiate",
        "legal-plain",
        "legal-freelancer",
    ],
}

ORCHESTRATOR_PREAMBLE = """You are an expert contract review assistant.

Apply ALL skill instructions provided below as a single integrated review policy.
The skill summary describes how skills fit together; each skill block adds requirements.

Rules:
- Output ONE markdown report only (no JSON, no code fences wrapping the whole report).
- Do not ask clarifying questions; infer document type and party perspective from the text.
- If perspective is unclear, state your assumption in the report header.
- Include a legal disclaimer at the top.
- Follow the output structures from the skills; merge overlapping sections instead of repeating them.

Required report sections (combine skill outputs into this outline):

1. **Contract Review** header (document name, type, assumed position, risk level, draft/executed if inferable)
2. **Pre-Signing Alerts** (blanks, missing exhibits, truncation)
3. **Executive Summary**
4. **Contract Safety Score** (0–100) and letter grade (from legal-review)
5. **Key Terms** table
6. **Red Flags Quick Scan** table
7. **Risk Analysis** (Critical / Important / Acceptable) with market standard and negotiability where relevant
8. **Risk Matrix** (clause, category, score 1–10, exposure) — from legal-risks
9. **Missing Protections** with suggested clause language — from legal-missing
10. **Negotiation Priority** table (top issues, ask, negotiability)
11. **Recommended Next Steps** checklist

End with: *This review is for informational purposes only and is not legal advice.*
"""


@dataclass
class ContractRecord:
    contract_id: int
    filename: str
    filepath: str
    text: str


def read_skill(skill_id: str, max_chars: Optional[int] = None) -> str:
    path = SKILL_REGISTRY.get(skill_id)
    if path is None:
        raise KeyError(f"Unknown skill: {skill_id!r}. Available: {', '.join(SKILL_REGISTRY)}")
    if not path.exists():
        raise FileNotFoundError(f"Skill file not found for {skill_id!r}: {path}")
    content = path.read_text(encoding="utf-8")
    if max_chars is not None and len(content) > max_chars:
        content = content[:max_chars] + "\n\n[... skill truncated for context limit ...]\n"
    return content


def build_system_prompt(skill_ids: List[str], max_chars_per_skill: Optional[int]) -> str:
    parts = [ORCHESTRATOR_PREAMBLE, "\n---\n# Loaded skills\n"]
    for skill_id in skill_ids:
        body = read_skill(skill_id, max_chars=max_chars_per_skill)
        parts.append(f"\n## Skill: {skill_id}\n\n{body}\n")
    return "".join(parts)


def load_contracts_2020(input_path: Path) -> List[ContractRecord]:
    raw = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"Expected a JSON array in {input_path}, got {type(raw).__name__}")

    records: List[ContractRecord] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"Item {index} is not an object")
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"Item {index} missing non-empty 'text' field")
        records.append(
            ContractRecord(
                contract_id=int(item.get("id", index + 1)),
                filename=str(item.get("filename", f"contract_{index + 1}")),
                filepath=str(item.get("filepath", "")),
                text=text.strip(),
            )
        )
    return records


def sanitize_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "contract"


def build_user_prompt(record: ContractRecord) -> str:
    return f"""Review the following contract using the loaded skill instructions.

DOCUMENT ID: {record.contract_id}
FILENAME: {record.filename}
SOURCE PATH: {record.filepath or "N/A"}

Contract text:
{record.text}
"""


def call_openrouter(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str,
    api_key: str,
    base_url: str,
    temperature: float,
    max_tokens: Optional[int],
    max_retries: int = 3,
) -> Dict[str, Any]:
    url = base_url.rstrip("/") + "/chat/completions"
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "HTTP-Referer": "https://github.com/local-contract-review",
        "X-Title": "Contracts 2020 batch review",
    }

    ssl_context = ssl.create_default_context(cafile=certifi.where()) if certifi else None
    last_error: Optional[Exception] = None
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

    for attempt in range(1, max_retries + 1):
        req = request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=180, context=ssl_context) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except retryable as exc:
            last_error = exc
            if attempt == max_retries:
                break
            wait = min(2**attempt, 15)
            print(f"  API error ({type(exc).__name__}), retry {attempt}/{max_retries} in {wait}s...", flush=True)
            time.sleep(wait)

    raise RuntimeError(f"OpenRouter request failed after {max_retries} attempts: {last_error}")


def extract_text_content(response: Dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        raise ValueError("OpenRouter response missing choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str):
        raise ValueError("OpenRouter response missing text content")
    return content.strip() + "\n"


def report_path_for(output_dir: Path, index: int, record: ContractRecord) -> Path:
    stem = sanitize_slug(Path(record.filename).stem)
    return output_dir / f"{index:03d}_{record.contract_id}_{stem}.md"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Review contracts_2020_sample.json via OpenRouter using repo contract-review skills.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="Input JSON (array of {id, filename, filepath, text})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for markdown reports and summary.json",
    )
    parser.add_argument(
        "--preset",
        choices=sorted(PRESETS),
        default="workflow",
        help="Which skills to load (default: workflow)",
    )
    parser.add_argument(
        "--skills",
        nargs="+",
        metavar="SKILL_ID",
        help="Override preset with explicit skill ids (e.g. contract-review legal-risks)",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenRouter model id")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="OpenRouter API base URL")
    parser.add_argument("--api-key", default=os.environ.get("OPENROUTER_API_KEY"), help="OpenRouter API key")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=None, help="Max output tokens per review")
    parser.add_argument(
        "--max-chars-per-skill",
        type=int,
        default=None,
        help="Truncate each skill file to this many characters (optional)",
    )
    parser.add_argument("--limit", type=int, default=0, help="Max number of contracts to review (0 = all)")
    parser.add_argument("--start", type=int, default=0, help="Skip first N contracts")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip contracts that already have a report file in output-dir",
    )
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds to wait between API calls")
    parser.add_argument(
        "--retries",
        type=int,
        default=5,
        help="Retry count for transient network/API errors (default: 5)",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Skip failed contracts and keep going instead of stopping the batch",
    )
    args = parser.parse_args(argv)

    if not args.api_key:
        print("ERROR: Set OPENROUTER_API_KEY or pass --api-key.", file=sys.stderr)
        return 2

    skill_ids = args.skills if args.skills else PRESETS[args.preset]
    for skill_id in skill_ids:
        if skill_id not in SKILL_REGISTRY:
            print(f"ERROR: Unknown skill {skill_id!r}. Choose from: {', '.join(SKILL_REGISTRY)}", file=sys.stderr)
            return 2

    if not args.input.exists():
        print(f"ERROR: Input file not found: {args.input}", file=sys.stderr)
        return 2

    try:
        system_prompt = build_system_prompt(skill_ids, max_chars_per_skill=args.max_chars_per_skill)
    except (FileNotFoundError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    records = load_contracts_2020(args.input)
    if args.start:
        records = records[args.start :]
    if args.limit:
        records = records[: args.limit]

    results: List[Dict[str, Any]] = []
    skipped = 0

    for index, record in enumerate(records, start=1):
        report_path = report_path_for(output_dir, args.start + index, record)
        if args.resume and report_path.exists():
            print(f"[{index}/{len(records)}] Skip (exists): {report_path.name}", flush=True)
            skipped += 1
            results.append(
                {
                    "contract_id": record.contract_id,
                    "filename": record.filename,
                    "report_file": str(report_path),
                    "status": "skipped_existing",
                }
            )
            continue

        print(f"[{index}/{len(records)}] Reviewing id={record.contract_id} {record.filename}...", flush=True)
        try:
            response = call_openrouter(
                system_prompt,
                build_user_prompt(record),
                model=args.model,
                api_key=args.api_key,
                base_url=args.base_url,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                max_retries=args.retries,
            )
            review_text = extract_text_content(response)
            report_path.write_text(review_text, encoding="utf-8")

            usage = response.get("usage") or {}
            results.append(
                {
                    "contract_id": record.contract_id,
                    "filename": record.filename,
                    "filepath": record.filepath,
                    "report_file": str(report_path),
                    "status": "ok",
                    "model": args.model,
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "preview": review_text.splitlines()[:6],
                }
            )
        except Exception as exc:
            print(f"  FAILED id={record.contract_id}: {exc}", file=sys.stderr, flush=True)
            results.append(
                {
                    "contract_id": record.contract_id,
                    "filename": record.filename,
                    "filepath": record.filepath,
                    "report_file": str(report_path),
                    "status": "error",
                    "error": str(exc),
                }
            )
            if not args.continue_on_error:
                break

        if args.delay > 0 and index < len(records):
            time.sleep(args.delay)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": str(args.input),
        "output_dir": str(output_dir),
        "model": args.model,
        "preset": args.preset,
        "skills": skill_ids,
        "reviewed_count": sum(1 for r in results if r.get("status") == "ok"),
        "skipped_count": skipped,
        "error_count": sum(1 for r in results if r.get("status") == "error"),
        "results": results,
    }
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Done. Reports in {output_dir}")
    print(f"Summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
