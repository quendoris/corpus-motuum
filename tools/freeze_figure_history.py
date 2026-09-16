#!/usr/bin/env python3
"""Freeze reproducible figure-pipeline evidence and canonical release assets.

This tool deliberately keeps the research snapshot separate from the canonical
book payload. The history tree may contain failed/intermediate outputs; the
canonical tree is copied only from the already assembled final figure set.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Iterable


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def link_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise SystemExit(f"missing source directory: {source}")
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(source)
        link_or_copy(path, destination / rel)


def copy_file(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise SystemExit(f"missing source file: {source}")
    link_or_copy(source, destination)


def iter_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            yield path


def describe_tree(root: Path, logical_root: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in iter_files(root):
        rel = path.relative_to(root)
        rows.append(
            {
                "path": str(Path(logical_root) / rel),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return rows


def reset_destination(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--structure-root", type=Path, required=True)
    parser.add_argument("--review-root", type=Path, required=True)
    parser.add_argument("--logical-root", type=Path, required=True)
    parser.add_argument("--manual-root", type=Path, required=True)
    parser.add_argument("--final-root", type=Path, required=True)
    parser.add_argument("--reference-pdf", type=Path, required=True)
    parser.add_argument(
        "--history-root",
        type=Path,
        default=Path("history/figures/pipeline-v1"),
    )
    parser.add_argument(
        "--canonical-root",
        type=Path,
        default=Path("corpus/figures/assets/v1"),
    )
    args = parser.parse_args()

    reset_destination(args.history_root)
    reset_destination(args.canonical_root)

    stages = {
        "structure": args.structure_root,
        "review": args.review_root,
        "logical": args.logical_root,
        "manual": args.manual_root,
        "final": args.final_root,
    }
    for name, source in stages.items():
        copy_tree(source, args.history_root / name)

    copy_file(
        args.reference_pdf,
        args.history_root / "reference" / args.reference_pdf.name,
    )

    # The release payload is intentionally sourced only from the assembled
    # final set, never from detector/debug/intermediate directories.
    copy_tree(args.final_root, args.canonical_root)

    history_rows = describe_tree(args.history_root, str(args.history_root))
    canonical_rows = describe_tree(args.canonical_root, str(args.canonical_root))

    payload = {
        "schema": "corpus-motuum-figure-history-freeze-v1",
        "principle": (
            "Research history preserves successful, failed and intermediate "
            "outputs; canonical assets are copied only from the validated final set."
        ),
        "sources": {
            "structure": str(args.structure_root),
            "review": str(args.review_root),
            "logical": str(args.logical_root),
            "manual": str(args.manual_root),
            "final": str(args.final_root),
            "reference_pdf": str(args.reference_pdf),
        },
        "history": {
            "root": str(args.history_root),
            "files": len(history_rows),
            "bytes": sum(int(row["bytes"]) for row in history_rows),
            "records": history_rows,
        },
        "canonical": {
            "root": str(args.canonical_root),
            "files": len(canonical_rows),
            "bytes": sum(int(row["bytes"]) for row in canonical_rows),
            "records": canonical_rows,
        },
    }
    manifest_path = args.history_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "history_files": len(history_rows),
                "history_bytes": payload["history"]["bytes"],
                "canonical_files": len(canonical_rows),
                "canonical_bytes": payload["canonical"]["bytes"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
