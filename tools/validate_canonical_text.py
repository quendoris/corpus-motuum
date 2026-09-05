#!/usr/bin/env python3
"""Validate page-aligned canonical text records against source provenance.

The validator intentionally checks editorial invariants rather than language
quality: stable page IDs, source hashes, batch references, status consistency,
and the separation between prereform diplomatic text and the normalized layer.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

PREREFORM_GLYPHS = set("ѣѢіІѳѲѵѴ")
WORD_FINAL_HARD_SIGN = re.compile(r"[А-Яа-яЁё]ъ(?=\b)")
VALID_STATUSES = {"draft", "reviewed", "verified"}


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"invalid JSON: {path}: {exc}") from exc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--source-manifest",
        type=Path,
        default=Path("corpus/source/page-manifest.json"),
    )
    ap.add_argument(
        "--pages",
        type=Path,
        default=Path("corpus/text/pages"),
    )
    ap.add_argument(
        "--batches",
        type=Path,
        default=Path("corpus/text/batches"),
    )
    args = ap.parse_args()

    source = load_json(args.source_manifest)
    source_by_id = {rec["id"]: rec for rec in source["records"]}
    errors: list[str] = []
    page_records: dict[str, dict] = {}

    for path in sorted(args.pages.glob("*.json")):
        rec = load_json(path)
        page_id = rec.get("id")
        if not page_id:
            errors.append(f"{path}: missing id")
            continue
        if page_id in page_records:
            errors.append(f"{path}: duplicate id {page_id}")
            continue
        page_records[page_id] = rec

        if path.stem != page_id:
            errors.append(f"{path}: filename/id mismatch ({page_id})")
        if rec.get("schema") != "corpus-motuum-text-page-v1":
            errors.append(f"{path}: unexpected schema {rec.get('schema')!r}")
        if rec.get("status") not in VALID_STATUSES:
            errors.append(f"{path}: invalid status {rec.get('status')!r}")

        src = source_by_id.get(page_id)
        if src is None:
            errors.append(f"{path}: id is absent from source manifest")
        else:
            if rec.get("physical_index") != src.get("physical_index"):
                errors.append(
                    f"{path}: physical_index {rec.get('physical_index')} != "
                    f"source {src.get('physical_index')}"
                )
            if rec.get("source_sha256") != src.get("sha256"):
                errors.append(f"{path}: source_sha256 does not match source manifest")

        diplomatic = rec.get("diplomatic_text")
        normalized = rec.get("normalized_text")
        if not isinstance(diplomatic, str) or not isinstance(normalized, str):
            errors.append(f"{path}: text layers must be strings")
            continue

        historical = sorted(PREREFORM_GLYPHS.intersection(normalized))
        if historical:
            errors.append(
                f"{path}: normalized_text still contains prereform glyphs: "
                f"{''.join(historical)}"
            )
        m = WORD_FINAL_HARD_SIGN.search(normalized)
        if m:
            errors.append(
                f"{path}: normalized_text contains word-final hard sign near "
                f"{m.group(0)!r}"
            )
        if not isinstance(rec.get("notes", []), list):
            errors.append(f"{path}: notes must be an array")

    batch_count = 0
    referenced_ids: set[str] = set()
    if args.batches.exists():
        for path in sorted(args.batches.glob("*.json")):
            batch_count += 1
            batch = load_json(path)
            if batch.get("schema") != "corpus-motuum-editorial-batch-v1":
                errors.append(f"{path}: unexpected schema {batch.get('schema')!r}")
            pages = batch.get("pages")
            if not isinstance(pages, list):
                errors.append(f"{path}: pages must be an array")
                continue
            if batch.get("completed_pages") != len(pages):
                errors.append(
                    f"{path}: completed_pages={batch.get('completed_pages')} "
                    f"but references={len(pages)}"
                )
            for item in pages:
                page_id = item.get("id")
                if page_id in referenced_ids:
                    errors.append(f"{path}: page {page_id} referenced by multiple batches")
                referenced_ids.add(page_id)
                page = page_records.get(page_id)
                if page is None:
                    errors.append(f"{path}: missing page record {page_id}")
                    continue
                if item.get("physical_index") != page.get("physical_index"):
                    errors.append(f"{path}: physical_index mismatch for {page_id}")
                if item.get("status") != page.get("status"):
                    errors.append(f"{path}: status mismatch for {page_id}")
                expected_path = str(args.pages / f"{page_id}.json")
                if item.get("path") != expected_path:
                    errors.append(
                        f"{path}: path for {page_id} is {item.get('path')!r}; "
                        f"expected {expected_path!r}"
                    )

    orphaned = sorted(set(page_records) - referenced_ids) if batch_count else []
    for page_id in orphaned:
        errors.append(f"page {page_id} is not referenced by any batch")

    print(f"Source physical pages: {len(source_by_id)}")
    print(f"Canonical page records: {len(page_records)}")
    print(f"Editorial batches: {batch_count}")
    print(f"Referenced canonical pages: {len(referenced_ids)}")

    if errors:
        print(f"Validation errors: {len(errors)}")
        for err in errors:
            print(f"- {err}")
        raise SystemExit(1)

    statuses: dict[str, int] = {}
    for rec in page_records.values():
        statuses[rec["status"]] = statuses.get(rec["status"], 0) + 1
    print("Statuses:", statuses)
    print("Canonical text validation passed.")


if __name__ == "__main__":
    main()
