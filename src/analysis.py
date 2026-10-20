"""Analisis kandidat fasilitas dengan buffer, jaringan, dan skor berbobot."""

from __future__ import annotations

import math
from typing import Any

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
from shapely import make_valid
from shapely.geometry import LineString, MultiLineString, Point

CRITERIA = ("population", "accessibility", "competition_distance")


def validate_weights(weights: dict[str, float]) -> None:
    """Memastikan bobot lengkap, nonnegatif, dan berjumlah satu."""
    if set(weights) != set(CRITERIA):
        raise ValueError(f"Bobot harus tepat berisi kriteria: {', '.join(CRITERIA)}.")
    if any(not math.isfinite(float(value)) or float(value) < 0 for value in weights.values()):
        raise ValueError("Bobot harus berupa angka finite dan tidak negatif.")
    if not math.isclose(sum(weights.values()), 1.0, abs_tol=1e-9):
        raise ValueError("Jumlah bobot harus sama dengan 1.")


def repair_geometries(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Memperbaiki geometri tidak valid dan membuang geometri kosong."""
    result = frame.copy()
    result = result[result.geometry.notna()].copy()
    if result.empty:
        raise ValueError("Layer tidak memiliki geometri yang dapat dianalisis.")
    result.geometry = make_valid(result.geometry.array)
    result = result[~result.geometry.is_empty].copy()
    if result.empty or not result.geometry.is_valid.all():
        raise ValueError("Perbaikan geometri gagal menghasilkan geometri valid.")
    return result


def project_layers(data: dict[str, Any]) -> tuple[dict[str, gpd.GeoDataFrame], str]:
    """Memperbaiki lalu memproyeksikan setiap layer ke UTM wilayah studi."""
    names = ("boundary", "roads", "competitors")
    layers: dict[str, gpd.GeoDataFrame] = {}
    for name in names:
        frame = data[name]
        if frame.crs is None:
            raise ValueError(f"Layer {name} tidak memiliki CRS; proyeksi tidak aman.")
        layers[name] = repair_geometries(frame)

    target_crs = layers["boundary"].estimate_utm_crs()
    if target_crs is None:
        raise ValueError("CRS UTM tidak dapat diperkirakan dari batas wilayah.")
    projected = {name: frame.to_crs(target_crs) for name, frame in layers.items()}
    for name, frame in projected.items():
        projected[name] = repair_geometries(frame)
    return projected, target_crs.to_string()


def _line_parts(geometry: Any) -> list[LineString]:
    """Mengambil komponen garis dari geometri jalan bertipe tunggal atau multi."""
    if isinstance(geometry, LineString):
        return [geometry]
    if isinstance(geometry, MultiLineString):
        return list(geometry.geoms)
    return []


def build_road_graph(roads: gpd.GeoDataFrame) -> nx.Graph:
    """Membentuk graf jalan tak berarah dengan panjang sisi dalam meter."""
    graph = nx.Graph()
    # Union topologis membagi garis pada perpotongan agar simpang tersambung.
    noded_roads = roads.geometry.union_all()
    for line in _line_parts(noded_roads):
            coordinates = list(line.coords)
            for start, end in zip(coordinates, coordinates[1:]):
                first = (round(start[0], 2), round(start[1], 2))
                second = (round(end[0], 2), round(end[1], 2))
                if first == second:
                    continue
                graph.add_node(first, x=first[0], y=first[1])
                graph.add_node(second, x=second[0], y=second[1])
                length = math.dist(first, second)
                if graph.has_edge(first, second):
                    graph[first][second]["length"] = min(
                        graph[first][second]["length"], length
                    )
                else:
                    graph.add_edge(first, second, length=length)
    if graph.number_of_nodes() < 2:
        raise ValueError("Jaringan jalan tidak cukup untuk membentuk graf aksesibilitas.")
    return graph


def generate_population_grid(
    boundary: gpd.GeoDataFrame,
    roads: gpd.GeoDataFrame,
    spacing_m: int,
    seed: int,
) -> gpd.GeoDataFrame:
    """Membuat grid populasi sintetis yang lebih padat di dekat jalan."""
    if spacing_m < 1:
        raise ValueError("Jarak grid populasi harus positif.")
    area = boundary.geometry.union_all()
    road_union = roads.geometry.union_all()
    min_x, min_y, max_x, max_y = area.bounds
    generator = np.random.default_rng(seed)
    records: list[dict[str, Any]] = []
    cell_id = 1

    for x in np.arange(min_x + spacing_m / 2, max_x, spacing_m):
        for y in np.arange(min_y + spacing_m / 2, max_y, spacing_m):
            point = Point(float(x), float(y))
            if not area.covers(point):
                continue
            road_distance = point.distance(road_union)
            road_factor = 0.25 + 0.75 * math.exp(-road_distance / 650.0)
            random_factor = float(generator.uniform(0.82, 1.18))
            population = int(round((180 + 520 * road_factor) * random_factor))
            records.append({
                "cell_id": f"POP{cell_id:03d}",
                "population": population,
                "road_distance_m": road_distance,
                "population_type": "sintetik",
                "geometry": point,
            })
            cell_id += 1

    if not records:
        raise ValueError("Tidak ada sel populasi di dalam wilayah studi.")
    return gpd.GeoDataFrame(records, geometry="geometry", crs=boundary.crs)


def generate_candidates(
    boundary: gpd.GeoDataFrame,
    roads: gpd.GeoDataFrame,
    count: int,
    spacing_m: int,
    max_road_distance_m: float,
) -> gpd.GeoDataFrame:
    """Membuat titik grid dalam wilayah yang juga dekat dengan jalan."""
    if count < 1 or spacing_m < 1 or max_road_distance_m < 0:
        raise ValueError("Parameter grid kandidat harus positif dan valid.")
    area = boundary.geometry.union_all()
    road_union = roads.geometry.union_all()
    min_x, min_y, max_x, max_y = area.bounds
    points: list[tuple[float, float, float]] = []

    for x in np.arange(min_x + spacing_m / 2, max_x, spacing_m):
        for y in np.arange(min_y + spacing_m / 2, max_y, spacing_m):
            point = Point(float(x), float(y))
            if not area.covers(point):
                continue
            distance = point.distance(road_union)
            if distance <= max_road_distance_m:
                points.append((float(x), float(y), float(distance)))

    points.sort(key=lambda row: (row[1], row[0]))
    if not points:
        raise ValueError("Tidak ada kandidat dekat jalan; ubah grid atau jarak jalan.")
    if len(points) > count:
        indices = np.linspace(0, len(points) - 1, count, dtype=int)
        points = [points[index] for index in indices]

    return gpd.GeoDataFrame(
        {
            "candidate_id": [f"K{index:03d}" for index in range(1, len(points) + 1)],
            "road_distance_m": [row[2] for row in points],
        },
        geometry=[Point(row[0], row[1]) for row in points],
        crs=boundary.crs,
    )


def _nearest_graph_node(graph: nx.Graph, point: Point, nodes: list[Any]) -> Any:
    """Memetakan titik populasi atau kandidat ke simpul jalan terdekat."""
    return min(
        nodes,
        key=lambda node: math.hypot(
            graph.nodes[node]["x"] - point.x,
            graph.nodes[node]["y"] - point.y,
        ),
    )


def measure_candidate_metrics(
    candidates: gpd.GeoDataFrame,
    population: gpd.GeoDataFrame,
    competitors: gpd.GeoDataFrame,
    graph: nx.Graph,
    radii: list[int],
) -> gpd.GeoDataFrame:
    """Mengukur populasi buffer/jaringan dan persaingan setiap kandidat."""
    graph_nodes = list(graph.nodes)
    population_nodes = [
        _nearest_graph_node(graph, point, graph_nodes) for point in population.geometry
    ]
    population_connectors = np.array([
        point.distance(Point(graph.nodes[node]["x"], graph.nodes[node]["y"]))
        for point, node in zip(population.geometry, population_nodes)
    ])
    population_values = population["population"].to_numpy(dtype=float)
    competitor_points = list(competitors.geometry)
    result = candidates.copy()

    for radius in radii:
        result[f"straight_population_{radius}m"] = 0
        result[f"network_population_{radius}m"] = 0
        result[f"competitors_{radius}m"] = 0

    nearest_distances: list[float] = []
    for row_index, candidate in candidates.iterrows():
        point = candidate.geometry
        distances = np.array([point.distance(other) for other in competitor_points])
        nearest_distances.append(float(distances.min()) if len(distances) else 0.0)
        source = _nearest_graph_node(graph, point, graph_nodes)
        candidate_connector = point.distance(
            Point(graph.nodes[source]["x"], graph.nodes[source]["y"])
        )
        network_distances = nx.single_source_dijkstra_path_length(
            graph, source, weight="length", cutoff=max(radii)
        )

        for radius in radii:
            in_buffer = np.array(
                [point.distance(cell) <= radius for cell in population.geometry]
            )
            result.at[row_index, f"straight_population_{radius}m"] = int(
                population_values[in_buffer].sum()
            )
            network_mask = np.array([
                candidate_connector + network_distances.get(node, math.inf) + connector
                <= radius
                for node, connector in zip(population_nodes, population_connectors)
            ])
            result.at[row_index, f"network_population_{radius}m"] = int(
                population_values[network_mask].sum()
            )
            result.at[row_index, f"competitors_{radius}m"] = int(
                np.count_nonzero(distances <= radius)
            )

    result["nearest_competitor_m"] = nearest_distances
    return result


def score_candidates(
    metrics: gpd.GeoDataFrame,
    radius: int,
    weights: dict[str, float],
) -> gpd.GeoDataFrame:
    """Menormalisasi tiga kriteria dan mengurutkan skor berbobot 0 sampai 1."""
    validate_weights(weights)
    columns = {
        "population": f"straight_population_{radius}m",
        "accessibility": f"network_population_{radius}m",
        "competition_distance": "nearest_competitor_m",
    }
    if any(column not in metrics for column in columns.values()):
        raise ValueError(f"Metrik untuk radius {radius} m belum tersedia.")

    ranked = metrics.copy()
    normalized_columns: dict[str, str] = {}
    for criterion, column in columns.items():
        values = ranked[column].astype(float)
        minimum, maximum = float(values.min()), float(values.max())
        normalized = (values - minimum) / (maximum - minimum) if maximum > minimum else 0.5
        normalized_column = f"norm_{criterion}"
        ranked[normalized_column] = normalized.clip(0.0, 1.0)
        normalized_columns[criterion] = normalized_column

    ranked["score"] = sum(
        float(weights[criterion]) * ranked[column]
        for criterion, column in normalized_columns.items()
    ).clip(0.0, 1.0)
    ranked = ranked.sort_values(
        ["score", "candidate_id"], ascending=[False, True]
    ).reset_index(drop=True)
    ranked["rank"] = np.arange(1, len(ranked) + 1)
    return ranked


def run_analysis(data: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Menyiapkan layer, menghitung metrik, peringkat, dan sensitivitas bobot."""
    weights = {key: float(value) for key, value in config["weights"].items()}
    validate_weights(weights)
    settings = config["analysis"]
    radii = sorted({int(value) for value in settings["service_radii_m"]})
    base_radius = int(settings["scoring_radius_m"])
    if any(radius < 1 for radius in radii) or base_radius not in radii:
        raise ValueError("Radius harus positif dan scoring_radius_m harus ada di daftar.")

    layers, projected_crs = project_layers(data)
    population = generate_population_grid(
        layers["boundary"], layers["roads"],
        int(settings["population_grid_spacing_m"]),
        int(settings["random_seed"]),
    )
    candidates = generate_candidates(
        layers["boundary"], layers["roads"],
        int(settings["candidate_count"]),
        int(settings["candidate_grid_spacing_m"]),
        float(settings["max_candidate_distance_to_road_m"]),
    )
    graph = build_road_graph(layers["roads"])
    metrics = measure_candidate_metrics(
        candidates, population, layers["competitors"], graph, radii
    )

    ranked_by_radius: dict[int, gpd.GeoDataFrame] = {}
    output_candidates = metrics.copy()
    for radius in radii:
        ranked = score_candidates(metrics, radius, weights)
        ranked_by_radius[radius] = ranked
        output_candidates[f"score_{radius}m"] = np.nan
        output_candidates[f"rank_{radius}m"] = 0
        for _, row in ranked.iterrows():
            mask = output_candidates["candidate_id"] == row["candidate_id"]
            output_candidates.loc[mask, f"score_{radius}m"] = row["score"]
            output_candidates.loc[mask, f"rank_{radius}m"] = int(row["rank"])

    base_ranked = ranked_by_radius[base_radius]
    output_candidates["score"] = output_candidates[f"score_{base_radius}m"]
    output_candidates["rank"] = output_candidates[f"rank_{base_radius}m"].astype(int)

    sensitivity_rows: list[dict[str, Any]] = []
    scenarios = [("baseline", weights)] + [
        (str(item["name"]), {key: float(item[key]) for key in CRITERIA})
        for item in config.get("sensitivity_weights", [])
    ]
    for radius in radii:
        baseline = ranked_by_radius[radius]
        baseline_top = baseline.head(5)["candidate_id"].tolist()
        baseline_ranks = dict(zip(baseline["candidate_id"], baseline["rank"]))
        for scenario_name, scenario_weights in scenarios:
            validate_weights(scenario_weights)
            ranked = (
                baseline if scenario_name == "baseline"
                else score_candidates(metrics, radius, scenario_weights)
            )
            top = ranked.head(5)["candidate_id"].tolist()
            overlap = len(set(baseline_top) & set(top))
            for _, row in ranked.iterrows():
                sensitivity_rows.append({
                    "radius_m": radius,
                    "scenario": scenario_name,
                    "weights": ";".join(
                        f"{key}={scenario_weights[key]:.2f}" for key in CRITERIA
                    ),
                    "candidate_id": row["candidate_id"],
                    "rank": int(row["rank"]),
                    "baseline_rank": int(baseline_ranks[row["candidate_id"]]),
                    "in_top5": row["candidate_id"] in top,
                    "baseline_top5": row["candidate_id"] in baseline_top,
                    "top5_overlap_count": overlap,
                    "top5_order_same": top == baseline_top,
                })

    return {
        **layers,
        "population": population,
        "candidates": output_candidates,
        "ranked": base_ranked,
        "sensitivity": pd.DataFrame(sensitivity_rows),
        "crs": projected_crs,
        "source_used": data["source_used"],
        "source_note": data["source_note"],
        "scoring_radius_m": base_radius,
        "weights": weights,
    }




