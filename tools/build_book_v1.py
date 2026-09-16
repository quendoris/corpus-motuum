#!/usr/bin/env python3
"""Build the page-aligned canonical payload used by Corpus Motuum 1.x releases."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

EXPECTED_FIGURES = {"numbered": 158, "atlas": 6, "paratext": 4}
BOOK_METADATA = {
    "title": "Практическая гимнастика",
    "subtitle": "Руководство к постепенному упражнению гимнастикой",
    "author": "Наполеон Лэнэ",
    "language": "ru",
}


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return data


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def source_figure_path(row: dict[str, Any], kind: str) -> str:
    key = "canonical_file" if kind == "numbered" else "file"
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise SystemExit(f"{kind} figure has no {key}")
    return value


def stable_figure_path(row: dict[str, Any], kind: str) -> str:
    return (Path("figures") / kind / Path(source_figure_path(row, kind)).name).as_posix()


def normalized_asset(row: dict[str, Any], kind: str) -> tuple[str, dict[str, Any]]:
    digest = row.get("canonical_sha256") if kind == "numbered" else row.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise SystemExit(f"{kind} asset has no valid SHA-256")
    placement = row.get("placement")
    if not isinstance(placement, dict):
        raise SystemExit(
            f"{kind} asset {row.get('label') or row.get('id')} has no release placement"
        )
    page_id = placement.get("page_id")
    if not isinstance(page_id, str) or not page_id:
        raise SystemExit(
            f"{kind} asset {row.get('label') or row.get('id')} has no release page id"
        )
    geometry = row.get("geometry") if isinstance(row.get("geometry"), dict) else {}
    item: dict[str, Any] = {
        "kind": kind,
        "file": stable_figure_path(row, kind),
        "sha256": digest,
        "width": geometry.get("width"),
        "height": geometry.get("height"),
        "classes": row.get("classes", []),
        "placement_evidence": placement.get("evidence"),
    }
    if kind == "numbered":
        item["label"] = str(row.get("label"))
    else:
        item["id"] = row.get("id")
    bbox = placement.get("page_bbox")
    if bbox is not None:
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise SystemExit(f"invalid release bbox for {item.get('label') or item.get('id')}")
        item["asset_bbox"] = [int(v) for v in bbox]
    if placement.get("requires_visual_placement_review"):
        item["requires_visual_placement_review"] = True
    source = row.get("source") if isinstance(row.get("source"), dict) else {}
    if source.get("asset_index") is not None:
        try:
            item["asset_index"] = int(source["asset_index"])
        except (TypeError, ValueError):
            pass
    return page_id, item


def figure_sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    bbox = row.get("asset_bbox")
    y = int(bbox[1]) if isinstance(bbox, list) and len(bbox) == 4 else 10**9
    return (
        y,
        int(row.get("asset_index") or 0),
        str(row.get("label") or row.get("id") or ""),
    )


def load_figures(path: Path | None) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any] | None, dict[str, int], list[str]]:
    if path is None:
        return {}, None, {key: 0 for key in EXPECTED_FIGURES}, []
    manifest = load_json(path)
    if manifest.get("schema") not in {
        "corpus-motuum-final-figure-release-v1",
        "corpus-motuum-final-figure-set-v1",
    }:
        raise SystemExit(f"unexpected figure manifest schema: {manifest.get('schema')!r}")
    by_page: dict[str, list[dict[str, Any]]] = defaultdict(list)
    counts: dict[str, int] = {}
    seen_files: set[str] = set()
    visual_review: list[str] = []
    for kind, expected in EXPECTED_FIGURES.items():
        rows = manifest.get(kind)
        if not isinstance(rows, list) or len(rows) != expected:
            raise SystemExit(
                f"figure census mismatch for {kind}: expected {expected}, got {len(rows) if isinstance(rows, list) else 'invalid'}"
            )
        counts[kind] = len(rows)
        for row in rows:
            if not isinstance(row, dict):
                raise SystemExit(f"invalid {kind} figure record")
            page_id, item = normalized_asset(row, kind)
            if item["file"] in seen_files:
                raise SystemExit(f"duplicate canonical release figure path: {item['file']}")
            seen_files.add(str(item["file"]))
            by_page[page_id].append(item)
            if item.get("requires_visual_placement_review"):
                visual_review.append(str(item.get("label") or item.get("id") or item["file"]))
    for rows in by_page.values():
        rows.sort(key=figure_sort_key)
    if sum(counts.values()) != 168 or len(seen_files) != 168:
        raise SystemExit("release figure set is not exactly 168 unique assets")
    return dict(by_page), manifest, counts, sorted(visual_review)


def validate_asset_bbox(page_id: str, asset: dict[str, Any], width: int, height: int) -> None:
    bbox = asset.get("asset_bbox")
    if bbox is None:
        return
    x1, y1, x2, y2 = (int(v) for v in bbox)
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise SystemExit(
            f"{page_id}: release asset {asset.get('label') or asset.get('id')} bbox {bbox} outside source {width}x{height}"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--edition",
        choices=("diplomatic", "normalized"),
        required=True,
    )
    ap.add_argument("--pages", type=Path, default=Path("corpus/text/pages"))
    ap.add_argument(
        "--source-manifest",
        type=Path,
        default=Path("corpus/source/page-manifest.json"),
    )
    ap.add_argument("--figures", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    source_manifest = load_json(args.source_manifest)
    if int(source_manifest.get("physical_pages", -1)) != 600:
        raise SystemExit("release expects a 600-page source manifest")
    source_records = {
        str(row["id"]): row
        for row in source_manifest.get("records", [])
        if isinstance(row, dict) and row.get("id")
    }
    if len(source_records) != 600:
        raise SystemExit(f"source manifest has {len(source_records)} records, expected 600")

    figure_map, figure_manifest, figure_counts, visual_review = load_figures(args.figures)
    text_key = "diplomatic_text" if args.edition == "diplomatic" else "normalized_text"

    pages: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for path in sorted(args.pages.glob("*.json")):
        record = load_json(path)
        page_id = record.get("id")
        if not isinstance(page_id, str) or not page_id:
            raise SystemExit(f"missing canonical page id: {path}")
        if page_id in seen_ids:
            raise SystemExit(f"duplicate canonical page id: {page_id}")
        seen_ids.add(page_id)
        if record.get("status") != "verified":
            raise SystemExit(f"release page is not verified: {page_id}")
        text = record.get(text_key)
        if not isinstance(text, str):
            raise SystemExit(f"{page_id}: {text_key} is not a string")
        source = source_records.get(page_id)
        if source is None:
            raise SystemExit(f"{page_id}: absent from source manifest")
        if record.get("physical_index") != source.get("physical_index"):
            raise SystemExit(f"{page_id}: physical index mismatch")
        if record.get("source_sha256") != source.get("sha256"):
            raise SystemExit(f"{page_id}: source hash mismatch")
        width = int(source.get("width") or 0)
        height = int(source.get("height") or 0)
        if width <= 0 or height <= 0:
            raise SystemExit(f"{page_id}: invalid source geometry")
        figures = figure_map.get(page_id, [])
        for asset in figures:
            validate_asset_bbox(page_id, asset, width, height)
        pages.append(
            {
                "id": page_id,
                "physical_index": int(record["physical_index"]),
                "page_type": record.get("page_type"),
                "text": text,
                "figures": figures,
                "notes": record.get("notes", []),
                "source_geometry": {
                    "width": width,
                    "height": height,
                    "printed_page": source.get("printed_page"),
                },
                "provenance": {
                    "source_sha256": record.get("source_sha256"),
                    "editorial_batch": record.get("editorial_batch"),
                },
            }
        )

    pages.sort(key=lambda row: int(row["physical_index"]))
    if len(pages) != 600:
        raise SystemExit(f"canonical page count is {len(pages)}, expected 600")
    if [int(row["physical_index"]) for row in pages] != list(range(1, 601)):
        raise SystemExit("physical page sequence is not exactly 1..600")
    unknown_pages = sorted(set(figure_map) - seen_ids)
    if unknown_pages:
        raise SystemExit("figures reference absent canonical pages: " + ", ".join(unknown_pages))
    linked = sum(len(page["figures"]) for page in pages)
    if linked != 168:
        raise SystemExit(f"release requires 168/168 page-linked assets, got {linked}")

    payload: dict[str, Any] = {
        "schema": "corpus-motuum-book-v1",
        "edition": args.edition,
        "text_layer": text_key,
        "metadata": BOOK_METADATA,
        "principle": (
            "Faithful page-aligned digitization. The build preserves canonical text, "
            "all 600 physical source-page boundaries and all 168 canonical image "
            "identities without language rewriting."
        ),
        "source": {
            "page_manifest": args.source_manifest.as_posix(),
            "page_manifest_sha256": sha256_file(args.source_manifest),
            "source_pdf_sha256": source_manifest.get("source_pdf_sha256"),
            "physical_pages": 600,
        },
        "figures": {
            "manifest": args.figures.as_posix(),
            "manifest_sha256": sha256_file(args.figures),
            "schema": figure_manifest.get("schema") if figure_manifest else None,
            "counts": figure_counts,
            "linked_assets": linked,
            "supplementary_assets": 0,
            "total_assets": linked,
            "visual_placement_review_required": visual_review,
        },
        "supplementary_assets": [],
        "pages": pages,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "edition": args.edition,
                "pages": 600,
                "linked_figures": linked,
                "visual_placement_review_required": visual_review,
                "output": args.out.as_posix(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
