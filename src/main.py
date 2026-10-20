"""Menjalankan pipeline GIS dan menulis seluruh artefak analisis."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import warnings
from pathlib import Path
from typing import Any

import geopandas as gpd

from src.analysis import run_analysis
from src.data_loader import load_config, load_data
from src.visualize import create_interactive_map, create_static_map

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _gpkg_safe(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Mengubah atribut list/dict agar dapat ditulis secara aman ke GeoPackage."""
    result = frame.copy()
    for column in result.columns:
        if column == result.geometry.name:
            continue
        if result[column].dtype == "object":
            result[column] = result[column].map(
                lambda value: json.dumps(value, ensure_ascii=False)
                if isinstance(value, (list, dict, tuple)) else value
            )
    return result


def write_outputs(result: dict[str, Any], output_dir: Path) -> None:
    """Menyimpan GeoPackage, peringkat CSV, sensitivitas, dan peta."""
    output_dir.mkdir(parents=True, exist_ok=True)
    gpkg_path = output_dir / "hasil.gpkg"
    if gpkg_path.exists():
        gpkg_path.unlink()

    # GDAL menulis GeoPackage di folder sementara, lalu berkas dipindahkan utuh.
    layers = {
        "study_area": result["boundary"],
        "roads": result["roads"],
        "competitors": result["competitors"],
        "population_synthetic": result["population"],
        "candidates": result["candidates"],
    }
    with tempfile.TemporaryDirectory(prefix="gis-gpkg-") as temporary_dir:
        temporary_gpkg = Path(temporary_dir) / "hasil.gpkg"
        for index, (layer_name, frame) in enumerate(layers.items()):
            _gpkg_safe(frame).to_file(
                temporary_gpkg,
                layer=layer_name,
                driver="GPKG",
                index=False,
                mode="w" if index == 0 else "a",
            )
        with temporary_gpkg.open("rb") as source_file, gpkg_path.open("wb") as target_file:
            shutil.copyfileobj(source_file, target_file)

    ranked = result["ranked"].drop(columns="geometry")
    ranked.insert(0, "source_data", result["source_used"])
    ranked.insert(1, "population_status", "sintetik; ilustrasi, bukan rekomendasi bisnis")
    ranked.insert(2, "projected_crs", result["crs"])
    ranked.to_csv(output_dir / "peringkat_kandidat.csv", index=False, encoding="utf-8-sig")
    result["sensitivity"].to_csv(
        output_dir / "analisis_sensitivitas.csv", index=False, encoding="utf-8-sig"
    )
    create_static_map(result, output_dir / "peta_statis.png")
    create_interactive_map(result, output_dir / "peta_interaktif.html")


def _arguments() -> argparse.Namespace:
    """Membaca konfigurasi, sumber data, dan folder keluaran dari terminal."""
    parser = argparse.ArgumentParser(
        description="Analisis spasial lokasi kandidat fasilitas di kawasan Kota Bengkulu."
    )
    parser.add_argument(
        "--config", type=Path, default=PROJECT_ROOT / "config.yaml",
        help="Lokasi file konfigurasi YAML.",
    )
    parser.add_argument(
        "--source", choices=("auto", "osm", "sample"), default=None,
        help="Pilih data OSM, fixture lokal, atau fallback otomatis.",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=PROJECT_ROOT / "outputs",
        help="Folder penyimpanan artefak analisis.",
    )
    return parser.parse_args()


def main() -> None:
    """Memuat data, mengerjakan analisis, lalu mencetak ringkasan aktual."""
    arguments = _arguments()
    config = load_config(arguments.config)
    data = load_data(config, arguments.config.resolve().parent, arguments.source)
    result = run_analysis(data, config)
    write_outputs(result, arguments.output_dir)

    print(f"Sumber jalan/pesaing: {result['source_used']} — {result['source_note']}")
    print(f"CRS analisis: {result['crs']}")
    print(f"Status populasi: sintetis (seed {config['analysis']['random_seed']})")
    print("Lima kandidat teratas:")
    print(result["ranked"].head(5)[[
        "rank", "candidate_id", "score",
        f"straight_population_{result['scoring_radius_m']}m",
        f"network_population_{result['scoring_radius_m']}m",
        "nearest_competitor_m",
    ]].to_string(index=False))

    sensitivity = result["sensitivity"]
    summary = sensitivity[
        (sensitivity["radius_m"] == result["scoring_radius_m"])
        & (sensitivity["scenario"] != "baseline")
    ].drop_duplicates(["radius_m", "scenario"])
    print("Ringkasan sensitivitas pada radius skor:")
    print(summary[["scenario", "top5_overlap_count", "top5_order_same"]].to_string(index=False))
    print(f"Artefak ditulis ke: {arguments.output_dir.resolve()}")


if __name__ == "__main__":
    # Izinkan perubahan hanya karena kegagalan fallback sudah ditampilkan sebagai warning.
    warnings.simplefilter("default", RuntimeWarning)
    main()





