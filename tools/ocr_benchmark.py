#!/usr/bin/env python3
"""Run and score OCR benchmark combinations."""
from __future__ import annotations

import argparse
import csv
import json
import platform
import re
import subprocess
import time
from collections import defaultdict
from pathlib import Path

from rapidfuzz.distance import Levenshtein

HISTORICAL_GLYPHS = ["ѣ", "Ѣ", "і", "І", "ѳ", "Ѳ", "ѵ", "Ѵ", "ъ", "Ъ"]


def strict_normalize(s: str) -> str:
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in s.split("\n")).strip()


def content_normalize(s: str) -> str:
    return re.sub(r"\s+", " ", strict_normalize(s)).strip()


def score_text(gt_raw: str, pred_raw: str) -> dict:
    gt_s = strict_normalize(gt_raw)
    pr_s = strict_normalize(pred_raw)
    gt_c = content_normalize(gt_raw)
    pr_c = content_normalize(pred_raw)

    strict_ed = Levenshtein.distance(gt_s, pr_s)
    content_ed = Levenshtein.distance(gt_c, pr_c)
    gt_words = gt_c.split()
    pr_words = pr_c.split()
    word_ed = Levenshtein.distance(gt_words, pr_words)

    editops = list(Levenshtein.editops(gt_c, pr_c))
    substitutions = sum(op.tag == "replace" for op in editops)
    deletions = sum(op.tag == "delete" for op in editops)
    insertions = sum(op.tag == "insert" for op in editops)
    merged = sum(op.tag == "delete" and gt_c[op.src_pos] == " " for op in editops)
    split = sum(op.tag == "insert" and pr_c[op.dest_pos] == " " for op in editops)

    bad_gt_positions = {op.src_pos for op in editops if op.tag in {"replace", "delete"}}
    glyph_stats = {}
    for glyph in HISTORICAL_GLYPHS:
        positions = [i for i, ch in enumerate(gt_c) if ch == glyph]
        correct = sum(i not in bad_gt_positions for i in positions)
        pred_n = pr_c.count(glyph)
        glyph_stats[glyph] = {
            "gt": len(positions),
            "pred": pred_n,
            "correct": correct,
            "recall": (correct / len(positions)) if positions else None,
            "false_positives": max(0, pred_n - correct),
        }

    return {
        "gt_chars_strict": len(gt_s),
        "gt_chars_content": len(gt_c),
        "gt_words": len(gt_words),
        "strict_edit_distance": strict_ed,
        "content_edit_distance": content_ed,
        "word_edit_distance": word_ed,
        "cer_strict": strict_ed / max(1, len(gt_s)),
        "cer_content": content_ed / max(1, len(gt_c)),
        "wer_content": word_ed / max(1, len(gt_words)),
        "substitutions": substitutions,
        "deletions": deletions,
        "insertions": insertions,
        "merged_word_boundaries": merged,
        "split_word_boundaries": split,
        "historical_glyphs": glyph_stats,
    }


def system_info() -> dict:
    info = {"platform": platform.platform(), "python": platform.python_version(), "cpu": platform.processor() or platform.machine()}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                info["ram_gib"] = round(int(line.split()[1]) / 1024 / 1024, 2)
                break
    except Exception:
        pass
    try:
        p = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"], capture_output=True, text=True, timeout=5)
        if p.returncode == 0:
            info["nvidia_gpu"] = [x.strip() for x in p.stdout.splitlines() if x.strip()]
    except Exception:
        pass
    try:
        p = subprocess.run(["tesseract", "--version"], capture_output=True, text=True, timeout=5)
        if p.returncode == 0:
            info["tesseract"] = p.stdout.splitlines()[0]
    except Exception:
        pass
    return info


def find_gt(gt_root: Path, sample_id: str) -> tuple[Path | None, dict]:
    d = gt_root / sample_id
    text = d / "gt_ocr.txt"
    meta = d / "metadata.json"
    md = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
    if not text.exists() or md.get("status") in {"pending", "excluded-uncertain"}:
        return None, md
    return text, md


def run_tesseract(image: Path, lang: str, psm: int, tessdata_dir: Path | None) -> tuple[int, str, str, float]:
    cmd = ["tesseract", str(image), "stdout", "-l", lang, "--psm", str(psm)]
    if tessdata_dir is not None:
        cmd.extend(["--tessdata-dir", str(tessdata_dir)])
    start = time.perf_counter()
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr, time.perf_counter() - start


