#!/usr/bin/env python3
"""Enrich the canonical final figure manifest with release page placement.

The final figure set records image identity and provenance. Reader formats also
need one unambiguous Russian source-page placement for every asset. This tool
builds a release-only manifest without rewriting the canonical figure manifest.
"""
from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

SHEET_RE = re.compile(r"(sheet-\d{3}-(?:left|right))")
EXPECTED = {"numbered": 158, "atlas": 6, "paratext": 4}


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return data


def page_from_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = SHEET_RE.search(value)
    return match.group(1) if match else None


def shifted_bbox(base: Any, trim: Any) -> list[int] | None:
    if not (
        isinstance(base, list)
        and len(base) == 4
        and isinstance(trim, list)
        and len(trim) == 4
    ):
        return None
    bx1, by1, _, _ = (int(v) for v in base)
    tx1, ty1, tx2, ty2 = (int(v) for v in trim)
    return [bx1 + tx1, by1 + ty1, bx1 + tx2, by1 + ty2]


def valid_bbox(value: Any) -> list[int] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        box = [int(v) for v in value]
    except (TypeError, ValueError):
        return None
    x1, y1, x2, y2 = box
    return box if x1 < x2 and y1 < y2 else None


def placement(
    page_id: str | None,
    physical_index: Any,
    bbox: list[int] | None,
    evidence: str,
    *,
    visual_review: bool = False,
) -> dict[str, Any]:
    if not page_id:
        raise SystemExit(f"release placement has no page id: {evidence}")
    result: dict[str, Any] = {
        "page_id": page_id,
        "physical_index": int(physical_index) if physical_index is not None else None,
        "page_bbox": bbox,
        "evidence": evidence,
    }
    if visual_review:
        result["requires_visual_placement_review"] = True
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--final-manifest", type=Path, required=True)
    ap.add_argument("--logical-manifest", type=Path, required=True)
    ap.add_argument("--manual-manifest", type=Path, required=True)
    ap.add_argument(
        "--overrides",
        type=Path,
        default=Path("corpus/figures/release-placement-overrides-v1.json"),
    )
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    final = load_json(args.final_manifest)
    logical = load_json(args.logical_manifest)
    manual = load_json(args.manual_manifest)
    overrides = load_json(args.overrides)

    logical_by_label: dict[str, dict[str, Any]] = {}
    for row in logical.get("rendered", []):
        if not isinstance(row, dict):
            continue
        for label in row.get("figure_labels", []):
            logical_by_label[str(label)] = row

    manual_numbered: dict[str, dict[str, Any]] = {}
    manual_by_id: dict[str, dict[str, Any]] = {}
    for row in manual.get("records", []):
        if not isinstance(row, dict):
            continue
        manual_by_id[str(row.get("id"))] = row
        if row.get("section") == "numbered" and row.get("label") is not None:
            manual_numbered[str(row["label"])] = row

    override_rows = overrides.get("numbered", {})
    if not isinstance(override_rows, dict):
        raise SystemExit("numbered placement overrides must be an object")

    output = deepcopy(final)
    output["schema"] = "corpus-motuum-final-figure-release-v1"
    output["canonical_manifest"] = args.final_manifest.as_posix()
    output["release_placement"] = {
        "logical_manifest": args.logical_manifest.as_posix(),
        "manual_manifest": args.manual_manifest.as_posix(),
        "overrides": args.overrides.as_posix(),
    }

    missing_bbox: list[str] = []
    placement_count = 0

    numbered = output.get("numbered", [])
    if not isinstance(numbered, list) or len(numbered) != EXPECTED["numbered"]:
        raise SystemExit("unexpected numbered figure census")
    for row in numbered:
        label = str(row.get("label"))
        source = row.get("source") if isinstance(row.get("source"), dict) else {}
        category = str(source.get("source_category") or "")
        p: dict[str, Any] | None = None

        if category == "direct":
            p = placement(
                str(source.get("page_id") or "") or None,
                source.get("physical_index"),
                valid_bbox(source.get("asset_bbox")),
                "canonical direct Russian detector crop",
            )
        elif category == "logical":
            logical_row = logical_by_label.get(label)
            if not logical_row:
                raise SystemExit(f"figure {label}: missing logical composition record")
            render = logical_row.get("render") if isinstance(logical_row.get("render"), dict) else {}
            page_id = page_from_path(render.get("source_page")) or str(source.get("page_id") or "") or None
            p = placement(
                page_id,
                source.get("physical_index"),
                valid_bbox(render.get("union_bbox")),
                f"logical composition {logical_row.get('id')} in Russian source coordinates",
            )
        elif category == "manual":
            record = manual_numbered.get(label)
            if not record:
                raise SystemExit(f"figure {label}: missing manual build record")
            kind = str(record.get("kind") or "")
            provenance = record.get("provenance") if isinstance(record.get("provenance"), dict) else {}
            if kind == "clean-asset-erasure":
                base = valid_bbox(provenance.get("asset_bbox"))
                trim = provenance.get("trim_bbox_in_asset")
                page_id = page_from_path(provenance.get("source_page")) or page_from_path(provenance.get("metrics"))
                p = placement(
                    page_id,
                    record.get("physical_index") or source.get("physical_index"),
                    shifted_bbox(base, trim),
                    f"manual clean asset {record.get('id')} placed from Russian detector provenance",
                )
            elif kind == "reference-restoration":
                override = override_rows.get(label)
                if not isinstance(override, dict):
                    raise SystemExit(f"reference-restored figure {label} needs a Russian placement override")
                p = placement(
                    str(override.get("page_id") or "") or None,
                    override.get("physical_index"),
                    valid_bbox(override.get("asset_bbox")),
                    str(override.get("evidence") or "declared Russian placement override"),
                    visual_review=bool(override.get("requires_visual_placement_review")),
                )
            else:
                raise SystemExit(f"figure {label}: unsupported manual kind {kind!r}")
        else:
            raise SystemExit(f"figure {label}: unsupported source category {category!r}")

        row["placement"] = p
        placement_count += 1
        if p.get("page_bbox") is None:
            missing_bbox.append(label)

    for section in ("atlas", "paratext"):
        rows = output.get(section, [])
        if not isinstance(rows, list) or len(rows) != EXPECTED[section]:
            raise SystemExit(f"unexpected {section} figure census")
        for row in rows:
            source = row.get("source") if isinstance(row.get("source"), dict) else {}
            record_id = str(row.get("id") or "")
            if source.get("section") in {"atlas", "paratext"}:
                record = manual_by_id.get(record_id)
                if not record:
                    raise SystemExit(f"{section} {record_id}: missing manual record")
                provenance = record.get("provenance") if isinstance(record.get("provenance"), dict) else {}
                base = valid_bbox(provenance.get("crop_bbox"))
                bbox = shifted_bbox(base, provenance.get("trim_bbox_in_crop"))
                p = placement(
                    str(provenance.get("source_page_id") or "") or page_from_path(provenance.get("source_file")),
                    record.get("physical_index"),
                    bbox,
                    f"manual {section} page-sticker provenance {record_id}",
                )
            else:
                p = placement(
                    str(source.get("id") or "") or None,
                    source.get("physical_index"),
                    valid_bbox(source.get("bbox")),
                    f"automatic {section} Russian detector crop {source.get('key') or record_id}",
                )
            row["placement"] = p
            placement_count += 1
            if p.get("page_bbox") is None:
                missing_bbox.append(record_id)

    if placement_count != 168:
        raise SystemExit(f"release placement census is {placement_count}, expected 168")
    if missing_bbox:
        raise SystemExit(f"release assets without exact source bbox: {missing_bbox}")

    output["release_placement"]["placed_assets"] = placement_count
    output["release_placement"]["exact_bbox_assets"] = placement_count
    output["release_placement"]["visual_review_required"] = []
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output["release_placement"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
