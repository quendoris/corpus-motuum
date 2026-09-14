#!/usr/bin/env python3
"""Build compact white/black visual evidence for the complete final figure set."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


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


def checked_rgba(path: Path) -> Image.Image:
    if not path.is_file():
        raise ValueError(f"missing final asset: {path}")
    with Image.open(path) as opened:
        image = opened.convert("RGBA")
    if image.getbbox() is None:
        raise ValueError(f"fully transparent final asset: {path}")
    return image


def composite(image: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    base = Image.new("RGBA", image.size, (*color, 255))
    base.alpha_composite(image)
    return base.convert("RGB")


def white_black(image: Image.Image) -> Image.Image:
    white = composite(image, (255, 255, 255))
    black = composite(image, (18, 18, 18))
    canvas = Image.new("RGB", (white.width * 2 + 4, white.height), (112, 112, 112))
    canvas.paste(white, (0, 0))
    canvas.paste(black, (white.width + 4, 0))
    return canvas


def fit(image: Image.Image, width: int, height: int) -> Image.Image:
    result = image.copy()
    result.thumbnail((width, height), Image.Resampling.LANCZOS)
    return result


def make_sheet(
    items: list[tuple[str, Image.Image]],
    output: Path,
    *,
    columns: int = 4,
    tile_width: int = 360,
    tile_height: int = 410,
    image_height: int = 352,
) -> None:
    if not items:
        return
    rows = math.ceil(len(items) / columns)
    sheet = Image.new("RGB", (columns * tile_width, rows * tile_height), (224, 224, 224))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, (label, image) in enumerate(items):
        column = index % columns
        row = index // columns
        x = column * tile_width
        y = row * tile_height
        draw.rectangle(
            (x + 3, y + 3, x + tile_width - 4, y + tile_height - 4),
            fill=(255, 255, 255),
        )
        fitted = fit(image, tile_width - 20, image_height - 12)
        px = x + (tile_width - fitted.width) // 2
        py = y + 8 + (image_height - fitted.height) // 2
        sheet.paste(fitted, (px, py))
        draw.text((x + 10, y + image_height + 10), label, fill=(15, 15, 15), font=font)
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, format="JPEG", quality=91, optimize=True, progressive=True)


def paginate(
    items: list[tuple[str, Image.Image]],
    directory: Path,
    prefix: str,
    *,
    page_size: int = 16,
    columns: int = 4,
) -> list[str]:
    outputs: list[str] = []
    for offset in range(0, len(items), page_size):
        output = directory / f"{prefix}-{offset // page_size + 1:02d}.jpg"
        make_sheet(items[offset : offset + page_size], output, columns=columns)
        outputs.append(str(output))
    return outputs


def alpha_statistics(image: Image.Image) -> dict[str, int]:
    alpha = image.getchannel("A")
    histogram = alpha.histogram()
    return {
        "transparent_pixels": int(histogram[0]),
        "opaque_pixels": int(histogram[255]),
        "soft_edge_pixels": int(sum(histogram[1:255])),
        "total_pixels": int(sum(histogram)),
    }


def numbered_items(
    records: list[dict[str, Any]],
    class_name: str | None,
    *,
    dual_background: bool,
) -> tuple[list[tuple[str, Image.Image]], list[dict[str, Any]]]:
    items: list[tuple[str, Image.Image]] = []
    checks: list[dict[str, Any]] = []
    for record in records:
        classes = [str(value) for value in record.get("classes", [])]
        if class_name is not None and class_name not in classes:
            continue
        path = Path(str(record["canonical_file"]))
        image = checked_rgba(path)
        actual = sha256_file(path)
        expected = str(record["canonical_sha256"])
        if actual != expected:
            raise ValueError(f"hash mismatch in final review: {path}")
        display = white_black(image) if dual_background else composite(image, (255, 255, 255))
        label = (
            f"fig. {record['label']} | {','.join(classes)} | "
            f"{image.width}x{image.height}"
        )
        items.append((label, display))
        checks.append({
            "label": str(record["label"]),
            "path": str(path),
            "sha256": actual,
            "classes": classes,
            "geometry": [image.width, image.height],
            "alpha": alpha_statistics(image),
        })
    return items, checks


def section_items(
    records: list[dict[str, Any]],
    *,
    dual_background: bool = True,
) -> tuple[list[tuple[str, Image.Image]], list[dict[str, Any]]]:
    items: list[tuple[str, Image.Image]] = []
    checks: list[dict[str, Any]] = []
    for record in records:
        path = Path(str(record["file"]))
        image = checked_rgba(path)
        actual = sha256_file(path)
        if actual != str(record["sha256"]):
            raise ValueError(f"hash mismatch in final review: {path}")
        display = white_black(image) if dual_background else composite(image, (255, 255, 255))
        label = f"{record['id']} | {image.width}x{image.height}"
        items.append((label, display))
        checks.append({
            "id": str(record["id"]),
            "path": str(path),
            "sha256": actual,
            "geometry": [image.width, image.height],
            "alpha": alpha_statistics(image),
        })
    return items, checks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--final-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("review/final-figure-set-v1"))
    args = parser.parse_args()

    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True)

    manifest_path = args.final_root / "manifest.json"
    manifest = load_json(manifest_path)
    numbered = list(manifest.get("numbered", []))

    all_items, all_checks = numbered_items(
        numbered, None, dual_background=False
    )
    restored_items, restored_checks = numbered_items(
        numbered, "restored", dual_background=True
    )
    manual_items, manual_checks = numbered_items(
        numbered, "manual", dual_background=True
    )
    atlas_items, atlas_checks = section_items(list(manifest.get("atlas", [])))
    paratext_items, paratext_checks = section_items(
        list(manifest.get("paratext", []))
    )

    all_sheets = paginate(
        all_items, args.out / "numbered", "numbered", page_size=16
    )
    restored_sheets = paginate(
        restored_items, args.out / "restored", "restored", page_size=12
    )
    manual_sheets = paginate(
        manual_items, args.out / "manual", "manual", page_size=12
    )
    atlas_sheets = paginate(
        atlas_items,
        args.out / "atlas",
        "atlas",
        page_size=6,
        columns=2,
    )
    paratext_sheets = paginate(
        paratext_items,
        args.out / "paratext",
        "paratext",
        page_size=8,
        columns=2,
    )

    expected = int(manifest["summary"]["expected_numbered_figures"])
    if len(numbered) != expected:
        raise ValueError(f"final set is incomplete: {len(numbered)} != {expected}")

    payload = {
        "schema": "corpus-motuum-final-figure-review-v1",
        "source_manifest": str(manifest_path),
        "source_manifest_sha256": sha256_file(manifest_path),
        "summary": {
            "numbered": len(all_checks),
            "restored": len(restored_checks),
            "manual_numbered": len(manual_checks),
            "atlas": len(atlas_checks),
            "paratext": len(paratext_checks),
            "numbered_contact_sheets": len(all_sheets),
        },
        "contact_sheets": {
            "numbered": all_sheets,
            "restored_white_black": restored_sheets,
            "manual_white_black": manual_sheets,
            "atlas_white_black": atlas_sheets,
            "paratext_white_black": paratext_sheets,
        },
        "checks": {
            "numbered": all_checks,
            "restored": restored_checks,
            "manual": manual_checks,
            "atlas": atlas_checks,
            "paratext": paratext_checks,
        },
    }
    (args.out / "review.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.out / "README.md").write_text(
        "# Final figure set visual review\n\n"
        "The numbered sheets verify the full canonical sequence. Restored, manual, "
        "atlas and paratext sheets show each transparent PNG on both white and "
        "near-black backgrounds so missing pale strokes, halos and opaque paper "
        "rectangles remain visible during review.\n",
        encoding="utf-8",
    )
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
