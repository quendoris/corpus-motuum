#!/usr/bin/env python3
"""Materialize auditable Exercise v1 draft records from canonical page text.

The tool intentionally performs no biomechanical, anatomical or scientific
interpretation. It only binds selected pilot identities to exact ranges of the
verified diplomatic and normalized-orthography text layers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

LAYERS = ("diplomatic_text", "normalized_text")
OUT_LAYER = {
    "diplomatic_text": "diplomatic",
    "normalized_text": "normalized_orthography",
}


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return value


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def trim_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def continuation_start(text: str) -> int:
    """Skip only an isolated printed folio on continuation pages."""
    match = re.match(r"\A\s*(?:[IVXLCDM]+|\d+)\s*(?:\n+|\Z)", text)
    return match.end() if match else 0


def exercise_heading(number: str, layer: str) -> re.Pattern[str]:
    word = "УПРАЖНЕНІЕ" if layer == "diplomatic_text" else "УПРАЖНЕНИЕ"
    return re.compile(rf"(?m)^\s*{re.escape(number)}-[еЕ]\s+{word}\.\s*$")


def any_exercise_heading(layer: str) -> re.Pattern[str]:
    word = "УПРАЖНЕНІЕ" if layer == "diplomatic_text" else "УПРАЖНЕНИЕ"
    return re.compile(rf"(?m)^\s*\d+-[еЕ]\s+{word}\.\s*$")


def load_pages(pages_root: Path) -> list[dict[str, Any]]:
    pages = [load_json(path) for path in sorted(pages_root.glob("*.json"))]
    pages.sort(key=lambda row: int(row.get("physical_index", -1)))
    if len(pages) != 600:
        raise SystemExit(f"expected 600 canonical pages, got {len(pages)}")
    if [int(p.get("physical_index", -1)) for p in pages] != list(range(1, 601)):
        raise SystemExit("canonical pages are not a contiguous physical sequence 1..600")
    for page in pages:
        if page.get("status") != "verified":
            raise SystemExit(f"unverified canonical page: {page.get('id')}")
        for layer in LAYERS:
            if not isinstance(page.get(layer), str):
                raise SystemExit(f"{page.get('id')}: missing {layer}")
    return pages


def extract_layer(
    pages: list[dict[str, Any]],
    start_index: int,
    number: str,
    layer: str,
    extraction: dict[str, Any],
) -> list[dict[str, Any]]:
    start_re = exercise_heading(number, layer)
    start_page = pages[start_index]
    text = str(start_page[layer])
    start_match = start_re.search(text)
    if not start_match:
        raise SystemExit(
            f"{start_page['id']}: cannot find exercise {number} heading in {layer}"
        )
    start_offset = start_match.start()

    end_mode = str(extraction.get("end") or "next_numbered_exercise")
    exact_markers = extraction.get("end_before") or {}
    exact_marker = None
    if end_mode == "exact_marker":
        exact_marker = exact_markers.get(OUT_LAYER[layer])
        if not isinstance(exact_marker, str) or not exact_marker:
            raise SystemExit(
                f"{start_page['id']}: exact_marker requires "
                f"{OUT_LAYER[layer]} end_before"
            )
    elif end_mode != "next_numbered_exercise":
        raise SystemExit(f"unsupported pilot extraction end mode: {end_mode}")

    next_re = any_exercise_heading(layer)
    spans: list[dict[str, Any]] = []
    for page_index in range(start_index, len(pages)):
        page = pages[page_index]
        page_text = str(page[layer])
        span_start = (
            start_offset
            if page_index == start_index
            else continuation_start(page_text)
        )
        stop_candidates: list[int] = []

        if exact_marker:
            pos = page_text.find(exact_marker, span_start)
            if pos >= 0:
                stop_candidates.append(pos)

        for match in next_re.finditer(page_text, span_start):
            if page_index == start_index and match.start() == start_offset:
                continue
            stop_candidates.append(match.start())
            break

        span_end = min(stop_candidates) if stop_candidates else len(page_text)
        span_start, span_end = trim_bounds(page_text, span_start, span_end)
        if span_start < span_end:
            value = page_text[span_start:span_end]
            spans.append(
                {
                    "page_id": str(page["id"]),
                    "physical_index": int(page["physical_index"]),
                    "start": span_start,
                    "end": span_end,
                    "text": value,
                    "sha256": sha256_text(value),
                }
            )

        if stop_candidates:
            break
    else:
        raise SystemExit(f"exercise {number}: no extraction boundary found")

    if not spans:
        raise SystemExit(f"exercise {number}: empty extracted source")
    return spans


def canonical_figure_id(label: str) -> str:
    match = re.fullmatch(r"(\d+)(bis)?", str(label).strip(), re.IGNORECASE)
    if not match:
        raise SystemExit(f"invalid figure label in pilot: {label!r}")
    return (
        f"figure-{int(match.group(1)):03d}"
        f"{'bis' if match.group(2) else ''}"
    )


def make_record(
    item: dict[str, Any],
    pages: list[dict[str, Any]],
    source_manifest: dict[str, Any],
    created_from_commit: str | None,
) -> dict[str, Any]:
    ex_id = str(item["id"])
    source = item["source"]
    anchor_id = str(source["page_id"])
    index_by_id = {str(page["id"]): idx for idx, page in enumerate(pages)}
    if anchor_id not in index_by_id:
        raise SystemExit(f"{ex_id}: anchor page absent: {anchor_id}")
    start_index = index_by_id[anchor_id]
    if int(pages[start_index]["physical_index"]) != int(source["physical_index"]):
        raise SystemExit(f"{ex_id}: pilot physical index mismatch")

    extraction = item.get("extraction") or {}
    number = str(
        extraction.get("start_number") or source["local_exercise_number"]
    )
    layer_spans = {
        layer: extract_layer(pages, start_index, number, layer, extraction)
        for layer in LAYERS
    }
    page_ids = [
        span["page_id"] for span in layer_spans["diplomatic_text"]
    ]
    normalized_ids = [
        span["page_id"] for span in layer_spans["normalized_text"]
    ]
    if page_ids != normalized_ids:
        raise SystemExit(
            f"{ex_id}: source layer page spans differ: "
            f"{page_ids} vs {normalized_ids}"
        )

    manifest_by_id = {
        str(row["id"]): row
        for row in source_manifest.get("records", [])
        if isinstance(row, dict) and row.get("id")
    }
    page_by_id = {str(page["id"]): page for page in pages}
    segments: list[dict[str, Any]] = []
    source_page_hashes: dict[str, str] = {}
    for page_id in page_ids:
        page = page_by_id[page_id]
        manifest = manifest_by_id.get(page_id)
        if manifest is None:
            raise SystemExit(f"{ex_id}: {page_id} absent from source manifest")
        expected_sha = str(manifest.get("sha256") or "")
        if str(page.get("source_sha256") or "") != expected_sha:
            raise SystemExit(f"{ex_id}: source hash mismatch on {page_id}")

        dip_span = next(
            row
            for row in layer_spans["diplomatic_text"]
            if row["page_id"] == page_id
        )
        norm_span = next(
            row
            for row in layer_spans["normalized_text"]
            if row["page_id"] == page_id
        )
        segments.append(
            {
                "page_id": page_id,
                "physical_index": int(page["physical_index"]),
                "printed_page": manifest.get("printed_page"),
                "source_sha256": expected_sha,
                "diplomatic": {
                    "start": dip_span["start"],
                    "end": dip_span["end"],
                    "sha256": dip_span["sha256"],
                },
                "normalized_orthography": {
                    "start": norm_span["start"],
                    "end": norm_span["end"],
                    "sha256": norm_span["sha256"],
                },
            }
        )
        source_page_hashes[page_id] = expected_sha

    diplomatic = "\n\n".join(
        row["text"] for row in layer_spans["diplomatic_text"]
    )
    normalized = "\n\n".join(
        row["text"] for row in layer_spans["normalized_text"]
    )
    source_title = str(item["source_title_diplomatic"])
    norm_title = str(item["source_title_normalized"])
    if source_title not in diplomatic:
        raise SystemExit(
            f"{ex_id}: diplomatic title not found in extracted source"
        )
    if norm_title not in normalized:
        raise SystemExit(
            f"{ex_id}: normalized title not found in extracted source"
        )

    figures = [
        canonical_figure_id(label)
        for label in source.get("figure_labels", [])
    ]
    physical_pages = [
        segment["physical_index"] for segment in segments
    ]
    printed_pages = [
        segment["printed_page"] for segment in segments
    ]
    return {
        "schema": "corpus-motuum-exercise-v1",
        "id": ex_id,
        "identity": {
            "status": "draft",
            "supersedes": None,
            "same_as": [],
        },
        "source": {
            "source_id": "source-001",
            "page_ids": page_ids,
            "physical_pages": physical_pages,
            "printed_pages": printed_pages,
            "figure_ids": figures,
            "text": {
                "diplomatic": diplomatic,
                "normalized_orthography": normalized,
            },
            "segments": segments,
            "source_claims": [],
        },
        "names": {
            "source": source_title,
            "normalized": norm_title,
            "modern": None,
            "aliases": [],
        },
        "description": {
            "normalized_description": "",
            "modern_description": None,
            "editorial_notes": [],
        },
        "execution": {
            "setup": None,
            "steps": [],
            "finish": None,
            "tempo": None,
            "breathing": None,
        },
        "phases": [],
        "equipment": [],
        "body_position": {
            "start": None,
            "end": None,
            "support": [],
            "orientation": None,
        },
        "movement": {
            "patterns": [],
            "planes": [],
            "laterality": "unknown",
            "joint_actions": [],
        },
        "anatomy": {"relations": []},
        "biomechanics": {
            "load_types": [],
            "notes": [],
            "claim_ids": [],
        },
        "kinetic_chain": {
            "chain_type": "unknown",
            "nodes": [],
            "edges": [],
        },
        "evidence": {"claim_ids": []},
        "risk_claims": [],
        "variants": [],
        "media": {
            "source_figures": figures,
            "restored_figures": [],
            "renders": [],
        },
        "provenance": {
            "schema_version": 1,
            "source_page_hashes": source_page_hashes,
            "editorial_notes": [
                "Machine-extracted source span only; anatomy, biomechanics, "
                "evidence and modern interpretation are intentionally unset "
                "in this draft."
            ],
            "reviewed_by": [],
            "created_from_commit": created_from_commit,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pilot",
        type=Path,
        default=Path("corpus/exercises/pilot-v1.json"),
    )
    parser.add_argument(
        "--pages",
        type=Path,
        default=Path("corpus/text/pages"),
    )
    parser.add_argument(
        "--source-manifest",
        type=Path,
        default=Path("corpus/source/page-manifest.json"),
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        default=Path("corpus/exercises/items"),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if checked-in records differ from deterministic output.",
    )
    args = parser.parse_args()

    pilot = load_json(args.pilot)
    pages = load_pages(args.pages)
    source_manifest = load_json(args.source_manifest)
    if str(source_manifest.get("source_pdf_sha256") or "") != str(
        pilot.get("source_pdf_sha256") or ""
    ):
        raise SystemExit("pilot/source PDF SHA mismatch")

    records = [
        make_record(
            item,
            pages,
            source_manifest,
            pilot.get("source_commit"),
        )
        for item in pilot.get("items", [])
    ]
    if len(records) != int(pilot.get("target_count", -1)):
        raise SystemExit(
            f"pilot expected {pilot.get('target_count')} records, "
            f"got {len(records)}"
        )

    args.out_root.mkdir(parents=True, exist_ok=True)
    changed: list[str] = []
    for record in records:
        path = args.out_root / f"{record['id']}.json"
        payload = json.dumps(
            record,
            ensure_ascii=False,
            indent=2,
        ) + "\n"
        if args.check:
            if (
                not path.is_file()
                or path.read_text(encoding="utf-8") != payload
            ):
                changed.append(path.as_posix())
        else:
            path.write_text(payload, encoding="utf-8")

    if args.check and changed:
        raise SystemExit(
            "pilot source records are stale: " + ", ".join(changed)
        )

    summary = {
        "records": len(records),
        "page_spans": {
            row["id"]: row["source"]["page_ids"]
            for row in records
        },
        "source_only": True,
        "output_root": args.out_root.as_posix(),
        "check": args.check,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
