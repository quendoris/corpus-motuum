#!/usr/bin/env python3
"""Build a conservative human-review image set from the full-book figure probe.

The full probe output is immutable evidence. This tool never modifies it. It
materializes only crops that survive high-confidence editorial/geometry cleanup
into separate review folders and writes a provenance manifest for every keep or
remove decision.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

DEFAULT_POLICY = Path("corpus/figures/curation-v1.json")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def asset_key(page_id: str, asset_index: int) -> str:
    return f"{page_id}#asset-{asset_index:02d}"


def is_numbered_figure_page(record: dict[str, Any]) -> bool:
    if record.get("caption_figure_numbers"):
        return True
    notes = " ".join(str(x) for x in record.get("notes", []))
    # Covers caption variants the original strict audit regex missed, including
    # `49. ФИГ.`, `ФИГ. 52`, `(89 ФИГ.)`, and `(126 ФИГ.)`.
    return bool(re.search(r"(?iu)(?:подпис\w*.{0,120}фиг|фиг.{0,120}подпис\w*)", notes))


def figure_hint(record: dict[str, Any], policy: dict[str, Any]) -> str:
    override = policy.get("figure_label_overrides", {}).get(record["id"])
    if override:
        labels = [str(x) for x in override]
    else:
        labels = [str(x) for x in record.get("caption_figure_numbers", [])]
    if not labels:
        return "fig-unknown"
    return "figs-" + "-".join(labels)


def materialize(src: Path, dst: Path, mode: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    if mode == "hardlink":
        try:
            os.link(src, dst)
            return
        except OSError:
            shutil.copy2(src, dst)
            return
    if mode == "symlink":
        dst.symlink_to(src.resolve())
        return
    shutil.copy2(src, dst)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--audit",
        type=Path,
        default=Path("work/figure-structure-full-v2/census-audit/figure-census-audit.json"),
    )
    ap.add_argument("--source-root", type=Path, default=Path("work/figure-structure-full-v2"))
    ap.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    ap.add_argument("--out", type=Path, default=Path("work/figure-review-v1"))
    ap.add_argument("--mode", choices=("hardlink", "copy", "symlink"), default="hardlink")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    audit = load_json(args.audit)
    policy = load_json(args.policy)
    true_atlas = set(policy.get("atlas_plate_pages", []))
    paratext = {
        asset_key(str(x["id"]), int(x["asset_index"])): x
        for x in policy.get("paratext_keep", [])
    }
    explicit_remove = {
        asset_key(str(x["id"]), int(x["asset_index"])): x
        for x in policy.get("explicit_remove", [])
    }

    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    category_counts: Counter[str] = Counter()

    for record in audit.get("records", []):
        page_id = str(record["id"])
        physical = int(record["physical_index"])
        numbered = is_numbered_figure_page(record)

        for asset in record.get("assets", []):
            idx = int(asset["asset_index"])
            key = asset_key(page_id, idx)
            base = {
                "key": key,
                "id": page_id,
                "physical_index": physical,
                "asset_index": idx,
                "bbox": asset.get("bbox"),
                "page_type": record.get("page_type"),
                "caption_figure_numbers": record.get("caption_figure_numbers", []),
                "mentioned_figure_numbers": record.get("mentioned_figure_numbers", []),
            }

            if key in explicit_remove:
                removed.append({**base, "reason": explicit_remove[key]["reason"]})
                continue

            if key in paratext:
                category = "paratext"
                label = str(paratext[key].get("label", "paratext"))
                name_prefix = label
            elif page_id in true_atlas:
                category = "atlas"
                name_prefix = "atlas"
            elif numbered:
                category = "numbered"
                name_prefix = figure_hint(record, policy)
            else:
                removed.append({
                    **base,
                    "reason": "no numbered-figure/atlas/paratext evidence after editorial+overlay review",
                })
                continue

            src = args.source_root / f"{page_id}-asset-{idx:02d}.clean.png"
            dst_name = f"{name_prefix}__p{physical:04d}__{page_id}__a{idx:02d}.png"
            dst = args.out / category / dst_name
            if not args.dry_run:
                if not src.is_file():
                    raise SystemExit(f"missing source crop: {src}")
                materialize(src, dst, args.mode)
            kept.append({
                **base,
                "category": category,
                "source": str(src),
                "review_file": str(dst),
            })
            category_counts[category] += 1

    summary = {
        "input_assets": len(kept) + len(removed),
        "kept_assets": len(kept),
        "removed_assets": len(removed),
        "categories": dict(sorted(category_counts.items())),
        "known_issues": policy.get("known_issues", []),
        "needs_reextract": policy.get("needs_reextract", []),
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.dry_run:
        return

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "manifest.json").write_text(
        json.dumps({
            "schema": "corpus-motuum-figure-review-set-v1",
            "source_audit": str(args.audit),
            "source_root": str(args.source_root),
            "policy": str(args.policy),
            "summary": summary,
            "kept": kept,
            "removed": removed,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.out / "removed.json").write_text(
        json.dumps({"schema": "corpus-motuum-figure-removals-v1", "records": removed}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
