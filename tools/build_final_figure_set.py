#!/usr/bin/env python3
"""Assemble the complete, numbered and provenance-rich final figure set."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np


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


def label_key(value: Any) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)(bis)?", str(value).strip(), re.IGNORECASE)
    if not match:
        raise ValueError(f"invalid figure label: {value!r}")
    return int(match.group(1)), 1 if match.group(2) else 0


def normalize_label(value: Any) -> str:
    number, suffix = label_key(value)
    return f"{number}{'bis' if suffix else ''}"


def label_filename(label: str) -> str:
    number, suffix = label_key(label)
    return f"figure-{number:03d}{'bis' if suffix else ''}.png"


def materialize(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise ValueError(f"source asset does not exist: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        destination.unlink()
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def validate_rgba(path: Path) -> dict[str, int]:
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None or image.ndim != 3 or image.shape[2] != 4:
        raise ValueError(f"expected lossless RGBA PNG: {path}")
    alpha = image[:, :, 3]
    if not np.any(alpha):
        raise ValueError(f"fully transparent asset: {path}")
    if np.any(image[alpha == 0, :3] != 255):
        raise ValueError(f"transparent pixels must carry white RGB: {path}")
    return {
        "width": int(image.shape[1]),
        "height": int(image.shape[0]),
        "alpha_pixels": int(np.count_nonzero(alpha)),
    }


def labels_for_page(
    records: list[dict[str, Any]], policy: dict[str, Any]
) -> list[str]:
    page_id = str(records[0]["id"])
    override = policy.get("figure_label_overrides", {}).get(page_id)
    raw = override if override else records[0].get("caption_figure_numbers", [])
    return [normalize_label(value) for value in raw]


def expected_labels() -> list[str]:
    labels = [str(number) for number in range(1, 157)]
    labels.extend(("28bis", "87bis"))
    return sorted(labels, key=label_key)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-manifest", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=Path("corpus/figures/curation-v1.json"))
    parser.add_argument("--regressions", type=Path, default=Path("corpus/figures/regressions-v1.json"))
    parser.add_argument("--logical-manifest", type=Path, required=True)
    parser.add_argument("--manual-manifest", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    args = parser.parse_args()

    review = load_json(args.review_manifest)
    policy = load_json(args.policy)
    regressions = load_json(args.regressions)
    logical = load_json(args.logical_manifest)
    manual = load_json(args.manual_manifest)

    resolved = args.out_root.resolve()
    cwd = Path.cwd().resolve()
    if resolved == cwd or len(resolved.parts) < len(cwd.parts) + 2:
        raise SystemExit(f"refusing unsafe output root: {args.out_root}")
    if args.out_root.exists():
        shutil.rmtree(args.out_root)
    args.out_root.mkdir(parents=True)

    source_map: dict[str, dict[str, Any]] = {}
    logical_pages: set[str] = set()
    logical_labels: set[str] = set()

    for record in logical.get("rendered", []):
        render = record["render"]
        page_source = str(render.get("source_page") or "")
        page_id = Path(page_source).stem if page_source else ""
        if page_id:
            logical_pages.add(page_id)
        for raw_label in record.get("figure_labels", []):
            label = normalize_label(raw_label)
            if label in logical_labels:
                raise ValueError(f"duplicate logical label: {label}")
            logical_labels.add(label)
            source_map[label] = {
                "source_category": "logical",
                "source_path": str(render["output"]["path"]),
                "source_sha256": str(render["output"]["sha256"]),
                "page_id": page_id or None,
                "physical_index": None,
                "editorial_id": record.get("id"),
                "detail": record.get("detail"),
            }

    numbered_by_page: dict[str, list[dict[str, Any]]] = defaultdict(list)
    paratext_records: list[dict[str, Any]] = []
    for entry in review.get("kept", []):
        category = str(entry.get("category"))
        if category == "numbered":
            numbered_by_page[str(entry["id"])].append(entry)
        elif category == "paratext":
            paratext_records.append(entry)

    for page_id, records in numbered_by_page.items():
        if page_id in logical_pages:
            continue
        records.sort(key=lambda row: int(row["asset_index"]))
        labels = labels_for_page(records, policy)
        if len(labels) != len(records):
            raise ValueError(
                f"cannot assign physical crops on {page_id}: "
                f"labels={labels}, assets={[row['asset_index'] for row in records]}"
            )
        for label, entry in zip(labels, records):
            if label in source_map:
                raise ValueError(f"duplicate source assignment for figure {label}")
            path = Path(str(entry["review_file"]))
            source_map[label] = {
                "source_category": "direct",
                "source_path": str(path),
                "source_sha256": sha256_file(path),
                "page_id": page_id,
                "physical_index": int(entry["physical_index"]),
                "asset_index": int(entry["asset_index"]),
                "asset_bbox": entry.get("bbox"),
            }

    manual_numbered: dict[str, dict[str, Any]] = {}
    manual_other: list[dict[str, Any]] = []
    for record in manual.get("records", []):
        section = str(record["section"])
        if section == "numbered":
            label = normalize_label(record["label"])
            manual_numbered[label] = record
            source_map[label] = {
                "source_category": "manual",
                "source_path": str(record["output"]["path"]),
                "source_sha256": str(record["output"]["sha256"]),
                "page_id": record.get("provenance", {}).get("source_page_id"),
                "physical_index": None,
                "editorial_id": record.get("id"),
                "manual_kind": record.get("kind"),
                "detail": record.get("reason"),
                "provenance": record.get("provenance"),
            }
        else:
            manual_other.append(record)

    expected = expected_labels()
    missing = sorted(set(expected) - set(source_map), key=label_key)
    extra = sorted(set(source_map) - set(expected), key=label_key)
    if missing or extra:
        raise ValueError(f"figure label census mismatch: missing={missing}, extra={extra}")

    restored_labels: set[str] = set()
    for entry in regressions.get("hard_set", []):
        if str(entry.get("failure_class", "")).startswith("weak-"):
            restored_labels.update(
                normalize_label(value) for value in entry.get("figure_labels", [])
            )
    restored_labels.update(
        label for label, record in manual_numbered.items() if record.get("restored")
    )

    numbered_manifest: list[dict[str, Any]] = []
    class_counts = defaultdict(int)
    for label in expected:
        source = source_map[label]
        source_path = Path(source["source_path"])
        actual_source_sha = sha256_file(source_path)
        if actual_source_sha != source["source_sha256"]:
            raise ValueError(
                f"source hash mismatch for figure {label}: "
                f"{source['source_sha256']} vs {actual_source_sha}"
            )
        canonical = args.out_root / "numbered" / label_filename(label)
        materialize(source_path, canonical)
        geometry = validate_rgba(canonical)
        canonical_sha = sha256_file(canonical)

        is_manual = source["source_category"] in {"logical", "manual"}
        is_restored = label in restored_labels
        classes: list[str] = []
        if is_manual:
            classes.append("manual")
        if is_restored:
            classes.append("restored")
        if not classes:
            classes.append("automatic")
        for class_name in classes:
            class_counts[class_name] += 1
            if class_name == "manual":
                subset = args.out_root / "manual" / "numbered" / label_filename(label)
            else:
                subset = args.out_root / class_name / label_filename(label)
            materialize(canonical, subset)

        numbered_manifest.append({
            "label": label,
            "canonical_file": str(canonical),
            "canonical_sha256": canonical_sha,
            "classes": classes,
            "geometry": geometry,
            "source": source,
        })

    atlas_manifest: list[dict[str, Any]] = []
    paratext_manifest: list[dict[str, Any]] = []

    for record in manual_other:
        source = Path(str(record["output"]["path"]))
        section = str(record["section"])
        if section not in {"atlas", "paratext"}:
            raise ValueError(f"unexpected manual section: {section}")
        filename = Path(str(record["output"]["path"])).name
        destination = args.out_root / section / filename
        materialize(source, destination)
        geometry = validate_rgba(destination)
        manual_copy = args.out_root / "manual" / section / filename
        materialize(destination, manual_copy)
        row = {
            "id": record["id"],
            "file": str(destination),
            "sha256": sha256_file(destination),
            "classes": ["manual"],
            "geometry": geometry,
            "source": record,
        }
        (atlas_manifest if section == "atlas" else paratext_manifest).append(row)

    for entry in sorted(
        paratext_records,
        key=lambda row: (int(row["physical_index"]), int(row["asset_index"])),
    ):
        source = Path(str(entry["review_file"]))
        destination = args.out_root / "paratext" / source.name
        materialize(source, destination)
        paratext_manifest.append({
            "id": entry["key"],
            "file": str(destination),
            "sha256": sha256_file(destination),
            "classes": ["automatic"],
            "geometry": validate_rgba(destination),
            "source": entry,
        })

    atlas_manifest.sort(key=lambda row: row["id"])
    paratext_manifest.sort(key=lambda row: row["id"])
    summary = {
        "numbered_figures": len(numbered_manifest),
        "expected_numbered_figures": len(expected),
        "numbered_label_min": expected[0],
        "numbered_label_max": expected[-1],
        "bis_labels": [label for label in expected if label.endswith("bis")],
        "class_counts": dict(sorted(class_counts.items())),
        "manual_overlap_note": "manual and restored are orthogonal; a reference-restored manual asset appears in both folders",
        "atlas_plates": len(atlas_manifest),
        "paratext_assets": len(paratext_manifest),
        "missing_labels": [],
        "extra_labels": [],
    }
    payload = {
        "schema": "corpus-motuum-final-figure-set-v1",
        "principle": "Russian-edition evidence is preferred. The public-domain French author edition is used only where the Russian print is absent or materially obscured; every substitution has file-level provenance.",
        "inputs": {
            "review_manifest": str(args.review_manifest),
            "policy": str(args.policy),
            "regressions": str(args.regressions),
            "logical_manifest": str(args.logical_manifest),
            "manual_manifest": str(args.manual_manifest),
        },
        "summary": summary,
        "numbered": numbered_manifest,
        "atlas": atlas_manifest,
        "paratext": paratext_manifest,
    }
    (args.out_root / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.out_root / "README.md").write_text(
        "# Final figure set v1\n\n"
        "numbered/ is the canonical complete sequence (1–156 plus 28bis and "
        "87bis). automatic/ contains untouched direct crops. restored/ "
        "contains every repaired weak/detail/text/bleed case. manual/ contains "
        "all explicit splits, joins, donor restorations, full atlas plates and "
        "the cover illustration. A file may be both restored and manual; hashes "
        "and provenance are recorded in manifest.json.\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
