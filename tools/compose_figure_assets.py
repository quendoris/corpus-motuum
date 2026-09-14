#!/usr/bin/env python3
"""Compose several detector crops into one logical figure.

The detector deliberately emits disconnected regions as separate physical
assets.  Editorial grouping happens later: this tool places selected crops back
into their source-page coordinate system without resizing or guessing a gap.
The result therefore preserves the geometry recorded in ``*.metrics.json`` and
keeps a machine-readable provenance record.

Two modes are available:

* ``one`` composes an explicitly supplied metrics file and asset indices;
* ``batch`` renders every declared composition in a versioned JSON manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np


DEFAULT_SPEC = Path("corpus/figures/logical-compositions-v1.json")
DERIVATIVES = ("clean", "source")
BACKGROUNDS = ("white", "transparent")


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"JSON file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return data


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checked_imwrite(path: Path, image: np.ndarray) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() != ".png":
        raise ValueError(f"logical figures must be lossless PNG files: {path}")
    if not cv2.imwrite(str(path), image):
        raise RuntimeError(f"failed to write image: {path}")
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f"empty image output: {path}")
    return sha256_file(path)


def _parse_bbox(raw: Any, *, asset_index: int, metrics_path: Path) -> tuple[int, int, int, int]:
    if not isinstance(raw, list) or len(raw) != 4:
        raise ValueError(
            f"asset {asset_index} in {metrics_path} has an invalid bbox: {raw!r}"
        )
    x1, y1, x2, y2 = (int(value) for value in raw)
    if not (0 <= x1 < x2 and 0 <= y1 < y2):
        raise ValueError(
            f"asset {asset_index} in {metrics_path} has an empty/negative bbox: {raw!r}"
        )
    return x1, y1, x2, y2


def _as_bgra(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f"could not read crop image: {path}")
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGRA)
    elif image.ndim == 3 and image.shape[2] == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    elif image.ndim != 3 or image.shape[2] != 4:
        raise ValueError(f"unsupported crop image shape {image.shape}: {path}")
    return image


def _alpha_over(destination: np.ndarray, source: np.ndarray) -> None:
    """Porter-Duff source-over for straight-alpha BGRA arrays."""
    src = source.astype(np.float32) / 255.0
    dst = destination.astype(np.float32) / 255.0
    src_alpha = src[:, :, 3:4]
    dst_alpha = dst[:, :, 3:4]
    out_alpha = src_alpha + dst_alpha * (1.0 - src_alpha)
    premultiplied = (
        src[:, :, :3] * src_alpha
        + dst[:, :, :3] * dst_alpha * (1.0 - src_alpha)
    )
    out_rgb = np.full_like(premultiplied, 1.0)
    np.divide(
        premultiplied,
        out_alpha,
        out=out_rgb,
        where=out_alpha > (1.0 / 65535.0),
    )
    result = np.concatenate((out_rgb, out_alpha), axis=2)
    destination[:] = np.clip(np.rint(result * 255.0), 0, 255).astype(np.uint8)


def _select_assets(
    metrics: dict[str, Any],
    metrics_path: Path,
    source_root: Path,
    asset_indices: list[int],
    derivative: str,
) -> list[dict[str, Any]]:
    if len(asset_indices) < 2:
        raise ValueError("a logical composition needs at least two asset indices")
    if len(set(asset_indices)) != len(asset_indices):
        raise ValueError(f"asset indices must be unique: {asset_indices}")

    by_index: dict[int, dict[str, Any]] = {}
    for fallback, asset in enumerate(metrics.get("assets", []), start=1):
        if not isinstance(asset, dict):
            continue
        index = int(asset.get("asset_index", fallback))
        by_index[index] = asset

    selected: list[dict[str, Any]] = []
    page_width = int(metrics.get("width", 0))
    page_height = int(metrics.get("height", 0))
    if page_width <= 0 or page_height <= 0:
        raise ValueError(f"invalid source-page dimensions in {metrics_path}")

    for index in asset_indices:
        if index not in by_index:
            raise ValueError(f"asset {index} is absent from {metrics_path}")
        record = by_index[index]
        bbox = _parse_bbox(record.get("bbox"), asset_index=index, metrics_path=metrics_path)
        x1, y1, x2, y2 = bbox
        if x2 > page_width or y2 > page_height:
            raise ValueError(
                f"asset {index} bbox {bbox} exceeds page {page_width}x{page_height}"
            )

        files = record.get("files", {})
        filename = files.get(derivative) if isinstance(files, dict) else None
        if not filename or Path(str(filename)).name != str(filename):
            raise ValueError(
                f"asset {index} has no safe {derivative!r} filename in {metrics_path}"
            )
        crop_path = source_root / str(filename)
        if not crop_path.is_file():
            raise ValueError(f"missing {derivative} crop for asset {index}: {crop_path}")

        hashes = record.get("sha256", {})
        declared_sha = hashes.get(derivative) if isinstance(hashes, dict) else None
        actual_sha = sha256_file(crop_path)
        if declared_sha and actual_sha != declared_sha:
            raise ValueError(
                f"SHA-256 mismatch for {crop_path}: expected {declared_sha}, got {actual_sha}"
            )

        image = _as_bgra(crop_path)
        expected_shape = (y2 - y1, x2 - x1)
        if image.shape[:2] != expected_shape:
            raise ValueError(
                f"crop dimensions do not match bbox for asset {index}: "
                f"image={image.shape[1]}x{image.shape[0]}, "
                f"bbox={expected_shape[1]}x{expected_shape[0]}"
            )
        selected.append(
            {
                "asset_index": index,
                "bbox": bbox,
                "path": crop_path,
                "sha256": actual_sha,
                "image": image,
            }
        )
    return selected


def compose_page_assets(
    metrics_path: Path,
    asset_indices: list[int],
    output_path: Path,
    *,
    source_root: Path | None = None,
    derivative: str = "clean",
    background: str = "white",
    padding: int = 0,
) -> dict[str, Any]:
    """Render selected crops at their original offsets inside their union bbox."""
    if derivative not in DERIVATIVES:
        raise ValueError(f"unsupported derivative {derivative!r}; choose from {DERIVATIVES}")
    if background not in BACKGROUNDS:
        raise ValueError(f"unsupported background {background!r}; choose from {BACKGROUNDS}")
    if padding < 0:
        raise ValueError("padding must be non-negative")

    metrics_path = Path(metrics_path)
    metrics = load_json(metrics_path)
    crop_root = Path(source_root) if source_root is not None else metrics_path.parent
    parts = _select_assets(
        metrics,
        metrics_path,
        crop_root,
        [int(value) for value in asset_indices],
        derivative,
    )

    x1 = min(part["bbox"][0] for part in parts)
    y1 = min(part["bbox"][1] for part in parts)
    x2 = max(part["bbox"][2] for part in parts)
    y2 = max(part["bbox"][3] for part in parts)
    width = x2 - x1 + 2 * padding
    height = y2 - y1 + 2 * padding
    canvas = np.zeros((height, width, 4), dtype=np.uint8)
    canvas[:, :, :3] = 255
    if background == "white":
        canvas[:, :, 3] = 255

    provenance_parts: list[dict[str, Any]] = []
    for part in parts:
        px1, py1, px2, py2 = part["bbox"]
        offset_x = px1 - x1 + padding
        offset_y = py1 - y1 + padding
        image = part["image"]
        target = canvas[
            offset_y : offset_y + image.shape[0],
            offset_x : offset_x + image.shape[1],
        ]
        _alpha_over(target, image)
        provenance_parts.append(
            {
                "asset_index": part["asset_index"],
                "bbox": list(part["bbox"]),
                "offset": [offset_x, offset_y],
                "file": str(part["path"]),
                "sha256": part["sha256"],
            }
        )

    output_sha = checked_imwrite(Path(output_path), canvas)
    return {
        "schema": "corpus-motuum-logical-figure-provenance-v1",
        "layout": "source-coordinates",
        "metrics": str(metrics_path),
        "source_page": metrics.get("source"),
        "source_page_sha256": metrics.get("source_sha256"),
        "source_page_size": [int(metrics["width"]), int(metrics["height"])],
        "derivative": derivative,
        "background": background,
        "padding": padding,
        "union_bbox": [x1, y1, x2, y2],
        "parts": provenance_parts,
        "output": {
            "path": str(output_path),
            "width": width,
            "height": height,
            "sha256": output_sha,
        },
    }


def _safe_relative_output(value: Any, *, entry_id: str) -> Path:
    path = Path(str(value))
    if not value or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"composition {entry_id!r} has an unsafe output path: {value!r}")
    if path.suffix.lower() != ".png":
        raise ValueError(f"composition {entry_id!r} output must end in .png: {path}")
    return path


def render_batch(
    spec_path: Path,
    source_root: Path,
    output_root: Path,
    *,
    only: set[str] | None = None,
) -> dict[str, Any]:
    spec = load_json(spec_path)
    schema = spec.get("schema")
    if schema != "corpus-motuum-logical-compositions-v1":
        raise ValueError(f"unsupported composition schema in {spec_path}: {schema!r}")
    entries = spec.get("compositions", [])
    if not isinstance(entries, list):
        raise ValueError(f"compositions must be an array in {spec_path}")

    seen_ids: set[str] = set()
    seen_outputs: set[Path] = set()
    seen_assets: set[tuple[str, int]] = set()
    selected: list[tuple[dict[str, Any], str, str, Path, list[int]]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"composition entry must be an object: {entry!r}")
        entry_id = str(entry.get("id", "")).strip()
        if not entry_id:
            raise ValueError("every composition needs a non-empty id")
        if entry_id in seen_ids:
            raise ValueError(f"duplicate composition id: {entry_id}")
        seen_ids.add(entry_id)

        if entry.get("layout", "source-coordinates") != "source-coordinates":
            raise ValueError(
                f"composition {entry_id!r} uses unsupported layout "
                f"{entry.get('layout')!r}"
            )
        page_id = str(entry.get("page_id", "")).strip()
        if not page_id or Path(page_id).name != page_id:
            raise ValueError(f"composition {entry_id!r} has an unsafe page_id: {page_id!r}")
        output_relative = _safe_relative_output(entry.get("output"), entry_id=entry_id)
        if output_relative in seen_outputs:
            raise ValueError(f"duplicate composition output path: {output_relative}")
        seen_outputs.add(output_relative)

        raw_indices = entry.get("asset_indices", [])
        if not isinstance(raw_indices, list):
            raise ValueError(f"composition {entry_id!r} asset_indices must be an array")
        asset_indices = [int(value) for value in raw_indices]
        for asset_index in asset_indices:
            asset_key = (page_id, asset_index)
            if asset_key in seen_assets:
                raise ValueError(
                    f"physical asset used by more than one composition: "
                    f"{page_id}#asset-{asset_index:02d}"
                )
            seen_assets.add(asset_key)
        if only is None or entry_id in only:
            selected.append((entry, entry_id, page_id, output_relative, asset_indices))

    if only is not None:
        unknown = sorted(only - seen_ids)
        if unknown:
            raise ValueError(f"unknown composition id(s): {', '.join(unknown)}")
    if not selected:
        raise ValueError("no compositions selected")

    rendered: list[dict[str, Any]] = []
    for entry, entry_id, page_id, output_relative, asset_indices in selected:
        provenance = compose_page_assets(
            source_root / f"{page_id}.metrics.json",
            asset_indices,
            output_root / output_relative,
            source_root=source_root,
            derivative=str(entry.get("derivative", "clean")),
            background=str(entry.get("background", "white")),
            padding=int(entry.get("padding", 0)),
        )
        rendered.append(
            {
                "id": entry_id,
                "figure_labels": entry.get("figure_labels", []),
                "status": entry.get("status", "ok_composite"),
                "detail": entry.get("detail"),
                "render": provenance,
            }
        )

    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "compositions-manifest.json"
    payload = {
        "schema": "corpus-motuum-logical-composition-build-v1",
        "spec": str(spec_path),
        "source_root": str(source_root),
        "output_root": str(output_root),
        "rendered_count": len(rendered),
        "rendered": rendered,
    }
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def write_provenance(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    one = subparsers.add_parser("one", help="compose assets from one page metrics file")
    one.add_argument("metrics", type=Path)
    one.add_argument("--assets", nargs="+", type=int, required=True)
    one.add_argument("--source-root", type=Path, default=None)
    one.add_argument("--derivative", choices=DERIVATIVES, default="clean")
    one.add_argument("--background", choices=BACKGROUNDS, default="white")
    one.add_argument("--padding", type=int, default=0)
    one.add_argument("--out", type=Path, required=True)
    one.add_argument("--provenance-out", type=Path, default=None)
    one.add_argument("--no-provenance", action="store_true")

    batch = subparsers.add_parser("batch", help="render a versioned composition manifest")
    batch.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    batch.add_argument("--source-root", type=Path, default=Path("work/figure-structure-full-v2"))
    batch.add_argument("--out-root", type=Path, default=Path("work/figure-logical-v1"))
    batch.add_argument("--only", action="append", default=None, metavar="ID")

    args = parser.parse_args()
    try:
        if args.command == "one":
            payload = compose_page_assets(
                args.metrics,
                args.assets,
                args.out,
                source_root=args.source_root,
                derivative=args.derivative,
                background=args.background,
                padding=args.padding,
            )
            if not args.no_provenance:
                provenance_path = args.provenance_out or args.out.with_name(
                    f"{args.out.stem}.provenance.json"
                )
                write_provenance(provenance_path, payload)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            payload = render_batch(
                args.spec,
                args.source_root,
                args.out_root,
                only=set(args.only) if args.only else None,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2))
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
