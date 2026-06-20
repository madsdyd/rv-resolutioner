#!/usr/bin/env python3
"""
Build the public resolutions dataset from one or more DOCX source files.

This is a deliberately small, review-friendly import tool for the Radikale
Venstre resolutions prototype. It is not meant to be a perfect general-purpose
DOCX parser. Its job is to produce a useful *draft* dataset that can be checked
by a human before publication.

Design notes
------------
The documents are semi-structured political documents rather than formal data.
Some years use Word heading styles consistently; older years may mostly use
visual formatting such as bold text and font size. The parser therefore uses a
hybrid strategy:

1. Prefer Word styles when they are available.
2. Fall back to known chapter names, bold text and conservative title heuristics.
3. Preserve the original local codes from the document, but do not assume that
   e.g. "F" means the same policy area every year.

The output is a canonical JSON object with metadata and a `resolutions` array.
The frontend still accepts the old raw-list format, but new generated data should
use this wrapped format because it is easier to validate, inspect and extend.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
from collections import Counter
import sys
from pathlib import Path
from typing import Any

from docx import Document


SCHEMA_VERSION = 1

# The parser uses this list as a safety net for documents where Word heading
# styles are absent or inconsistent. The names are policy-area labels, not stable
# identifiers: the actual local chapter code may differ from year to year.
COMMON_CHAPTERS = [
    "Demokrati og frihed",
    "Økonomi og skat",
    "Arbejdsmarked, erhvervspolitik og offentlig sektor",
    "Børn, unge og uddannelse",
    "Sundhed og etik",
    "Miljø, klima og trafik",
    "Miljø, klima, trafik og bæredygtighed",
    "EU og udenrigspolitik",
    "Social- og udlændingepolitik",
    "Retspolitik",
    "Andet",
    "Aktualitetsresolutioner",
    "Aktualitetsresolution",
]

# A small Danish stopword list used only for deterministic demo keywords. These
# keywords should eventually be replaced or reviewed by an LLM-assisted workflow.
STOPWORDS = set(
    """
    og i at på med for til af de den det der som en et er har skal vil kan om fra ikke
    eller også men så radikale venstre mener ønsker arbejde derfor dette disse blive
    være alle samt hvor når hvis mere under over efter ved mod hvorledes deres vores
    """.split()
)


def clean_text(text: str) -> str:
    """Normalise whitespace while preserving paragraph-level newlines."""
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", "\n", text)
    return text.strip()


def split_para_text(text: str) -> list[str]:
    """Split a DOCX paragraph if it contains blank-line separated chunks."""
    parts = []
    for chunk in re.split(r"\n\s*\n+", text.replace("\xa0", " ")):
        chunk = clean_text(chunk)
        if chunk:
            parts.append(chunk)
    return parts


def load_document_metadata(path: Path) -> dict[str, dict[str, Any]]:
    """Load required per-source-document metadata from years.json.

    The keys in years.json are exact DOCX filenames, for example
    "Vedtagne resolutioner 2025.docx". This is intentionally stricter than
    inferring dates from a year in the filename: validity periods are political
    decisions and should be explicit, reviewable project data.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    documents = raw.get("documents")
    if not isinstance(documents, dict):
        raise ValueError("years.json must contain a top-level 'documents' object")
    return documents


