#!/usr/bin/env python3
"""Verify that all expected physical pages have all requested OCR candidates."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, default=Path("corpus/source/page-manifest.json"))
    ap.add_argument("--root", type=Path, default=Path("corpus/ocr/raw"))
    ap.add_argument("--engines", nargs="+", default=["rus", "orus", "kraken"])
    args = ap.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    ids = [r["id"] for r in manifest["records"]]
    print(f"Expected physical pages: {len(ids)}")

    any_missing = False
    for engine in args.engines:
        d = args.root / engine
        missing = [page_id for page_id in ids if not (d / f"{page_id}.txt").exists()]
        extras = sorted(
            p.stem for p in d.glob("*.txt") if p.stem not in set(ids)
        ) if d.exists() else []
        run_meta = d / "run.json"
        print(
            f"{engine}: present={len(ids) - len(missing)}/{len(ids)}, "
            f"missing={len(missing)}, extras={len(extras)}, run.json={'yes' if run_meta.exists() else 'no'}"
        )
        if missing:
            any_missing = True
            print("  first missing:", ", ".join(missing[:20]))
        if extras:
            print("  first extras:", ", ".join(extras[:20]))

    if any_missing:
        raise SystemExit(1)
    print("All requested OCR layers are complete.")


if __name__ == "__main__":
    main()
