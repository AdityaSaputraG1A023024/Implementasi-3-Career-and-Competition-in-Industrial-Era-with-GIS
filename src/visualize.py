"""Membuat peta statis dan peta web interaktif dari hasil analisis."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import folium
import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def create_static_map(result: dict[str, Any], output_path: Path) -> None:
    """Menggambar batas, jaringan, pesaing, dan lima kandidat terbaik."""
    boundary = result["boundary"]
    roads = result["roads"]
    competitors = result["competitors"]
    candidates = result["candidates"]
    best = candidates[candidates["rank"] <= 5]

    figure, axis = plt.subplots(figsize=(10, 8))
    boundary.boundary.plot(ax=axis, color="#333333", linewidth=1.5, label="Wilayah")
    roads.plot(ax=axis, color="#9aa0a6", linewidth=0.8, label="Jalan")
    candidates.plot(ax=axis, color="#6baed6", markersize=18, label="Kandidat")
    competitors.plot(ax=axis, color="#d62728", marker="x", markersize=60,
                     label="Pesaing")
    best.plot(ax=axis, color="#2ca02c", marker="*", markersize=130,
              label="Top 5")
    axis.set_title(
        f"Kandidat fasilitas — radius skor {result['scoring_radius_m']} m\n"
        f"Sumber: {result['source_used']}; populasi sintetis"
    )
    axis.set_xlabel("Easting (meter)")
    axis.set_ylabel("Northing (meter)")
    axis.set_aspect("equal")
    axis.legend(loc="best")
    axis.grid(alpha=0.2)
    figure.tight_layout()
    figure.savefig(output_path, dpi=180)
    plt.close(figure)


def create_interactive_map(result: dict[str, Any], output_path: Path) -> None:
    """Menyimpan peta Folium tanpa basemap agar geometri tetap terlihat offline."""
    boundary = result["boundary"].to_crs("EPSG:4326")
    roads = result["roads"].to_crs("EPSG:4326")
    competitors = result["competitors"].to_crs("EPSG:4326")
    candidates = result["candidates"].to_crs("EPSG:4326")
    center = boundary.geometry.union_all().centroid
    map_view = folium.Map(location=[center.y, center.x], zoom_start=14, tiles=None)

    folium.GeoJson(
        boundary,
        name="Wilayah studi",
        style_function=lambda _: {"color": "#333333", "fillOpacity": 0.04},
    ).add_to(map_view)
    folium.GeoJson(
        roads,
        name="Jaringan jalan",
        style_function=lambda _: {"color": "#777777", "weight": 2},
    ).add_to(map_view)

    for _, row in competitors.iterrows():
        folium.Marker(
            location=[row.geometry.y, row.geometry.x],
            tooltip=str(row.get("name", row.get("competitor_id", "Pesaing"))),
            icon=folium.Icon(color="red", icon="shopping-cart", prefix="fa"),
        ).add_to(map_view)

    radius = result["scoring_radius_m"]
    for _, row in candidates.iterrows():
        is_top = int(row["rank"]) <= 5
        popup = (
            f"{row['candidate_id']} | Peringkat {int(row['rank'])} | "
            f"Skor {row['score']:.3f} | Populasi buffer "
            f"{int(row[f'straight_population_{radius}m'])}"
        )
        folium.CircleMarker(
            location=[row.geometry.y, row.geometry.x],
            radius=8 if is_top else 4,
            color="#16803c" if is_top else "#2878b5",
            fill=True,
            fill_opacity=0.85,
            popup=popup,
        ).add_to(map_view)
        if is_top:
            folium.Circle(
                location=[row.geometry.y, row.geometry.x],
                radius=radius,
                color="#16803c",
                fill=False,
                weight=1,
                opacity=0.5,
            ).add_to(map_view)

    folium.LayerControl().add_to(map_view)
    map_view.save(str(output_path))
