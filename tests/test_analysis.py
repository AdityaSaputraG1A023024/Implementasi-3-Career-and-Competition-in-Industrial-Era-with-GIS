"""Uji CRS, geometri, bobot, skor, dan hasil pipeline data sample."""

import unittest
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
import networkx as nx
from shapely.geometry import Polygon

from src.analysis import build_road_graph, project_layers, repair_geometries, run_analysis, validate_weights
from src.data_loader import load_config, load_data

ROOT = Path(__file__).resolve().parents[1]


class SpatialAnalysisTests(unittest.TestCase):
    """Memastikan keluaran inti memenuhi kontrak spasial dan skoring."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(ROOT / "config.yaml")
        cls.data = load_data(cls.config, ROOT, source_override="sample")
        cls.result = run_analysis(cls.data, cls.config)

    def test_layers_use_projected_crs_and_valid_geometries(self) -> None:
        layers, crs = project_layers(self.data)
        self.assertNotEqual(crs, "EPSG:4326")
        for frame in layers.values():
            self.assertTrue(frame.geometry.is_valid.all())
            self.assertFalse(frame.crs.is_geographic)

    def test_auto_source_falls_back_to_local_sample(self) -> None:
        with patch("src.data_loader._load_osm", side_effect=ConnectionError("offline")):
            with self.assertWarns(RuntimeWarning):
                data = load_data(self.config, ROOT)
        self.assertEqual(data["source_used"], "sample")

    def test_weights_sum_to_one(self) -> None:
        weights = self.config["weights"]
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        validate_weights(weights)
        with self.assertRaises(ValueError):
            validate_weights({"population": 0.5, "accessibility": 0.5,
                              "competition_distance": 0.5})

    def test_invalid_polygon_is_repaired(self) -> None:
        invalid = gpd.GeoDataFrame(
            geometry=[Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])],
            crs="EPSG:4326",
        )
        fixed = repair_geometries(invalid)
        self.assertTrue(fixed.geometry.is_valid.all())

    def test_scores_are_normalized(self) -> None:
        scores = self.result["candidates"]["score"]
        self.assertTrue(((scores >= 0.0) & (scores <= 1.0)).all())
        for column in ("norm_population", "norm_accessibility",
                       "norm_competition_distance"):
            self.assertTrue(
                ((self.result["ranked"][column] >= 0.0)
                 & (self.result["ranked"][column] <= 1.0)).all()
            )

    def test_network_access_and_sensitivity_are_available(self) -> None:
        radius = self.result["scoring_radius_m"]
        network = self.result["candidates"][f"network_population_{radius}m"]
        straight = self.result["candidates"][f"straight_population_{radius}m"]
        self.assertTrue((network <= straight).all())
        layers, _ = project_layers(self.data)
        graph = build_road_graph(layers["roads"])
        self.assertTrue(nx.is_connected(graph))
        self.assertGreaterEqual(len(self.result["ranked"]), 5)
        self.assertFalse(self.result["sensitivity"].empty)


if __name__ == "__main__":
    unittest.main()






