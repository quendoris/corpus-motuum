#!/usr/bin/env python3
"""Run Kraken OCR over Corpus Motuum benchmark inputs and score against GT."""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

# Reuse the exact same scorer and system reporting as the Tesseract benchmark.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ocr_benchmark import find_gt, score_text, system_info  # noqa: E402

GLYPH_FAMILIES = {
    "yat": ("ѣ", "Ѣ"),
    "decimal_i": ("і", "І"),
    "fita": ("ѳ", "Ѳ"),
    "izhitsa": ("ѵ", "Ѵ"),
    "hard_sign": ("ъ", "Ъ"),
}


def run_kraken(
    image: Path,
    output: Path,
    model: str,
    device: str,
    precision: str,
    batch_size: int,
    workers: int,
) -> tuple[int, str, str, float]:
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "kraken",
        "-i", str(image), str(output),
        "--device", device,
        "--precision", precision,
        "segment", "-bl",
        "ocr", "-m", model,
        "-B", str(batch_size),
        "--num-line-workers", str(workers),
    ]
    start = time.perf_counter()
    p = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.perf_counter() - start
    return p.returncode, p.stdout, p.stderr, elapsed


def aggregate(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("status") == "scored":
            groups[(row["model"], row["device"], row["precision"], row["preset"])].append(row)

    out: list[dict] = []
    for key, rs in sorted(groups.items()):
        chars_c = sum(r["score"]["gt_chars_content"] for r in rs)
        chars_s = sum(r["score"]["gt_chars_strict"] for r in rs)
        words = sum(r["score"]["gt_words"] for r in rs)
        seconds = sum(r["seconds"] for r in rs)
        rec = {
            "engine": "kraken",
            "model": key[0],
            "device": key[1],
            "precision": key[2],
            "preset": key[3],
            "pages_scored": len(rs),
            "seconds_total": seconds,
            "seconds_per_page": seconds / len(rs),
            "cer_content": sum(r["score"]["content_edit_distance"] for r in rs) / max(1, chars_c),
            "wer_content": sum(r["score"]["word_edit_distance"] for r in rs) / max(1, words),
            "cer_strict": sum(r["score"]["strict_edit_distance"] for r in rs) / max(1, chars_s),
            "merged_word_boundaries": sum(r["score"]["merged_word_boundaries"] for r in rs),
            "split_word_boundaries": sum(r["score"]["split_word_boundaries"] for r in rs),
            # Startup/model-loading cost is included. Treat this as a naive smoke-test projection,
            # not a production throughput estimate; a later batched runner will amortize startup.
            "estimated_600_pages_minutes_naive": (seconds / len(rs)) * 600 / 60,
        }
        for family, glyphs in GLYPH_FAMILIES.items():
            gt_n = correct = fp = 0
            for row in rs:
                for glyph in glyphs:
                    stats = row["score"]["historical_glyphs"].get(glyph, {})
                    gt_n += stats.get("gt", 0)
                    correct += stats.get("correct", 0)
                    fp += stats.get("false_positives", 0)
            rec[f"{family}_gt"] = gt_n
            rec[f"{family}_recall"] = (correct / gt_n) if gt_n else None
            rec[f"{family}_false_positives"] = fp
        out.append(rec)
    return out


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", type=Path, default=Path("work/benchmark-v1/inputs/manifest.json"))
    ap.add_argument("--gt", type=Path, default=Path("benchmark/v1/ground_truth"))
    ap.add_argument("--out", type=Path, default=Path("work/benchmark-v1/runs-kraken"))
    ap.add_argument("--model", required=True, help="Kraken model filename or absolute path")
    ap.add_argument("--device", default="cpu", help="cpu or cuda:0")
    ap.add_argument("--precision", default="32", help="32, 16-mixed, bf16-mixed, ...")
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--only-presets", nargs="*", default=None)
    ap.add_argument("--only-samples", nargs="*", default=None)
    args = ap.parse_args()

    inputs = json.loads(args.inputs.read_text(encoding="utf-8"))
    records = inputs["records"]
    if args.only_presets:
        allowed = set(args.only_presets)
        records = [r for r in records if r["preset"] in allowed]
    if args.only_samples:
        allowed = set(args.only_samples)
        records = [r for r in records if r["sample_id"] in allowed]

    args.out.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []

    for rec in records:
        sample_id = rec["sample_id"]
        image = Path(rec["path"])
        gt_path, gt_meta = find_gt(args.gt, sample_id)
        pred_path = args.out / "predictions" / rec["preset"] / f"{sample_id}.txt"
        err_path = args.out / "stderr" / rec["preset"] / f"{sample_id}.txt"
        err_path.parent.mkdir(parents=True, exist_ok=True)

        code, stdout, stderr, elapsed = run_kraken(
            image=image,
            output=pred_path,
            model=args.model,
            device=args.device,
            precision=args.precision,
            batch_size=args.batch_size,
            workers=args.workers,
        )
        err_path.write_text(stderr, encoding="utf-8")
        row = {
            "engine": "kraken",
            "model": args.model,
            "device": args.device,
            "precision": args.precision,
            "preset": rec["preset"],
            "sample_id": sample_id,
            "seconds": elapsed,
            "prediction": str(pred_path),
            "gt_status": gt_meta.get("status"),
        }
        if code != 0:
            row.update(status="engine-error", returncode=code, stdout=stdout[-1000:], stderr=stderr[-2000:])
        elif not pred_path.exists():
            row.update(status="engine-error", returncode=code, stderr="Kraken exited successfully but no output text file was produced")
        elif gt_path is None:
            row["status"] = "unscored-no-gt"
        else:
            # Kraken 7.1's multilingual base model is trained/evaluated in NFD. Compare
            # canonical Unicode equivalents in NFC so decomposed й/ё etc. are not counted
            # as OCR errors merely because another engine emits composed code points.
            prediction = unicodedata.normalize("NFC", pred_path.read_text(encoding="utf-8"))
            gt_text = unicodedata.normalize("NFC", gt_path.read_text(encoding="utf-8"))
            row["status"] = "scored"
            row["score"] = score_text(gt_text, prediction)
        rows.append(row)

    summary = aggregate(rows)
    payload = {
        "system": system_info(),
        "engine": "kraken",
        "model": args.model,
        "device": args.device,
        "precision": args.precision,
        "inputs": str(args.inputs),
        "ground_truth": str(args.gt),
        "rows": rows,
        "summary": summary,
    }
    (args.out / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    formatted = []
    for r in summary:
        x = dict(r)
        for key in ("seconds_total", "seconds_per_page", "estimated_600_pages_minutes_naive"):
            x[key] = f"{x[key]:.3f}"
        for key in ("cer_content", "wer_content", "cer_strict"):
            x[key] = f"{x[key]:.6f}"
        formatted.append(x)
    write_csv(args.out / "summary.csv", formatted)

    print(f"Completed {len(rows)} Kraken runs.")
    print(f"Scored GT pages: {len({r['sample_id'] for r in rows if r.get('status') == 'scored'})}")
    if summary:
        best = min(summary, key=lambda x: x["cer_content"])
        print(f"Best content CER: {best['cer_content']:.4%} — {best['preset']}")
        print(f"Summary: {args.out / 'summary.csv'}")
    else:
        print(f"No scored runs. Inspect: {args.out / 'results.json'}")


if __name__ == "__main__":
    main()
