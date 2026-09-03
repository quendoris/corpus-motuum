#!/usr/bin/env python3
"""Run Kraken across the full book in multi-page CLI batches.

Kraken 7.1 accepts repeated `-i input output` pairs in one invocation. Batching
keeps model startup cost from being paid once per page. The current production
candidate intentionally uses CPU segmentation/recognition because the tested
CUDA baseline segmenter mixed CPU and CUDA tensors on this environment.
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


def chunks(items: list[dict], n: int):
    for i in range(0, len(items), n):
        yield i // n + 1, items[i:i + n]


def kraken_version() -> str | None:
    p = subprocess.run(["kraken", "--version"], capture_output=True, text=True)
    text = (p.stdout or p.stderr).strip()
    return text.splitlines()[0] if text else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", type=Path, default=Path("work/book-v1/inputs/manifest.json"))
    ap.add_argument("--preset", default="raw-gray")
    ap.add_argument("--model", required=True)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--precision", default="32-true")
    ap.add_argument("--batch-pages", type=int, default=24)
    ap.add_argument("--line-batch-size", type=int, default=64)
    ap.add_argument("--line-workers", type=int, default=4)
    ap.add_argument("--out", type=Path, default=Path("corpus/ocr/raw/kraken"))
    ap.add_argument("--diagnostics", type=Path, default=Path("work/book-v1/logs/kraken"))
    ap.add_argument("--no-resume", action="store_true")
    args = ap.parse_args()

    manifest = json.loads(args.inputs.read_text(encoding="utf-8"))
    records = [r for r in manifest["records"] if r["preset"] == args.preset]
    if not records:
        raise SystemExit(f"no inputs for preset {args.preset}")

    args.out.mkdir(parents=True, exist_ok=True)
    args.diagnostics.mkdir(parents=True, exist_ok=True)

    pending = []
    resumed = 0
    for rec in records:
        dest = args.out / f"{rec['sample_id']}.txt"
        if dest.exists() and not args.no_resume:
            resumed += 1
        else:
            pending.append(rec)

    failures: list[dict] = []
    written = 0
    ocr_seconds = 0.0
    started = time.time()
    total_batches = (len(pending) + args.batch_pages - 1) // args.batch_pages if pending else 0

    for batch_no, batch in chunks(pending, args.batch_pages):
        cmd = ["kraken", "-n"]
        expected: list[tuple[str, Path]] = []
        for rec in batch:
            dest = args.out / f"{rec['sample_id']}.txt"
            expected.append((rec["sample_id"], dest))
            cmd.extend(["-i", rec["path"], str(dest)])
        cmd.extend([
            "--device", args.device,
            "--precision", args.precision,
            "segment", "-bl",
            "ocr", "-m", args.model,
            "-B", str(args.line_batch_size),
            "--num-line-workers", str(args.line_workers),
        ])

        start = time.perf_counter()
        p = subprocess.run(cmd, capture_output=True, text=True)
        elapsed = time.perf_counter() - start
        ocr_seconds += elapsed

        log_base = args.diagnostics / f"batch-{batch_no:04d}"
        log_base.with_suffix(".stdout.txt").write_text(p.stdout, encoding="utf-8")
        log_base.with_suffix(".stderr.txt").write_text(p.stderr, encoding="utf-8")

        combined = f"{p.stdout}\n{p.stderr}"
        internal_error = "Failed processing" in combined or " ERROR " in combined
        missing = [sample_id for sample_id, dest in expected if not dest.exists()]
        if p.returncode != 0 or internal_error or missing:
            failures.append({
                "batch": batch_no,
                "returncode": p.returncode,
                "internal_error_marker": internal_error,
                "missing_outputs": missing,
                "stdout_tail": p.stdout[-3000:],
                "stderr_tail": p.stderr[-3000:],
            })
        written += sum(dest.exists() for _, dest in expected)
        print(
            f"Kraken batch {batch_no}/{total_batches}: pages={len(batch)}, "
            f"elapsed={elapsed:.1f}s, outputs={sum(dest.exists() for _, dest in expected)}, "
            f"failed_batches={len(failures)}"
        )

    run = {
        "schema": "corpus-motuum-ocr-run-v1",
        "engine": "kraken",
        "engine_version": kraken_version(),
        "model": args.model,
        "preset": args.preset,
        "device": args.device,
        "precision": args.precision,
        "batch_pages": args.batch_pages,
        "line_batch_size": args.line_batch_size,
        "line_workers": args.line_workers,
        "input_manifest": str(args.inputs),
        "input_manifest_sha256": sha256(args.inputs),
        "pages_expected": len(records),
        "pages_written_this_run": written,
        "pages_resumed": resumed,
        "failures": failures,
        "ocr_seconds_this_run": ocr_seconds,
        "wall_seconds": time.time() - started,
        "system": {
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
    }
    (args.out / "run.json").write_text(
        json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    available = sum(
        (args.out / f"{rec['sample_id']}.txt").exists()
        for rec in records
    )
    print(f"Output: {args.out}")
    print(f"Available text pages: {available}/{len(records)}")
    if failures:
        raise SystemExit(
            f"Kraken completed with {len(failures)} failed batches. "
            "Successful page files are kept; rerun is resumable."
        )


if __name__ == "__main__":
    main()