def metadata_for_document(path: Path, document_metadata: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Return explicit metadata for one DOCX file or fail with a useful error."""
    source_name = path.name
    metadata = document_metadata.get(source_name)
    if metadata is None:
        known = ", ".join(sorted(document_metadata)) or "<none>"
        raise ValueError(
            f"Missing years.json metadata for source document: {source_name}. "
            f"Known documents: {known}"
        )

    required = ["year", "adopted_date", "valid_from", "valid_until"]
    missing = [field for field in required if field not in metadata]
    if missing:
        raise ValueError(
            f"Incomplete years.json metadata for source document: {source_name}. "
            f"Missing fields: {', '.join(missing)}"
        )

    return metadata



def load_policy_areas(path: Path) -> dict[str, Any]:
    """Load canonical policy-area mappings from policy_areas.json.

    The mapping is deliberately explicit: source chapter titles are mapped to a
    canonical `policy_area` value through exact aliases. The parser does not use
    fuzzy matching or guessing, because policy-area harmonisation is an editorial
    decision rather than a pure text-similarity problem.
    """
    if not path.exists():
        return {"areas": {}}

    raw = json.loads(path.read_text(encoding="utf-8"))
    areas = raw.get("areas")
    if not isinstance(areas, dict):
        raise ValueError("policy_areas.json must contain a top-level 'areas' object")
    return raw


def build_policy_alias_map(policy_areas: dict[str, Any]) -> dict[str, str]:
    """Return a lookup from source chapter title/alias to canonical policy area."""
    alias_map: dict[str, str] = {}
    duplicates: dict[str, list[str]] = {}

    for canonical, config in policy_areas.get("areas", {}).items():
        aliases = set(config.get("aliases", []))
        aliases.add(canonical)
        for alias in aliases:
            if alias in alias_map and alias_map[alias] != canonical:
                duplicates.setdefault(alias, [alias_map[alias]]).append(canonical)
            alias_map[alias] = canonical

    if duplicates:
        details = "; ".join(f"{alias!r}: {areas}" for alias, areas in sorted(duplicates.items()))
        raise ValueError(f"Duplicate policy-area aliases in policy_areas.json: {details}")

    return alias_map


def canonical_policy_area(chapter_title: str, alias_map: dict[str, str]) -> str:
    """Map a source chapter title to the canonical policy area used in the UI."""
    return alias_map.get(chapter_title, chapter_title)


def update_policy_areas_file(path: Path, missing_titles: set[str]) -> None:
    """Add missing source chapter titles as identity mappings.

    This helper intentionally does not try to harmonise new titles. It only makes
    new titles explicit in policy_areas.json so a human can later decide whether
    they should remain separate or become aliases of an existing canonical area.
    """
    if path.exists():
        data = load_policy_areas(path)
    else:
        data = {"areas": {}}

    areas = data.setdefault("areas", {})
    for title in sorted(missing_titles):
        areas.setdefault(title, {"aliases": [title]})

    ordered = {"areas": {key: areas[key] for key in sorted(areas)}}
    path.write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def strip_chapter_code(text: str) -> tuple[str | None, str]:
    """Return local chapter code and title, e.g. 'F Miljø...' -> ('F', 'Miljø...')."""
    match = re.match(r"^([A-ZÆØÅ])\s+(.+)$", text.strip())
    if match:
        return match.group(1), match.group(2).strip()
    return None, text.strip()


def strip_resolution_code(text: str) -> tuple[str | None, str]:
    """Return local resolution code and title, e.g. 'F12 Roadpricing' -> ('F12', ...)."""
    match = re.match(r"^([A-ZÆØÅ]\d+)\s+(.+)$", text.strip())
    if match:
        return match.group(1), match.group(2).strip()
    return None, text.strip()


def is_known_chapter_text(text: str) -> bool:
    """Check whether text matches a known policy-area title, with or without code."""
    text = text.strip()
    _, bare = strip_chapter_code(text)
    return text in COMMON_CHAPTERS or bare in COMMON_CHAPTERS




def strip_repeated_title_paragraph(title: str, text: str) -> str | None:
    """Remove a repeated title only when it is a standalone first line.

    Some DOCX files contain the resolution title both as a semantic heading and
    again as the first body paragraph. In that case the repeated body paragraph
    should be removed. However, the title may also be the start of a real
    sentence, for example "Giv ... ved fremvisning ..."; that must be kept.

    A small compatibility case handles older DOCX exports where a repeated
    title was concatenated with a standard "Radikale Venstre ..." body opener.
    """
    lines = text.splitlines()
    if lines and clean_text(lines[0]) == title:
        rest = clean_text("\n".join(lines[1:]))
        return rest or None

    prefix = f"{title} "
    if text.startswith(prefix):
        rest = text[len(prefix):].strip()
        if rest.startswith("Radikale Venstre "):
            return rest

    return text


def looks_like_title(text: str) -> bool:
    """Conservative fallback title detector for style-poor documents."""
    text = text.strip()
    if len(text) > 140 or len(text.split()) > 17:
        return False
    if text.startswith((
        "Radikale Venstre ", "Vi ", "Som ", "Derfor ", "Det ", "I dag ", "For ",
        "Dette ", "Både ", "På ", "Med ", "At ", "-at ", "•", "·", "Alle skal ",
    )):
        return False
    return True


def paragraph_items(path: Path) -> list[dict[str, Any]]:
    """
    Convert DOCX paragraphs into parser items with text and visual metadata.

    python-docx exposes paragraph styles and run-level formatting. We keep the
    fields that are useful for detecting chapters and titles in documents where
    the editor used formatting instead of semantic heading styles.
    """
    doc = Document(path)
    items = []

    for para in doc.paragraphs:
        if not clean_text(para.text):
            continue

        runs = [run for run in para.runs if run.text.strip()]
        sizes = [run.font.size.pt for run in runs if run.font.size]
        bolds = [bool(run.bold) for run in runs if run.text.strip()]

        all_bold = bool(bolds) and all(bolds)
        any_bold = any(bolds)
        max_size = max(sizes) if sizes else None
        min_size = min(sizes) if sizes else None
        style = para.style.name if para.style else ""

        parts = split_para_text(para.text)
        for idx, part in enumerate(parts):
            # Drop common DOCX artefacts from page numbers/table of contents.
            if re.fullmatch(r"\d+", part) or part == "Indhold":
                continue
            items.append({
                "text": part,
                "style": style,
                "all_bold": all_bold,
                "any_bold": any_bold,
                "max_size": max_size,
                "min_size": min_size,
                "split_index": idx,
                "split_count": len(parts),
            })

    return items


def is_chapter_item(item: dict[str, Any]) -> bool:
    """Identify a policy-area chapter heading."""
    text = item["text"].strip()
    if is_known_chapter_text(text):
        return True
    if item["style"] == "Heading 1":
        return True
    if item["all_bold"] and item["max_size"] and item["max_size"] >= 13 and len(text.split()) <= 8:
        return True
    return False


def is_title_item(item: dict[str, Any]) -> bool:
    """Identify a resolution title within the current chapter."""
    text = item["text"].strip()
    if item["style"] == "Heading 2" and not is_known_chapter_text(text):
        return True
    if item["all_bold"] and item["max_size"] and 11 <= item["max_size"] < 13.5:
        return True
    if item["split_count"] > 1 and item["split_index"] == 0 and looks_like_title(text):
        return True
    return False


def make_keywords(record: dict[str, Any], count: int = 8) -> list[str]:
    """
    Produce deterministic demo keywords from word frequencies.

    This is intentionally simple and transparent. It is useful for the prototype,
    but LLM-generated and human-reviewed search terms will be better for the real
    yearly import workflow.
    """
    text = f"{record.get('title', '')} {record.get('body', '')} {record.get('local_chapter_title', '')}"
    words = re.findall(r"[A-Za-zÆØÅæøå0-9][A-Za-zÆØÅæøå0-9\\-]{2,}", text.lower())
    counts = Counter(word.strip("-") for word in words if word not in STOPWORDS and len(word.strip("-")) > 2)

    title_words = set(re.findall(r"[A-Za-zÆØÅæøå0-9][A-Za-zÆØÅæøå0-9\\-]{2,}", record.get("title", "").lower()))
    scores = {}
    for word, occurrences in counts.items():
        scores[word] = occurrences + (3 if word in title_words else 0) + (1 if len(word) > 8 else 0)

    return sorted(scores, key=lambda word: (-scores[word], word))[:count]


def parse_docx(
    path: Path,
    document_metadata: dict[str, dict[str, Any]],
    policy_alias_map: dict[str, str],
    missing_policy_areas: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse one DOCX source file into resolution records plus diagnostics."""
    items = paragraph_items(path)
    dates = metadata_for_document(path, document_metadata)
    year = int(dates["year"])
    records = []
    current_chapter = None
    current_chapter_code = None
    current = None

    def flush_current() -> None:
        """Finish the currently open resolution, if it has body text."""
        nonlocal current
        if current and current.get("body_parts"):
            current["body"] = clean_text("\n\n".join(current.pop("body_parts")))
            records.append(current)
        current = None

    for item in items:
        text = item["text"]

        if is_chapter_item(item):
            flush_current()
            current_chapter_code, current_chapter = strip_chapter_code(text)
            continue

        if current_chapter and is_title_item(item):
            flush_current()
            resolution_code, title = strip_resolution_code(text)
            policy_area = canonical_policy_area(current_chapter, policy_alias_map)
            if policy_area == current_chapter and current_chapter not in policy_alias_map:
                missing_policy_areas.add(current_chapter)

            current = {
                "id": None,
                "year": year,
                "adopted_date": dates["adopted_date"],
                "valid_from": dates["valid_from"],
                "valid_until": dates["valid_until"],
                "local_chapter_code": current_chapter_code,
                "local_chapter_title": current_chapter,
                "policy_area": policy_area,
                "local_resolution_code": resolution_code,
                "title": title,
                "body_parts": [],
                "source_file": path.name,
            }
            continue

        if current:
            # Some documents repeat the title at the beginning of the body. Only
            # remove it when it is clearly a standalone repeated title, not when
            # the title is part of the first real sentence.
            if not current["body_parts"]:
                body_text = strip_repeated_title_paragraph(current["title"], text)
                if body_text:
                    current["body_parts"].append(body_text)
            else:
                current["body_parts"].append(text)

    flush_current()

    for idx, record in enumerate(records, 1):
        if not record["id"]:
            code = record["local_resolution_code"] or f"{idx:03d}"
            record["id"] = f"{year}-{code}"
        record["code"] = record["local_resolution_code"] or record["id"].split("-", 1)[1]
        record["chapter_code"] = record["local_chapter_code"]
        record["chapter_title"] = record["local_chapter_title"]
        record.setdefault("policy_area", record["chapter_title"])
        record["keywords"] = make_keywords(record)
        record["generated_search_terms"] = []

    diagnostics = {
        "file": path.name,
        "year": year,
        "paragraphs_seen": len(items),
        "word_headings_seen": sum(1 for item in items if item["style"] in ("Heading 1", "Heading 2")),
        "resolution_count": len(records),
        "chapter_counts": dict(Counter(record["local_chapter_title"] for record in records)),
        "policy_area_counts": dict(Counter(record["policy_area"] for record in records)),
        "first_title": records[0]["title"] if records else None,
        "last_title": records[-1]["title"] if records else None,
        "metadata": {
            "adopted_date": dates["adopted_date"],
            "valid_from": dates["valid_from"],
            "valid_until": dates["valid_until"],
            "note": dates.get("note", ""),
        },
    }

    return records, diagnostics


def build_dataset(records: list[dict[str, Any]], diagnostics: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap resolution records in a self-describing dataset object."""
    source_documents = []
    for diag in diagnostics:
        dates = diag["metadata"]
        source_documents.append({
            "year": diag["year"],
            "filename": diag["file"],
            "adopted_date": dates["adopted_date"],
            "valid_from": dates["valid_from"],
            "valid_until": dates["valid_until"],
            "resolution_count": diag["resolution_count"],
            "note": dates.get("note", ""),
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "source_documents": source_documents,
        "resolutions": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse DOCX resolution documents into public/resolutions.json")
    parser.add_argument("docx", nargs="+", type=Path, help="DOCX source files to parse")
    parser.add_argument("--out", type=Path, default=Path("public/resolutions.json"), help="Output JSON file")
    parser.add_argument("--years", type=Path, default=Path("years.json"), help="Per-source-document adoption/validity metadata")
    parser.add_argument("--policy-areas", type=Path, default=Path("policy_areas.json"), help="Canonical policy-area mapping")
    parser.add_argument("--update-policy-areas", action="store_true", help="Add missing policy areas to policy_areas.json as identity mappings")
    parser.add_argument("--diagnostics", type=Path, default=Path("PARSER_DIAGNOSIS.generated.json"), help="Machine-readable parser diagnostics")
    parser.add_argument("--legacy-array", action="store_true", help="Write the old raw-array format instead of the canonical wrapper")
    args = parser.parse_args()

    document_metadata = load_document_metadata(args.years)
    policy_areas = load_policy_areas(args.policy_areas)
    policy_alias_map = build_policy_alias_map(policy_areas)
    missing_policy_areas: set[str] = set()
    all_records = []
    diagnostics = []

    for path in args.docx:
        records, diag = parse_docx(path, document_metadata, policy_alias_map, missing_policy_areas)
        all_records.extend(records)
        diagnostics.append(diag)

    all_records.sort(key=lambda record: (record["year"], record["id"]))

    if missing_policy_areas:
        if args.update_policy_areas:
            update_policy_areas_file(args.policy_areas, missing_policy_areas)
            print(f"Updated {args.policy_areas} with {len(missing_policy_areas)} missing policy area(s).")
        else:
            print("WARNING: Unmapped policy area source title(s):", file=sys.stderr)
            for title in sorted(missing_policy_areas):
                print(f"- {title}", file=sys.stderr)
            print("Run again with --update-policy-areas to add them as identity mappings.", file=sys.stderr)

    output_data: Any = all_records if args.legacy_array else build_dataset(all_records, diagnostics)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output_data, ensure_ascii=False, indent=2), encoding="utf-8")
    args.diagnostics.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote {len(all_records)} resolutions to {args.out}")
    for diag in diagnostics:
        print(f"- {diag['file']}: {diag['resolution_count']} resolutions")


if __name__ == "__main__":
    main()
