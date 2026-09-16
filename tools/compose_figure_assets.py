#!/usr/bin/env python3
"""Compose detector crops or clipped crop parts into logical figures.

The detector emits physical evidence.  A logical illustration may be assembled
from several physical assets (figure 129), while one physical asset may contain
several printed illustrations (figures 35/36 and 144/145).  This tool preserves
source-page coordinates, never resizes a part, writes lossless RGBA PNG, and
records machine-readable provenance.

Modes:

* one composes explicitly supplied complete asset indices;
* batch renders the versioned logical-layout manifest, including clipped parts.
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
    if image.ndim != 3 or image.shape[2] != 4:
        raise ValueError(f"logical figures must be RGBA/BGRA arrays: {image.shape}")
    if not cv2.imwrite(str(path), image):
        raise RuntimeError(f"failed to write image: {path}")
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f"empty image output: {path}")
    return sha256_file(path)


def _parse_bbox(
    raw: Any,
    *,
    asset_index: int,
    metrics_path: Path,
    field: str = "bbox",
) -> tuple[int, int, int, int]:
    if not isinstance(raw, list) or len(raw) != 4:
        raise ValueError(
            f"asset {asset_index} in {metrics_path} has an invalid {field}: {raw!r}"
        )
    x1, y1, x2, y2 = (int(value) for value in raw)
    if not (0 <= x1 < x2 and 0 <= y1 < y2):
        raise ValueError(
            f"asset {asset_index} in {metrics_path} has an empty/negative "
            f"{field}: {raw!r}"
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


def _asset_map(metrics: dict[str, Any]) -> dict[int, dict[str, Any]]:
    by_index: dict[int, dict[str, Any]] = {}
    for fallback, asset in enumerate(metrics.get("assets", []), start=1):
        if not isinstance(asset, dict):
            continue
        index = int(asset.get("asset_index", fallback))
        if index in by_index:
            raise ValueError(f"duplicate asset index in metrics: {index}")
        by_index[index] = asset
    return by_index


def _normalize_part_specs(entry: dict[str, Any], *, entry_id: str) -> list[dict[str, Any]]:
    has_assets = "asset_indices" in entry
    has_parts = "parts" in entry
    if has_assets == has_parts:
        raise ValueError(
            f"composition {entry_id!r} must declare exactly one of "
            "asset_indices or parts"
        )

    if has_assets:
        raw = entry.get("asset_indices")
        if not isinstance(raw, list) or len(raw) < 2:
            raise ValueError(
                f"composition {entry_id!r} needs at least two asset indices"
            )
        indices = [int(value) for value in raw]
        if len(set(indices)) != len(indices):
            raise ValueError(
                f"composition {entry_id!r} asset indices must be unique: {indices}"
            )
        return [
            {"asset_index": index, "trim_transparent": False}
            for index in indices
        ]

    raw_parts = entry.get("parts")
    if not isinstance(raw_parts, list) or not raw_parts:
        raise ValueError(f"composition {entry_id!r} parts must be a non-empty array")
    normalized: list[dict[str, Any]] = []
    for position, raw in enumerate(raw_parts, start=1):
        if not isinstance(raw, dict):
            raise ValueError(
                f"composition {entry_id!r} part {position} must be an object"
            )
        if "asset_index" not in raw:
            raise ValueError(
                f"composition {entry_id!r} part {position} needs asset_index"
            )
        part = {
            "asset_index": int(raw["asset_index"]),
            "trim_transparent": bool(raw.get("trim_transparent", False)),
        }
        if "clip_bbox" in raw:
            part["clip_bbox"] = raw["clip_bbox"]
        normalized.append(part)
    return normalized


def _validate_page_dimensions(
    metrics: dict[str, Any], metrics_path: Path
) -> tuple[int, int]:
    page_width = int(metrics.get("width", 0))
    page_height = int(metrics.get("height", 0))
    if page_width <= 0 or page_height <= 0:
        raise ValueError(f"invalid source-page dimensions in {metrics_path}")
    return page_width, page_height


def _declared_part_windows(
    metrics: dict[str, Any],
    metrics_path: Path,
    part_specs: list[dict[str, Any]],
) -> list[tuple[int, tuple[int, int, int, int]]]:
    page_width, page_height = _validate_page_dimensions(metrics, metrics_path)
    by_index = _asset_map(metrics)
    windows: list[tuple[int, tuple[int, int, int, int]]] = []
    for part in part_specs:
        index = int(part["asset_index"])
        if index not in by_index:
            raise ValueError(f"asset {index} is absent from {metrics_path}")
        asset_bbox = _parse_bbox(
            by_index[index].get("bbox"),
            asset_index=index,
            metrics_path=metrics_path,
        )
        if asset_bbox[2] > page_width or asset_bbox[3] > page_height:
            raise ValueError(
                f"asset {index} bbox {asset_bbox} exceeds page "
                f"{page_width}x{page_height}"
            )
        if "clip_bbox" in part:
            window = _parse_bbox(
                part["clip_bbox"],
                asset_index=index,
                metrics_path=metrics_path,
                field="clip_bbox",
            )
            if not (
                asset_bbox[0] <= window[0] < window[2] <= asset_bbox[2]
                and asset_bbox[1] <= window[1] < window[3] <= asset_bbox[3]
            ):
                raise ValueError(
                    f"asset {index} clip_bbox {window} is outside asset bbox "
                    f"{asset_bbox} in {metrics_path}"
                )
        else:
            window = asset_bbox
        windows.append((index, window))
    return windows


def _select_parts(
    metrics: dict[str, Any],
    metrics_path: Path,
    source_root: Path,
    part_specs: list[dict[str, Any]],
    derivative: str,
) -> list[dict[str, Any]]:
    by_index = _asset_map(metrics)
    declared = _declared_part_windows(metrics, metrics_path, part_specs)
    selected: list[dict[str, Any]] = []
    cache: dict[int, tuple[dict[str, Any], Path, str, np.ndarray, tuple[int, int, int, int]]] = {}

    for part_spec, (index, requested_bbox) in zip(part_specs, declared):
        if index not in cache:
            record = by_index[index]
            asset_bbox = _parse_bbox(
                record.get("bbox"), asset_index=index, metrics_path=metrics_path
            )
            files = record.get("files", {})
            filename = files.get(derivative) if isinstance(files, dict) else None
            if not filename or Path(str(filename)).name != str(filename):
                raise ValueError(
                    f"asset {index} has no safe {derivative!r} filename in "
                    f"{metrics_path}"
                )
            crop_path = source_root / str(filename)
            if not crop_path.is_file():
                raise ValueError(
                    f"missing {derivative} crop for asset {index}: {crop_path}"
                )
            hashes = record.get("sha256", {})
            declared_sha = hashes.get(derivative) if isinstance(hashes, dict) else None
            actual_sha = sha256_file(crop_path)
            if declared_sha and actual_sha != declared_sha:
                raise ValueError(
                    f"SHA-256 mismatch for {crop_path}: expected {declared_sha}, "
                    f"got {actual_sha}"
                )
            image = _as_bgra(crop_path)
            expected_shape = (
                asset_bbox[3] - asset_bbox[1],
                asset_bbox[2] - asset_bbox[0],
            )
            if image.shape[:2] != expected_shape:
                raise ValueError(
                    f"crop dimensions do not match bbox for asset {index}: "
                    f"image={image.shape[1]}x{image.shape[0]}, "
                    f"bbox={expected_shape[1]}x{expected_shape[0]}"
                )
            cache[index] = (record, crop_path, actual_sha, image, asset_bbox)

        _, crop_path, actual_sha, full_image, asset_bbox = cache[index]
        rx1 = requested_bbox[0] - asset_bbox[0]
        ry1 = requested_bbox[1] - asset_bbox[1]
        rx2 = requested_bbox[2] - asset_bbox[0]
        ry2 = requested_bbox[3] - asset_bbox[1]
        image = full_image[ry1:ry2, rx1:rx2].copy()
        output_bbox = requested_bbox

        if part_spec.get("trim_transparent", False):
            ys, xs = np.where(image[:, :, 3] > 0)
            if xs.size == 0:
                raise ValueError(
                    f"asset {index} clip {requested_bbox} is fully transparent"
                )
            tx1, ty1 = int(xs.min()), int(ys.min())
            tx2, ty2 = int(xs.max() + 1), int(ys.max() + 1)
            image = image[ty1:ty2, tx1:tx2]
            output_bbox = (
                requested_bbox[0] + tx1,
                requested_bbox[1] + ty1,
                requested_bbox[0] + tx2,
                requested_bbox[1] + ty2,
            )

        selected.append(
            {
                "asset_index": index,
                "asset_bbox": asset_bbox,
                "requested_bbox": requested_bbox,
                "bbox": output_bbox,
                "trim_transparent": bool(
                    part_spec.get("trim_transparent", False)
                ),
                "path": crop_path,
                "sha256": actual_sha,
                "image": image,
            }
        )

    for i, first in enumerate(selected):
        for second in selected[i + 1 :]:
            if first["asset_index"] != second["asset_index"]:
                continue
            if _bbox_overlap(first["requested_bbox"], second["requested_bbox"]):
                raise ValueError(
                    f"overlapping windows from asset {first['asset_index']}: "
                    f"{first['requested_bbox']} and {second['requested_bbox']}"
                )
    return selected


def _bbox_overlap(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> bool:
    return (
        max(first[0], second[0]) < min(first[2], second[2])
        and max(first[1], second[1]) < min(first[3], second[3])
    )


def _compose_selected_parts(
    metrics: dict[str, Any],
    metrics_path: Path,
    parts: list[dict[str, Any]],
    output_path: Path,
    *,
    derivative: str,
    background: str,
    padding: int,
) -> dict[str, Any]:
    if not parts:
        raise ValueError("a logical composition needs at least one part")
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
        px1, py1, _, _ = part["bbox"]
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
                "asset_bbox": list(part["asset_bbox"]),
                "requested_bbox": list(part["requested_bbox"]),
                "bbox": list(part["bbox"]),
                "trim_transparent": part["trim_transparent"],
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


def compose_page_parts(
    metrics_path: Path,
    part_specs: list[dict[str, Any]],
    output_path: Path,
    *,
    source_root: Path | None = None,
    derivative: str = "clean",
    background: str = "transparent",
    padding: int = 0,
) -> dict[str, Any]:
    """Render declared crop parts at their original page-coordinate offsets."""
    if derivative not in DERIVATIVES:
        raise ValueError(
            f"unsupported derivative {derivative!r}; choose from {DERIVATIVES}"
        )
    if background not in BACKGROUNDS:
        raise ValueError(
            f"unsupported background {background!r}; choose from {BACKGROUNDS}"
        )
    if padding < 0:
        raise ValueError("padding must be non-negative")
    if not isinstance(part_specs, list) or not part_specs:
        raise ValueError("a logical composition needs at least one part")

    metrics_path = Path(metrics_path)
    metrics = load_json(metrics_path)
    crop_root = Path(source_root) if source_root is not None else metrics_path.parent
    parts = _select_parts(
        metrics,
        metrics_path,
        crop_root,
        part_specs,
        derivative,
    )
    return _compose_selected_parts(
        metrics,
        metrics_path,
        parts,
        Path(output_path),
        derivative=derivative,
        background=background,
        padding=padding,
    )


def compose_page_assets(
    metrics_path: Path,
    asset_indices: list[int],
    output_path: Path,
    *,
    source_root: Path | None = None,
    derivative: str = "clean",
    background: str = "transparent",
    padding: int = 0,
) -> dict[str, Any]:
    """Compatibility wrapper for composing two or more complete assets."""
    indices = [int(value) for value in asset_indices]
    if len(indices) < 2:
        raise ValueError("a logical composition needs at least two asset indices")
    if len(set(indices)) != len(indices):
        raise ValueError(f"asset indices must be unique: {indices}")
    return compose_page_parts(
        metrics_path,
        [
            {"asset_index": index, "trim_transparent": False}
            for index in indices
        ],
        output_path,
        source_root=source_root,
        derivative=derivative,
        background=background,
        padding=padding,
    )


def _safe_relative_output(value: Any, *, entry_id: str) -> Path:
    path = Path(str(value))
    if not value or path.is_absolute() or ".." in path.parts:
        raise ValueError(
            f"composition {entry_id!r} has an unsafe output path: {value!r}"
        )
    if path.suffix.lower() != ".png":
        raise ValueError(
            f"composition {entry_id!r} output must end in .png: {path}"
        )
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
        raise ValueError(
            f"unsupported composition schema in {spec_path}: {schema!r}"
        )
    entries = spec.get("compositions", [])
    if not isinstance(entries, list):
        raise ValueError(f"compositions must be an array in {spec_path}")

    seen_ids: set[str] = set()
    seen_outputs: set[Path] = set()
    seen_windows: dict[
        tuple[str, int], list[tuple[tuple[int, int, int, int], str]]
    ] = {}
    selected: list[
        tuple[dict[str, Any], str, str, Path, list[dict[str, Any]]]
    ] = []

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
            raise ValueError(
                f"composition {entry_id!r} has an unsafe page_id: {page_id!r}"
            )
        output_relative = _safe_relative_output(
            entry.get("output"), entry_id=entry_id
        )
        if output_relative in seen_outputs:
            raise ValueError(
                f"duplicate composition output path: {output_relative}"
            )
        seen_outputs.add(output_relative)

        part_specs = _normalize_part_specs(entry, entry_id=entry_id)
        metrics_path = source_root / f"{page_id}.metrics.json"
        metrics = load_json(metrics_path)
        windows = _declared_part_windows(metrics, metrics_path, part_specs)
        for asset_index, window in windows:
            key = (page_id, asset_index)
            for previous, previous_id in seen_windows.get(key, []):
                if _bbox_overlap(previous, window):
                    raise ValueError(
                        "physical asset windows overlap across compositions: "
                        f"{page_id}#asset-{asset_index:02d} "
                        f"{previous_id!r} {previous} vs {entry_id!r} {window}"
                    )
            seen_windows.setdefault(key, []).append((window, entry_id))

        if only is None or entry_id in only:
            selected.append(
                (entry, entry_id, page_id, output_relative, part_specs)
            )

    if only is not None:
        unknown = sorted(only - seen_ids)
        if unknown:
            raise ValueError(
                f"unknown composition id(s): {', '.join(unknown)}"
            )
    if not selected:
        raise ValueError("no compositions selected")

    rendered: list[dict[str, Any]] = []
    for entry, entry_id, page_id, output_relative, part_specs in selected:
        provenance = compose_page_parts(
            source_root / f"{page_id}.metrics.json",
            part_specs,
            output_root / output_relative,
            source_root=source_root,
            derivative=str(entry.get("derivative", "clean")),
            background=str(entry.get("background", "transparent")),
            padding=int(entry.get("padding", 0)),
        )
        rendered.append(
            {
                "id": entry_id,
                "figure_labels": entry.get("figure_labels", []),
                "status": entry.get("status", "ok_logical"),
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

    one = subparsers.add_parser(
        "one", help="compose complete assets from one page metrics file"
    )
    one.add_argument("metrics", type=Path)
    one.add_argument("--assets", nargs="+", type=int, required=True)
    one.add_argument("--source-root", type=Path, default=None)
    one.add_argument("--derivative", choices=DERIVATIVES, default="clean")
    one.add_argument(
        "--background", choices=BACKGROUNDS, default="transparent"
    )
    one.add_argument("--padding", type=int, default=0)
    one.add_argument("--out", type=Path, required=True)
    one.add_argument("--provenance-out", type=Path, default=None)
    one.add_argument("--no-provenance", action="store_true")

    batch = subparsers.add_parser(
        "batch", help="render a versioned logical-layout manifest"
    )
    batch.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    batch.add_argument(
        "--source-root", type=Path, default=Path("work/figure-structure-full-v2")
    )
    batch.add_argument(
        "--out-root", type=Path, default=Path("work/figure-logical-v1")
    )
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
