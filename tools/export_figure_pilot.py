#!/usr/bin/env python3
"""Export a stratified source-page pack for calibrating figure extraction.

The selector uses the already reviewed canonical text only to choose a useful
pilot set (pages with explicit figure captions plus negative controls). The
image-segmentation model itself must not depend on those captions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

CAPTION_RE = re.compile(r"(?mi)^\s*(\d+)\s+ФИГ\.\s*$")


@dataclass(frozen=True)
class Page:
    id: str
    physical_index: int
    text_path: Path
    image_path: Path
    source_sha256: str | None
    figure_numbers: tuple[int, ...]

    @property
    def is_positive(self) -> bool:
        return bool(self.figure_numbers)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def evenly_spaced(items: list[Page], count: int) -> list[Page]:
    if count <= 0 or not items:
        return []
    if count >= len(items):
        return list(items)
    if count == 1:
        return [items[len(items) // 2]]

    out: list[Page] = []
    seen: set[int] = set()
    n = len(items)
    for i in range(count):
        idx = round(i * (n - 1) / (count - 1))
        if idx not in seen:
            out.append(items[idx])
            seen.add(idx)
    return out


def load_pages(text_root: Path, pages_root: Path, source_manifest: Path) -> list[Page]:
    source_by_id: dict[str, dict] = {}
    if source_manifest.exists():
        payload = json.loads(source_manifest.read_text(encoding="utf-8"))
        source_by_id = {str(r["id"]): r for r in payload.get("records", [])}

    pages: list[Page] = []
    for text_path in sorted(text_root.glob("*.json")):
        data = json.loads(text_path.read_text(encoding="utf-8"))
        if data.get("status") != "verified":
            continue
        page_id = str(data["id"])
        physical_index = int(data["physical_index"])
        diplomatic = str(data.get("diplomatic_text", ""))
        figure_numbers = tuple(int(x) for x in CAPTION_RE.findall(diplomatic))

        rec = source_by_id.get(page_id, {})
        image_path = pages_root / f"{page_id}.jpg"
        pages.append(Page(
            id=page_id,
            physical_index=physical_index,
            text_path=text_path,
            image_path=image_path,
            source_sha256=rec.get("sha256") or data.get("source_sha256"),
            figure_numbers=figure_numbers,
        ))
    return sorted(pages, key=lambda p: p.physical_index)


def choose_pilot(pages: list[Page], positive_count: int, negative_count: int) -> list[Page]:
    positives = [p for p in pages if p.is_positive]
    negatives = [p for p in pages if not p.is_positive]

    selected_pos = evenly_spaced(positives, positive_count)
    selected_neg = evenly_spaced(negatives, negative_count)
    selected = {p.id: p for p in selected_pos + selected_neg}
    return sorted(selected.values(), key=lambda p: p.physical_index)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text-root", type=Path, default=Path("corpus/text/pages"))
    ap.add_argument("--pages-root", type=Path, default=Path("work/book-v1/pages"))
    ap.add_argument("--source-manifest", type=Path, default=Path("corpus/source/page-manifest.json"))
    ap.add_argument("--positive-count", type=int, default=24)
    ap.add_argument("--negative-count", type=int, default=8)
    ap.add_argument("--out", type=Path, default=Path("work/figure-pilot/figure-pilot-v0.tar.gz"))
    args = ap.parse_args()

    pages = load_pages(args.text_root, args.pages_root, args.source_manifest)
    if not pages:
        raise SystemExit(f"no verified canonical pages found under {args.text_root}")

    selected = choose_pilot(pages, args.positive_count, args.negative_count)
    if not selected:
        raise SystemExit("pilot selection is empty")

    missing = [str(p.image_path) for p in selected if not p.image_path.exists()]
    if missing:
        preview = "\n".join(f"  - {x}" for x in missing[:12])
        suffix = "\n  ..." if len(missing) > 12 else ""
        raise SystemExit(
            "source JPEGs are missing; run tools/extract_book_pages.py first.\n"
            f"Missing selected pages:\n{preview}{suffix}"
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="corpus-motuum-figure-pilot-") as td:
        root = Path(td) / "figure-pilot-v0"
        images_dir = root / "images"
        text_dir = root / "canonical"
        images_dir.mkdir(parents=True)
        text_dir.mkdir(parents=True)

        records = []
        for p in selected:
            image_sha = sha256(p.image_path)
            if p.source_sha256 and image_sha != p.source_sha256:
                raise SystemExit(
                    f"{p.id}: source JPEG SHA-256 mismatch\n"
                    f"manifest/canonical: {p.source_sha256}\n"
                    f"actual:             {image_sha}"
                )

            image_dst = images_dir / p.image_path.name
            text_dst = text_dir / p.text_path.name
            image_dst.write_bytes(p.image_path.read_bytes())
            text_dst.write_bytes(p.text_path.read_bytes())
            records.append({
                "id": p.id,
                "physical_index": p.physical_index,
                "class": "positive" if p.is_positive else "negative-control",
                "figure_numbers_from_canonical_caption": list(p.figure_numbers),
                "image": str(image_dst.relative_to(root)),
                "canonical": str(text_dst.relative_to(root)),
                "source_sha256": image_sha,
            })

        manifest = {
            "schema": "corpus-motuum-figure-pilot-v0",
            "purpose": "calibration and validation of figure segmentation; captions are selection/QA metadata only",
            "selection": {
                "positive_strategy": "evenly spaced verified pages with explicit /^N ФИГ.$/ diplomatic captions",
                "negative_strategy": "evenly spaced verified pages without such captions",
                "requested_positive_count": args.positive_count,
                "requested_negative_count": args.negative_count,
            },
            "records": records,
        }
        (root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (root / "README.md").write_text(
            "# Figure extraction pilot v0\n\n"
            "This pack is for calibrating the image-segmentation model. Canonical text is included only "
            "to identify figure-caption pages and for QA; it must not drive the segmentation mask.\n",
            encoding="utf-8",
        )

        with tarfile.open(args.out, "w:gz") as tf:
            tf.add(root, arcname=root.name)

    positives = sum(p.is_positive for p in selected)
    negatives = len(selected) - positives
    print(f"Selected pages: {len(selected)} ({positives} positive, {negatives} negative controls)")
    print(f"Archive: {args.out}")


if __name__ == "__main__":
    main()
