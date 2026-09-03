#!/usr/bin/env python3
"""Package a contiguous editorial batch: source images + all OCR candidates.

The archive is intended for human/model editorial review outside the local
working tree. It does not modify or commit generated images.
"""
from __future__ import annotations

import argparse
import json
import shutil
import tarfile
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, default=Path("corpus/source/page-manifest.json"))
    ap.add_argument("--ocr-root", type=Path, default=Path("corpus/ocr/raw"))
    ap.add_argument("--start", type=int, required=True, help="1-based physical page index")
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--engines", nargs="+", default=["rus", "orus", "kraken"])
    ap.add_argument("--out-dir", type=Path, default=Path("work/editorial-batches"))
    args = ap.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    records = manifest["records"]
    if args.start < 1 or args.start > len(records):
        raise SystemExit(f"--start must be between 1 and {len(records)}")
    selected = records[args.start - 1:args.start - 1 + args.count]
    if not selected:
        raise SystemExit("empty selection")

    first = selected[0]["physical_index"]
    last = selected[-1]["physical_index"]
    name = f"editorial-{first:04d}-{last:04d}"
    root = args.out_dir / name
    if root.exists():
        shutil.rmtree(root)
    (root / "pages").mkdir(parents=True)
    for engine in args.engines:
        (root / "ocr" / engine).mkdir(parents=True)

    batch_records = []
    for rec in selected:
        source = Path(rec["path"])
        if not source.exists():
            raise SystemExit(f"missing source page: {source}")
        shutil.copy2(source, root / "pages" / f"{rec['id']}{source.suffix.lower()}")

        ocr_files = {}
        for engine in args.engines:
            src = args.ocr_root / engine / f"{rec['id']}.txt"
            if not src.exists():
                raise SystemExit(f"missing {engine} OCR for {rec['id']}: {src}")
            dst = root / "ocr" / engine / src.name
            shutil.copy2(src, dst)
            ocr_files[engine] = str(dst.relative_to(root))

        batch_records.append({
            **{k: rec.get(k) for k in (
                "id", "physical_index", "source_pdf_sheet", "spread_side",
                "printed_page", "sha256", "extraction_method", "held_out_benchmark_v1"
            )},
            "page_file": f"pages/{rec['id']}{source.suffix.lower()}",
            "ocr": ocr_files,
        })

    batch_manifest = {
        "schema": "corpus-motuum-editorial-batch-v1",
        "source_manifest": str(args.manifest),
        "range": {"start": first, "end": last, "count": len(batch_records)},
        "engines": args.engines,
        "records": batch_records,
    }
    (root / "manifest.json").write_text(
        json.dumps(batch_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (root / "README.txt").write_text(
        "Corpus Motuum editorial batch\n\n"
        "Each page has the source image and page-aligned raw OCR candidates.\n"
        "Raw OCR is evidence only; it is not canonical text.\n",
        encoding="utf-8",
    )

    archive = args.out_dir / f"{name}.tar.gz"
    args.out_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(root, arcname=name)

    print(f"Pages: {first}-{last} ({len(batch_records)})")
    print(f"Directory: {root}")
    print(f"Archive: {archive}")


if __name__ == "__main__":
    main()
