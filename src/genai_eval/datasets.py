"""Load and validate JSONL golden datasets.

Schema (see CLAUDE.md): required fields id, language, category, prompt,
max_tokens, temperature; optional expected_output, contexts, metric_set.
Golden sets are code — validation is strict so a malformed line fails the run
instead of silently skewing a baseline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


REQUIRED_FIELDS = ("id", "language", "category", "prompt", "max_tokens", "temperature")


class DatasetError(ValueError):
    """A golden dataset file is malformed."""


@dataclass(frozen=True)
class GoldenItem:
    id: str
    language: str
    category: str
    prompt: str
    max_tokens: int
    temperature: float
    expected_output: str | None = None
    contexts: tuple[str, ...] | None = None
    metric_set: tuple[str, ...] | None = None


def load_golden_dataset(path: str | Path) -> list[GoldenItem]:
    path = Path(path)
    if not path.is_file():
        raise DatasetError(f"Golden dataset not found: {path}")

    items: list[GoldenItem] = []
    seen_ids: set[str] = set()
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetError(f"{path}:{lineno}: invalid JSON: {exc}") from exc

        missing = [f for f in REQUIRED_FIELDS if f not in raw]
        if missing:
            raise DatasetError(f"{path}:{lineno}: missing required fields {missing}")
        if raw["id"] in seen_ids:
            raise DatasetError(f"{path}:{lineno}: duplicate id {raw['id']!r}")
        seen_ids.add(raw["id"])

        contexts = raw.get("contexts")
        metric_set = raw.get("metric_set")
        items.append(GoldenItem(
            id=str(raw["id"]),
            language=str(raw["language"]),
            category=str(raw["category"]),
            prompt=str(raw["prompt"]),
            max_tokens=int(raw["max_tokens"]),
            temperature=float(raw["temperature"]),
            expected_output=raw.get("expected_output"),
            contexts=tuple(contexts) if contexts else None,
            metric_set=tuple(metric_set) if metric_set else None,
        ))

    if not items:
        raise DatasetError(f"{path}: dataset is empty")
    return items
