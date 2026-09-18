#!/usr/bin/env python3
"""Package patched 1.0.x/1.1.x editions with source-facsimile title matter."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

import package_book_release as base

EXPECTED_VERSIONS = {"diplomatic": "1.0.1", "normalized": "1.1.1"}


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return value


def copy_facsimiles(book: dict[str, Any], policy: dict[str, Any], source_root: Path, package_root: Path) -> int:
    pages = {str(p["id"]): p for p in book.get("pages", []) if isinstance(p, dict)}
    count = 0
    for row in policy.get("facsimile_pages", []):
        pid = str(row.get("id") or "")
        page = pages.get(pid)
        if page is None:
            raise SystemExit(f"frontmatter policy page missing from book: {pid}")
        source = source_root / f"{pid}.jpg"
        if not source.is_file():
            raise SystemExit(f"regenerated source page missing: {source}")
        expected = str(page.get("provenance", {}).get("source_sha256") or "")
        if len(expected) != 64 or base.sha256_file(source) != expected:
            raise SystemExit(f"frontmatter source hash mismatch: {pid}")
        destination = package_root / "facsimile" / "frontmatter" / f"{pid}.jpg"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        if base.sha256_file(destination) != expected:
            raise SystemExit(f"frontmatter copy hash mismatch: {pid}")
        count += 1
    return count


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--book", type=Path, required=True)
    ap.add_argument("--figure-root", type=Path, required=True)
    ap.add_argument("--source-page-root", type=Path, required=True)
    ap.add_argument("--frontmatter-policy", type=Path, default=Path("corpus/release/frontmatter-v1.json"))
    ap.add_argument("--version", required=True)
    ap.add_argument("--out-dir", type=Path, default=Path("dist"))
    ap.add_argument("--publication", action="store_true")
    args = ap.parse_args()

    book = load_json(args.book)
    policy = load_json(args.frontmatter_policy)
    if policy.get("schema") != "corpus-motuum-frontmatter-policy-v1":
        raise SystemExit("unsupported frontmatter policy")
    edition = str(book.get("edition") or "")
    expected_version = EXPECTED_VERSIONS.get(edition)
    if expected_version is None or args.version != expected_version:
        raise SystemExit(
            f"edition/version mismatch: {edition!r} expects {expected_version!r}, got {args.version!r}"
        )
    if len(book.get("pages", [])) != 600:
        raise SystemExit("release book must contain exactly 600 canonical pages")
    if book.get("figures", {}).get("total_assets") != 168 or book.get("figures", {}).get("linked_assets") != 168:
        raise SystemExit("release book must contain 168/168 page-linked canonical assets")
    if book.get("figures", {}).get("supplementary_assets") != 0:
        raise SystemExit("release book may not contain supplementary assets")

    suffix = "prereform" if edition == "diplomatic" else "normalized"
    package_name = f"corpus-motuum-v{args.version}-{suffix}"
    package_root = args.out_dir / package_name
    archive = args.out_dir / f"{package_name}.tar.gz"
    if package_root.exists():
        shutil.rmtree(package_root)
    package_root.mkdir(parents=True)
    shutil.copyfile(args.book, package_root / "book.json")
    shutil.copyfile(args.frontmatter_policy, package_root / "frontmatter-policy.json")

    copied = base.copy_assets(book, args.figure_root, package_root)
    if copied != 168:
        raise SystemExit(f"release package copied {copied} canonical assets, expected 168")
    facsimiles = copy_facsimiles(book, policy, args.source_page_root, package_root)
    if facsimiles != 6:
        raise SystemExit(f"release package copied {facsimiles} facsimiles, expected 6")

    tool_root = Path(__file__).resolve().parent
    base.run_tool(
        tool_root / "build_reader_formats.py",
        "--book", str(package_root / "book.json"),
        "--root", str(package_root),
        "--version", args.version,
    )
    base.run_tool(
        tool_root / "finalize_pdf_geometry.py",
        "--book", str(package_root / "book.json"),
        "--root", str(package_root),
    )
    base.run_tool(
        tool_root / "apply_frontmatter_facsimile.py",
        "--book", str(package_root / "book.json"),
        "--root", str(package_root),
        "--policy", str(package_root / "frontmatter-policy.json"),
    )
    validator_args = [
        "--book", str(package_root / "book.json"),
        "--root", str(package_root),
        "--policy", str(package_root / "frontmatter-policy.json"),
        "--report", str(package_root / "release-equivalence.json"),
    ]
    if args.publication:
        validator_args.append("--publication")
    base.run_tool(tool_root / "validate_reader_formats_patch.py", *validator_args)

    formats = load_json(package_root / "format-manifest.json")
    equivalence = load_json(package_root / "release-equivalence.json")
    release_manifest = {
        "schema": "corpus-motuum-release-v1.1",
        "version": args.version,
        "edition": edition,
        "text_layer": book.get("text_layer"),
        "principle": book.get("principle"),
        "source_commit": base.git_head(),
        "source_pdf_sha256": book.get("source", {}).get("source_pdf_sha256"),
        "source_page_manifest_sha256": book.get("source", {}).get("page_manifest_sha256"),
        "canonical_pages": 600,
        "canonical_figures": book.get("figures", {}).get("counts"),
        "canonical_figure_total": copied,
        "frontmatter_facsimile_pages": facsimiles,
        "frontmatter_policy": "frontmatter-policy.json",
        "frontmatter_policy_sha256": base.sha256_file(package_root / "frontmatter-policy.json"),
        "reader_formats": formats.get("formats"),
        "equivalence_report": "release-equivalence.json",
        "publication_ready": bool(equivalence.get("publication_ready")),
        "book_json_sha256": base.sha256_file(package_root / "book.json"),
    }
    (package_root / "release-manifest.json").write_text(
        json.dumps(release_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    base.write_sha256s(package_root)
    base.write_deterministic_tar_gz(package_root, archive)
    print(json.dumps({
        "version": args.version,
        "edition": edition,
        "pages": 600,
        "canonical_figures": copied,
        "facsimile_pages": facsimiles,
        "formats": ["json", "html", "epub3", "fb2", "pdf", "txt"],
        "publication_ready": release_manifest["publication_ready"],
        "directory": package_root.as_posix(),
        "archive": archive.as_posix(),
        "archive_sha256": base.sha256_file(archive),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
