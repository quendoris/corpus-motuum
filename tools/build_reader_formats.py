#!/usr/bin/env python3
"""Render one canonical 1.x book payload into reader-friendly formats.

Outputs are views of the same 600-page canonical payload:
- HTML: open, inspectable reflow view;
- EPUB 3: primary reflowable e-reader format;
- FB2: reflowable FictionBook 2.0 format;
- PDF: fixed 600-page reconstruction preserving source page boundaries;
- TXT: universal text fallback with form-feed source-page boundaries.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

BOOK_TITLE = "Практическая гимнастика"
BOOK_SUBTITLE = "Руководство к постепенному упражнению гимнастикой"
BOOK_AUTHOR = "Наполеон Лэнэ"
EPUB_NS = "http://www.idpf.org/2007/ops"
FB2_NS = "http://www.gribuser.ru/xml/fictionbook/2.0"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", FB2_NS)
ET.register_namespace("l", XLINK_NS)


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return data


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def text_blocks(text: str) -> list[str]:
    if not text:
        return []
    return [
        block.strip("\n")
        for block in re.split(r"\n\s*\n", text.strip("\n"))
        if block.strip()
    ]


def figure_name(asset: dict[str, Any]) -> str:
    return str(
        asset.get("label")
        or asset.get("id")
        or Path(str(asset.get("file") or "image")).stem
    )


def all_assets(book: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in book.get("pages", []):
        if isinstance(page, dict):
            rows.extend(
                row for row in page.get("figures", []) if isinstance(row, dict)
            )
    return rows


def verify_book(book: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    pages = book.get("pages")
    if not isinstance(pages, list) or len(pages) != 600:
        raise SystemExit("reader formats require exactly 600 source pages")
    indices = [
        int(page.get("physical_index", -1))
        for page in pages
        if isinstance(page, dict)
    ]
    if indices != list(range(1, 601)):
        raise SystemExit("reader formats require physical page sequence 1..600")
    if book.get("figures", {}).get("total_assets") != 168:
        raise SystemExit("reader formats require exactly 168 canonical assets")
    if int(book.get("figures", {}).get("supplementary_assets", -1)) != 0:
        raise SystemExit("reader formats require all 168 assets to be assigned to source pages")
    if book.get("supplementary_assets") not in ([], None):
        raise SystemExit("reader formats refuse supplementary assets")

    assets = all_assets(book)
    if len(assets) != 168:
        raise SystemExit(f"expected 168 page-linked assets, got {len(assets)}")
    seen: set[str] = set()
    for asset in assets:
        rel = str(asset.get("file") or "")
        if not rel or rel in seen:
            raise SystemExit(f"invalid or duplicate asset path: {rel!r}")
        seen.add(rel)
        path = root / rel
        if not path.is_file():
            raise SystemExit(f"reader asset missing: {path}")
        expected = str(asset.get("sha256") or "")
        if len(expected) != 64 or sha256_file(path) != expected:
            raise SystemExit(f"reader asset hash mismatch: {path}")
    return assets


def insertion_index(
    page: dict[str, Any], asset: dict[str, Any], blocks: list[str]
) -> int:
    """Map fixed source placement to a stable reflow insertion point."""
    if not blocks:
        return 0
    bbox = asset.get("asset_bbox")
    geometry = (
        page.get("source_geometry")
        if isinstance(page.get("source_geometry"), dict)
        else {}
    )
    source_h = int(geometry.get("height") or 0)
    if isinstance(bbox, list) and len(bbox) == 4 and source_h > 0:
        center = (float(bbox[1]) + float(bbox[3])) / 2.0
        ratio = min(1.0, max(0.0, center / source_h))
        return min(len(blocks), max(0, int(round(ratio * len(blocks)))))

    label = str(asset.get("label") or "")
    if label:
        pattern = re.compile(
            rf"(?:\(|\b){re.escape(label)}\s*(?:фиг|ФИГ)", re.IGNORECASE
        )
        for idx, block in enumerate(blocks):
            if pattern.search(block):
                if re.fullmatch(
                    rf"\s*{re.escape(label)}\s*(?:фиг\.?|ФИГ\.?)\s*",
                    block,
                    re.IGNORECASE,
                ):
                    return idx
                return min(len(blocks), idx + 1)
    return len(blocks)


def page_flow(page: dict[str, Any]) -> list[tuple[str, Any]]:
    blocks = text_blocks(str(page.get("text") or ""))
    insertions: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for asset in page.get("figures", []):
        if isinstance(asset, dict):
            insertions[insertion_index(page, asset, blocks)].append(asset)
    for rows in insertions.values():
        rows.sort(
            key=lambda row: (
                (row.get("asset_bbox") or [0, 10**9])[1],
                int(row.get("asset_index") or 0),
                str(row.get("label") or row.get("id") or ""),
            )
        )

    flow: list[tuple[str, Any]] = []
    for idx in range(len(blocks) + 1):
        for asset in insertions.get(idx, []):
            flow.append(("figure", asset))
        if idx < len(blocks):
            flow.append(("text", blocks[idx]))
    return flow


def render_block_html(block: str) -> str:
    return "<p>" + "<br/>".join(html.escape(line) for line in block.splitlines()) + "</p>"


def render_figure_html(
    asset: dict[str, Any], *, src: str | None = None
) -> str:
    release_path = str(asset["file"])
    image_src = src if src is not None else release_path
    name = html.escape(figure_name(asset), quote=True)
    return (
        '<figure class="illustration" '
        f'data-asset="{html.escape(release_path, quote=True)}">'
        f'<img src="{html.escape(image_src, quote=True)}" alt="{name}"/>'
        "</figure>"
    )


def build_html(book: dict[str, Any], version: str) -> str:
    parts = [
        "<!doctype html>",
        '<html lang="ru">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{html.escape(BOOK_TITLE)} - v{html.escape(version)}</title>",
        "<style>",
        "html{background:#fff;color:#111}",
        "body{max-width:48rem;margin:0 auto;padding:1.4rem;font-family:serif;line-height:1.48}",
        ".source-page{margin:0;padding:.2rem 0}",
        ".source-pagebreak{display:block;height:0;overflow:hidden}",
        "p{margin:.65em 0;text-align:justify;hyphens:auto}",
        ".illustration{margin:1.15rem auto;text-align:center;break-inside:avoid}",
        ".illustration img{display:block;max-width:92%;height:auto;margin:0 auto}",
        "</style>",
        "</head><body>",
    ]
    for page in book["pages"]:
        page_id = str(page["id"])
        physical = int(page["physical_index"])
        parts.append(
            f'<section class="source-page" id="{html.escape(page_id, quote=True)}" '
            f'data-physical-index="{physical}">'
        )
        parts.append(
            f'<span class="source-pagebreak" data-source-page="{html.escape(page_id, quote=True)}" '
            f'data-physical-index="{physical}"></span>'
        )
        for kind, value in page_flow(page):
            parts.append(
                render_block_html(value)
                if kind == "text"
                else render_figure_html(value)
            )
        parts.append("</section>")
    parts.extend(["</body></html>", ""])
    return "\n".join(parts)


def zip_info(
    name: str, compress_type: int = zipfile.ZIP_DEFLATED
) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = compress_type
    info.external_attr = 0o644 << 16
    return info


def epub_asset_path(asset: dict[str, Any]) -> str:
    rel = Path(str(asset["file"]))
    if len(rel.parts) < 3 or rel.parts[0] != "figures":
        raise SystemExit(f"unexpected release figure path: {rel}")
    return (Path("images") / Path(*rel.parts[1:])).as_posix()


def build_epub(
    book: dict[str, Any],
    root: Path,
    output: Path,
    version: str,
    assets: list[dict[str, Any]],
) -> None:
    identifier = f"urn:sha256:{book['source']['source_pdf_sha256']}:{book['edition']}"
    content_parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<!DOCTYPE html>',
        f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="{EPUB_NS}" lang="ru">',
        '<head><meta charset="utf-8"/><title>'
        + html.escape(BOOK_TITLE)
        + '</title><link rel="stylesheet" type="text/css" href="style.css"/></head><body>',
    ]
    for page in book["pages"]:
        page_id = str(page["id"])
        physical = int(page["physical_index"])
        content_parts.append(f'<section class="source-page" id="{page_id}">')
        content_parts.append(
            f'<span class="source-pagebreak" epub:type="pagebreak" role="doc-pagebreak" '
            f'id="page-{physical}" aria-label="{physical}" data-source-page="{page_id}"></span>'
        )
        for kind, value in page_flow(page):
            if kind == "text":
                content_parts.append(render_block_html(value))
            else:
                content_parts.append(
                    render_figure_html(value, src=epub_asset_path(value))
                )
        content_parts.append("</section>")
    content_parts.append("</body></html>")
    content_xhtml = "\n".join(content_parts)

    nav = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<!DOCTYPE html>',
        f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="{EPUB_NS}" lang="ru">',
        "<head><title>Навигация</title></head><body>",
        '<nav epub:type="toc" id="toc"><h1>Содержание</h1><ol>'
        '<li><a href="content.xhtml">Практическая гимнастика</a></li>'
        "</ol></nav>",
        '<nav epub:type="page-list" id="pages"><h2>Исходные страницы</h2><ol>',
    ]
    for page in book["pages"]:
        physical = int(page["physical_index"])
        nav.append(
            f'<li><a href="content.xhtml#page-{physical}">{physical}</a></li>'
        )
    nav.extend(["</ol></nav></body></html>"])
    nav_xhtml = "\n".join(nav)

    image_items: list[str] = []
    for idx, asset in enumerate(
        sorted(assets, key=lambda row: str(row["file"])), 1
    ):
        href = epub_asset_path(asset)
        image_items.append(
            f'<item id="img{idx}" href="{html.escape(href, quote=True)}" media-type="image/png"/>'
        )

    opf = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="ru">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{html.escape(identifier)}</dc:identifier>
    <dc:title>{html.escape(BOOK_TITLE)}</dc:title>
    <dc:creator>{html.escape(BOOK_AUTHOR)}</dc:creator>
    <dc:language>ru</dc:language>
    <meta property="dcterms:modified">1980-01-01T00:00:00Z</meta>
    <meta property="corpus-motuum:version">{html.escape(version)}</meta>
  </metadata>
  <manifest>
    <item id="content" href="content.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="css" href="style.css" media-type="text/css"/>
    {''.join(image_items)}
  </manifest>
  <spine><itemref idref="content"/></spine>
</package>'''
    container_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/package.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>'''
    css = (
        "body{font-family:serif;line-height:1.45;margin:5%;}"
        "p{text-align:justify;margin:.65em 0;}"
        "figure{text-align:center;margin:1em auto;page-break-inside:avoid;break-inside:avoid;}"
        "img{max-width:92%;height:auto;}"
        ".source-pagebreak{display:block;height:0;overflow:hidden;}"
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w") as zf:
        zf.writestr(
            zip_info("mimetype", zipfile.ZIP_STORED), b"application/epub+zip"
        )
        zf.writestr(
            zip_info("META-INF/container.xml"), container_xml.encode("utf-8")
        )
        zf.writestr(zip_info("OEBPS/package.opf"), opf.encode("utf-8"))
        zf.writestr(
            zip_info("OEBPS/content.xhtml"), content_xhtml.encode("utf-8")
        )
        zf.writestr(zip_info("OEBPS/nav.xhtml"), nav_xhtml.encode("utf-8"))
        zf.writestr(zip_info("OEBPS/style.css"), css.encode("utf-8"))
        for asset in sorted(assets, key=lambda row: str(row["file"])):
            zf.writestr(
                zip_info("OEBPS/" + epub_asset_path(asset)),
                (root / str(asset["file"])).read_bytes(),
            )


def image_id(asset: dict[str, Any]) -> str:
    return "img-" + re.sub(r"[^A-Za-z0-9_-]+", "-", str(asset["file"]))


def build_fb2(
    book: dict[str, Any], root: Path, output: Path, assets: list[dict[str, Any]]
) -> None:
    q = lambda name: f"{{{FB2_NS}}}{name}"
    root_xml = ET.Element(q("FictionBook"))
    description = ET.SubElement(root_xml, q("description"))
    title_info = ET.SubElement(description, q("title-info"))
    ET.SubElement(title_info, q("genre")).text = "antique"
    author = ET.SubElement(title_info, q("author"))
    ET.SubElement(author, q("first-name")).text = "Наполеон"
    ET.SubElement(author, q("last-name")).text = "Лэнэ"
    ET.SubElement(title_info, q("book-title")).text = BOOK_TITLE
    annotation = ET.SubElement(title_info, q("annotation"))
    ET.SubElement(annotation, q("p")).text = BOOK_SUBTITLE
    ET.SubElement(title_info, q("lang")).text = "ru"

    document_info = ET.SubElement(description, q("document-info"))
    ET.SubElement(document_info, q("program-used")).text = (
        "corpus-motuum deterministic reader build"
    )
    doc_author = ET.SubElement(document_info, q("author"))
    ET.SubElement(doc_author, q("nickname")).text = "corpus-motuum"
    ET.SubElement(document_info, q("id")).text = (
        f"{book['source']['source_pdf_sha256']}:{book['edition']}"
    )
    ET.SubElement(document_info, q("version")).text = "1.0"

    body = ET.SubElement(root_xml, q("body"))
    for page in book["pages"]:
        section = ET.SubElement(body, q("section"), {"id": str(page["id"])})
        for kind, value in page_flow(page):
            if kind == "text":
                ET.SubElement(section, q("p")).text = "\n".join(
                    value.splitlines()
                )
            else:
                ET.SubElement(
                    section,
                    q("image"),
                    {f"{{{XLINK_NS}}}href": "#" + image_id(value)},
                )

    for asset in sorted(assets, key=lambda row: str(row["file"])):
        binary = ET.SubElement(
            root_xml,
            q("binary"),
            {"id": image_id(asset), "content-type": "image/png"},
        )
        binary.text = base64.b64encode(
            (root / str(asset["file"])).read_bytes()
        ).decode("ascii")

    output.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root_xml).write(output, encoding="utf-8", xml_declaration=True)


def find_font(bold: bool = False) -> str:
    candidates = (
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSerif-Bold.ttf",
        ]
        if bold
        else [
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
        ]
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    raise SystemExit("no Cyrillic-capable system serif font found")


def register_fonts() -> None:
    if "CMSerif" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("CMSerif", find_font(False)))
    if "CMSerifBold" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("CMSerifBold", find_font(True)))


def pdf_rect(
    page: dict[str, Any],
    asset: dict[str, Any],
    page_w: float,
    page_h: float,
    margin: float,
) -> tuple[float, float, float, float] | None:
    bbox = asset.get("asset_bbox")
    geometry = (
        page.get("source_geometry")
        if isinstance(page.get("source_geometry"), dict)
        else {}
    )
    sw, sh = int(geometry.get("width") or 0), int(geometry.get("height") or 0)
    if not (
        isinstance(bbox, list)
        and len(bbox) == 4
        and sw > 0
        and sh > 0
    ):
        return None
    x1, y1, x2, y2 = (float(v) for v in bbox)
    cw, ch = page_w - 2 * margin, page_h - 2 * margin
    x = margin + (x1 / sw) * cw
    top = page_h - margin - (y1 / sh) * ch
    w = max(1.0, ((x2 - x1) / sw) * cw)
    h = max(1.0, ((y2 - y1) / sh) * ch)
    return x, top - h, w, h


def fallback_pdf_rect(
    page: dict[str, Any],
    asset: dict[str, Any],
    root: Path,
    page_w: float,
    page_h: float,
    margin: float,
) -> tuple[float, float, float, float]:
    with Image.open(root / str(asset["file"])) as image:
        aspect = image.width / max(1, image.height)
    width = min(page_w * 0.48, page_w - 2 * margin)
    height = width / max(aspect, 0.05)
    if height > page_h * 0.34:
        height = page_h * 0.34
        width = height * aspect
    blocks = text_blocks(str(page.get("text") or ""))
    idx = insertion_index(page, asset, blocks)
    ratio = 0.5 if not blocks else idx / max(1, len(blocks))
    center_y = page_h - margin - ratio * (page_h - 2 * margin)
    y = max(
        margin,
        min(page_h - margin - height, center_y - height / 2),
    )
    x = (page_w - width) / 2
    return x, y, width, height


def available_interval(
    y: float,
    leading: float,
    blockers: list[tuple[float, float, float, float]],
    page_w: float,
    margin: float,
) -> tuple[float, float] | None:
    intervals = [(margin, page_w - margin)]
    band_bottom, band_top = y - leading * 0.25, y + leading * 0.8
    for x, by, bw, bh in blockers:
        if by >= band_top or by + bh <= band_bottom:
            continue
        bx1, bx2 = x - 4.0, x + bw + 4.0
        next_intervals: list[tuple[float, float]] = []
        for a, b in intervals:
            if bx2 <= a or bx1 >= b:
                next_intervals.append((a, b))
            else:
                if a < bx1:
                    next_intervals.append((a, max(a, bx1)))
                if bx2 < b:
                    next_intervals.append((min(b, bx2), b))
        intervals = [
            (a, b) for a, b in next_intervals if b - a >= 54
        ]
        if not intervals:
            return None
    return max(intervals, key=lambda pair: pair[1] - pair[0]) if intervals else None


def is_heading(line: str) -> bool:
    letters = [ch for ch in line if ch.isalpha()]
    return (
        bool(letters)
        and len(line) <= 95
        and sum(ch.isupper() for ch in letters) / len(letters) > 0.82
    )


def layout_pdf_text(
    page: dict[str, Any],
    blockers: list[tuple[float, float, float, float]],
    page_w: float,
    page_h: float,
    margin: float,
    font_size: float,
) -> list[tuple[str, float, float, str, float]] | None:
    leading = font_size * 1.28
    y = page_h - margin - font_size
    result: list[tuple[str, float, float, str, float]] = []
    blocks = text_blocks(str(page.get("text") or ""))
    for bi, block in enumerate(blocks):
        if bi:
            y -= leading * 0.48
        logical_lines = block.splitlines() or [""]
        for logical in logical_lines:
            words = logical.split()
            if not words:
                y -= leading * 0.55
                continue
            pos = 0
            while pos < len(words):
                attempts = 0
                interval = available_interval(
                    y, leading, blockers, page_w, margin
                )
                while (
                    interval is None
                    and y > margin + leading
                    and attempts < 200
                ):
                    y -= leading
                    interval = available_interval(
                        y, leading, blockers, page_w, margin
                    )
                    attempts += 1
                if interval is None or y < margin + leading * 0.35:
                    return None
                x1, x2 = interval
                font_name = "CMSerifBold" if is_heading(logical) else "CMSerif"
                size = font_size * (0.93 if font_name.endswith("Bold") else 1.0)
                current: list[str] = []
                while pos < len(words):
                    candidate = " ".join(current + [words[pos]])
                    if (
                        current
                        and pdfmetrics.stringWidth(
                            candidate, font_name, size
                        )
                        > x2 - x1
                    ):
                        break
                    if (
                        not current
                        and pdfmetrics.stringWidth(
                            candidate, font_name, size
                        )
                        > x2 - x1
                    ):
                        current.append(words[pos])
                        pos += 1
                        break
                    current.append(words[pos])
                    pos += 1
                result.append(
                    (font_name, x1, y, " ".join(current), size)
                )
                y -= leading
    return result


def build_pdf(
    book: dict[str, Any], root: Path, output: Path
) -> dict[str, Any]:
    register_fonts()
    page_w, page_h = 450.0, 720.0
    margin = 28.0
    output.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(
        str(output),
        pagesize=(page_w, page_h),
        pageCompression=1,
        invariant=1,
    )
    c.setTitle(BOOK_TITLE)
    c.setAuthor(BOOK_AUTHOR)
    fallback_assets: list[str] = []
    fallback_pages: list[str] = []
    minimum_font = 99.0

    for page in book["pages"]:
        page_id = str(page["id"])
        c.bookmarkPage(page_id)
        rects: list[
            tuple[dict[str, Any], tuple[float, float, float, float]]
        ] = []
        for asset in page.get("figures", []):
            if not isinstance(asset, dict):
                continue
            rect = pdf_rect(page, asset, page_w, page_h, margin)
            if rect is None:
                rect = fallback_pdf_rect(
                    page, asset, root, page_w, page_h, margin
                )
                fallback_assets.append(
                    str(
                        asset.get("label")
                        or asset.get("id")
                        or asset.get("file")
                    )
                )
                fallback_pages.append(page_id)
            rects.append((asset, rect))
        blockers = [rect for _, rect in rects]

        layout = None
        chosen = None
        for size in (
            10.0,
            9.5,
            9.0,
            8.5,
            8.0,
            7.5,
            7.0,
            6.5,
            6.0,
            5.5,
        ):
            layout = layout_pdf_text(
                page, blockers, page_w, page_h, margin, size
            )
            if layout is not None:
                chosen = size
                break
        if layout is None or chosen is None:
            raise SystemExit(f"PDF text does not fit source page {page_id}")
        minimum_font = min(minimum_font, chosen)
        for font_name, x, y, text, size in layout:
            c.setFont(font_name, size)
            c.drawString(x, y, text)

        for asset, (x, y, w, h) in rects:
            c.drawImage(
                ImageReader(str(root / str(asset["file"]))),
                x,
                y,
                width=w,
                height=h,
                preserveAspectRatio=True,
                anchor="c",
                mask="auto",
            )
        c.showPage()
    c.save()
    return {
        "pages": 600,
        "fallback_placements": sorted(set(fallback_assets)),
        "fallback_pages": sorted(set(fallback_pages)),
        "minimum_font_size": minimum_font,
        "page_size_points": [page_w, page_h],
    }


def build_txt(book: dict[str, Any], output: Path) -> None:
    output.write_text(
        "\n\f\n".join(str(page.get("text") or "") for page in book["pages"])
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--book", type=Path, required=True)
    ap.add_argument(
        "--root",
        type=Path,
        required=True,
        help="Package root containing book.json and figures/.",
    )
    ap.add_argument("--version", required=True)
    args = ap.parse_args()

    book = load_json(args.book)
    assets = verify_book(book, args.root)
    html_path = args.root / "book.html"
    epub_path = args.root / "book.epub"
    fb2_path = args.root / "book.fb2"
    pdf_path = args.root / "book.pdf"
    txt_path = args.root / "book.txt"

    html_path.write_text(build_html(book, args.version), encoding="utf-8")
    build_epub(book, args.root, epub_path, args.version, assets)
    build_fb2(book, args.root, fb2_path, assets)
    pdf_report = build_pdf(book, args.root, pdf_path)
    build_txt(book, txt_path)

    report = {
        "schema": "corpus-motuum-reader-formats-v1",
        "version": args.version,
        "edition": book.get("edition"),
        "pages": 600,
        "canonical_assets": 168,
        "formats": {
            "html": {
                "file": "book.html",
                "sha256": sha256_file(html_path),
            },
            "epub3": {
                "file": "book.epub",
                "sha256": sha256_file(epub_path),
            },
            "fb2": {
                "file": "book.fb2",
                "sha256": sha256_file(fb2_path),
            },
            "pdf": {
                "file": "book.pdf",
                "sha256": sha256_file(pdf_path),
                **pdf_report,
            },
            "txt": {
                "file": "book.txt",
                "sha256": sha256_file(txt_path),
            },
        },
    }
    (args.root / "format-manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
