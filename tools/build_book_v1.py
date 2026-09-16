#!/usr/bin/env python3
"""Build the neutral, page-aligned digital-book payload used by 1.x releases."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

EXPECTED_FIGURES = {"numbered": 158, "atlas": 6, "paratext": 4}
FIGURE_ORDER = {"numbered": 0, "atlas": 1, "paratext": 2}
PAGE_KEYS = ("russian_source_page_id", "source_page_id", "page_id")
PATH_PAGE_KEYS = ("source_page", "source_file", "metrics")
SHEET_ID_RE = re.compile(r"(sheet-\d{3}-(?:left|right))")
BBOX_KEYS = ("placement_page_bbox", "asset_bbox", "union_bbox", "crop_page_bbox", "bbox")
BOOK_METADATA = {
    "title": "Практическая гимнастика",
    "subtitle": "Руководство к постепенному упражнению гимнастикой",
    "author": "Наполеон Лэнэ",
    "language": "ru",
}


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


def walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def unique_nested_string(source: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    candidates: list[str] = []
    for node in walk_dicts(source):
        for key in keys:
            value = node.get(key)
            if isinstance(value, str) and value:
                candidates.append(value)
    unique = list(dict.fromkeys(candidates))
    return unique[0] if len(unique) == 1 else None


def page_id_from_paths(source: dict[str, Any]) -> str | None:
    candidates: list[str] = []
    for node in walk_dicts(source):
        for key in PATH_PAGE_KEYS:
            value = node.get(key)
            if not isinstance(value, str):
                continue
            match = SHEET_ID_RE.search(value)
            if match:
                candidates.append(match.group(1))
    unique = list(dict.fromkeys(candidates))
    return unique[0] if len(unique) == 1 else None


def figure_page_id(row: dict[str, Any]) -> str | None:
    """Find one Russian physical-page id without guessing from arbitrary ids."""
    source = row.get("source")
    if not isinstance(source, dict):
        return None

    for key in PAGE_KEYS:
        candidates: list[str] = []
        for node in walk_dicts(source):
            value = node.get(key)
            if isinstance(value, str) and value.startswith("sheet-"):
                candidates.append(value)
        unique = list(dict.fromkeys(candidates))
        if len(unique) == 1:
            return unique[0]
        if len(unique) > 1:
            return None

    path_page = page_id_from_paths(source)
    if path_page:
        return path_page

    direct = source.get("id")
    if isinstance(direct, str) and direct.startswith("sheet-"):
        return direct
    return None


def bbox_from_source(source: dict[str, Any]) -> list[int] | None:
    """Return one unambiguous source-page bbox, preferring explicit placement keys."""
    for key in BBOX_KEYS:
        candidates: list[tuple[int, int, int, int]] = []
        for node in walk_dicts(source):
            value = node.get(key)
            if not isinstance(value, list) or len(value) != 4:
                continue
            try:
                box = tuple(int(part) for part in value)
            except (TypeError, ValueError):
                continue
            x1, y1, x2, y2 = box
            if x1 < x2 and y1 < y2:
                candidates.append(box)
        unique = list(dict.fromkeys(candidates))
        if len(unique) == 1:
            return list(unique[0])
        if len(unique) > 1:
            return None
    return None


def source_figure_path(row: dict[str, Any], kind: str) -> str:
    key = "canonical_file" if kind == "numbered" else "file"
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise SystemExit(f"{kind} figure has no {key}: {row}")
    return value


def stable_figure_path(row: dict[str, Any], kind: str) -> str:
    return (Path("figures") / kind / Path(source_figure_path(row, kind)).name).as_posix()


def load_placement_overrides(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    payload = load_json(path)
    if payload.get("schema") != "corpus-motuum-release-placement-overrides-v1":
        raise SystemExit(f"unexpected placement override schema: {payload.get('schema')!r}")
    rows = payload.get("numbered", {})
    if not isinstance(rows, dict):
        raise SystemExit("placement overrides numbered must be an object")
    return {str(key): value for key, value in rows.items() if isinstance(value, dict)}


def normalize_figure(
    row: dict[str, Any], kind: str, overrides: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], str | None]:
    digest = row.get("canonical_sha256") if kind == "numbered" else row.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise SystemExit(f"{kind} figure has no valid SHA-256")

    geometry = row.get("geometry") if isinstance(row.get("geometry"), dict) else {}
    item: dict[str, Any] = {
        "kind": kind,
        "file": stable_figure_path(row, kind),
        "sha256": digest,
        "width": geometry.get("width"),
        "height": geometry.get("height"),
    }
    label: str | None = None
    if kind == "numbered":
        label = str(row.get("label") or "")
        item["label"] = label
        item["classes"] = row.get("classes", [])
    else:
        item["id"] = row.get("id")
        item["classes"] = row.get("classes", [])

    source = row.get("source")
    page_id = figure_page_id(row)
    bbox = bbox_from_source(source) if isinstance(source, dict) else None

    override = overrides.get(label or "") if kind == "numbered" else None
    if override:
        override_page = override.get("page_id")
        if isinstance(override_page, str) and override_page:
            if page_id and page_id != override_page:
                raise SystemExit(
                    f"figure {label}: placement override page {override_page} conflicts with provenance {page_id}"
                )
            page_id = override_page
        override_bbox = override.get("asset_bbox")
        if isinstance(override_bbox, list) and len(override_bbox) == 4:
            bbox = [int(value) for value in override_bbox]
        item["placement_evidence"] = override.get("evidence")
        if override.get("requires_visual_placement_review"):
            item["requires_visual_placement_review"] = True

    if bbox is not None:
        item["asset_bbox"] = bbox
    if source and isinstance(source, dict):
        asset_index = None
        for node in walk_dicts(source):
            if node.get("asset_index") is not None:
                try:
                    asset_index = int(node["asset_index"])
                    break
                except (TypeError, ValueError):
                    pass
        if asset_index is not None:
            item["asset_index"] = asset_index

    return item, page_id


def figure_sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    bbox = row.get("asset_bbox")
    y = int(bbox[1]) if isinstance(bbox, list) and len(bbox) == 4 else 10**9
    return (
        y,
        int(row.get("asset_index") or 0),
        str(row.get("label") or row.get("id") or ""),
    )


def load_figures(
    path: Path | None,
    overrides: dict[str, dict[str, Any]],
) -> tuple[
    dict[str, list[dict[str, Any]]],
    list[dict[str, Any]],
    dict[str, Any] | None,
    dict[str, int],
]:
    if path is None:
        return {}, [], None, {key: 0 for key in EXPECTED_FIGURES}

    manifest = load_json(path)
    counts: dict[str, int] = {}
    by_page: dict[str, list[dict[str, Any]]] = defaultdict(list)
    supplementary: list[dict[str, Any]] = []
    seen_files: set[str] = set()

    for kind, expected in EXPECTED_FIGURES.items():
        rows = manifest.get(kind)
        if not isinstance(rows, list):
            raise SystemExit(f"figure manifest: {kind} is not a list")
        counts[kind] = len(rows)
        if len(rows) != expected:
            raise SystemExit(
                f"figure census mismatch for {kind}: expected {expected}, got {len(rows)}"
            )

        for row in rows:
            if not isinstance(row, dict):
                raise SystemExit(f"figure manifest: invalid {kind} record")
            item, page_id = normalize_figure(row, kind, overrides)
            if item["file"] in seen_files:
                raise SystemExit(f"duplicate canonical figure path: {item['file']}")
            seen_files.add(str(item["file"]))

            if page_id:
                item["page_id"] = page_id
                by_page[page_id].append(item)
            else:
                supplementary.append(item)

    for rows in by_page.values():
        rows.sort(key=figure_sort_key)
    supplementary.sort(key=figure_sort_key)

    total = sum(counts.values())
    accounted = sum(len(rows) for rows in by_page.values()) + len(supplementary)
    if total != 168 or accounted != total:
        raise SystemExit(
            f"figure accounting mismatch: census={total}, accounted={accounted}, expected=168"
        )

    return dict(by_page), supplementary, manifest, counts


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
        help="Canonical figure-set manifest produced by build_final_figure_set.py.",
    )
    parser.add_argument(
        "--placement-overrides",
        type=Path,
        default=Path("corpus/figures/release-placement-overrides-v1.json"),
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    source_manifest = load_json(args.source_manifest)
    source_records = {
        str(row["id"]): row
        for row in source_manifest.get("records", [])
        if isinstance(row, dict) and row.get("id")
    }

    overrides = load_placement_overrides(args.placement_overrides)
    figure_map, supplementary, figure_manifest, figure_counts = load_figures(
        args.figures, overrides
    )
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
                "source_geometry": {
                    "width": int(source.get("width") or 0),
                    "height": int(source.get("height") or 0),
                    "printed_page": source.get("printed_page"),
                },
                "provenance": {
                    "source_sha256": record.get("source_sha256"),
                    "editorial_batch": record.get("editorial_batch"),
                },
            }
        )

    pages.sort(key=lambda row: int(row["physical_index"]))
    expected_count = int(source_manifest.get("physical_pages", -1))
    if expected_count != 600:
        raise SystemExit(f"release expects a 600-page source manifest, got {expected_count}")
    if len(pages) != expected_count:
        raise SystemExit(f"page count mismatch: canonical={len(pages)} source={expected_count}")
    actual_indices = [int(row["physical_index"]) for row in pages]
    if actual_indices != list(range(1, 601)):
        raise SystemExit("physical page sequence is not exactly 1..600")

    unknown_figure_pages = sorted(set(figure_map) - seen_ids)
    if unknown_figure_pages:
        raise SystemExit(
            "canonical figures reference absent pages: " + ", ".join(unknown_figure_pages)
        )

    linked_figure_count = sum(len(row["figures"]) for row in pages)
    total_figure_count = linked_figure_count + len(supplementary)
    if args.figures and total_figure_count != 168:
        raise SystemExit(f"book must account for 168 figures, got {total_figure_count}")
    if args.figures and supplementary:
        labels = [str(row.get("label") or row.get("id") or row.get("file")) for row in supplementary]
        raise SystemExit(
            "release requires every canonical asset to resolve to a source page; unresolved: "
            + ", ".join(labels)
        )
    if args.figures and linked_figure_count != 168:
        raise SystemExit(f"release requires 168/168 page-linked assets, got {linked_figure_count}")

    payload: dict[str, Any] = {
        "schema": "corpus-motuum-book-v1",
        "edition": args.edition,
        "text_layer": text_key,
        "metadata": BOOK_METADATA,
        "principle": (
            "Faithful page-aligned digitization. The build preserves canonical "
            "text, all 600 physical source-page boundaries, and all canonical "
            "figure identities without language rewriting."
        ),
        "source": {
            "page_manifest": args.source_manifest.as_posix(),
            "page_manifest_sha256": sha256_file(args.source_manifest),
            "source_pdf_sha256": source_manifest.get("source_pdf_sha256"),
            "physical_pages": expected_count,
        },
        "figures": {
            "manifest": args.figures.as_posix() if args.figures else None,
            "manifest_sha256": sha256_file(args.figures) if args.figures else None,
            "placement_overrides": args.placement_overrides.as_posix()
            if args.placement_overrides and args.placement_overrides.exists()
            else None,
            "schema": figure_manifest.get("schema") if figure_manifest else None,
            "counts": figure_counts,
            "linked_assets": linked_figure_count,
            "supplementary_assets": 0,
            "total_assets": total_figure_count,
        },
        "supplementary_assets": [],
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
                "supplementary_figures": 0,
                "total_figures": total_figure_count,
                "output": args.out.as_posix(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
