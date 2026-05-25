#!/usr/bin/env python3
"""Generate Anki TSV cards for changed NeetCode solution files."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable


SUPPORTED_EXTENSIONS = {".py", ".java", ".js", ".ts", ".cpp", ".go", ".rs"}
EXCLUDED_TOP_LEVEL_DIRS = {".git", ".github", ".codex", ".agents", "anki", "docs", "scripts"}
TOPIC_TAGS = {
    "arrays": ["array", "arrays", "matrix", "hash", "set"],
    "two_pointers": ["two pointer", "two-pointer", "pair", "palindrome"],
    "sliding_window": ["sliding window", "substring", "subarray", "window"],
    "stack": ["stack", "monotonic"],
    "binary_search": ["binary search", "search sorted"],
    "dp": ["dynamic programming", "dp", "memo", "tabulation"],
    "graph": ["graph", "bfs", "dfs", "network", "course schedule"],
    "tree": ["tree", "binary tree", "bst"],
    "heap": ["heap", "priority queue", "top k", "median"],
    "trie": ["trie", "prefix tree"],
    "linked_list": ["linked list", "list node"],
    "backtracking": ["backtracking", "permutation", "combination", "subset"],
    "intervals": ["interval", "meeting"],
    "greedy": ["greedy"],
    "prefix_sum": ["prefix sum", "prefix"],
}


class OpenAIUnavailableError(RuntimeError):
    """Raised when the OpenAI API cannot be used for the whole run."""


def run_git(args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def repo_root() -> Path:
    return Path(run_git(["rev-parse", "--show-toplevel"]))


def git_ref_exists(ref: str) -> bool:
    return subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    ).returncode == 0


def github_push_before() -> str | None:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return None
    try:
        with open(event_path, encoding="utf-8") as event_file:
            event = json.load(event_file)
    except (OSError, json.JSONDecodeError):
        return None
    before = event.get("before")
    if isinstance(before, str) and before and set(before) != {"0"}:
        return before
    return None


def changed_files(base: str | None, head: str) -> list[Path]:
    if base:
        return [Path(line) for line in run_git(["diff", "--name-only", f"{base}...{head}"]).splitlines() if line]

    event_before = github_push_before()
    if event_before:
        return [
            Path(line)
            for line in run_git(["diff", "--name-only", f"{event_before}...{head}"]).splitlines()
            if line
        ]

    if git_ref_exists(f"{head}^"):
        return [Path(line) for line in run_git(["diff", "--name-only", f"{head}^", head]).splitlines() if line]

    print("No previous commit found; falling back to all supported solution files.")
    return all_solution_files(repo_root())


def all_solution_files(root: Path) -> list[Path]:
    return sorted(
        path.relative_to(root)
        for ext in SUPPORTED_EXTENSIONS
        for path in root.glob(f"**/*{ext}")
        if is_solution_file(path.relative_to(root))
    )


def is_solution_file(path: Path) -> bool:
    parts = path.parts
    if not parts or parts[0] in EXCLUDED_TOP_LEVEL_DIRS:
        return False
    if path.suffix not in SUPPORTED_EXTENSIONS:
        return False
    return path.is_relative_to(Path(".")) and not any(part.startswith(".") for part in parts)


def slugify(value: str) -> str:
    value = value.strip().lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return re.sub(r"_+", "_", value).strip("_") or "neetcode_problem"


def problem_name_from_path(path: Path) -> str:
    stem = path.stem
    if re.fullmatch(r"submission-\d+", stem) and len(path.parts) >= 2:
        return path.parts[-2]
    return stem


def obvious_topic_tags(problem_name: str, relative_path: Path, code: str) -> list[str]:
    haystack = f"{relative_path} {problem_name} {code[:3000]}".lower().replace("_", " ").replace("-", " ")
    tags = []
    for tag, needles in TOPIC_TAGS.items():
        if any(needle in haystack for needle in needles):
            tags.append(tag)
    return tags[:3]


def card_prompt(problem_name: str, relative_path: Path, code: str) -> str:
    return f"""Create 3 to 6 high-value Anki cards for this NeetCode solution.

Problem: {problem_name}
Path: {relative_path}

Focus cards on problem-solving pattern, key trick, why the algorithm works,
common mistakes, time and space complexity, and indexing or boundary issues.
Avoid asking what individual code lines do.

Return only valid JSON with this shape:
{{"cards":[{{"front":"...","back":"...","tags":["neetcode","problem_slug","topic"]}}]}}

