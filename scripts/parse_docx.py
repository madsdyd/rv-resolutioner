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


def parse_docx(path: Path, document_metadata: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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
            current = {
                "id": None,
                "year": year,
                "adopted_date": dates["adopted_date"],
                "valid_from": dates["valid_from"],
                "valid_until": dates["valid_until"],
                "local_chapter_code": current_chapter_code,
                "local_chapter_title": current_chapter,
                "local_resolution_code": resolution_code,
                "title": title,
                "body_parts": [],
                "source_file": path.name,
            }
            continue

        if current:
            # Some 2023 paragraphs contain "Title Body..." in the same paragraph.
            # If that happens, remove the repeated title before adding the body.
            if text.startswith(current["title"] + " "):
                rest = text[len(current["title"]):].strip()
                if rest:
                    current["body_parts"].append(rest)
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
        record["keywords"] = make_keywords(record)
        record["generated_search_terms"] = []

    diagnostics = {
        "file": path.name,
        "year": year,
        "paragraphs_seen": len(items),
        "word_headings_seen": sum(1 for item in items if item["style"] in ("Heading 1", "Heading 2")),
        "resolution_count": len(records),
        "chapter_counts": dict(Counter(record["local_chapter_title"] for record in records)),
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
    parser.add_argument("--diagnostics", type=Path, default=Path("PARSER_DIAGNOSIS.generated.json"), help="Machine-readable parser diagnostics")
    parser.add_argument("--legacy-array", action="store_true", help="Write the old raw-array format instead of the canonical wrapper")
    args = parser.parse_args()

    document_metadata = load_document_metadata(args.years)
    all_records = []
    diagnostics = []

    for path in args.docx:
        records, diag = parse_docx(path, document_metadata)
        all_records.extend(records)
        diagnostics.append(diag)

    all_records.sort(key=lambda record: (record["year"], record["id"]))

    output_data: Any = all_records if args.legacy_array else build_dataset(all_records, diagnostics)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output_data, ensure_ascii=False, indent=2), encoding="utf-8")
    args.diagnostics.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote {len(all_records)} resolutions to {args.out}")
    for diag in diagnostics:
        print(f"- {diag['file']}: {diag['resolution_count']} resolutions")


if __name__ == "__main__":
    main()