def aggregate(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("status") == "scored":
            groups[(row["engine"], row["model"], row["psm"], row["preset"])].append(row)
    out = []
    for key, rs in sorted(groups.items()):
        chars_c = sum(r["score"]["gt_chars_content"] for r in rs)
        chars_s = sum(r["score"]["gt_chars_strict"] for r in rs)
        words = sum(r["score"]["gt_words"] for r in rs)
        seconds = sum(r["seconds"] for r in rs)
        out.append({
            "engine": key[0], "model": key[1], "psm": key[2], "preset": key[3],
            "pages_scored": len(rs),
            "seconds_total": seconds,
            "seconds_per_page": seconds / len(rs),
            "cer_content": sum(r["score"]["content_edit_distance"] for r in rs) / max(1, chars_c),
            "wer_content": sum(r["score"]["word_edit_distance"] for r in rs) / max(1, words),
            "cer_strict": sum(r["score"]["strict_edit_distance"] for r in rs) / max(1, chars_s),
            "merged_word_boundaries": sum(r["score"]["merged_word_boundaries"] for r in rs),
            "split_word_boundaries": sum(r["score"]["split_word_boundaries"] for r in rs),
            "estimated_600_pages_minutes": (seconds / len(rs)) * 600 / 60,
        })
    return out


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", type=Path, default=Path("work/benchmark-v1/inputs/manifest.json"))
    ap.add_argument("--gt", type=Path, default=Path("benchmark/v1/ground_truth"))
    ap.add_argument("--out", type=Path, default=Path("work/benchmark-v1/runs"))
    ap.add_argument("--langs", nargs="+", default=["rus"])
    ap.add_argument("--psm", nargs="+", type=int, default=[3, 6])
    ap.add_argument("--only-presets", nargs="*", default=None)
    ap.add_argument("--tessdata-dir", type=Path, default=None)
    args = ap.parse_args()

    inputs = json.loads(args.inputs.read_text(encoding="utf-8"))
    records = inputs["records"]
    if args.only_presets:
        allowed = set(args.only_presets); records = [r for r in records if r["preset"] in allowed]
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for lang in args.langs:
        for psm in args.psm:
            model_failed = False
            for rec in records:
                if model_failed: continue
                sample_id = rec["sample_id"]; image = Path(rec["path"])
                gt_path, gt_meta = find_gt(args.gt, sample_id)
                dest = args.out / "tesseract" / lang / f"psm-{psm}" / rec["preset"]
                dest.mkdir(parents=True, exist_ok=True)
                pred_path = dest / f"{sample_id}.txt"; err_path = dest / f"{sample_id}.stderr.txt"
                code, stdout, stderr, elapsed = run_tesseract(image, lang, psm, args.tessdata_dir)
                pred_path.write_text(stdout, encoding="utf-8"); err_path.write_text(stderr, encoding="utf-8")
                row = {"engine":"tesseract","model":lang,"psm":psm,"preset":rec["preset"],"sample_id":sample_id,"seconds":elapsed,"prediction":str(pred_path),"gt_status":gt_meta.get("status")}
                if code != 0:
                    row.update(status="engine-error", returncode=code, stderr=stderr[-1000:])
                    if "Failed loading language" in stderr or "Error opening data file" in stderr: model_failed = True
                elif gt_path is None:
                    row["status"] = "unscored-no-gt"
                else:
                    row["status"] = "scored"; row["score"] = score_text(gt_path.read_text(encoding="utf-8"), stdout)
                rows.append(row)

    summary = aggregate(rows)
    (args.out / "results.json").write_text(json.dumps({"system":system_info(),"inputs":str(args.inputs),"ground_truth":str(args.gt),"rows":rows,"summary":summary}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary_csv = [{**r,"cer_content":f"{r['cer_content']:.6f}","wer_content":f"{r['wer_content']:.6f}","cer_strict":f"{r['cer_strict']:.6f}","seconds_total":f"{r['seconds_total']:.3f}","seconds_per_page":f"{r['seconds_per_page']:.3f}","estimated_600_pages_minutes":f"{r['estimated_600_pages_minutes']:.2f}"} for r in summary]
    write_csv(args.out / "summary.csv", summary_csv)
    print(f"Completed {len(rows)} OCR runs.")
    print(f"Scored ground-truth pages: {len({r['sample_id'] for r in rows if r.get('status') == 'scored'})}")
    if summary:
        best = min(summary, key=lambda x: x["cer_content"])
        print(f"Best content CER: {best['cer_content']:.4%} — {best['model']} psm={best['psm']} {best['preset']}")
        print(f"Summary: {args.out / 'summary.csv'}")


if __name__ == "__main__":
    main()
