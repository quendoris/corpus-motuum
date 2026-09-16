#!/usr/bin/env python3
"""Package a corpus-motuum 1.x book payload into a reproducible release archive."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Any, Iterable

EXPECTED_VERSIONS = {
    "diplomatic": "1.0.0",
    "normalized": "1.1.0",
}


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return data


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_head() -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return proc.stdout.strip() or None


def iter_assets(book: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for page in book.get("pages", []):
        if isinstance(page, dict):
            for row in page.get("figures", []):
                if isinstance(row, dict):
                    yield row
    for row in book.get("supplementary_assets", []):
        if isinstance(row, dict):
            yield row


def source_asset_path(figure_root: Path, asset: dict[str, Any]) -> Path:
    kind = str(asset.get("kind") or "")
    release_path = Path(str(asset.get("file") or ""))
    if kind not in {"numbered", "atlas", "paratext"}:
        raise SystemExit(f"invalid figure kind: {kind!r}")
    if not release_path.name:
        raise SystemExit(f"figure has no release filename: {asset}")
    return figure_root / kind / release_path.name


def copy_assets(book: dict[str, Any], figure_root: Path, package_root: Path) -> int:
    seen: set[str] = set()
    count = 0
    for asset in iter_assets(book):
        rel = str(asset.get("file") or "")
        if not rel or rel in seen:
            if rel in seen:
                raise SystemExit(f"duplicate figure in book payload: {rel}")
            raise SystemExit("figure without release path")
        seen.add(rel)

        expected = str(asset.get("sha256") or "")
        source = source_asset_path(figure_root, asset)
        if not source.is_file():
            raise SystemExit(f"canonical figure missing: {source}")
        actual = sha256_file(source)
        if actual != expected:
            raise SystemExit(
                f"canonical figure hash mismatch: {source}: expected {expected}, got {actual}"
            )

        destination = package_root / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        if sha256_file(destination) != expected:
            raise SystemExit(f"copied figure hash mismatch: {destination}")
        count += 1
    return count


def asset_html(asset: dict[str, Any]) -> str:
    path = html.escape(str(asset.get("file") or ""), quote=True)
    label = asset.get("label") or asset.get("id") or "illustration"
    caption = html.escape(str(label))
    return (
        '<figure class="illustration">'
        f'<img src="{path}" alt="{caption}" loading="lazy">'
        f'<figcaption>{caption}</figcaption>'
        "</figure>"
    )


def build_html(book: dict[str, Any], version: str) -> str:
    edition = str(book.get("edition") or "")
    title = "Corpus Motuum — digital edition"
    parts = [
        "<!doctype html>",
        '<html lang="ru">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{html.escape(title)} v{html.escape(version)}</title>",
        "<style>",
        "body{max-width:880px;margin:0 auto;padding:2rem;font-family:serif;line-height:1.45}",
        ".release-meta{font-family:sans-serif;color:#444;border-bottom:1px solid #bbb;padding-bottom:1rem}",
        ".page{padding:2rem 0;border-bottom:1px solid #ddd}",
        ".page-meta{font:0.85rem sans-serif;color:#666;margin-bottom:1rem}",
        ".page-text{white-space:pre-wrap;font:inherit;margin:0}",
        ".illustration{text-align:center;margin:1.5rem auto}",
        ".illustration img{max-width:100%;height:auto}",
        ".illustration figcaption{font-size:.9rem;color:#555}",
        "</style>",
        "</head>",
        "<body>",
        '<header class="release-meta">',
        f"<h1>{html.escape(title)}</h1>",
        f"<p>Version {html.escape(version)} · edition {html.escape(edition)} · 600 physical pages.</p>",
        "</header>",
    ]

    pages = book.get("pages", [])
    if not isinstance(pages, list) or len(pages) != 600:
        raise SystemExit(f"HTML build expects 600 pages, got {len(pages) if isinstance(pages, list) else 'invalid'}")

    for page in pages:
        if not isinstance(page, dict):
            raise SystemExit("invalid page record")
        page_id = str(page.get("id") or "")
        physical_index = int(page.get("physical_index"))
        text = str(page.get("text") or "")
        parts.extend(
            [
                f'<article class="page" id="{html.escape(page_id, quote=True)}" data-physical-index="{physical_index}">',
                f'<div class="page-meta">Physical page {physical_index} · {html.escape(page_id)}</div>',
                f'<div class="page-text">{html.escape(text)}</div>',
            ]
        )
        figures = page.get("figures", [])
        if isinstance(figures, list):
            parts.extend(asset_html(row) for row in figures if isinstance(row, dict))
        parts.append("</article>")

    # Assets whose provenance does not resolve to exactly one Russian physical
    # page remain packaged and checksummed, but are not inserted at an invented
    # position in this faithful page-order HTML view.
    parts.extend(["</body>", "</html>", ""])
    return "\n".join(parts)


def write_sha256s(root: Path) -> None:
    rows: list[str] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.name == "SHA256SUMS":
            continue
        rel = path.relative_to(root).as_posix()
        rows.append(f"{sha256_file(path)}  {rel}")
    (root / "SHA256SUMS").write_text("\n".join(rows) + "\n", encoding="utf-8")


def add_to_tar(tar: tarfile.TarFile, root: Path, package_name: str) -> None:
    directory_info = tarfile.TarInfo(package_name)
    directory_info.type = tarfile.DIRTYPE
    directory_info.mode = 0o755
    directory_info.mtime = 0
    directory_info.uid = directory_info.gid = 0
    directory_info.uname = directory_info.gname = ""
    tar.addfile(directory_info)

    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        rel = path.relative_to(root).as_posix()
        name = f"{package_name}/{rel}"
        info = tarfile.TarInfo(name)
        info.mtime = 0
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        if path.is_dir():
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            tar.addfile(info)
        elif path.is_file():
            info.size = path.stat().st_size
            info.mode = 0o644
            with path.open("rb") as handle:
                tar.addfile(info, handle)
        else:
            raise SystemExit(f"unsupported release filesystem entry: {path}")


def write_deterministic_tar_gz(root: Path, archive: Path) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    with archive.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
                add_to_tar(tar, root, root.name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", type=Path, required=True)
    parser.add_argument("--figure-root", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()

    book = load_json(args.book)
    edition = str(book.get("edition") or "")
    expected_version = EXPECTED_VERSIONS.get(edition)
    if expected_version is None:
        raise SystemExit(f"unsupported 1.x edition: {edition}")
    if args.version != expected_version:
        raise SystemExit(
            f"edition/version mismatch: {edition} must be {expected_version}, got {args.version}"
        )
    if book.get("figures", {}).get("total_assets") != 168:
        raise SystemExit("release book must account for exactly 168 canonical assets")

    suffix = "prereform" if edition == "diplomatic" else "normalized"
    package_name = f"corpus-motuum-v{args.version}-{suffix}"
    package_root = args.out_dir / package_name
    archive = args.out_dir / f"{package_name}.tar.gz"

    if package_root.exists():
        shutil.rmtree(package_root)
    package_root.mkdir(parents=True)

    shutil.copyfile(args.book, package_root / "book.json")
    copied = copy_assets(book, args.figure_root, package_root)
    if copied != 168:
        raise SystemExit(f"release package must contain 168 figures, copied {copied}")

    (package_root / "book.html").write_text(
        build_html(book, args.version), encoding="utf-8"
    )

    release_manifest = {
        "schema": "corpus-motuum-release-v1",
        "version": args.version,
        "edition": edition,
        "text_layer": book.get("text_layer"),
        "principle": book.get("principle"),
        "source_commit": git_head(),
        "source_pdf_sha256": book.get("source", {}).get("source_pdf_sha256"),
        "source_page_manifest_sha256": book.get("source", {}).get("page_manifest_sha256"),
        "canonical_pages": len(book.get("pages", [])),
        "canonical_figures": book.get("figures", {}).get("counts"),
        "canonical_figure_total": copied,
        "book_json_sha256": sha256_file(package_root / "book.json"),
        "book_html_sha256": sha256_file(package_root / "book.html"),
    }
    (package_root / "release-manifest.json").write_text(
        json.dumps(release_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_sha256s(package_root)
    write_deterministic_tar_gz(package_root, archive)

    print(
        json.dumps(
            {
                "version": args.version,
                "edition": edition,
                "pages": 600,
                "figures": copied,
                "directory": package_root.as_posix(),
                "archive": archive.as_posix(),
                "archive_sha256": sha256_file(archive),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
