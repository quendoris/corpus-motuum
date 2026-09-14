#!/usr/bin/env python3
"""Build compact visual evidence for the final figure-corpus pass.

The script never edits detector output. It inventories the curated crops,
computes a conservative bleed-through suspicion score, and writes review JPEGs
plus a JSON report. Full-resolution derivatives stay in the workflow artifact;
only compact review material is suitable for Git.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


DEFAULT_TARGETS = [
    "sheet-001-right",
    "sheet-076-right",
    "sheet-084-left",
    "sheet-128-left",
    "sheet-133-right",
    "sheet-228-left",
    "sheet-256-right",
    "sheet-297-left",
    "sheet-297-right",
    "sheet-298-left",
    "sheet-298-right",
    "sheet-299-left",
    "sheet-299-right",
]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def checked_read(path: Path, flags: int = cv2.IMREAD_UNCHANGED) -> np.ndarray:
    image = cv2.imread(str(path), flags)
    if image is None:
        raise RuntimeError(f"could not read image: {path}")
    return image


def as_rgba_pil(image: np.ndarray) -> Image.Image:
    if image.ndim == 2:
        return Image.fromarray(image, mode="L").convert("RGBA")
    if image.shape[2] == 3:
        return Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB), mode="RGB").convert("RGBA")
    if image.shape[2] == 4:
        return Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGRA2RGBA), mode="RGBA")
    raise ValueError(f"unsupported image shape: {image.shape}")


def composite(image: np.ndarray, background: tuple[int, int, int]) -> Image.Image:
    rgba = as_rgba_pil(image)
    base = Image.new("RGBA", rgba.size, (*background, 255))
    base.alpha_composite(rgba)
    return base.convert("RGB")


def fit_image(image: Image.Image, width: int, height: int) -> Image.Image:
    result = image.copy()
    result.thumbnail((width, height), Image.Resampling.LANCZOS)
    return result


def label_lines(draw: ImageDraw.ImageDraw, lines: list[str], x: int, y: int) -> None:
    font = ImageFont.load_default()
    for line in lines:
        draw.text((x, y), line, fill=(15, 15, 15), font=font)
        y += 15


def make_contact_sheet(
    items: list[tuple[str, Image.Image]],
    output: Path,
    *,
    columns: int = 4,
    tile_width: int = 350,
    tile_height: int = 390,
    image_height: int = 330,
) -> None:
    if not items:
        return
    rows = math.ceil(len(items) / columns)
    sheet = Image.new("RGB", (columns * tile_width, rows * tile_height), (232, 232, 232))
    draw = ImageDraw.Draw(sheet)
    for index, (label, image) in enumerate(items):
        col = index % columns
        row = index // columns
        x = col * tile_width
        y = row * tile_height
        draw.rectangle((x + 2, y + 2, x + tile_width - 3, y + tile_height - 3), fill=(255, 255, 255))
        fitted = fit_image(image, tile_width - 20, image_height - 10)
        px = x + (tile_width - fitted.width) // 2
        py = y + 8 + (image_height - fitted.height) // 2
        sheet.paste(fitted, (px, py))
        label_lines(draw, [label], x + 10, y + image_height + 10)
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, format="JPEG", quality=91, optimize=True, progressive=True)


def paginate_contact_sheets(
    items: list[tuple[str, Image.Image]],
    output_dir: Path,
    prefix: str,
    *,
    page_size: int = 16,
) -> list[str]:
    paths: list[str] = []
    for offset in range(0, len(items), page_size):
        path = output_dir / f"{prefix}-{offset // page_size + 1:02d}.jpg"
        make_contact_sheet(items[offset : offset + page_size], path)
        paths.append(str(path))
    return paths


def asset_paths(
    structure_root: Path, page_id: str, asset_index: int
) -> tuple[Path, Path, Path]:
    prefix = structure_root / f"{page_id}-asset-{asset_index:02d}"
    return (
        prefix.with_suffix(".clean.png"),
        prefix.with_suffix(".source.png"),
        prefix.with_suffix(".support.png"),
    )


def score_bleedthrough(
    clean_path: Path,
    source_path: Path,
    support_path: Path,
    w_stroke: float,
) -> dict[str, float]:
    clean = checked_read(clean_path)
    source = checked_read(source_path)
    support = checked_read(support_path, cv2.IMREAD_GRAYSCALE) > 0
    if clean.shape[:2] != support.shape or source.shape[:2] != support.shape:
        raise RuntimeError(f"shape mismatch while scoring {clean_path}")

    clean_gray = (
        clean
        if clean.ndim == 2
        else cv2.cvtColor(clean[:, :, :3], cv2.COLOR_BGR2GRAY)
    )
    source_gray = (
        source
        if source.ndim == 2
        else cv2.cvtColor(source[:, :, :3], cv2.COLOR_BGR2GRAY)
    )
    alpha = (
        np.full(clean_gray.shape, 255, np.uint8)
        if clean.ndim != 3 or clean.shape[2] < 4
        else clean[:, :, 3]
    )
    valid = (alpha > 0) & support
    if not np.any(valid):
        return {
            "score": 0.0,
            "support_pixels": 0.0,
            "weak_fraction": 0.0,
            "unanchored_weak_fraction": 0.0,
            "source_paper_variation": 0.0,
        }

    darkness = 255.0 - clean_gray.astype(np.float32)
    values = darkness[valid]
    strong_threshold = max(42.0, float(np.percentile(values, 62.0)))
    weak = valid & (darkness >= 3.0) & (darkness < strong_threshold)
    strong = valid & (darkness >= strong_threshold)

    if np.any(strong):
        distance = cv2.distanceTransform(
            np.where(strong, 0, 1).astype(np.uint8), cv2.DIST_L2, 5
        )
        anchor_distance = max(3.0, 2.5 * w_stroke)
        unanchored = weak & (distance > anchor_distance)
    else:
        unanchored = weak

    kernel = max(7, int(round(8.0 * max(w_stroke, 1.0))) | 1)
    local_paper = cv2.GaussianBlur(source_gray, (kernel, kernel), 0)
    paper_delta = np.abs(
        source_gray.astype(np.float32) - local_paper.astype(np.float32)
    )
    paper_zone = (alpha > 0) & (~cv2.dilate(support.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool))
    paper_variation = (
        float(np.percentile(paper_delta[paper_zone], 90.0))
        if np.any(paper_zone)
        else 0.0
    )

    support_pixels = int(np.count_nonzero(valid))
    weak_fraction = float(np.count_nonzero(weak)) / support_pixels
    unanchored_fraction = float(np.count_nonzero(unanchored)) / support_pixels
    score = (
        100.0 * (1.8 * unanchored_fraction + 0.35 * weak_fraction)
        + 0.45 * paper_variation
    )
    return {
        "score": float(score),
        "support_pixels": float(support_pixels),
        "weak_fraction": weak_fraction,
        "unanchored_weak_fraction": unanchored_fraction,
        "source_paper_variation": paper_variation,
        "strong_threshold": strong_threshold,
    }


def side_by_side(source: np.ndarray, clean: np.ndarray, max_height: int = 310) -> Image.Image:
    src = fit_image(composite(source, (255, 255, 255)), 330, max_height)
    dst = fit_image(composite(clean, (255, 255, 255)), 330, max_height)
    height = max(src.height, dst.height)
    canvas = Image.new("RGB", (src.width + dst.width + 8, height), (240, 240, 240))
    canvas.paste(src, (0, (height - src.height) // 2))
    canvas.paste(dst, (src.width + 8, (height - dst.height) // 2))
    return canvas


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page-root", type=Path, required=True)
    parser.add_argument("--structure-root", type=Path, required=True)
    parser.add_argument("--review-root", type=Path, required=True)
    parser.add_argument("--logical-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("review/finalization-v1"))
    parser.add_argument("--top-bleedthrough", type=int, default=48)
    parser.add_argument("--target", action="append", default=None)
    args = parser.parse_args()

    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True)

    review = load_json(args.review_root / "manifest.json")
    records: list[dict[str, Any]] = []
    numbered_items: list[tuple[str, Image.Image]] = []

    for entry in review.get("kept", []):
        page_id = str(entry["id"])
        asset_index = int(entry["asset_index"])
        category = str(entry["category"])
        clean_path, source_path, support_path = asset_paths(
            args.structure_root, page_id, asset_index
        )
        metrics = load_json(args.structure_root / f"{page_id}.metrics.json")
        score = score_bleedthrough(
            clean_path,
            source_path,
            support_path,
            float(metrics.get("W_stroke", 2.0)),
        )
        record = {
            "key": entry["key"],
            "page_id": page_id,
            "physical_index": int(entry["physical_index"]),
            "asset_index": asset_index,
            "category": category,
            "caption_figure_numbers": entry.get("caption_figure_numbers", []),
            "bbox": entry.get("bbox"),
            **score,
        }
        records.append(record)
        if category == "numbered":
            clean = checked_read(clean_path)
            label = (
                f"{entry['key']} | figs={entry.get('caption_figure_numbers', [])} "
                f"| score={score['score']:.2f}"
            )
            numbered_items.append((label, composite(clean, (255, 255, 255))))

    numbered_items.sort(key=lambda item: item[0])
    numbered_sheets = paginate_contact_sheets(
        numbered_items, args.out / "numbered", "numbered"
    )

    ranked = sorted(records, key=lambda record: record["score"], reverse=True)
    bleed_items: list[tuple[str, Image.Image]] = []
    for record in ranked[: args.top_bleedthrough]:
        clean_path, source_path, _ = asset_paths(
            args.structure_root, record["page_id"], record["asset_index"]
        )
        clean = checked_read(clean_path)
        source = checked_read(source_path)
        label = (
            f"{record['key']} | figs={record['caption_figure_numbers']} "
            f"| score={record['score']:.2f}"
        )
        bleed_items.append((label, side_by_side(source, clean)))
    bleed_sheets = paginate_contact_sheets(
        bleed_items,
        args.out / "bleedthrough",
        "bleedthrough",
        page_size=12,
    )

    logical_items: list[tuple[str, Image.Image]] = []
    logical_manifest_path = args.logical_root / "compositions-manifest.json"
    if logical_manifest_path.is_file():
        logical = load_json(logical_manifest_path)
        for rendered in logical.get("rendered", []):
            output = Path(rendered["render"]["output"]["path"])
            image = checked_read(output)
            logical_items.append(
                (
                    f"{rendered['id']} | figs={rendered.get('figure_labels', [])}",
                    composite(image, (30, 30, 30)),
                )
            )
    logical_sheet = args.out / "logical" / "logical.jpg"
    make_contact_sheet(logical_items, logical_sheet)

    targets = args.target or DEFAULT_TARGETS
    target_items: list[tuple[str, Image.Image]] = []
    target_files: list[str] = []
    for page_id in targets:
        source = args.page_root / f"{page_id}.jpg"
        image = checked_read(source, cv2.IMREAD_COLOR)
        pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        target = fit_image(pil, 900, 1200)
        target_path = args.out / "targets" / f"{page_id}.jpg"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target.save(target_path, format="JPEG", quality=91, optimize=True, progressive=True)
        target_files.append(str(target_path))
        target_items.append((page_id, target))
    target_sheet = args.out / "targets" / "contact.jpg"
    make_contact_sheet(
        target_items,
        target_sheet,
        columns=3,
        tile_width=390,
        tile_height=650,
        image_height=600,
    )

    payload = {
        "schema": "corpus-motuum-figure-finalization-review-v1",
        "review_source": str(args.review_root / "manifest.json"),
        "logical_source": str(logical_manifest_path),
        "summary": {
            "kept_assets": len(records),
            "numbered_assets": len(numbered_items),
            "bleedthrough_ranked": len(records),
            "bleedthrough_previewed": len(bleed_items),
            "logical_assets": len(logical_items),
            "target_pages": len(target_files),
        },
        "numbered_contact_sheets": numbered_sheets,
        "bleedthrough_contact_sheets": bleed_sheets,
        "logical_contact_sheet": str(logical_sheet),
        "target_files": target_files,
        "target_contact_sheet": str(target_sheet),
        "bleedthrough_ranking": ranked,
    }
    (args.out / "review.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
