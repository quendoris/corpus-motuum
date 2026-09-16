#!/usr/bin/env python3
"""Package a corpus-motuum 1.x edition with reader formats and equivalence QA."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any, Iterable

EXPECTED_VERSIONS = {"diplomatic": "1.0.0", "normalized": "1.1.0"}


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


def git_head() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def iter_assets(book: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for page in book.get("pages", []):
        if not isinstance(page, dict):
            continue
        for row in page.get("figures", []):
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
            raise SystemExit(f"duplicate or invalid figure in book payload: {rel!r}")
        seen.add(rel)
        expected = str(asset.get("sha256") or "")
        source = source_asset_path(figure_root, asset)
        if not source.is_file():
            raise SystemExit(f"canonical figure missing: {source}")
        if sha256_file(source) != expected:
            raise SystemExit(f"canonical figure hash mismatch: {source}")
        destination = package_root / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        if sha256_file(destination) != expected:
            raise SystemExit(f"copied figure hash mismatch: {destination}")
        count += 1
    return count


def write_sha256s(root: Path) -> None:
    rows: list[str] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.name == "SHA256SUMS":
            continue
        rows.append(f"{sha256_file(path)}  {path.relative_to(root).as_posix()}")
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
        info = tarfile.TarInfo(f"{package_name}/{rel}")
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
            raise SystemExit(f"unsupported release entry: {path}")


def write_deterministic_tar_gz(root: Path, archive: Path) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    with archive.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
                add_to_tar(tar, root, root.name)


def run_tool(script: Path, *args: str) -> None:
    subprocess.run([sys.executable, str(script), *args], check=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--book", type=Path, required=True)
    ap.add_argument("--figure-root", type=Path, required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--out-dir", type=Path, default=Path("dist"))
    ap.add_argument(
        "--publication",
        action="store_true",
        help="Fail if any fixed-layout placement still requires manual visual review.",
    )
    args = ap.parse_args()

    book = load_json(args.book)
    edition = str(book.get("edition") or "")
    expected_version = EXPECTED_VERSIONS.get(edition)
    if expected_version is None or args.version != expected_version:
        raise SystemExit(
            f"edition/version mismatch: {edition!r} expects {expected_version!r}, got {args.version!r}"
        )
    if len(book.get("pages", [])) != 600:
        raise SystemExit("release book must contain exactly 600 canonical pages")
    if book.get("figures", {}).get("total_assets") != 168:
        raise SystemExit("release book must contain exactly 168 canonical assets")
    if book.get("figures", {}).get("linked_assets") != 168:
        raise SystemExit("release book must have 168/168 assets linked to source pages")
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

    copied = copy_assets(book, args.figure_root, package_root)
    if copied != 168:
        raise SystemExit(f"release package copied {copied} assets, expected 168")

    tool_root = Path(__file__).resolve().parent
    run_tool(
        tool_root / "build_reader_formats.py",
        "--book",
        str(package_root / "book.json"),
        "--root",
        str(package_root),
        "--version",
        args.version,
    )
    validator_args = [
        "--book",
        str(package_root / "book.json"),
        "--root",
        str(package_root),
        "--report",
        str(package_root / "release-equivalence.json"),
    ]
    if args.publication:
        validator_args.append("--publication")
    run_tool(tool_root / "validate_reader_formats.py", *validator_args)

    formats = load_json(package_root / "format-manifest.json")
    equivalence = load_json(package_root / "release-equivalence.json")
    release_manifest = {
        "schema": "corpus-motuum-release-v1",
        "version": args.version,
        "edition": edition,
        "text_layer": book.get("text_layer"),
        "principle": book.get("principle"),
        "source_commit": git_head(),
        "source_pdf_sha256": book.get("source", {}).get("source_pdf_sha256"),
        "source_page_manifest_sha256": book.get("source", {}).get("page_manifest_sha256"),
        "canonical_pages": 600,
        "canonical_figures": book.get("figures", {}).get("counts"),
        "canonical_figure_total": copied,
        "reader_formats": formats.get("formats"),
        "equivalence_report": "release-equivalence.json",
        "publication_ready": bool(equivalence.get("publication_ready")),
        "book_json_sha256": sha256_file(package_root / "book.json"),
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
                "formats": ["json", "html", "epub3", "fb2", "pdf", "txt"],
                "publication_ready": release_manifest["publication_ready"],
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