Solution:
```text
{code}
```"""


def extract_response_text(payload: dict) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]

    chunks = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks)


def call_openai(prompt: str, api_key: str, model: str, retries: int = 2) -> dict:
    request_body = json.dumps(
        {
            "model": model,
            "input": [
                {
                    "role": "system",
                    "content": (
                        "You write concise, accurate Anki cards for coding interview solutions. "
                        "Return only JSON."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=request_body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code in {401, 403, 429} or attempt == retries:
                raise OpenAIUnavailableError(f"OpenAI API request failed with HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            if attempt == retries:
                raise OpenAIUnavailableError(f"OpenAI API request failed: {exc.reason}") from exc
        time.sleep(2**attempt)

    raise OpenAIUnavailableError("OpenAI API request failed.")


def parse_cards(response_payload: dict) -> list[dict]:
    response_text = extract_response_text(response_payload).strip()
    if response_text.startswith("```"):
        response_text = re.sub(r"^```(?:json)?\s*", "", response_text)
        response_text = re.sub(r"\s*```$", "", response_text)
    data = json.loads(response_text)
    cards = data.get("cards", [])
    if not isinstance(cards, list):
        raise ValueError("OpenAI response did not contain a cards list.")
    return cards


def normalize_card(card: dict, required_tags: list[str]) -> tuple[str, str, str]:
    front = str(card.get("front", "")).strip()
    back = str(card.get("back", "")).strip()
    raw_tags = card.get("tags", [])
    if isinstance(raw_tags, str):
        raw_tags = raw_tags.split()
    tags = [slugify(tag) for tag in [*required_tags, *raw_tags] if str(tag).strip()]
    tags = list(dict.fromkeys(tags))
    if not front or not back:
        raise ValueError("Card is missing front or back text.")
    return front, back, " ".join(tags)


def write_tsv(output_path: Path, rows: Iterable[tuple[str, str, str]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as tsv_file:
        writer = csv.writer(tsv_file, delimiter="\t", lineterminator="\n")
        writer.writerow(["Front", "Back", "Tags"])
        writer.writerows(rows)


def generate_for_file(root: Path, relative_path: Path, output_dir: Path, api_key: str, model: str) -> Path:
    source_path = root / relative_path
    code = source_path.read_text(encoding="utf-8", errors="replace")
    problem_name = problem_name_from_path(relative_path)
    problem_slug = slugify(problem_name)
    required_tags = ["neetcode", problem_slug, *obvious_topic_tags(problem_name, relative_path, code)]
    response = call_openai(card_prompt(problem_name, relative_path, code), api_key, model)
    cards = parse_cards(response)
    rows = [normalize_card(card, required_tags) for card in cards]
    if not rows:
        raise ValueError("OpenAI returned no cards.")

    output_name = f"{slugify(str(relative_path.with_suffix('')))}.tsv"
    output_path = output_dir / output_name
    write_tsv(output_path, rows)
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="Generate cards for all supported solution files.")
    parser.add_argument("--dry-run", action="store_true", help="Print files that would be processed without calling OpenAI.")
    parser.add_argument("--base", help="Base git ref for changed-file detection.")
    parser.add_argument("--head", default=os.environ.get("GITHUB_SHA", "HEAD"), help="Head git ref for changed-file detection.")
    parser.add_argument("--output-dir", default="anki/pending", help="Directory for generated TSV files.")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-5.5"), help="OpenAI model to use.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = repo_root()
    os.chdir(root)

    candidates = all_solution_files(root) if args.all else changed_files(args.base, args.head)
    solution_files = [path for path in candidates if is_solution_file(path) and (root / path).is_file()]

    if not solution_files:
        print("No changed solution files found. Nothing to generate.")
        return 0

    print("Solution files to process:")
    for path in solution_files:
        print(f"  - {path}")

    if args.dry_run:
        print("Dry run complete; no OpenAI calls were made.")
        return 0

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY is required unless --dry-run is used.", file=sys.stderr)
        return 2

    output_dir = root / args.output_dir
    failures = []
    generated = []
    for relative_path in solution_files:
        print(f"Generating cards for {relative_path}...")
        try:
            output_path = generate_for_file(root, relative_path, output_dir, api_key, args.model)
        except OpenAIUnavailableError as exc:
            print(f"OpenAI API is unavailable; stopping run: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:  # Keep processing other files after per-file failures.
            print(f"Failed to generate cards for {relative_path}: {exc}", file=sys.stderr)
            failures.append(relative_path)
            continue
        print(f"Wrote {output_path.relative_to(root)}")
        generated.append(output_path)

    if generated:
        print(f"Generated {len(generated)} TSV file(s) in {output_dir.relative_to(root)}.")
        if failures:
            print(f"{len(failures)} file(s) failed; generated output for the rest.")
        return 0

    print("No TSV files were generated.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
