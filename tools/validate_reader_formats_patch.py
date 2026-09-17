#!/usr/bin/env python3
"""Validate patched 1.0.x/1.1.x reader formats with source facsimile title matter."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

import fitz

import validate_reader_formats as base

FB2_NS = base.FB2_NS
XLINK_NS = base.XLINK_NS


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return value


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def policy_rows(book: dict[str, Any], policy: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    pages = {str(p["id"]): p for p in book["pages"]}
    rows: list[dict[str, Any]] = []
    for declared in policy.get("facsimile_pages", []):
        pid = str(declared["id"])
        page = pages.get(pid)
        if page is None:
            raise SystemExit(f"policy references absent page: {pid}")
        source = root / "facsimile" / "frontmatter" / f"{pid}.jpg"
        if not source.is_file():
            raise SystemExit(f"facsimile file missing: {source}")
        expected = str(page.get("provenance", {}).get("source_sha256") or "")
        if sha256_file(source) != expected:
            raise SystemExit(f"facsimile hash mismatch: {pid}")
        rows.append({"id": pid, "physical_index": int(page["physical_index"]), "page": page, "path": source})
    rows.sort(key=lambda row: row["physical_index"])
    if len(rows) != 6:
        raise SystemExit(f"expected six facsimile pages, got {len(rows)}")
    return rows


def validate_fb2(book: dict[str, Any], root: Path, assets: dict[str, dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    tree = ET.parse(root / "book.fb2")
    doc = tree.getroot()
    q = lambda name: f"{{{FB2_NS}}}{name}"
    sections = doc.findall(f".//{q('body')}/{q('section')}")
    expected_ids = [str(p["id"]) for p in book["pages"]]
    actual_ids = [str(section.get("id")) for section in sections]
    if actual_ids != expected_ids:
        raise SystemExit("FB2 source-page sequence differs from canonical book")

    facsimile_ids = {row["id"] for row in rows}
    expected_text = base.expected_page_text(book)
    id_to_rel = {base.fb2_image_id(rel): rel for rel in assets}
    actual_refs: list[str] = []
    facsimile_refs: list[str] = []
    for section in sections:
        page_id = str(section.get("id"))
        paragraphs = section.findall(q("p"))
        actual_text = base.norm(" ".join(" ".join(p.itertext()) for p in paragraphs))
        if actual_text != expected_text[page_id]:
            raise SystemExit(f"FB2 text mismatch on {page_id}")
        for image in section.findall(q("image")):
            href = (image.get(f"{{{XLINK_NS}}}href") or "").lstrip("#")
            if href.startswith("frontmatter-"):
                if page_id not in facsimile_ids or href != f"frontmatter-{page_id}":
                    raise SystemExit(f"unexpected FB2 facsimile reference {href} on {page_id}")
                facsimile_refs.append(page_id)
                continue
            rel = id_to_rel.get(href)
            if rel is None:
                raise SystemExit(f"FB2 references unknown canonical image id {href}")
            if page_id in facsimile_ids:
                raise SystemExit(f"FB2 still displays detached canonical figure on facsimile page {page_id}")
            actual_refs.append(rel)

    expected_visible = [
        str(asset["file"])
        for page in book["pages"]
        if str(page["id"]) not in facsimile_ids
        for asset in page.get("figures", [])
    ]
    if actual_refs != expected_visible:
        raise SystemExit("FB2 visible canonical figure order differs from facsimile policy")
    if facsimile_refs != [row["id"] for row in rows]:
        raise SystemExit("FB2 facsimile page sequence mismatch")

    binaries = doc.findall(q("binary"))
    canonical_seen: set[str] = set()
    facsimile_seen: set[str] = set()
    for binary in binaries:
        key = str(binary.get("id") or "")
        data = base64.b64decode("".join(binary.itertext()).encode("ascii"))
        if key.startswith("frontmatter-"):
            pid = key.removeprefix("frontmatter-")
            row = next((r for r in rows if r["id"] == pid), None)
            if row is None or sha256_bytes(data) != sha256_file(row["path"]):
                raise SystemExit(f"FB2 facsimile binary mismatch: {pid}")
            facsimile_seen.add(pid)
            continue
        rel = id_to_rel.get(key)
        if rel is None:
            raise SystemExit(f"FB2 binary id is unknown: {key}")
        if sha256_bytes(data) != str(assets[rel]["sha256"]):
            raise SystemExit(f"FB2 canonical binary hash mismatch: {rel}")
        canonical_seen.add(rel)
    if canonical_seen != set(assets):
        raise SystemExit("FB2 canonical binary inventory differs from 168 assets")
    if facsimile_seen != facsimile_ids:
        raise SystemExit("FB2 facsimile binary inventory mismatch")
    if len(binaries) != 168 + len(rows):
        raise SystemExit(f"FB2 binary count mismatch: {len(binaries)}")
    return {
        "pages": 600,
        "canonical_assets_embedded": 168,
        "canonical_assets_visible": len(actual_refs),
        "facsimile_pages": len(rows),
        "text_equivalent": True,
    }


def validate_pdf(book: dict[str, Any], root: Path, rows: list[dict[str, Any]], publication: bool) -> dict[str, Any]:
    manifest = load_json(root / "format-manifest.json")
    meta = manifest.get("formats", {}).get("pdf", {})
    fallbacks = list(meta.get("fallback_placements") or [])
    if publication and fallbacks:
        raise SystemExit("publication PDF contains fallback placements: " + ", ".join(map(str, fallbacks)))

    by_physical = {int(row["physical_index"]): row for row in rows}
    facsimile_ids = {row["id"] for row in rows}
    expected_text = base.expected_page_text(book)
    doc = fitz.open(root / "book.pdf")
    if len(doc) != 600:
        doc.close(); raise SystemExit(f"PDF has {len(doc)} pages, expected 600")
    total_images = 0
    substituted_assets = 0
    coverage: dict[str, list[float]] = {}
    for physical, (pdf_page, canonical) in enumerate(zip(doc, book["pages"]), 1):
        pid = str(canonical["id"])
        extracted = base.norm(pdf_page.get_text("text"))
        if extracted != expected_text[pid]:
            doc.close(); raise SystemExit(f"PDF extracted text mismatch on {pid} (physical {physical})")
        images = pdf_page.get_image_info(xrefs=True)
        if physical in by_physical:
            if len(images) != 1:
                doc.close(); raise SystemExit(f"facsimile PDF page {pid} must contain exactly one full-page image")
            substituted_assets += len(canonical.get("figures", []))
            bbox = fitz.Rect(images[0]["bbox"])
            wc = bbox.width / pdf_page.rect.width
            hc = bbox.height / pdf_page.rect.height
            if wc < 0.995 or hc < 0.995:
                doc.close(); raise SystemExit(f"facsimile does not fill PDF page {pid}: {wc:.4f}x{hc:.4f}")
            geom = canonical.get("source_geometry") or {}
            expected_ratio = float(geom.get("width")) / float(geom.get("height"))
            actual_ratio = pdf_page.rect.width / pdf_page.rect.height
            if abs(expected_ratio - actual_ratio) > 0.002:
                doc.close(); raise SystemExit(f"facsimile page aspect mismatch on {pid}")
            coverage[pid] = [wc, hc]
        else:
            expected_count = len(canonical.get("figures", []))
            if len(images) != expected_count:
                doc.close(); raise SystemExit(f"PDF image count mismatch on {pid}: expected {expected_count}, got {len(images)}")
        total_images += len(images)
        for info in images:
            bbox = fitz.Rect(info["bbox"])
            if bbox.is_empty or bbox.x0 < -0.1 or bbox.y0 < -0.1 or bbox.x1 > pdf_page.rect.width + 0.1 or bbox.y1 > pdf_page.rect.height + 0.1:
                doc.close(); raise SystemExit(f"PDF image outside bounds on {pid}: {bbox}")
    doc.close()
    expected_total = 168 - substituted_assets + len(rows)
    if total_images != expected_total:
        raise SystemExit(f"PDF total image count mismatch: expected {expected_total}, got {total_images}")
    return {
        "pages": 600,
        "canonical_assets": 168,
        "canonical_assets_substituted_by_facsimile": substituted_assets,
        "facsimile_pages": len(rows),
        "pdf_image_placements": total_images,
        "facsimile_coverage": coverage,
        "text_equivalent": True,
        "fallback_placements": fallbacks,
        "publication_placement_ready": not fallbacks,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--book", type=Path, required=True)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--policy", type=Path, required=True)
    ap.add_argument("--report", type=Path, default=None)
    ap.add_argument("--publication", action="store_true")
    args = ap.parse_args()

    book = load_json(args.book)
    policy = load_json(args.policy)
    assets = base.expected_assets(book)
    rows = policy_rows(book, policy, args.root)

    for name in ("book.html", "book.epub", "book.fb2", "book.pdf", "book.txt"):
        if not (args.root / name).is_file():
            raise SystemExit(f"reader format missing: {name}")

    report: dict[str, Any] = {
        "schema": "corpus-motuum-release-equivalence-v1.1",
        "publication_mode": args.publication,
        "canonical_pages": 600,
        "canonical_assets": 168,
        "frontmatter_policy": policy.get("schema"),
        "facsimile_pages": [row["id"] for row in rows],
        "html": base.validate_html(book, args.root, assets),
        "epub3": base.validate_epub(book, args.root, assets),
        "fb2": validate_fb2(book, args.root, assets, rows),
        "txt": base.validate_txt(book, args.root),
        "pdf": validate_pdf(book, args.root, rows, args.publication),
    }
    report["publication_ready"] = bool(
        report["pdf"]["publication_placement_ready"]
        and report["pdf"]["text_equivalent"]
        and report["html"]["text_equivalent"]
        and report["epub3"]["text_equivalent"]
        and report["fb2"]["text_equivalent"]
        and report["txt"]["text_equivalent"]
    )
    if args.publication and not report["publication_ready"]:
        raise SystemExit("patched reader formats are not publication-ready")
    target = args.report or (args.root / "release-equivalence.json")
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
