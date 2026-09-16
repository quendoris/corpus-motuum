#!/usr/bin/env python3
"""Import a local figure-review snapshot into durable repository history.

The source directory is copied byte-for-byte into history/figures. Files larger
than the configured threshold can be registered as exact-path Git LFS entries;
smaller files remain normal Git objects. A separate snapshot manifest records
SHA-256 and size for every imported source file without altering the original
manifest.json or removed.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

MIB = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(MIB), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_root() -> Path:
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(proc.stdout.strip()).resolve()


def exact_lfs_track(repo: Path, path: Path) -> None:
    rel = path.resolve().relative_to(repo).as_posix()
    subprocess.run(["git", "lfs", "track", rel], cwd=repo, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("work/figure-review-v1"),
        help="local review tree to preserve",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path("history/figures/figure-review-v1"),
        help="durable repository destination",
    )
    parser.add_argument(
        "--lfs-threshold-mib",
        type=float,
        default=5.0,
        help="individual files strictly larger than this use Git LFS",
    )
    parser.add_argument(
        "--track-lfs",
        action="store_true",
        help="run git lfs track for each large file using an exact path",
    )
    args = parser.parse_args()

    repo = git_root()
    source = (repo / args.source).resolve() if not args.source.is_absolute() else args.source.resolve()
    destination = (
        (repo / args.destination).resolve()
        if not args.destination.is_absolute()
        else args.destination.resolve()
    )

    if not source.is_dir():
        raise SystemExit(f"source directory does not exist: {source}")
    try:
        destination.relative_to(repo)
    except ValueError as exc:
        raise SystemExit("destination must be inside the repository") from exc
    if destination == source:
        raise SystemExit("source and destination must be different")

    required = ("atlas", "numbered", "paratext")
    missing = [name for name in required if not (source / name).is_dir()]
    missing += [
        name
        for name in ("manifest.json", "removed.json")
        if not (source / name).is_file()
    ]
    if missing:
        raise SystemExit(f"incomplete figure-review snapshot; missing: {', '.join(missing)}")

    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, copy_function=shutil.copy2)

    threshold = int(args.lfs_threshold_mib * MIB)
    rows: list[dict[str, object]] = []
    large_files: list[Path] = []
    for path in sorted(p for p in destination.rglob("*") if p.is_file()):
        rel = path.relative_to(destination).as_posix()
        size = path.stat().st_size
        storage = "lfs" if size > threshold else "git"
        rows.append(
            {
                "path": rel,
                "bytes": size,
                "sha256": sha256_file(path),
                "storage": storage,
            }
        )
        if storage == "lfs":
            large_files.append(path)

    manifest = {
        "schema": "corpus-motuum-figure-review-history-v1",
        "source": args.source.as_posix(),
        "destination": destination.relative_to(repo).as_posix(),
        "principle": "Preserve the complete review snapshot, including removed and unsuccessful evidence.",
        "lfs_threshold_bytes": threshold,
        "files": len(rows),
        "bytes": sum(int(row["bytes"]) for row in rows),
        "lfs_files": len(large_files),
        "records": rows,
    }
    (destination / "snapshot-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if args.track_lfs:
        subprocess.run(["git", "lfs", "install", "--local"], cwd=repo, check=True)
        for path in large_files:
            exact_lfs_track(repo, path)

    print(
        json.dumps(
            {
                "destination": destination.relative_to(repo).as_posix(),
                "files": len(rows),
                "bytes": manifest["bytes"],
                "lfs_files": len(large_files),
                "lfs_threshold_bytes": threshold,
                "lfs_tracking_applied": bool(args.track_lfs),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
