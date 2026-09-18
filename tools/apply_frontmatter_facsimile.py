#!/usr/bin/env python3
"""Apply source-page facsimiles to declared title matter in reader formats.

The canonical text/figure corpus is not changed. This is a presentation-layer
patch: PDF gets full-page source facsimiles with an invisible searchable text
layer; HTML/EPUB show the facsimile while retaining hidden canonical markup;
FB2 shows the facsimile plus the canonical reflowable transcription and omits
separate figure placements on those same title pages.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

import fitz
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

FB2_NS = "http://www.gribuser.ru/xml/fictionbook/2.0"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", FB2_NS)
ET.register_namespace("l", XLINK_NS)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return value


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def facsimile_rows(book: dict[str, Any], policy: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    by_id = {str(page["id"]): page for page in book.get("pages", []) if isinstance(page, dict)}
    rows: list[dict[str, Any]] = []
    for declared in policy.get("facsimile_pages", []):
        page_id = str(declared.get("id") or "")
        page = by_id.get(page_id)
        if page is None:
            raise SystemExit(f"facsimile page absent from book: {page_id}")
        physical = int(page.get("physical_index") or -1)
        if physical != int(declared.get("physical_index") or -2):
            raise SystemExit(f"facsimile physical index mismatch: {page_id}")
        if str(page.get("page_type") or "") != str(declared.get("page_type") or ""):
            raise SystemExit(f"facsimile page type mismatch: {page_id}")
        path = root / "facsimile" / "frontmatter" / f"{page_id}.jpg"
        if not path.is_file():
            raise SystemExit(f"facsimile source missing: {path}")
        expected = str(page.get("provenance", {}).get("source_sha256") or "")
        if len(expected) != 64 or sha256_file(path) != expected:
            raise SystemExit(f"facsimile source hash mismatch: {page_id}")
        rows.append({"id": page_id, "physical_index": physical, "page": page, "path": path})
    rows.sort(key=lambda row: row["physical_index"])
    return rows


def find_font() -> str:
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
    ):
        if Path(candidate).is_file():
            return candidate
    raise SystemExit("no Cyrillic-capable system serif font found")


def build_pdf_replacements(rows: list[dict[str, Any]], output: Path) -> None:
    if "CMFacsimileSerif" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("CMFacsimileSerif", find_font()))
    c = canvas.Canvas(str(output), pageCompression=1, invariant=1)
    for row in rows:
        with Image.open(row["path"]) as image:
            iw, ih = image.size
        page_h = 720.0
        page_w = page_h * iw / ih
        c.setPageSize((page_w, page_h))
        c.bookmarkPage(row["id"])
        c.drawImage(
            ImageReader(str(row["path"])), 0, 0,
            width=page_w, height=page_h,
            preserveAspectRatio=True, anchor="c",
        )
        text = str(row["page"].get("text") or "")
        if text:
            text_obj = c.beginText(3.0, page_h - 3.0)
            text_obj.setFont("CMFacsimileSerif", 1.0)
            text_obj.setLeading(1.15)
            text_obj.setTextRenderMode(3)
            for line in text.splitlines():
                text_obj.textLine(line if line else " ")
            c.drawText(text_obj)
        c.showPage()
    c.save()


def patch_pdf(book: dict[str, Any], rows: list[dict[str, Any]], root: Path) -> None:
    pdf_path = root / "book.pdf"
    source = fitz.open(pdf_path)
    if len(source) != 600:
        source.close()
        raise SystemExit(f"input PDF has {len(source)} pages, expected 600")
    index_to_row = {int(row["physical_index"]) - 1: row for row in rows}
    with tempfile.TemporaryDirectory(prefix="corpus-motuum-frontmatter-") as tmp:
        replacement_path = Path(tmp) / "frontmatter.pdf"
        build_pdf_replacements(rows, replacement_path)
        replacements = fitz.open(replacement_path)
        if len(replacements) != len(rows):
            source.close(); replacements.close()
            raise SystemExit("frontmatter replacement page count mismatch")
        output = fitz.open()
        ri = 0
        for idx in range(600):
            if idx in index_to_row:
                output.insert_pdf(replacements, from_page=ri, to_page=ri, links=True, annots=True)
                ri += 1
            else:
                output.insert_pdf(source, from_page=idx, to_page=idx, links=True, annots=True)
        metadata = {k: v for k, v in source.metadata.items() if isinstance(v, str) and v}
        if metadata:
            output.set_metadata(metadata)
        temp = pdf_path.with_suffix(".frontmatter.tmp.pdf")
        output.save(temp, garbage=4, deflate=True, no_new_id=True, preserve_metadata=True)
        output.close(); replacements.close(); source.close()
        temp.replace(pdf_path)


def css_rules(rows: list[dict[str, Any]]) -> str:
    selectors: list[str] = []
    for row in rows:
        pid = row["id"]
        selectors.extend((f"#{pid}>p", f"#{pid}>figure.illustration"))
    hidden = ",".join(selectors)
    return (
        "\n.frontmatter-facsimile{margin:0 auto;text-align:center;break-inside:avoid;}"
        ".frontmatter-facsimile img{display:block;width:100%;max-width:100%;height:auto;margin:0 auto;}"
        f"{hidden}{{position:absolute!important;width:1px!important;height:1px!important;"
        "padding:0!important;margin:-1px!important;overflow:hidden!important;clip:rect(0,0,0,0)!important;"
        "white-space:nowrap!important;border:0!important;}\n"
    )


def insert_facsimile_markup(text: str, rows: list[dict[str, Any]], epub: bool) -> str:
    for row in rows:
        pid = row["id"]
        section_token = f'id="{pid}"'
        start = text.find(section_token)
        if start < 0:
            raise SystemExit(f"reader markup has no source section {pid}")
        close_span = text.find("</span>", start)
        if close_span < 0:
            raise SystemExit(f"reader markup has no pagebreak span on {pid}")
        insert_at = close_span + len("</span>")
        src = f"frontmatter/{pid}.jpg" if epub else f"facsimile/frontmatter/{pid}.jpg"
        markup = (
            f'<div class="frontmatter-facsimile" data-source-facsimile="{pid}">'
            f'<img src="{src}" alt="Original source page {row["physical_index"]}"/>'
            "</div>"
        )
        text = text[:insert_at] + markup + text[insert_at:]
    return text


def patch_html(rows: list[dict[str, Any]], root: Path) -> None:
    path = root / "book.html"
    text = path.read_text(encoding="utf-8")
    text = insert_facsimile_markup(text, rows, epub=False)
    if "</style>" not in text:
        raise SystemExit("HTML release has no style block")
    text = text.replace("</style>", css_rules(rows) + "</style>", 1)
    path.write_text(text, encoding="utf-8")


def rewrite_epub(rows: list[dict[str, Any]], root: Path) -> None:
    path = root / "book.epub"
    with zipfile.ZipFile(path, "r") as src:
        order = [info.filename for info in src.infolist()]
        payload = {name: src.read(name) for name in order}
    content = payload["OEBPS/content.xhtml"].decode("utf-8")
    content = insert_facsimile_markup(content, rows, epub=True)
    payload["OEBPS/content.xhtml"] = content.encode("utf-8")
    css = payload["OEBPS/style.css"].decode("utf-8") + css_rules(rows)
    payload["OEBPS/style.css"] = css.encode("utf-8")
    opf = payload["OEBPS/package.opf"].decode("utf-8")
    additions = "".join(
        f'<item id="frontmatter-{row["physical_index"]}" href="frontmatter/{row["id"]}.jpg" media-type="image/jpeg"/>'
        for row in rows
    )
    if "</manifest>" not in opf:
        raise SystemExit("EPUB package has no manifest")
    opf = opf.replace("</manifest>", additions + "</manifest>", 1)
    payload["OEBPS/package.opf"] = opf.encode("utf-8")
    for row in rows:
        name = f'OEBPS/frontmatter/{row["id"]}.jpg'
        payload[name] = row["path"].read_bytes()
        order.append(name)

    temp = path.with_suffix(".frontmatter.tmp.epub")
    with zipfile.ZipFile(temp, "w") as dst:
        for name in order:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED
            dst.writestr(info, payload[name])
    temp.replace(path)


def patch_fb2(rows: list[dict[str, Any]], root: Path) -> None:
    path = root / "book.fb2"
    tree = ET.parse(path)
    doc = tree.getroot()
    q = lambda name: f"{{{FB2_NS}}}{name}"
    sections = {str(s.get("id")): s for s in doc.findall(f".//{q('body')}/{q('section')}")}
    for row in rows:
        section = sections.get(row["id"])
        if section is None:
            raise SystemExit(f"FB2 missing source section {row['id']}")
        for child in list(section):
            if child.tag == q("image"):
                section.remove(child)
        image_id = f"frontmatter-{row['id']}"
        image = ET.Element(q("image"), {f"{{{XLINK_NS}}}href": "#" + image_id})
        section.insert(0, image)
        binary = ET.SubElement(doc, q("binary"), {"id": image_id, "content-type": "image/jpeg"})
        binary.text = base64.b64encode(row["path"].read_bytes()).decode("ascii")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def update_format_manifest(rows: list[dict[str, Any]], root: Path) -> None:
    path = root / "format-manifest.json"
    manifest = load_json(path)
    formats = manifest.setdefault("formats", {})
    mapping = {"html": "book.html", "epub3": "book.epub", "fb2": "book.fb2", "pdf": "book.pdf"}
    for key, filename in mapping.items():
        meta = formats.get(key)
        if not isinstance(meta, dict):
            raise SystemExit(f"format manifest missing {key}")
        meta["sha256"] = sha256_file(root / filename)
    manifest["frontmatter_facsimile"] = {
        "schema": "corpus-motuum-frontmatter-policy-v1",
        "pages": [row["id"] for row in rows],
        "physical_indices": [row["physical_index"] for row in rows],
        "count": len(rows),
        "source_hashes": {row["id"]: sha256_file(row["path"]) for row in rows},
    }
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--book", type=Path, required=True)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--policy", type=Path, required=True)
    args = ap.parse_args()

    book = load_json(args.book)
    policy = load_json(args.policy)
    if policy.get("schema") != "corpus-motuum-frontmatter-policy-v1":
        raise SystemExit("unsupported frontmatter policy")
    rows = facsimile_rows(book, policy, args.root)
    if len(rows) != 6:
        raise SystemExit(f"release expects exactly six title-matter facsimiles, got {len(rows)}")
    patch_html(rows, args.root)
    rewrite_epub(rows, args.root)
    patch_fb2(rows, args.root)
    patch_pdf(book, rows, args.root)
    update_format_manifest(rows, args.root)
    print(json.dumps({"facsimile_pages": [row["id"] for row in rows], "count": len(rows)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
