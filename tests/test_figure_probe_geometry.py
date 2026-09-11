#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE = REPO_ROOT / "tools" / "probe_figure_segmentation.py"
if not PROBE.exists():
    PROBE = Path("/mnt/data/probe_figure_segmentation_v2_compact.py")
spec = importlib.util.spec_from_file_location("figure_probe_v2", PROBE)
probe = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = probe
assert spec.loader is not None
spec.loader.exec_module(probe)


class FigureProbeGeometryTests(unittest.TestCase):
    def test_bbox_index_preserves_long_component_edge(self):
        rail = probe.Component(1, 10, 10, 300, 5, 1200, 160.0, 12.5)
        fragment = probe.Component(2, 312, 12, 3, 3, 9, 313.5, 13.5)
        graph = probe.build_bbox_neighbor_graph([rail, fragment], max_gap=5.0)
        self.assertTrue(any(probe._edge_other(edge, 1) == 2 for edge in graph[1]))

    def test_bbox_overlap_alone_does_not_merge_assets(self):
        a_comp = probe.Component(1, 0, 0, 100, 10, 1000, 50.0, 5.0)
        b_comp = probe.Component(2, 50, 50, 100, 10, 1000, 100.0, 55.0)
        a = probe.Region([1], [1], [0, 0, 100, 100], 1000, 100, 0.0, 5.0)
        b = probe.Region([2], [2], [50, 50, 150, 150], 1000, 100, 0.0, 5.0)
        out = probe.coalesce_regions(
            [a, b], {1: a_comp, 2: b_comp}, {1: [], 2: []}, 20.0
        )
        self.assertEqual(len(out), 2)

    def test_small_gap_closes_but_open_u_does_not_fill(self):
        near = np.zeros((160, 160), np.uint8)
        cv2.line(near, (30, 30), (130, 30), 255, 2)
        cv2.line(near, (30, 30), (30, 130), 255, 2)
        cv2.line(near, (130, 30), (130, 130), 255, 2)
        cv2.line(near, (30, 130), (78, 130), 255, 2)
        cv2.line(near, (82, 130), (130, 130), 255, 2)
        hard, _, diag = probe.closure_and_alpha(near, 2.0, 0.35, 0.45, 3.0, 1.5, 2.0)
        self.assertTrue(diag["closure_accepted"])
        self.assertGreater(hard[80, 80], 0)

        open_u = np.zeros((160, 160), np.uint8)
        cv2.line(open_u, (30, 30), (30, 130), 255, 2)
        cv2.line(open_u, (130, 30), (130, 130), 255, 2)
        cv2.line(open_u, (30, 130), (130, 130), 255, 2)
        hard, _, diag = probe.closure_and_alpha(open_u, 2.0, 0.35, 0.45, 3.0, 1.5, 2.0)
        self.assertFalse(diag["closure_accepted"])
        self.assertEqual(int(hard[80, 80]), 0)

    def test_end_to_end_keeps_two_figures_separate_and_hashes_outputs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "page.png"
            out = root / "out"
            image = np.full((1200, 800, 3), 242, np.uint8)
            yy, xx = np.mgrid[0:1200, 0:800]
            shade = (7 * np.sin(xx / 180) + 5 * np.cos(yy / 260)).astype(np.int16)
            for channel in range(3):
                image[:, :, channel] = np.clip(
                    image[:, :, channel].astype(np.int16) + shade, 0, 255
                )
            for y in range(90, 1050, 42):
                cv2.putText(
                    image,
                    "LOREM IPSUM TEXT LINE SAMPLE",
                    (50, y),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (55, 55, 55),
                    1,
                    cv2.LINE_AA,
                )
            cv2.rectangle(image, (90, 300), (360, 610), (242, 242, 242), -1)
            cv2.rectangle(image, (440, 300), (710, 610), (242, 242, 242), -1)
            cv2.rectangle(image, (120, 340), (315, 545), (35, 35, 35), 2)
            for x in range(145, 310, 28):
                cv2.line(image, (x, 360), (x, 525), (50, 50, 50), 1)
            cv2.circle(image, (210, 410), 32, (30, 30, 30), 2)
            cv2.line(image, (120, 545), (214, 545), (35, 35, 35), 2)
            cv2.line(image, (220, 545), (315, 545), (35, 35, 35), 2)
            cv2.line(image, (470, 350), (660, 350), (25, 25, 25), 3)
            cv2.line(image, (475, 350), (475, 555), (25, 25, 25), 3)
            cv2.line(image, (655, 350), (655, 555), (25, 25, 25), 3)
            for y in range(380, 540, 25):
                cv2.line(image, (475, y), (655, y), (55, 55, 55), 1)
            self.assertTrue(cv2.imwrite(str(source), image))

            class Args:
                graph_gap_scale = 2.2
                text_penalty = 5.0
                growth_budget = 6.0
                support_budget = 4.6
                support_text_penalty = 5.8
                closure_budget = 0.35
                closure_boundary_min = 0.72
                max_bridge_width = 3.5
                margin_scale = 1.5
                alpha_scale = 2.0

            result = probe.process_page(source, out, Args())
            self.assertEqual(result["accepted_asset_count"], 2)
            boxes = [asset["bbox"] for asset in result["assets"]]
            self.assertLess(boxes[0][2], boxes[1][0])
            for asset in result["assets"]:
                for role, name in asset["files"].items():
                    path = out / name
                    self.assertTrue(path.is_file())
                    self.assertEqual(probe.sha256_file(path), asset["sha256"][role])


if __name__ == "__main__":
    unittest.main()
