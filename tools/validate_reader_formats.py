#!/usr/bin/env python3
"""Validate reader-friendly release formats against canonical book.json.

The validator compares content, source-page order and canonical image inventory.
Reflowable formats are required to be content-equivalent, not pixel-identical.
The fixed PDF is additionally required to contain exactly 600 pages and the
expected number of image placements on every source page.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

import fitz

FB2_NS = "http://www.gribuser.ru/xml/fictionbook/2.0"
XLINK_NS = "http://www.w3.org/1999/xlink"
XHTML_NS = "http://www.w3.org/1999/xhtml"
EPUB_NS = "http://www.idpf.org/2007/ops"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return value


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def expected_assets(book: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for page in book["pages"]:
        for asset in page.get("figures", []):
            rel = str(asset["file"])
            if rel in result:
                raise SystemExit(f"duplicate canonical asset path: {rel}")
            result[rel] = asset
    if len(result) != 168:
        raise SystemExit(f"canonical book has {len(result)} assets, expected 168")
    return result


def expected_page_text(book: dict[str, Any]) -> dict[str, str]:
    return {str(p["id"]): norm(str(p.get("text") or "")) for p in book["pages"]}


class ReleaseHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page_ids: list[str] = []
        self.page_text: dict[str, list[str]] = {}
        self.assets: list[str] = []
        self._page: str | None = None
        self._in_p = 0
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = dict(attrs_list)
        if tag == "section" and "source-page" in (attrs.get("class") or "").split():
            page_id = attrs.get("id")
            if not page_id:
                raise ValueError("HTML source-page section without id")
            self._page = page_id
            self.page_ids.append(page_id)
            self.page_text.setdefault(page_id, [])
        elif tag == "p" and self._page:
            self._in_p += 1
            self._buf = []
        elif tag == "figure":
            asset = attrs.get("data-asset")
            if asset:
                self.assets.append(asset)

    def handle_endtag(self, tag: str) -> None:
        if tag == "p" and self._in_p and self._page:
            self.page_text[self._page].append(" ".join(self._buf))
            self._in_p -= 1
            self._buf = []
        elif tag == "section" and self._page:
            self._page = None

    def handle_data(self, data: str) -> None:
        if self._in_p:
            self._buf.append(data)


def validate_html(book: dict[str, Any], root: Path, assets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    parser = ReleaseHTMLParser()
    parser.feed((root / "book.html").read_text(encoding="utf-8"))
    expected_ids = [str(p["id"]) for p in book["pages"]]
    if parser.page_ids != expected_ids:
        raise SystemExit("HTML source-page sequence differs from canonical book")
    expected_text = expected_page_text(book)
    for page_id in expected_ids:
        actual = norm(" ".join(parser.page_text.get(page_id, [])))
        if actual != expected_text[page_id]:
            raise SystemExit(f"HTML text mismatch on {page_id}")
    if parser.assets != [
        str(asset["file"])
        for page in book["pages"]
        for asset in page.get("figures", [])
    ]:
        raise SystemExit("HTML figure order differs from canonical book")
    if set(parser.assets) != set(assets) or len(parser.assets) != 168:
        raise SystemExit("HTML figure inventory mismatch")
    return {"pages": 600, "assets": 168, "text_equivalent": True}


def epub_image_path(rel: str) -> str:
    p = Path(rel)
    if len(p.parts) < 3 or p.parts[0] != "figures":
        raise SystemExit(f"unexpected canonical figure path: {rel}")
    return (Path("OEBPS/images") / Path(*p.parts[1:])).as_posix()


def validate_epub(book: dict[str, Any], root: Path, assets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    path = root / "book.epub"
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        if not infos or infos[0].filename != "mimetype":
            raise SystemExit("EPUB mimetype is not the first ZIP member")
        if infos[0].compress_type != zipfile.ZIP_STORED:
            raise SystemExit("EPUB mimetype must be stored without compression")
        if zf.read("mimetype") != b"application/epub+zip":
            raise SystemExit("EPUB mimetype payload is invalid")
        bad_names = [info.filename for info in infos if ".." in Path(info.filename).parts]
        if bad_names:
            raise SystemExit(f"EPUB contains unsafe parent paths: {bad_names}")

        content = ET.fromstring(zf.read("OEBPS/content.xhtml"))
        ns = {"x": XHTML_NS, "epub": EPUB_NS}
        sections = content.findall(".//x:section[@class='source-page']", ns)
        expected_ids = [str(p["id"]) for p in book["pages"]]
        actual_ids = [str(section.get("id")) for section in sections]
        if actual_ids != expected_ids:
            raise SystemExit("EPUB source-page sequence differs from canonical book")
        expected_text = expected_page_text(book)
        actual_assets: list[str] = []
        for section in sections:
            page_id = str(section.get("id"))
            paragraphs = section.findall(".//x:p", ns)
            actual_text = norm(" ".join(" ".join(p.itertext()) for p in paragraphs))
            if actual_text != expected_text[page_id]:
                raise SystemExit(f"EPUB text mismatch on {page_id}")
            for figure in section.findall(".//x:figure", ns):
                rel = figure.get("data-asset")
                img = figure.find("x:img", ns)
                if not rel or img is None:
                    raise SystemExit(f"EPUB malformed figure on {page_id}")
                if img.get("src") != str(Path(epub_image_path(rel)).relative_to("OEBPS")):
                    raise SystemExit(f"EPUB image path mismatch for {rel}")
                actual_assets.append(rel)

        canonical_order = [
            str(asset["file"])
            for page in book["pages"]
            for asset in page.get("figures", [])
        ]
        if actual_assets != canonical_order:
            raise SystemExit("EPUB figure order differs from canonical book")
        for rel, asset in assets.items():
            member = epub_image_path(rel)
            try:
                data = zf.read(member)
            except KeyError as exc:
                raise SystemExit(f"EPUB missing canonical image {member}") from exc
            if sha256_bytes(data) != str(asset["sha256"]):
                raise SystemExit(f"EPUB image hash mismatch: {rel}")

        nav = ET.fromstring(zf.read("OEBPS/nav.xhtml"))
        page_links = nav.findall(
            ".//x:nav[@epub:type='page-list']//x:a", ns
        )
        if len(page_links) != 600:
            raise SystemExit(f"EPUB page-list has {len(page_links)} entries, expected 600")
    return {"pages": 600, "assets": 168, "text_equivalent": True, "page_list": 600}


def fb2_image_id(rel: str) -> str:
    return "img-" + re.sub(r"[^A-Za-z0-9_-]+", "-", rel)


def validate_fb2(book: dict[str, Any], root: Path, assets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    tree = ET.parse(root / "book.fb2")
    root_xml = tree.getroot()
    q = lambda name: f"{{{FB2_NS}}}{name}"
    sections = root_xml.findall(f".//{q('body')}/{q('section')}")
    expected_ids = [str(p["id"]) for p in book["pages"]]
    actual_ids = [str(section.get("id")) for section in sections]
    if actual_ids != expected_ids:
        raise SystemExit("FB2 source-page sequence differs from canonical book")
    expected_text = expected_page_text(book)
    actual_refs: list[str] = []
    id_to_rel = {fb2_image_id(rel): rel for rel in assets}
    for section in sections:
        page_id = str(section.get("id"))
        paragraphs = section.findall(q("p"))
        actual_text = norm(" ".join(" ".join(p.itertext()) for p in paragraphs))
        if actual_text != expected_text[page_id]:
            raise SystemExit(f"FB2 text mismatch on {page_id}")
        for image in section.findall(q("image")):
            href = image.get(f"{{{XLINK_NS}}}href") or ""
            key = href.lstrip("#")
            if key not in id_to_rel:
                raise SystemExit(f"FB2 references unknown image id {key}")
            actual_refs.append(id_to_rel[key])
    canonical_order = [
        str(asset["file"])
        for page in book["pages"]
        for asset in page.get("figures", [])
    ]
    if actual_refs != canonical_order:
        raise SystemExit("FB2 figure order differs from canonical book")

    binaries = root_xml.findall(q("binary"))
    if len(binaries) != 168:
        raise SystemExit(f"FB2 embeds {len(binaries)} binaries, expected 168")
    seen: set[str] = set()
    for binary in binaries:
        key = str(binary.get("id") or "")
        rel = id_to_rel.get(key)
        if not rel:
            raise SystemExit(f"FB2 binary id is unknown: {key}")
        data = base64.b64decode("".join(binary.itertext()).encode("ascii"))
        if sha256_bytes(data) != str(assets[rel]["sha256"]):
            raise SystemExit(f"FB2 embedded image hash mismatch: {rel}")
        seen.add(rel)
    if seen != set(assets):
        raise SystemExit("FB2 binary inventory differs from canonical assets")
    return {"pages": 600, "assets": 168, "text_equivalent": True}


def validate_txt(book: dict[str, Any], root: Path) -> dict[str, Any]:
    raw = (root / "book.txt").read_text(encoding="utf-8")
    chunks = raw.rstrip("\n").split("\f")
    if len(chunks) != 600:
        raise SystemExit(f"TXT has {len(chunks)} source page chunks, expected 600")
    for page, chunk in zip(book["pages"], chunks):
        if norm(chunk) != norm(str(page.get("text") or "")):
            raise SystemExit(f"TXT text mismatch on {page['id']}")
    return {"pages": 600, "text_equivalent": True}


def validate_pdf(book: dict[str, Any], root: Path, publication: bool) -> dict[str, Any]:
    manifest = load_json(root / "format-manifest.json")
    pdf_meta = manifest.get("formats", {}).get("pdf", {})
    fallbacks = list(pdf_meta.get("fallback_placements") or [])
    fallback_pages = list(pdf_meta.get("fallback_pages") or [])
    if publication and fallbacks:
        raise SystemExit(
            "publication PDF contains unreviewed fallback placement(s): "
            + ", ".join(str(x) for x in fallbacks)
        )

    doc = fitz.open(root / "book.pdf")
    if len(doc) != 600:
        raise SystemExit(f"PDF has {len(doc)} pages, expected 600")
    expected_text = expected_page_text(book)
    total_images = 0
    for idx, (page, expected_page) in enumerate(zip(doc, book["pages"]), 1):
        page_id = str(expected_page["id"])
        extracted = norm(page.get_text("text"))
        if extracted != expected_text[page_id]:
            raise SystemExit(f"PDF extracted text mismatch on {page_id} (physical {idx})")
        info = page.get_image_info(xrefs=True)
        expected_count = len(expected_page.get("figures", []))
        if len(info) != expected_count:
            raise SystemExit(
                f"PDF image count mismatch on {page_id}: expected {expected_count}, got {len(info)}"
            )
        total_images += len(info)
        for row in info:
            bbox = fitz.Rect(row["bbox"])
            if bbox.is_empty or bbox.x0 < -0.1 or bbox.y0 < -0.1 or bbox.x1 > page.rect.width + 0.1 or bbox.y1 > page.rect.height + 0.1:
                raise SystemExit(f"PDF image outside page bounds on {page_id}: {bbox}")
    if total_images != 168:
        raise SystemExit(f"PDF contains {total_images} image placements, expected 168")
    doc.close()
    return {
        "pages": 600,
        "assets": total_images,
        "text_equivalent": True,
        "fallback_placements": fallbacks,
        "fallback_pages": fallback_pages,
        "publication_placement_ready": not fallbacks,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--book", type=Path, required=True)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--report", type=Path, default=None)
    ap.add_argument("--publication", action="store_true")
    args = ap.parse_args()

    book = load_json(args.book)
    assets = expected_assets(book)
    if len(book.get("pages", [])) != 600:
        raise SystemExit("canonical book does not have 600 pages")
    if book.get("figures", {}).get("linked_assets") != 168:
        raise SystemExit("canonical book does not have 168 linked figures")
    if book.get("figures", {}).get("supplementary_assets") != 0:
        raise SystemExit("canonical book still has supplementary figures")

    expected_files = ["book.html", "book.epub", "book.fb2", "book.pdf", "book.txt"]
    for name in expected_files:
        if not (args.root / name).is_file():
            raise SystemExit(f"reader format missing: {name}")

    report: dict[str, Any] = {
        "schema": "corpus-motuum-release-equivalence-v1",
        "publication_mode": args.publication,
        "canonical_pages": 600,
        "canonical_assets": 168,
        "formats": {},
    }
    report["formats"]["html"] = validate_html(book, args.root, assets)
    report["formats"]["epub3"] = validate_epub(book, args.root, assets)
    report["formats"]["fb2"] = validate_fb2(book, args.root, assets)
    report["formats"]["txt"] = validate_txt(book, args.root)
    report["formats"]["pdf"] = validate_pdf(book, args.root, args.publication)
    report["publication_ready"] = bool(
        report["formats"]["pdf"]["publication_placement_ready"]
    )
    report["file_sha256"] = {
        name: sha256_file(args.root / name) for name in expected_files
    }

    out = args.report or (args.root / "release-equivalence.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
