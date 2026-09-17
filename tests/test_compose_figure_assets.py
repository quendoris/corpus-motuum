from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSITOR = REPO_ROOT / "tools" / "compose_figure_assets.py"
spec = importlib.util.spec_from_file_location("compose_figure_assets", COMPOSITOR)
compose = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = compose
assert spec.loader is not None
spec.loader.exec_module(compose)


class ComposeFigureAssetsTests(unittest.TestCase):
    def _fixture(self, root: Path) -> Path:
        first = np.zeros((3, 4, 4), dtype=np.uint8)
        first[:, :, :3] = (20, 20, 20)
        first[:, :, 3] = 255
        second = np.zeros((2, 3, 4), dtype=np.uint8)
        second[:, :, :3] = (80, 80, 80)
        second[:, :, 3] = 255
        first_path = root / "page-asset-01.clean.png"
        second_path = root / "page-asset-02.clean.png"
        self.assertTrue(cv2.imwrite(str(first_path), first))
        self.assertTrue(cv2.imwrite(str(second_path), second))
        metrics = {
            "source": "work/pages/page.jpg",
            "source_sha256": "source-sha",
            "width": 30,
            "height": 40,
            "assets": [
                {
                    "asset_index": 1,
                    "bbox": [5, 7, 9, 10],
                    "files": {"clean": first_path.name},
                    "sha256": {"clean": compose.sha256_file(first_path)},
                },
                {
                    "asset_index": 2,
                    "bbox": [6, 13, 9, 15],
                    "files": {"clean": second_path.name},
                    "sha256": {"clean": compose.sha256_file(second_path)},
                },
            ],
        }
        metrics_path = root / "page.metrics.json"
        metrics_path.write_text(json.dumps(metrics), encoding="utf-8")
        return metrics_path

    def test_preserves_page_offsets_and_white_gap(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            metrics_path = self._fixture(root)
            output_path = root / "logical.png"
            provenance = compose.compose_page_assets(
                metrics_path,
                [1, 2],
                output_path,
                background="white",
            )

            output = cv2.imread(str(output_path), cv2.IMREAD_UNCHANGED)
            self.assertEqual(output.shape, (8, 4, 4))
            self.assertEqual(provenance["union_bbox"], [5, 7, 9, 15])
            self.assertEqual(
                [part["offset"] for part in provenance["parts"]],
                [[0, 0], [1, 6]],
            )
            self.assertTrue(np.all(output[3:6, :, :3] == 255))
            self.assertTrue(np.all(output[:, :, 3] == 255))

    def test_clips_one_merged_asset_in_source_coordinates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            metrics_path = self._fixture(root)
            output_path = root / "top.png"
            provenance = compose.compose_page_parts(
                metrics_path,
                [
                    {
                        "asset_index": 1,
                        "clip_bbox": [5, 7, 9, 9],
                        "trim_transparent": True,
                    }
                ],
                output_path,
            )

            output = cv2.imread(str(output_path), cv2.IMREAD_UNCHANGED)
            self.assertEqual(output.shape, (2, 4, 4))
            self.assertEqual(provenance["union_bbox"], [5, 7, 9, 9])
            self.assertEqual(
                provenance["parts"][0]["requested_bbox"], [5, 7, 9, 9]
            )
            self.assertEqual(provenance["background"], "transparent")

    def test_rejects_crop_whose_dimensions_disagree_with_bbox(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            metrics_path = self._fixture(root)
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            metrics["assets"][1]["bbox"] = [6, 13, 10, 15]
            metrics_path.write_text(json.dumps(metrics), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "dimensions do not match bbox"):
                compose.compose_page_assets(
                    metrics_path,
                    [1, 2],
                    root / "logical.png",
                )

    def test_rejects_changed_input_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            metrics_path = self._fixture(root)
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            metrics["assets"][0]["sha256"]["clean"] = "0" * 64
            metrics_path.write_text(json.dumps(metrics), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                compose.compose_page_assets(
                    metrics_path,
                    [1, 2],
                    root / "logical.png",
                )

    def test_batch_allows_disjoint_clip_reuse(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._fixture(root)
            spec_path = root / "compositions.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "schema": "corpus-motuum-logical-compositions-v1",
                        "compositions": [
                            {
                                "id": "top",
                                "page_id": "page",
                                "parts": [
                                    {
                                        "asset_index": 1,
                                        "clip_bbox": [5, 7, 9, 8],
                                    }
                                ],
                                "output": "numbered/top.png",
                            },
                            {
                                "id": "bottom",
                                "page_id": "page",
                                "parts": [
                                    {
                                        "asset_index": 1,
                                        "clip_bbox": [5, 8, 9, 10],
                                    }
                                ],
                                "output": "numbered/bottom.png",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output_root = root / "logical"
            manifest = compose.render_batch(spec_path, root, output_root)
            self.assertEqual(manifest["rendered_count"], 2)
            self.assertTrue((output_root / "numbered/top.png").is_file())
            self.assertTrue((output_root / "numbered/bottom.png").is_file())

    def test_batch_rejects_overlapping_clip_reuse(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._fixture(root)
            spec_path = root / "compositions.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "schema": "corpus-motuum-logical-compositions-v1",
                        "compositions": [
                            {
                                "id": "first",
                                "page_id": "page",
                                "parts": [
                                    {
                                        "asset_index": 1,
                                        "clip_bbox": [5, 7, 9, 9],
                                    }
                                ],
                                "output": "numbered/first.png",
                            },
                            {
                                "id": "second",
                                "page_id": "page",
                                "parts": [
                                    {
                                        "asset_index": 1,
                                        "clip_bbox": [5, 8, 9, 10],
                                    }
                                ],
                                "output": "numbered/second.png",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "windows overlap"):
                compose.render_batch(spec_path, root, root / "logical")

    def test_batch_writes_declared_relative_output_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._fixture(root)
            spec_path = root / "compositions.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "schema": "corpus-motuum-logical-compositions-v1",
                        "compositions": [
                            {
                                "id": "figure-test",
                                "figure_labels": ["test"],
                                "page_id": "page",
                                "asset_indices": [1, 2],
                                "layout": "source-coordinates",
                                "output": "numbered/figure-test.png",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output_root = root / "logical"
            manifest = compose.render_batch(spec_path, root, output_root)
            self.assertEqual(manifest["rendered_count"], 1)
            self.assertTrue((output_root / "numbered/figure-test.png").is_file())
            self.assertTrue((output_root / "compositions-manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
