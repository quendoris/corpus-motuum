#!/usr/bin/env python3
"""Synchronize machine-owned source fields into Exercise v1 records.

The extractor owns exact source selection and source-derived identity fields.
Editorial interpretation, anatomy, biomechanics, evidence, variants and renders
are explicitly preserved.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any


SOURCE_KEYS = (
    "source_id",
    "page_ids",
    "physical_pages",
    "printed_pages",
    "figure_ids",
    "text",
    "segments",
)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return value


def merged_record(
    generated: dict[str, Any],
    existing: dict[str, Any] | None,
) -> dict[str, Any]:
    if existing is None:
        return copy.deepcopy(generated)
    if existing.get("schema") != "corpus-motuum-exercise-v1":
        raise SystemExit(f"{generated['id']}: existing item has wrong schema")
    if existing.get("id") != generated.get("id"):
        raise SystemExit(f"{generated['id']}: existing item identity mismatch")

    result = copy.deepcopy(existing)

    current_source = result.setdefault("source", {})
    generated_source = generated["source"]
    preserved_claims = copy.deepcopy(current_source.get("source_claims", []))
    for key in SOURCE_KEYS:
        current_source[key] = copy.deepcopy(generated_source[key])
    current_source["source_claims"] = preserved_claims

    result.setdefault("names", {})["source"] = generated["names"]["source"]
    result["names"]["normalized"] = generated["names"]["normalized"]

    result.setdefault("media", {})["source_figures"] = copy.deepcopy(
        generated["media"]["source_figures"]
    )

    provenance = result.setdefault("provenance", {})
    provenance["source_page_hashes"] = copy.deepcopy(
        generated["provenance"]["source_page_hashes"]
    )
    provenance["created_from_commit"] = generated["provenance"][
        "created_from_commit"
    ]

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated-root", type=Path, required=True)
    parser.add_argument(
        "--items-root",
        type=Path,
        default=Path("corpus/exercises/items"),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if source-owned fields are not synchronized.",
    )
    args = parser.parse_args()

    generated_paths = sorted(args.generated_root.glob("EX-*.json"))
    if not generated_paths:
        raise SystemExit("no generated exercise records found")
    args.items_root.mkdir(parents=True, exist_ok=True)

    stale: list[str] = []
    for generated_path in generated_paths:
        generated = load_json(generated_path)
        target = args.items_root / generated_path.name
        existing = load_json(target) if target.is_file() else None
        merged = merged_record(generated, existing)
        payload = json.dumps(merged, ensure_ascii=False, indent=2) + "\n"
        if args.check:
            if not target.is_file() or target.read_text(encoding="utf-8") != payload:
                stale.append(target.as_posix())
        else:
            target.write_text(payload, encoding="utf-8")

    if args.check and stale:
        raise SystemExit(
            "exercise source lock is stale: " + ", ".join(stale)
        )

    print(
        json.dumps(
            {
                "generated_records": len(generated_paths),
                "check": args.check,
                "stale": stale,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
