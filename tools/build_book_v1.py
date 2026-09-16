#!/usr/bin/env python3
"""Build a neutral, page-aligned digital-book payload for the 1.x releases."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def figure_page_id(row: dict[str, Any]) -> str | None:
    source = row.get("source")
    if not isinstance(source, dict):
        return None

    direct = source.get("page_id") or source.get("id")
    if isinstance(direct, str) and direct:
        return direct

    provenance = source.get("provenance")
    if isinstance(provenance, dict):
        value = provenance.get("source_page_id")
        if isinstance(value, str) and value:
            return value
    return None


def canonical_figure_path(row: dict[str, Any], kind: str) -> str | None:
    if kind == "numbered":
        value = row.get("canonical_file")
    else:
        value = row.get("file")
    return str(value) if value else None


def load_figures(path: Path | None) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any] | None]:
    if path is None:
        return {}, None
    manifest = load_json(path)
    by_page: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for kind in ("numbered", "atlas", "paratext"):
        for row in manifest.get(kind, []):
            if not isinstance(row, dict):
                continue
            page_id = figure_page_id(row)
            if not page_id:
                continue
            item: dict[str, Any] = {
                "kind": kind,
                "file": canonical_figure_path(row, kind),
                "sha256": row.get("canonical_sha256") if kind == "numbered" else row.get("sha256"),
                "classes": row.get("classes", []),
            }
            if kind == "numbered":
                item["label"] = row.get("label")
            else:
                item["id"] = row.get("id")
            source = row.get("source")
            if isinstance(source, dict):
                if source.get("asset_index") is not None:
                    item["asset_index"] = source.get("asset_index")
                if source.get("asset_bbox") is not None:
                    item["asset_bbox"] = source.get("asset_bbox")
                if source.get("bbox") is not None and "asset_bbox" not in item:
                    item["asset_bbox"] = source.get("bbox")
            by_page[page_id].append(item)

    for rows in by_page.values():
        rows.sort(
            key=lambda row: (
                {"numbered": 0, "atlas": 1, "paratext": 2}.get(str(row.get("kind")), 9),
                int(row.get("asset_index") or 0),
                str(row.get("label") or row.get("id") or ""),
            )
        )
    return dict(by_page), manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--edition",
        choices=("diplomatic", "normalized"),
        required=True,
        help="Text layer used for the book payload.",
    )
    parser.add_argument("--pages", type=Path, default=Path("corpus/text/pages"))
    parser.add_argument(
        "--source-manifest",
        type=Path,
        default=Path("corpus/source/page-manifest.json"),
    )
    parser.add_argument(
        "--figures",
        type=Path,
        default=None,
        help="Optional canonical figure-set manifest.",
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    source_manifest = load_json(args.source_manifest)
    source_records = {
        str(row["id"]): row
        for row in source_manifest.get("records", [])
        if isinstance(row, dict) and row.get("id")
    }

    figure_map, figure_manifest = load_figures(args.figures)
    text_key = "diplomatic_text" if args.edition == "diplomatic" else "normalized_text"

    pages: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for path in sorted(args.pages.glob("*.json")):
        record = load_json(path)
        page_id = record.get("id")
        if not isinstance(page_id, str) or not page_id:
            raise SystemExit(f"missing page id: {path}")
        if page_id in seen_ids:
            raise SystemExit(f"duplicate page id: {page_id}")
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

        pages.append(
            {
                "id": page_id,
                "physical_index": int(record["physical_index"]),
                "page_type": record.get("page_type"),
                "text": text,
                "figures": figure_map.get(page_id, []),
                "notes": record.get("notes", []),
                "provenance": {
                    "source_sha256": record.get("source_sha256"),
                    "editorial_batch": record.get("editorial_batch"),
                },
            }
        )

    pages.sort(key=lambda row: int(row["physical_index"]))
    expected_indices = list(range(1, len(pages) + 1))
    actual_indices = [int(row["physical_index"]) for row in pages]
    if actual_indices != expected_indices:
        raise SystemExit("physical page sequence is not contiguous from 1")
    if len(pages) != int(source_manifest.get("physical_pages", -1)):
        raise SystemExit(
            f"page count mismatch: canonical={len(pages)} "
            f"source={source_manifest.get('physical_pages')}"
        )

    linked_figure_count = sum(len(row["figures"]) for row in pages)
    payload: dict[str, Any] = {
        "schema": "corpus-motuum-book-v1",
        "edition": args.edition,
        "text_layer": text_key,
        "principle": (
            "Faithful page-aligned digitization. The build preserves canonical "
            "text and source order and does not perform language rewriting."
        ),
        "source": {
            "page_manifest": str(args.source_manifest),
            "page_manifest_sha256": sha256_file(args.source_manifest),
            "source_pdf_sha256": source_manifest.get("source_pdf_sha256"),
            "physical_pages": source_manifest.get("physical_pages"),
        },
        "figures": {
            "manifest": str(args.figures) if args.figures else None,
            "manifest_sha256": sha256_file(args.figures) if args.figures else None,
            "linked_assets": linked_figure_count,
            "schema": figure_manifest.get("schema") if figure_manifest else None,
        },
        "pages": pages,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "edition": args.edition,
                "pages": len(pages),
                "linked_figures": linked_figure_count,
                "output": str(args.out),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
