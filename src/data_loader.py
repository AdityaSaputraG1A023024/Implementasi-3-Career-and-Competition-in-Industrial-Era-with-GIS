"""Memuat fixture lokal atau data jalan dan POI dari OpenStreetMap."""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import geopandas as gpd
import osmnx as ox
import pandas as pd
import yaml
from shapely.geometry import box


def load_config(config_path: Path) -> dict[str, Any]:
    """Membaca konfigurasi YAML tanpa bergantung pada direktori kerja."""
    with config_path.open("r", encoding="utf-8-sig") as file:
        config = yaml.safe_load(file)
    if not isinstance(config, dict):
        raise ValueError("Isi config.yaml harus berupa objek YAML.")
    return config


def _load_sample(config: dict[str, Any], root: Path) -> dict[str, Any]:
    """Memuat batas, jalan, dan pesaing contoh dari GeoJSON lokal."""
    paths = config["sample_data"]
    boundary = gpd.read_file(root / paths["boundary"])
    roads = gpd.read_file(root / paths["roads"])
    competitors = gpd.read_file(root / paths["competitors"])
    boundary["name"] = config["study_area"]["name"]
    return {
        "boundary": boundary,
        "roads": roads,
        "competitors": competitors,
        "source_used": "sample",
        "source_note": "Fixture lokal ilustratif; bukan data OSM atau survei lapangan.",
    }


def _load_osm(config: dict[str, Any]) -> dict[str, Any]:
    """Mengambil jaringan jalan dan POI pesaing untuk bounding box terkonfigurasi."""
    west, south, east, north = config["study_area"]["bbox_wgs84"]
    bbox = (west, south, east, north)
    polygon = box(west, south, east, north)
    boundary = gpd.GeoDataFrame(
        {"name": [config["study_area"]["name"]]},
        geometry=[polygon],
        crs="EPSG:4326",
    )

    ox.settings.requests_timeout = 30
    graph = ox.graph.graph_from_bbox(
        bbox,
        network_type=config["osm"].get("network_type", "drive"),
        simplify=True,
        retain_all=False,
    )
    roads = ox.convert.graph_to_gdfs(graph, nodes=False, edges=True).reset_index()
    roads = roads.set_crs("EPSG:4326", allow_override=True)

    competitors = ox.features.features_from_bbox(
        bbox,
        tags=config["osm"]["competitor_tags"],
    ).reset_index()
    if competitors.empty:
        raise RuntimeError("OpenStreetMap tidak mengembalikan POI pesaing pada area ini.")
    competitors = gpd.GeoDataFrame(
        competitors,
        geometry=competitors.geometry.representative_point(),
        crs="EPSG:4326",
    )
    return {
        "boundary": boundary,
        "roads": roads,
        "competitors": competitors,
        "source_used": "openstreetmap",
        "source_note": "Jaringan jalan dan POI diunduh dari OpenStreetMap.",
    }


def load_data(
    config: dict[str, Any], root: Path, source_override: str | None = None
) -> dict[str, Any]:
    """Memilih sumber data dan memberi peringatan jelas saat fallback diperlukan."""
    source = source_override or config["study_area"].get("source", "auto")
    if source == "sample":
        return _load_sample(config, root)
    if source == "osm":
        return _load_osm(config)
    if source != "auto":
        raise ValueError("Sumber data harus 'auto', 'osm', atau 'sample'.")

    try:
        return _load_osm(config)
    except Exception as error:
        warnings.warn(
            f"Pengambilan OSM gagal ({type(error).__name__}: {error}); "
            "beralih ke fixture sample lokal. Hasil akan bersifat ilustratif.",
            RuntimeWarning,
            stacklevel=2,
        )
        return _load_sample(config, root)



