#!/usr/bin/env python3
"""Run one Tesseract model across the full-book OCR input manifest.

Outputs are page-aligned UTF-8 text files intended to be committed to Git.
Diagnostics remain under work/ and are ignored by Git.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import time
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def version() -> str | None:
    p = subprocess.run(["tesseract", "--version"], capture_output=True, text=True)
    return p.stdout.splitlines()[0] if p.returncode == 0 and p.stdout else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", type=Path, default=Path("work/book-v1/inputs/manifest.json"))
    ap.add_argument("--model", required=True)
    ap.add_argument("--preset", default="local-gray-150")
    ap.add_argument("--psm", type=int, default=3)
    ap.add_argument("--tessdata-dir", type=Path, default=None)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--diagnostics", type=Path, default=None)
    ap.add_argument("--no-resume", action="store_true")
    args = ap.parse_args()

    manifest = json.loads(args.inputs.read_text(encoding="utf-8"))
    records = [r for r in manifest["records"] if r["preset"] == args.preset]
    if not records:
        raise SystemExit(f"no inputs for preset {args.preset}")

    args.out.mkdir(parents=True, exist_ok=True)
    diagnostics = args.diagnostics or Path(f"work/book-v1/logs/tesseract-{args.model}")
    diagnostics.mkdir(parents=True, exist_ok=True)

    failures: list[dict] = []
    processed = skipped = 0
    total_seconds = 0.0
    started = time.time()

    for idx, rec in enumerate(records, start=1):
        dest = args.out / f"{rec['sample_id']}.txt"
        if dest.exists() and not args.no_resume:
            skipped += 1
            continue

        cmd = [
            "tesseract", rec["path"], "stdout", "-l", args.model,
            "--psm", str(args.psm),
        ]
        if args.tessdata_dir is not None:
            cmd.extend(["--tessdata-dir", str(args.tessdata_dir)])

        start = time.perf_counter()
        p = subprocess.run(cmd, capture_output=True, text=True)
        elapsed = time.perf_counter() - start
        total_seconds += elapsed

        (diagnostics / f"{rec['sample_id']}.stderr.txt").write_text(p.stderr, encoding="utf-8")
        if p.returncode != 0:
            failures.append({
                "id": rec["sample_id"],
                "returncode": p.returncode,
                "stderr": p.stderr[-2000:],
            })
        else:
            dest.write_text(p.stdout, encoding="utf-8")
            processed += 1

        if idx % 25 == 0 or idx == len(records):
            print(
                f"{args.model}: {idx}/{len(records)} visited; "
                f"written={processed}, resumed={skipped}, failures={len(failures)}"
            )

    run = {
        "schema": "corpus-motuum-ocr-run-v1",
        "engine": "tesseract",
        "engine_version": version(),
        "model": args.model,
        "preset": args.preset,
        "psm": args.psm,
        "tessdata_dir": str(args.tessdata_dir) if args.tessdata_dir else None,
        "input_manifest": str(args.inputs),
        "input_manifest_sha256": sha256(args.inputs),
        "pages_expected": len(records),
        "pages_written_this_run": processed,
        "pages_resumed": skipped,
        "failures": failures,
        "ocr_seconds_this_run": total_seconds,
        "wall_seconds": time.time() - started,
        "system": {
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
    }
    (args.out / "run.json").write_text(
        json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Output: {args.out}")
    if failures:
        raise SystemExit(f"OCR completed with {len(failures)} failures; rerun is resumable")


if __name__ == "__main__":
    main()
