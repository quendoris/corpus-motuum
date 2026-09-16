#!/usr/bin/env python3
"""Repair deterministic canonical-text invariants required by releases 1.0/1.1.

This tool performs only two mechanical corrections:
1. synchronize each page record's source_sha256 with the immutable page manifest;
2. remove prereform word-final hard signs from normalized_text only.

It never changes diplomatic_text, page ordering, wording apart from the normalized
final hard-sign rule, or editorial status.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

WORD_FINAL_HARD_SIGN = re.compile(r"([А-Яа-яЁё])[ъЪ](?![А-Яа-яЁё])")


def load_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=Path, default=Path("corpus/text/pages"))
    parser.add_argument(
        "--source-manifest",
        type=Path,
        default=Path("corpus/source/page-manifest.json"),
    )
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    source = load_json(args.source_manifest)
    source_by_id = {
        str(row["id"]): row
        for row in source.get("records", [])
        if isinstance(row, dict) and row.get("id")
    }

    changed_files = 0
    hash_repairs = 0
    hard_sign_repairs = 0

    for path in sorted(args.pages.glob("*.json")):
        record = load_json(path)
        page_id = str(record.get("id") or "")
        source_row = source_by_id.get(page_id)
        if source_row is None:
            raise SystemExit(f"{path}: page is absent from source manifest")

        changed = False
        expected_hash = source_row.get("sha256")
        if record.get("source_sha256") != expected_hash:
            record["source_sha256"] = expected_hash
            hash_repairs += 1
            changed = True

        normalized = record.get("normalized_text")
        if not isinstance(normalized, str):
            raise SystemExit(f"{path}: normalized_text is not a string")
        repaired, count = WORD_FINAL_HARD_SIGN.subn(r"\1", normalized)
        if count:
            record["normalized_text"] = repaired
            hard_sign_repairs += count
            changed = True

        if changed:
            changed_files += 1
            if args.write:
                path.write_text(
                    json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

    summary = {
        "changed_files": changed_files,
        "source_hash_repairs": hash_repairs,
        "word_final_hard_sign_repairs": hard_sign_repairs,
        "mode": "write" if args.write else "check",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if changed_files and not args.write:
        raise SystemExit(
            "canonical release invariants need repair; rerun with --write and review the diff"
        )


if __name__ == "__main__":
    main()
