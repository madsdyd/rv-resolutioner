#!/usr/bin/env python3
"""
Validate and summarise the generated resolutions JSON file.

This script is intentionally pragmatic rather than strict. It checks the things
that are most likely to reveal parser problems:

- missing required fields
- duplicate ids
- suspiciously short titles or bodies
- invalid date ranges
- likely page-number artefacts in body text
- counts by year and policy area

Run it after every yearly import before publishing the data file.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = [
    "id",
    "year",
    "title",
    "body",
    "valid_from",
    "valid_until",
    "chapter_title",
]


def load_resolutions(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load either the canonical wrapper format or the legacy raw-list format."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return raw, {"format": "legacy-array"}
    if isinstance(raw, dict):
        return raw.get("resolutions", []), raw
    raise TypeError(f"Unsupported JSON root type: {type(raw).__name__}")


def parse_date(value: str, field: str, record_id: str, warnings: list[str]) -> _dt.date | None:
    """Parse an ISO date and turn parse failures into validation warnings."""
    try:
        return _dt.date.fromisoformat(value)
    except Exception:
        warnings.append(f"{record_id}: invalid {field}: {value!r}")
        return None


def validate_record(record: dict[str, Any], warnings: list[str]) -> None:
    """Validate one resolution record and append human-readable warnings."""
    record_id = str(record.get("id", "<missing id>"))

    for field in REQUIRED_FIELDS:
        if field not in record or record[field] in (None, ""):
            warnings.append(f"{record_id}: missing required field {field!r}")

    title = str(record.get("title", "")).strip()
    body = str(record.get("body", "")).strip()

    if len(title) < 4:
        warnings.append(f"{record_id}: suspiciously short title: {title!r}")
    if len(body) < 40:
        warnings.append(f"{record_id}: suspiciously short body ({len(body)} chars): {title!r}")

    valid_from = parse_date(str(record.get("valid_from", "")), "valid_from", record_id, warnings)
    valid_until = parse_date(str(record.get("valid_until", "")), "valid_until", record_id, warnings)
    if valid_from and valid_until and valid_until <= valid_from:
        warnings.append(f"{record_id}: valid_until is not after valid_from")

    # This catches the common DOCX artefact where a page number is pulled into a
    # body paragraph by itself. It is a warning because some real lists contain
    # numbers too.
    if re.search(r"(?:^|\n)\s*\d+\s*(?:\n|$)", body):
        warnings.append(f"{record_id}: body may contain an isolated page number")


def build_report(resolutions: list[dict[str, Any]], metadata: dict[str, Any], warnings: list[str]) -> str:
    """Create a concise import report suitable for terminal output or a file."""
    by_year = Counter(record.get("year") for record in resolutions)
    by_year_chapter: dict[int, Counter[str]] = defaultdict(Counter)

    for record in resolutions:
        year = record.get("year")
        chapter = record.get("chapter_title") or "<missing chapter>"
        by_year_chapter[year][chapter] += 1

    lines = []
    lines.append("Resolution data validation report")
    lines.append("=================================")
    lines.append(f"Format: {metadata.get('format', 'canonical-wrapper')}")
    if metadata.get("schema_version") is not None:
        lines.append(f"Schema version: {metadata.get('schema_version')}")
    if metadata.get("generated_at"):
        lines.append(f"Generated at: {metadata.get('generated_at')}")
    lines.append(f"Total resolutions: {len(resolutions)}")
    lines.append("")

    lines.append("Counts by year")
    lines.append("--------------")
    for year in sorted(by_year):
        lines.append(f"{year}: {by_year[year]} resolutioner")
    lines.append("")

    lines.append("Counts by year and policy area")
    lines.append("------------------------------")
    for year in sorted(by_year_chapter):
        lines.append(str(year))
        for chapter, count in by_year_chapter[year].most_common():
            lines.append(f"  - {chapter}: {count}")
    lines.append("")

    lines.append("Warnings")
    lines.append("--------")
    if warnings:
        for warning in warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("No warnings.")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate generated resolutions JSON")
    parser.add_argument("json_file", type=Path, nargs="?", default=Path("public/resolutions.json"))
    parser.add_argument("--report", type=Path, help="Optional path for a text report")
    args = parser.parse_args()

    resolutions, metadata = load_resolutions(args.json_file)
    warnings: list[str] = []

    seen_ids = set()
    for record in resolutions:
        record_id = record.get("id")
        if record_id in seen_ids:
            warnings.append(f"{record_id}: duplicate id")
        seen_ids.add(record_id)
        validate_record(record, warnings)

    report = build_report(resolutions, metadata, warnings)
    print(report)

    if args.report:
        args.report.write_text(report + "\n", encoding="utf-8")

    return 1 if warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
