#!/usr/bin/env python3
"""Run Kraken across the full book in multi-page CLI batches.

Kraken 7.1 accepts repeated `-i input output` pairs in one invocation. Batching
keeps model startup cost from being paid once per page. The current production
candidate intentionally uses CPU segmentation/recognition because the tested
CUDA baseline segmenter mixed CPU and CUDA tensors on this environment.

If a multi-page Kraken process crashes or silently misses outputs, the runner
retries only the missing pages one-by-one in fresh processes. This keeps a
single pathological page or transient segfault from discarding the rest of a
batch.
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


def build_cmd(records: list[dict], out: Path, model: str, device: str,
              precision: str, line_batch_size: int, line_workers: int) -> tuple[list[str], list[tuple[str, Path]]]:
    cmd = ["kraken", "-n"]
    expected: list[tuple[str, Path]] = []
    for rec in records:
        dest = out / f"{rec['sample_id']}.txt"
        expected.append((rec["sample_id"], dest))
        cmd.extend(["-i", rec["path"], str(dest)])
    cmd.extend([
        "--device", device,
        "--precision", precision,
        "segment", "-bl",
        "ocr", "-m", model,
        "-B", str(line_batch_size),
        "--num-line-workers", str(line_workers),
    ])
    return cmd, expected


def run_process(cmd: list[str]) -> tuple[subprocess.CompletedProcess[str], float]:
    start = time.perf_counter()
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p, time.perf_counter() - start


def has_internal_error(p: subprocess.CompletedProcess[str]) -> bool:
    combined = f"{p.stdout}\n{p.stderr}"
    return "Failed processing" in combined or " ERROR " in combined


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
    ap.add_argument(
        "--no-single-retry",
        action="store_true",
        help="do not retry missing outputs one page at a time after a failed batch",
    )
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

    final_failures: list[dict] = []
    batch_incidents: list[dict] = []
    written_before = sum((args.out / f"{r['sample_id']}.txt").exists() for r in records)
    ocr_seconds = 0.0
    started = time.time()
    total_batches = (len(pending) + args.batch_pages - 1) // args.batch_pages if pending else 0

    for batch_no, batch in chunks(pending, args.batch_pages):
        cmd, expected = build_cmd(
            batch, args.out, args.model, args.device, args.precision,
            args.line_batch_size, args.line_workers,
        )
        p, elapsed = run_process(cmd)
        ocr_seconds += elapsed

        log_base = args.diagnostics / f"batch-{batch_no:04d}"
        log_base.with_suffix(".stdout.txt").write_text(p.stdout, encoding="utf-8")
        log_base.with_suffix(".stderr.txt").write_text(p.stderr, encoding="utf-8")

        internal_error = has_internal_error(p)
        missing_ids = [sample_id for sample_id, dest in expected if not dest.exists()]
        batch_bad = p.returncode != 0 or internal_error or missing_ids
        if batch_bad:
            batch_incidents.append({
                "batch": batch_no,
                "returncode": p.returncode,
                "internal_error_marker": internal_error,
                "missing_outputs_before_retry": missing_ids,
                "stdout_tail": p.stdout[-3000:],
                "stderr_tail": p.stderr[-3000:],
            })

        recovered = 0
        if missing_ids and not args.no_single_retry:
            by_id = {rec["sample_id"]: rec for rec in batch}
            for sample_id in list(missing_ids):
                rec = by_id[sample_id]
                single_cmd, single_expected = build_cmd(
                    [rec], args.out, args.model, args.device, args.precision,
                    args.line_batch_size, args.line_workers,
                )
                sp, selapsed = run_process(single_cmd)
                ocr_seconds += selapsed
                slog = args.diagnostics / f"retry-{sample_id}"
                slog.with_suffix(".stdout.txt").write_text(sp.stdout, encoding="utf-8")
                slog.with_suffix(".stderr.txt").write_text(sp.stderr, encoding="utf-8")
                dest = single_expected[0][1]
                if dest.exists() and sp.returncode == 0 and not has_internal_error(sp):
                    recovered += 1
                else:
                    final_failures.append({
                        "sample_id": sample_id,
                        "returncode": sp.returncode,
                        "internal_error_marker": has_internal_error(sp),
                        "output_exists": dest.exists(),
                        "stdout_tail": sp.stdout[-3000:],
                        "stderr_tail": sp.stderr[-3000:],
                    })

        remaining = sum(not dest.exists() for _, dest in expected)
        print(
            f"Kraken batch {batch_no}/{total_batches}: pages={len(batch)}, "
            f"elapsed={elapsed:.1f}s, initial_missing={len(missing_ids)}, "
            f"single_recovered={recovered}, remaining_missing={remaining}"
        )

    available = sum(
        (args.out / f"{rec['sample_id']}.txt").exists()
        for rec in records
    )
    missing_final = [
        rec["sample_id"] for rec in records
        if not (args.out / f"{rec['sample_id']}.txt").exists()
    ]
    written_this_run = max(0, available - written_before)

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
        "single_retry_enabled": not args.no_single_retry,
        "input_manifest": str(args.inputs),
        "input_manifest_sha256": sha256(args.inputs),
        "pages_expected": len(records),
        "pages_written_this_run": written_this_run,
        "pages_resumed": resumed,
        "pages_available_after_run": available,
        "missing_after_run": missing_final,
        "batch_incidents": batch_incidents,
        "failures": final_failures,
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

    print(f"Output: {args.out}")
    print(f"Available text pages: {available}/{len(records)}")
    if missing_final:
        raise SystemExit(
            f"Kraken still misses {len(missing_final)} pages after single-page retries: "
            + ", ".join(missing_final[:20])
        )


if __name__ == "__main__":
    main()
