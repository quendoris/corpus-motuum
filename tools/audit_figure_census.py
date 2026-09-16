#!/usr/bin/env python3
"""Cross-check the full-book figure probe against editorial page metadata.

This is a review aid, not a segmentation accuracy metric.  Explicit `N ФИГ.`
captions are strong indexing evidence, but caption count is not assumed to equal
logical asset count: one numbered illustration can contain disconnected parts,
and atlas plates can contain many uncaptioned diagrams.

The audit joins three independent views of a page:

* physical/editorial identity from `corpus/text/pages/*.json`;
* detected assets from the full-book probe `index.json` / `*.metrics.json`;
* per-asset geometry (bbox, edge contact, text-area fraction) from metrics.

It writes JSON, CSV and a compact Markdown triage report.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

CAPTION_RE = re.compile(r"(?mi)^\s*(\d+)\s+ФИГ\.\s*$")
MENTION_RE = re.compile(r"(?iu)(\d+)\s*фиг\.")
ATLAS_RE = re.compile(r"(?iu)^\s*Таб(?:л)?\.?\s*[IVXLC0-9]+")

TITLE_TYPES = {"title", "half-title", "cover-title"}


def page_id_from_source(source: str) -> str:
    return Path(source).stem


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def unique_ints(values: list[str]) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for raw in values:
        value = int(raw)
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def asset_geometry(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    width = max(1, int(metrics.get("width", 1)))
    height = max(1, int(metrics.get("height", 1)))
    out: list[dict[str, Any]] = []
    for fallback_index, asset in enumerate(metrics.get("assets", []), start=1):
        x1, y1, x2, y2 = [int(v) for v in asset.get("bbox", [0, 0, 0, 0])]
        bw = max(0, x2 - x1)
        bh = max(0, y2 - y1)
        area_fraction = (bw * bh) / (width * height)
        edge_margin_x = max(2, round(width * 0.01))
        edge_margin_y = max(2, round(height * 0.01))
        touches_edge = (
            x1 <= edge_margin_x
            or y1 <= edge_margin_y
            or x2 >= width - edge_margin_x
            or y2 >= height - edge_margin_y
        )
        long_side = max(bw, bh, 1)
        short_side = max(min(bw, bh), 1)
        aspect = long_side / short_side
        region = asset.get("region", {})
        out.append({
            "asset_index": int(asset.get("asset_index", fallback_index)),
            "bbox": [x1, y1, x2, y2],
            "bbox_width": bw,
            "bbox_height": bh,
            "bbox_area_fraction": round(area_fraction, 6),
            "aspect_ratio_long_over_short": round(aspect, 3),
            "touches_page_edge_1pct": bool(touches_edge),
            "text_area_fraction": region.get("text_area_fraction"),
            "max_seed_score": region.get("max_seed_score"),
        })
    return out


def classify(record: dict[str, Any]) -> tuple[list[str], int]:
    flags: list[str] = []
    score = 0
    assets = int(record["detected_asset_count"])
    captions = len(record["caption_figure_numbers"])
    page_type = record.get("page_type") or "unknown"
    atlas = bool(record["atlas_evidence"])
    geometry = record["assets"]

    if record["page_class"] != "normal-text-candidate":
        flags.append("DOMAIN_UNSUPPORTED")
        score += 8

    if page_type in TITLE_TYPES and assets:
        flags.append("TITLE_TYPOGRAPHY_FALSE_POSITIVE_RISK")
        score += min(10, 3 + assets)

    if page_type == "cover" and assets:
        # Covers may contain genuine illustrations and large display type.
        flags.append("COVER_MIXED_CONTENT_REVIEW")
        score += 4

    if atlas:
        flags.append("ATLAS_MULTI_DIAGRAM")
        # Atlas counts are not comparable to caption counts.
    else:
        if captions and assets == 0:
            flags.append("CAPTIONED_FIGURE_MISSED")
            score += 12
        elif captions and assets > captions:
            flags.append("ASSET_COUNT_GT_CAPTION_INDEX")
            score += min(10, 2 + (assets - captions) * 2)
        elif captions and assets < captions:
            flags.append("ASSET_COUNT_LT_CAPTION_INDEX")
            score += min(10, 4 + (captions - assets) * 2)
        elif not captions and assets and page_type == "body":
            flags.append("UNCAPTIONED_ASSETS_REVIEW")
            score += min(8, 2 + assets)

    thin_edge = [
        a for a in geometry
        if a["touches_page_edge_1pct"]
        and a["aspect_ratio_long_over_short"] >= 8.0
        and a["bbox_area_fraction"] <= 0.08
    ]
    if thin_edge:
        flags.append("EDGE_RULE_ARTIFACT_RISK")
        score += min(8, 2 + len(thin_edge) * 2)

    if assets >= 5:
        flags.append("EXTREME_ASSET_COUNT")
        score += 4
    elif assets >= 3:
        flags.append("HIGH_ASSET_COUNT")
        score += 2

    notes = " ".join(record.get("notes", []))
    if assets and re.search(r"(?iu)штамп", notes):
        flags.append("PROVENANCE_MARK_FALSE_POSITIVE_RISK")
        score += 6
    if re.search(r"(?iu)просвеч|просвет|bleed", notes):
        flags.append("RESTORE_BLEED_THROUGH_REFERENCE")
        score += 2

    return flags, score


def markdown_report(records: list[dict[str, Any]], index_path: Path) -> str:
    flag_counts = Counter(flag for r in records for flag in r["flags"])
    asset_hist = Counter(int(r["detected_asset_count"]) for r in records)
    classes = Counter(r["page_class"] for r in records)
    ranked = sorted(records, key=lambda r: (-int(r["review_score"]), int(r["physical_index"])))

    lines = [
        "# Full-book figure census audit",
        "",
        "> This is a triage/index cross-check, not an accuracy score. Caption count is evidence, not a logical-asset ground truth.",
        "",
        f"Source index: `{index_path}`",
        "",
        "## Corpus summary",
        "",
        f"- pages: {len(records)}",
        f"- detected assets total: {sum(int(r['detected_asset_count']) for r in records)}",
        f"- pages with >=1 detected asset: {sum(bool(r['detected_asset_count']) for r in records)}",
        f"- pages with explicit figure captions: {sum(bool(r['caption_figure_numbers']) for r in records)}",
        f"- atlas / plate pages identified from editorial metadata: {sum(bool(r['atlas_evidence']) for r in records)}",
        "",
        "### Page classes",
        "",
    ]
    for key, value in classes.most_common():
        lines.append(f"- `{key}`: {value}")
    lines += ["", "### Detected assets per page", ""]
    for key in sorted(asset_hist):
        lines.append(f"- `{key}`: {asset_hist[key]}")
    lines += ["", "### Review flags", ""]
    for key, value in flag_counts.most_common():
        lines.append(f"- `{key}`: {value}")

    lines += [
        "",
        "## Ranked page/index review",
        "",
        "|score|physical|page id|type|canonical figure indices|detected|flags|",
        "|---:|---:|---|---|---|---:|---|",
    ]
    for r in ranked:
        if int(r["review_score"]) <= 0:
            continue
        figures = ", ".join(str(x) for x in r["caption_figure_numbers"]) or "—"
        flags = ", ".join(r["flags"]) or "—"
        lines.append(
            f"|{r['review_score']}|{r['physical_index']}|`{r['id']}`|{r['page_type']}|{figures}|{r['detected_asset_count']}|{flags}|"
        )

    lines += [
        "",
        "## Interpretation rules",
        "",
        "- `N ФИГ.` is a strong page-level index: zero detected assets is a likely miss and extra detected assets deserve inspection.",
        "- More than one detected asset for one caption is **not automatically** a false split; `sheet-228-left` is the known semantic-grouping control.",
        "- Atlas/plate sheets are not judged by caption-count equality. They contain multiple diagrams whose logical grouping must be reviewed separately.",
        "- Title/cover typography, scanner-edge rules, library stamps and bleed-through are separate false-positive/restoration families and should not be tuned away with one global threshold.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=Path, default=Path("work/figure-structure-full-v2/index.json"))
    ap.add_argument("--text-root", type=Path, default=Path("corpus/text/pages"))
    ap.add_argument("--metrics-root", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=Path("work/figure-structure-full-v2/census-audit"))
    args = ap.parse_args()

    index = load_json(args.index)
    metrics_root = args.metrics_root or args.index.parent
    records: list[dict[str, Any]] = []

    for fallback_index, page in enumerate(index.get("pages", []), start=1):
        page_id = page_id_from_source(str(page["source"]))
        canonical_path = args.text_root / f"{page_id}.json"
        canonical = load_json(canonical_path) if canonical_path.exists() else {}
        diplomatic = str(canonical.get("diplomatic_text", ""))
        notes = list(canonical.get("notes", []))
        caption_numbers = unique_ints(CAPTION_RE.findall(diplomatic))
        mentioned_numbers = unique_ints(MENTION_RE.findall(diplomatic))
        atlas_evidence = bool(ATLAS_RE.search(diplomatic)) or any(
            re.search(r"(?iu)лист\s+чертеж|таблиц", str(note)) for note in notes
        )

        metrics_path = metrics_root / f"{page_id}.metrics.json"
        metrics = load_json(metrics_path) if metrics_path.exists() else page
        geometry = asset_geometry(metrics)

        record: dict[str, Any] = {
            "id": page_id,
            "physical_index": int(canonical.get("physical_index", fallback_index)),
            "page_type": canonical.get("page_type", "unknown"),
            "page_class": page.get("page_class", metrics.get("page_class", "unknown")),
            "caption_figure_numbers": caption_numbers,
            "mentioned_figure_numbers": mentioned_numbers,
            "atlas_evidence": atlas_evidence,
            "detected_asset_count": int(metrics.get("accepted_asset_count", len(page.get("assets", [])))),
            "notes": notes,
            "assets": geometry,
        }
        flags, score = classify(record)
        record["flags"] = flags
        record["review_score"] = score
        records.append(record)

    records.sort(key=lambda r: int(r["physical_index"]))
    args.out.mkdir(parents=True, exist_ok=True)

    json_path = args.out / "figure-census-audit.json"
    json_path.write_text(json.dumps({
        "schema": "corpus-motuum-figure-census-audit-v1",
        "source_index": str(args.index),
        "records": records,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    csv_path = args.out / "figure-census-audit.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        fieldnames = [
            "physical_index", "id", "page_type", "page_class",
            "caption_figure_numbers", "mentioned_figure_numbers",
            "atlas_evidence", "detected_asset_count", "review_score", "flags",
        ]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in records:
            writer.writerow({
                "physical_index": r["physical_index"],
                "id": r["id"],
                "page_type": r["page_type"],
                "page_class": r["page_class"],
                "caption_figure_numbers": ";".join(map(str, r["caption_figure_numbers"])),
                "mentioned_figure_numbers": ";".join(map(str, r["mentioned_figure_numbers"])),
                "atlas_evidence": int(bool(r["atlas_evidence"])),
                "detected_asset_count": r["detected_asset_count"],
                "review_score": r["review_score"],
                "flags": ";".join(r["flags"]),
            })

    md_path = args.out / "figure-census-audit.md"
    md_path.write_text(markdown_report(records, args.index) + "\n", encoding="utf-8")

    print(f"pages: {len(records)}")
    print(f"json: {json_path}")
    print(f"csv:  {csv_path}")
    print(f"md:   {md_path}")
    print("top review pages:")
    for r in sorted(records, key=lambda r: (-int(r["review_score"]), int(r["physical_index"])))[:20]:
        print(
            f"  {r['physical_index']:>3} {r['id']:<16} score={r['review_score']:<2} "
            f"fig={r['caption_figure_numbers']!s:<14} assets={r['detected_asset_count']:<2} "
            f"{','.join(r['flags'])}"
        )


if __name__ == "__main__":
    main()
