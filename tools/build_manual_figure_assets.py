#!/usr/bin/env python3
"""Build declared manual, restored-reference and full-plate figure assets.

All outputs are lossless RGBA stickers: accepted ink is crisp, enclosed paper
is white, the exterior is transparent, and the edge fades through white.
Detector crops and source pages are read-only evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import cv2
import fitz
import numpy as np


SCHEMA = "corpus-motuum-manual-figure-assets-v1"


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


def checked_read(path: Path, flags: int = cv2.IMREAD_UNCHANGED) -> np.ndarray:
    image = cv2.imread(str(path), flags)
    if image is None:
        raise ValueError(f"could not read image: {path}")
    return image


def checked_write(path: Path, image: np.ndarray) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() != ".png":
        raise ValueError(f"manual asset must be PNG: {path}")
    if image.ndim != 3 or image.shape[2] != 4:
        raise ValueError(f"manual asset must be BGRA: {path} {image.shape}")
    image = image.copy()
    image[image[:, :, 3] == 0, :3] = 255
    if not cv2.imwrite(str(path), image):
        raise RuntimeError(f"failed to write {path}")
    return sha256_file(path)


def safe_output(root: Path, value: Any) -> Path:
    relative = Path(str(value))
    if not value or relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe output path: {value!r}")
    return root / relative


def as_bgra(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGRA)
    if image.ndim == 3 and image.shape[2] == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    if image.ndim == 3 and image.shape[2] == 4:
        return image.copy()
    raise ValueError(f"unsupported image shape: {image.shape}")


def page_bbox(entry: dict[str, Any], width: int, height: int) -> tuple[int, int, int, int]:
    if "crop_page_bbox" in entry:
        raw = entry["crop_page_bbox"]
        if not isinstance(raw, list) or len(raw) != 4:
            raise ValueError(f"invalid crop_page_bbox in {entry.get('id')}")
        x1, y1, x2, y2 = (int(value) for value in raw)
    elif "crop_normalized" in entry:
        raw = entry["crop_normalized"]
        if not isinstance(raw, list) or len(raw) != 4:
            raise ValueError(f"invalid crop_normalized in {entry.get('id')}")
        x1 = int(round(float(raw[0]) * width))
        y1 = int(round(float(raw[1]) * height))
        x2 = int(round(float(raw[2]) * width))
        y2 = int(round(float(raw[3]) * height))
    else:
        raise ValueError(f"{entry.get('id')} needs a crop bbox")
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise ValueError(
            f"crop outside source for {entry.get('id')}: {(x1, y1, x2, y2)} "
            f"vs {width}x{height}"
        )
    return x1, y1, x2, y2


def erase_mask(
    shape: tuple[int, int],
    crop_bbox: tuple[int, int, int, int],
    boxes: list[Any],
) -> np.ndarray:
    mask = np.zeros(shape, np.uint8)
    cx1, cy1, _, _ = crop_bbox
    for raw in boxes:
        if not isinstance(raw, list) or len(raw) != 4:
            raise ValueError(f"invalid erase bbox: {raw!r}")
        x1, y1, x2, y2 = (int(value) for value in raw)
        lx1 = max(0, x1 - cx1)
        ly1 = max(0, y1 - cy1)
        lx2 = min(shape[1], x2 - cx1)
        ly2 = min(shape[0], y2 - cy1)
        if lx1 < lx2 and ly1 < ly2:
            mask[ly1:ly2, lx1:lx2] = 255
    return mask


def trim_rgba(image: np.ndarray, padding: int = 4) -> tuple[np.ndarray, list[int]]:
    alpha = image[:, :, 3]
    ys, xs = np.where(alpha > 0)
    if xs.size == 0:
        raise ValueError("cannot trim a fully transparent image")
    x1 = max(0, int(xs.min()) - padding)
    y1 = max(0, int(ys.min()) - padding)
    x2 = min(image.shape[1], int(xs.max()) + 1 + padding)
    y2 = min(image.shape[0], int(ys.max()) + 1 + padding)
    return image[y1:y2, x1:x2].copy(), [x1, y1, x2, y2]


def remove_small_components(mask: np.ndarray, minimum: int) -> np.ndarray:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        (mask > 0).astype(np.uint8), connectivity=8
    )
    result = np.zeros_like(mask)
    for label in range(1, count):
        if int(stats[label, cv2.CC_STAT_AREA]) >= minimum:
            result[labels == label] = 255
    return result


def retain_largest_cluster(mask: np.ndarray, radius: int) -> tuple[np.ndarray, dict[str, Any]]:
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1)
    )
    expanded = cv2.dilate(mask, kernel)
    count, labels = cv2.connectedComponents((expanded > 0).astype(np.uint8), connectivity=8)
    if count <= 1:
        return mask, {"cluster_count": 0, "kept_cluster": None}
    scores: list[tuple[int, int]] = []
    for label in range(1, count):
        scores.append((int(np.count_nonzero((labels == label) & (mask > 0))), label))
    _, chosen = max(scores)
    kept = np.where((labels == chosen) & (mask > 0), 255, 0).astype(np.uint8)
    return kept, {
        "cluster_count": count - 1,
        "kept_cluster": int(chosen),
        "cluster_support_pixels": int(np.count_nonzero(kept)),
    }


def holes(mask: np.ndarray) -> np.ndarray:
    h, w = mask.shape
    padded = cv2.copyMakeBorder(mask, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    flood = padded.copy()
    flood_mask = np.zeros((h + 4, w + 4), np.uint8)
    cv2.floodFill(flood, flood_mask, (0, 0), 255)
    outside = flood[1:-1, 1:-1]
    return cv2.bitwise_not(outside)


def make_sticker(
    bgr: np.ndarray,
    entry: dict[str, Any],
    crop_bbox: tuple[int, int, int, int],
) -> tuple[np.ndarray, dict[str, Any], dict[str, np.ndarray]]:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    scale = max(15, int(round(min(gray.shape) * 0.055)))
    if scale % 2 == 0:
        scale += 1
    paper = cv2.morphologyEx(
        gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (scale, scale))
    )
    residual = np.maximum(
        paper.astype(np.int16) - gray.astype(np.int16), 0
    ).astype(np.uint8)
    otsu, _ = cv2.threshold(residual, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    threshold = float(entry.get("ink_threshold", max(4.0, min(24.0, 0.58 * otsu))))
    support = np.where(residual >= threshold, 255, 0).astype(np.uint8)

    erased = erase_mask(
        gray.shape, crop_bbox, list(entry.get("erase_page_bboxes", []))
    )
    support[erased > 0] = 0
    minimum = max(1, int(entry.get("min_component_area", 2)))
    support = remove_small_components(support, minimum)

    cluster_diag: dict[str, Any] | None = None
    if entry.get("retain_largest_cluster", False):
        radius = max(1, int(entry.get("cluster_radius", 10)))
        support, cluster_diag = retain_largest_cluster(support, radius)

    if not np.any(support):
        raise ValueError(f"empty manual support for {entry.get('id')}")

    close_radius = max(1, int(entry.get("close_radius", 1)))
    close_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (2 * close_radius + 1, 2 * close_radius + 1)
    )
    closed = cv2.morphologyEx(support, cv2.MORPH_CLOSE, close_kernel)
    territory = cv2.bitwise_or(closed, holes(closed))

    margin = max(1, int(entry.get("white_margin", 2)))
    margin_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (2 * margin + 1, 2 * margin + 1)
    )
    hard = cv2.dilate(territory, margin_kernel)
    fade = max(1.0, float(entry.get("alpha_fade", 4.0)))
    distance = cv2.distanceTransform((hard == 0).astype(np.uint8), cv2.DIST_L2, 5)
    alpha = np.where(
        hard > 0, 255.0, np.clip(255.0 * (1.0 - distance / fade), 0.0, 255.0)
    ).astype(np.uint8)

    values = residual[support > 0]
    high = max(8.0, float(np.percentile(values, 99.5)))
    ink = np.clip(residual.astype(np.float32) * (225.0 / high), 0.0, 238.0)
    clean = np.full_like(gray, 255)
    ink_gray = np.clip(255.0 - ink, 0.0, 255.0).astype(np.uint8)
    clean[support > 0] = ink_gray[support > 0]
    clean[alpha == 0] = 255
    rgba = np.dstack((clean, clean, clean, alpha))
    diagnostics = {
        "paper_kernel": scale,
        "otsu": float(otsu),
        "ink_threshold": threshold,
        "support_pixels": int(np.count_nonzero(support)),
        "territory_pixels": int(np.count_nonzero(territory)),
        "alpha_pixels": int(np.count_nonzero(alpha)),
        "erased_pixels": int(np.count_nonzero(erased)),
        "cluster": cluster_diag,
    }
    return rgba, diagnostics, {"support": support, "alpha": alpha}


def asset_record(metrics: dict[str, Any], asset_index: int) -> dict[str, Any]:
    for fallback, record in enumerate(metrics.get("assets", []), 1):
        if int(record.get("asset_index", fallback)) == asset_index:
            return record
    raise ValueError(f"asset {asset_index} absent from metrics")


def build_clean_asset(
    entry: dict[str, Any], structure_root: Path
) -> tuple[np.ndarray, dict[str, Any], dict[str, np.ndarray]]:
    page_id = str(entry["page_id"])
    index = int(entry["asset_index"])
    metrics_path = structure_root / f"{page_id}.metrics.json"
    metrics = load_json(metrics_path)
    record = asset_record(metrics, index)
    bbox = tuple(int(value) for value in record["bbox"])
    path = structure_root / str(record["files"]["clean"])
    expected = record.get("sha256", {}).get("clean")
    actual = sha256_file(path)
    if expected and actual != expected:
        raise ValueError(f"clean crop hash mismatch: {path}")
    image = as_bgra(checked_read(path))
    erased = erase_mask(
        image.shape[:2], bbox, list(entry.get("erase_page_bboxes", []))
    )
    image[erased > 0] = (255, 255, 255, 0)
    trimmed, trim = trim_rgba(image)
    return trimmed, {
        "source_kind": "detector-clean",
        "metrics": str(metrics_path),
        "source_page": metrics.get("source"),
        "source_page_sha256": metrics.get("source_sha256"),
        "asset_index": index,
        "asset_bbox": list(bbox),
        "source_file": str(path),
        "source_file_sha256": actual,
        "erase_page_bboxes": entry.get("erase_page_bboxes", []),
        "trim_bbox_in_asset": trim,
    }, {"alpha": trimmed[:, :, 3]}


def render_reference_crop(
    document: fitz.Document, entry: dict[str, Any], zoom: float
) -> tuple[np.ndarray, tuple[int, int, int, int], dict[str, Any]]:
    page_number = int(entry["reference_pdf_page"])
    if not (1 <= page_number <= len(document)):
        raise ValueError(f"reference PDF page outside document: {page_number}")
    page = document[page_number - 1]
    pix = page.get_pixmap(
        matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB, alpha=False
    )
    rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    bbox = page_bbox(entry, pix.width, pix.height)
    x1, y1, x2, y2 = bbox
    return bgr[y1:y2, x1:x2].copy(), bbox, {
        "reference_pdf_page": page_number,
        "reference_printed_page": entry.get("reference_printed_page"),
        "render_zoom": zoom,
        "render_size": [pix.width, pix.height],
        "crop_bbox": list(bbox),
        "crop_normalized": entry.get("crop_normalized"),
    }


def build_page_sticker(
    entry: dict[str, Any], page_root: Path
) -> tuple[np.ndarray, dict[str, Any], dict[str, np.ndarray], np.ndarray]:
    page_id = str(entry["page_id"])
    path = page_root / f"{page_id}.jpg"
    page = checked_read(path, cv2.IMREAD_COLOR)
    bbox = page_bbox(entry, page.shape[1], page.shape[0])
    x1, y1, x2, y2 = bbox
    crop = page[y1:y2, x1:x2].copy()
    rgba, diagnostics, images = make_sticker(crop, entry, bbox)
    trimmed, trim = trim_rgba(rgba)
    images = {key: value[trim[1]:trim[3], trim[0]:trim[2]].copy() for key, value in images.items()}
    provenance = {
        "source_kind": "russian-page",
        "source_file": str(path),
        "source_file_sha256": sha256_file(path),
        "source_page_id": page_id,
        "crop_bbox": list(bbox),
        "crop_normalized": entry.get("crop_normalized"),
        "erase_page_bboxes": entry.get("erase_page_bboxes", []),
        "trim_bbox_in_crop": trim,
        "sticker_diagnostics": diagnostics,
    }
    return trimmed, provenance, images, crop


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=Path("corpus/figures/manual-assets-v1.json"))
    parser.add_argument("--page-root", type=Path, required=True)
    parser.add_argument("--structure-root", type=Path, required=True)
    parser.add_argument("--reference-pdf", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--reference-zoom", type=float, default=2.4)
    args = parser.parse_args()

    spec = load_json(args.spec)
    if spec.get("schema") != SCHEMA:
        raise SystemExit(f"unsupported manual asset schema: {spec.get('schema')!r}")
    expected_pdf = str(spec["reference_source"]["pdf_sha256"])
    actual_pdf = sha256_file(args.reference_pdf)
    if actual_pdf != expected_pdf:
        raise SystemExit(
            f"reference PDF SHA-256 mismatch: expected {expected_pdf}, got {actual_pdf}"
        )
    document = fitz.open(args.reference_pdf)
    records: list[dict[str, Any]] = []

    for section in ("numbered", "atlas", "paratext"):
        for entry in spec.get(section, []):
            output = safe_output(args.out_root, entry["output"])
            kind = str(entry["kind"])
            diagnostics_images: dict[str, np.ndarray] = {}
            raw_crop: np.ndarray | None = None
            if kind == "clean-asset-erasure":
                rgba, provenance, diagnostics_images = build_clean_asset(
                    entry, args.structure_root
                )
            elif kind == "page-sticker":
                rgba, provenance, diagnostics_images, raw_crop = build_page_sticker(
                    entry, args.page_root
                )
            elif kind == "reference-restoration":
                raw_crop, bbox, reference = render_reference_crop(
                    document, entry, args.reference_zoom
                )
                rgba, sticker_diag, diagnostics_images = make_sticker(
                    raw_crop, entry, bbox
                )
                rgba, trim = trim_rgba(rgba)
                diagnostics_images = {
                    key: value[trim[1]:trim[3], trim[0]:trim[2]].copy()
                    for key, value in diagnostics_images.items()
                }
                provenance = {
                    "source_kind": "public-domain-reference",
                    "reference": spec["reference_source"],
                    "reference_pdf_file": str(args.reference_pdf),
                    "reference_pdf_sha256": actual_pdf,
                    **reference,
                    "trim_bbox_in_crop": trim,
                    "sticker_diagnostics": sticker_diag,
                }
            else:
                raise ValueError(f"unknown manual asset kind {kind!r}")

            output_sha = checked_write(output, rgba)
            diag_root = args.out_root / "diagnostics" / str(entry["id"])
            diag_root.mkdir(parents=True, exist_ok=True)
            if raw_crop is not None:
                cv2.imwrite(
                    str(diag_root / "source.jpg"),
                    raw_crop,
                    [cv2.IMWRITE_JPEG_QUALITY, 94],
                )
            for name, image in diagnostics_images.items():
                cv2.imwrite(str(diag_root / f"{name}.png"), image)

            records.append({
                "id": entry["id"],
                "section": section,
                "label": entry.get("label"),
                "kind": kind,
                "restored": bool(entry.get("restored", False)),
                "reason": entry.get("reason"),
                "output": {
                    "path": str(output),
                    "sha256": output_sha,
                    "width": int(rgba.shape[1]),
                    "height": int(rgba.shape[0]),
                },
                "provenance": provenance,
            })

    manifest = {
        "schema": "corpus-motuum-manual-figure-build-v1",
        "spec": str(args.spec),
        "reference_pdf_sha256": actual_pdf,
        "record_count": len(records),
        "records": records,
    }
    args.out_root.mkdir(parents=True, exist_ok=True)
    (args.out_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "records": len(records),
        "sections": {
            section: sum(row["section"] == section for row in records)
            for section in ("numbered", "atlas", "paratext")
        },
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
