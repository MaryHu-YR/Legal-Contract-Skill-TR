#!/usr/bin/env python3
"""Batch contract review runner using the OpenRouter Chat Completions API.

Reads a JSON file whose values contain contract text fields, sends each
contract through an LLM review prompt derived from the contract-review skill,
and writes one markdown report per reviewed contract plus a summary JSON file.
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
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib import error, request

try:
    import certifi
except ImportError:
    certifi = None


DEFAULT_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4-turbo")
DEFAULT_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
DEFAULT_INPUT = os.environ.get("CONTRACT_REVIEW_INPUT", "combined_contracts_penalty_term.json")
DEFAULT_OUTPUT_DIR = os.environ.get("CONTRACT_REVIEW_OUTPUT_DIR", "openrouter_reviews")
SKILL_PATH = Path(__file__).resolve().with_name("skill.md")


def load_skill_prompt() -> str:
    skill_text = SKILL_PATH.read_text(encoding="utf-8")
    return (
        "You are a contract review assistant. Apply the following skill instructions exactly. "
        "Use the skill as the controlling review policy and produce one markdown contract review only. "
        "Do not output JSON. Do not include code fences or explanatory preambles.\n\n"
        f"{skill_text}\n\n"
        "Additional batch-review instructions:\n"
        "- The contract text is already provided below, so do not ask clarifying questions.\n"
        "- If the party perspective is unclear, infer it from the document and note N/A if needed.\n"
        "- Keep the output in the markdown structure specified by the skill.\n"
    )


SKILL_PROMPT = load_skill_prompt()


@dataclass
class ReviewTarget:
    entry_id: str
    version_name: str
    contract_text: str


def sanitize_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "contract"


def extract_entry_label(entry_id: str, version_name: str) -> str:
    return f"{entry_id}_{version_name}"


def load_targets(input_path: Path, field_name: str, also_original: bool) -> List[ReviewTarget]:
    data = json.loads(input_path.read_text(encoding="utf-8"))
    targets: List[ReviewTarget] = []

    for entry_id, entry in data.items():
        if field_name not in entry:
            raise KeyError(f'Entry {entry_id!r} does not contain field {field_name!r}')
        targets.append(ReviewTarget(entry_id=str(entry_id), version_name=field_name, contract_text=entry[field_name]))
        if also_original and field_name != "original contract" and "original contract" in entry:
            targets.append(ReviewTarget(entry_id=str(entry_id), version_name="original contract", contract_text=entry["original contract"]))

    return targets


def build_user_prompt(target: ReviewTarget) -> str:
    return f"""Review the following contract using the provided skill instructions.

DOCUMENT ID: {target.entry_id}
VERSION: {target.version_name}

Contract text:
{target.contract_text}
"""


def call_openrouter(
    prompt: str,
    model: str,
    api_key: str,
    base_url: str,
    temperature: float,
    max_tokens: Optional[int],
    max_retries: int = 3,
) -> Dict[str, Any]:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SKILL_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "HTTP-Referer": "https://github.com/github-copilot",
        "X-Title": "Contracts batch review",
    }

    ssl_context = None
    if certifi:
        ssl_context = ssl.create_default_context(cafile=certifi.where())

    last_error: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        req = request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=120, context=ssl_context) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body)
        except (error.HTTPError, error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt == max_retries:
                break
            time.sleep(min(2 ** attempt, 8))

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


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Batch review contracts with OpenRouter.")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Input JSON file")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Directory for generated reports")
    parser.add_argument("--field", default="Preturbed contract", help="Contract text field to review")
    parser.add_argument("--also-original", action="store_true", help="Also review the original contract text")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenRouter model name")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="OpenRouter base URL")
    parser.add_argument("--api-key", default=os.environ.get("OPENROUTER_API_KEY"), help="OpenRouter API key")
    parser.add_argument("--temperature", type=float, default=0.2, help="Model temperature")
    parser.add_argument("--max-tokens", type=int, default=None, help="Maximum output tokens per review")
    parser.add_argument("--limit", type=int, default=0, help="Limit the number of reviewed contracts")
    parser.add_argument("--start", type=int, default=0, help="Skip the first N reviewed contracts")
    args = parser.parse_args(argv)

    if not args.api_key:
        print("ERROR: OPENROUTER_API_KEY is not set. Export it or pass --api-key.", file=sys.stderr)
        return 2

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    targets = load_targets(input_path, args.field, args.also_original)
    if args.start:
        targets = targets[args.start :]
    if args.limit:
        targets = targets[: args.limit]

    results: List[Dict[str, Any]] = []
    for index, target in enumerate(targets, start=1):
        entry_label = extract_entry_label(target.entry_id, target.version_name)
        print(f"[{index}/{len(targets)}] Reviewing {entry_label}...", flush=True)
        response = call_openrouter(
            prompt=build_user_prompt(target),
            model=args.model,
            api_key=args.api_key,
            base_url=args.base_url,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )
        review_text = extract_text_content(response)

        slug = sanitize_slug(entry_label)
        report_path = output_dir / f"{index:03d}_{slug}.md"
        report_path.write_text(review_text, encoding="utf-8")

        results.append(
            {
                "entry_id": target.entry_id,
                "version": target.version_name,
                "report_file": str(report_path),
                "preview": review_text.splitlines()[:8],
            }
        )

    summary = {
        "generated_at": date.today().isoformat(),
        "input": str(input_path),
        "model": args.model,
        "reviewed_count": len(results),
        "results": results,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Done. Wrote {len(results)} review files to {output_dir}")
    print(f"Summary: {output_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())