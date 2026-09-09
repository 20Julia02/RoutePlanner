"""Build, update and preview reusable walking networks."""

from __future__ import annotations

import copy
import heapq
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

from shapely.geometry import LineString

from geojson_utils import (
    NodeIndex,
    feature_collection,
    iter_lines,
    line_length,
    number,
    point_coordinates,
    positive_number,
    property_value,
    report_progress,
    to_xy,
    unit_multiplier,
    valid_line,
)
from models import CostMatrix, Edge, Graph, Node
from planning_models import EdgeGeometry, GeoJSON, PlanningError, PreparedNetwork, ProgressCallback


TIME_UNITS = {"seconds": 1.0, "minutes": 60.0, "hours": 3600.0}
MAX_FIELD_NAME_LENGTH = 128
MAX_WARNINGS = 100
MAX_EDGE_TIME_SECONDS = 7 * 24 * 3600
MAX_VISIT_TIME_SECONDS = 7 * 24 * 3600
MAX_ATTRACTION_WEIGHT = 1_000_000


def _field_name(fields: Mapping[str, Any], key: str) -> str:
    value = str(fields.get(key) or "")
    if len(value) > MAX_FIELD_NAME_LENGTH:
        raise PlanningError(
            f"Nazwa pola może mieć najwyżej {MAX_FIELD_NAME_LENGTH} znaków."
        )
    return value


@dataclass
class _EdgeRow:
    """Validated edge data awaiting conversion to graph objects."""

    edge_id: int
    start_id: int
    end_id: int
    length: float
    walk_time: float
    coordinates: List[List[float]]
    source_id: Any
    properties: Dict[str, Any]


def _validated_lines(
    edge_features: Sequence[GeoJSON], warnings: List[str]
) -> list[tuple[GeoJSON, List[List[float]]]]:
    """Validate source lines while retaining warnings for skipped geometries."""

    lines = []
    invalid_count = 0
    for line_number, (feature, coordinates) in enumerate(iter_lines(edge_features), start=1):
        try:
            normalized = valid_line(coordinates)
        except PlanningError as exc:
            invalid_count += 1
            if len(warnings) < MAX_WARNINGS:
                warnings.append(f"Pominięto krawędź {line_number}: {exc}")
            continue
        lines.append((feature, normalized))
    if not lines:
        raise PlanningError(
            "Warstwa sieci nie zawiera żadnej poprawnej krawędzi z co najmniej dwoma punktami."
        )
    if invalid_count:
        warnings.append(f"Liczba pominiętych krawędzi: {invalid_count}.")
    return lines


def _build_edge_rows(
    raw_lines: Sequence[tuple[GeoJSON, List[List[float]]]],
    fields: Mapping[str, Any],
    reference_latitude: float,
    topology_tolerance: float,
    walking_speed: float,
) -> tuple[NodeIndex, List[_EdgeRow]]:
    """Create topology nodes and normalized edge rows from source lines."""

    endpoint_index = NodeIndex(topology_tolerance)
    next_node_id = 1

    def node_for(coordinate: Sequence[float]) -> int:
        nonlocal next_node_id
        point = to_xy(coordinate, reference_latitude)
        existing, _ = endpoint_index.nearest(point, topology_tolerance)
        if existing is not None:
            return existing
        node_id = next_node_id
        next_node_id += 1
        endpoint_index.add(node_id, point)
        return node_id

    edge_time_field = _field_name(fields, "edge_time")
    edge_time_multiplier = unit_multiplier(
        fields.get("edge_time_unit", "seconds"),
        TIME_UNITS,
        "jednostka czasu przejścia",
    )
    rows = []
    for edge_id, (feature, coordinates) in enumerate(raw_lines, start=1):
        start_id = node_for(coordinates[0])
        end_id = node_for(coordinates[-1])
        if start_id == end_id:
            continue
        length = line_length(coordinates)
        raw_time = property_value(feature, edge_time_field, "czas przejścia", default=None)
        walk_time = (
            length / walking_speed
            if raw_time in (None, "")
            else number(raw_time, edge_time_field) * edge_time_multiplier
        )
        if walk_time <= 0:
            raise PlanningError("Czas przejścia krawędzi musi być większy od zera.")
        if walk_time > MAX_EDGE_TIME_SECONDS:
            raise PlanningError("Czas przejścia pojedynczej krawędzi jest zbyt duży.")
        rows.append(
            _EdgeRow(
                edge_id=edge_id,
                start_id=start_id,
                end_id=end_id,
                length=length,
                walk_time=walk_time,
                coordinates=coordinates,
                source_id=feature.get("id", edge_id),
                properties=copy.deepcopy(feature.get("properties") or {}),
            )
        )
    if not rows:
        raise PlanningError("Po przygotowaniu topologii sieć nie zawiera żadnej poprawnej krawędzi.")
    return endpoint_index, rows


def _attach_attractions(
    attraction_features: Sequence[GeoJSON],
    endpoint_index: NodeIndex,
    reference_latitude: float,
    snap_distance: float,
    weight_field: str,
    duration_field: str,
    duration_multiplier: float,
    warnings: List[str],
) -> tuple[Dict[int, Dict[str, float]], Dict[int, List[GeoJSON]]]:
    """Snap source attractions to graph vertices and aggregate their values."""

    attraction_index = NodeIndex(snap_distance)
    for node_id, point in endpoint_index.points.items():
        attraction_index.add(node_id, point)

    values_by_vertex: Dict[int, Dict[str, float]] = {}
    features_by_vertex: Dict[int, List[GeoJSON]] = {}
    for position, feature in enumerate(attraction_features, start=1):
        lonlat = point_coordinates(feature)
        node_id, distance = attraction_index.nearest(
            to_xy(lonlat, reference_latitude), snap_distance
        )
        if node_id is None:
            if len(warnings) < MAX_WARNINGS:
                warnings.append(
                    f"Pominięto atrakcję {position}: najbliższy wierzchołek "
                    f"jest dalej niż {snap_distance:g} m."
                )
            continue
        weight = number(
            property_value(feature, weight_field, "waga atrakcji"),
            weight_field,
            default=0,
        )
        duration = number(
            property_value(feature, duration_field, "czas zwiedzania"),
            duration_field,
            default=0,
        ) * duration_multiplier
        if weight < 0 or duration < 0:
            raise PlanningError("Waga i czas zwiedzania atrakcji nie mogą być ujemne.")
        if weight > MAX_ATTRACTION_WEIGHT:
            raise PlanningError("Waga pojedynczej atrakcji jest zbyt duża.")
        if duration > MAX_VISIT_TIME_SECONDS:
            raise PlanningError("Czas zwiedzania pojedynczej atrakcji jest zbyt duży.")

        aggregate = values_by_vertex.setdefault(node_id, {"weight": 0.0, "duration": 0.0})
        aggregate["weight"] += weight
        aggregate["duration"] += duration

        copied = copy.deepcopy(feature)
        properties = copied.setdefault("properties", {})
        properties.update(
            {
                "_snap_distance_m": round(distance, 2),
                "_visit_duration_seconds": round(duration, 2),
                "_attraction_weight": weight,
                "_enabled": True,
                "_planner_id": f"{node_id}-{len(features_by_vertex.get(node_id, [])) + 1}",
            }
        )
        features_by_vertex.setdefault(node_id, []).append(copied)

    if not values_by_vertex:
        raise PlanningError("Żadnej atrakcji nie udało się dowiązać do sieci pieszej.")
    return values_by_vertex, features_by_vertex


def _create_graph(
    endpoint_index: NodeIndex,
    edge_rows: Sequence[_EdgeRow],
    attraction_values: Mapping[int, Mapping[str, float]],
    reference_latitude: float,
) -> tuple[Graph, Dict[int, EdgeGeometry]]:
    """Convert normalized preparation data to graph and geometry objects."""

    graph = Graph()
    for node_id in sorted(endpoint_index.points):
        x, y = endpoint_index.points[node_id]
        aggregate = attraction_values.get(node_id, {})
        graph.add_node(
            Node(
                id=node_id,
                x=x,
                y=y,
                weight=int(round(aggregate.get("weight", 0))),
                time=float(aggregate.get("duration", 0)),
            )
        )

    prepared_edges = {}
    for row in edge_rows:
        graph.add_edge(
            Edge(
                id=row.edge_id,
                start=graph.nodes[row.start_id],
                end=graph.nodes[row.end_id],
                length=row.length,
                time=row.walk_time,
            )
        )
        prepared_edges[row.edge_id] = EdgeGeometry(
            coordinates=row.coordinates,
            line_xy=LineString(
                [to_xy(coordinate, reference_latitude) for coordinate in row.coordinates]
            ),
            start_id=row.start_id,
            end_id=row.end_id,
            source_id=row.source_id,
            properties=row.properties,
        )
    return graph, prepared_edges


def prepare_network(
    payload: Mapping[str, Any],
    *,
    calculate_cost_matrix: bool = False,
    progress: ProgressCallback = None,
) -> PreparedNetwork:
    """Validate GeoJSON input and build a reusable walking network."""

    report_progress(progress, 5, "Sprawdzam dane wejściowe i atrybuty.")
    warnings: List[str] = []
    edge_features = feature_collection(payload.get("edges"), "Dane sieci")
    attraction_features = feature_collection(payload.get("attractions"), "Dane atrakcji")
    fields = payload.get("fields") or {}
    options = payload.get("options") or {}
    if not isinstance(fields, Mapping) or not isinstance(options, Mapping):
        raise PlanningError("Pola i opcje muszą być obiektami JSON.")

    topology_tolerance = positive_number(
        options.get("topology_tolerance_m", 1), "tolerancja topologii"
    )
    snap_distance = positive_number(
        options.get("snap_distance_m", 500), "promień dowiązania"
    )
    walking_speed = positive_number(
        options.get("walking_speed_mps", 1.4), "prędkość poruszania się"
    )
    if topology_tolerance > 1_000:
        raise PlanningError("Tolerancja topologii nie może przekraczać 1000 m.")
    if snap_distance > 100_000:
        raise PlanningError("Promień dowiązania nie może przekraczać 100 km.")
    if walking_speed > 100:
        raise PlanningError("Prędkość poruszania się nie może przekraczać 100 m/s.")

    raw_lines = _validated_lines(edge_features, warnings)
    report_progress(progress, 12, "Buduję topologię sieci pieszej.")
    latitudes = [coordinate[1] for _, line in raw_lines for coordinate in line]
    reference_latitude = sum(latitudes) / len(latitudes)
    endpoint_index, edge_rows = _build_edge_rows(
        raw_lines,
        fields,
        reference_latitude,
        topology_tolerance,
        walking_speed,
    )

    weight_field = _field_name(fields, "attraction_weight")
    duration_field = _field_name(fields, "attraction_duration")
    if not weight_field or not duration_field:
        raise PlanningError("Wskaż pola wagi oraz czasu zwiedzania atrakcji.")
    duration_multiplier = unit_multiplier(
        fields.get("attraction_duration_unit", "minutes"),
        TIME_UNITS,
        "jednostka czasu zwiedzania",
    )
    attraction_values, attractions_by_vertex = _attach_attractions(
        attraction_features,
        endpoint_index,
        reference_latitude,
        snap_distance,
        weight_field,
        duration_field,
        duration_multiplier,
        warnings,
    )
    report_progress(progress, 20, "Dowiązuję atrakcje do wierzchołków grafu.")

    graph, prepared_edges = _create_graph(
        endpoint_index, edge_rows, attraction_values, reference_latitude
    )
    if not graph.attractions:
        raise PlanningError("Wśród dowiązanych atrakcji nie ma żadnej z wagą większą od zera.")
    matrix = build_cost_matrix(graph, progress=progress) if calculate_cost_matrix else CostMatrix()
    return PreparedNetwork(
        graph=graph,
        edges=prepared_edges,
        attractions_by_vertex=attractions_by_vertex,
        reference_latitude=reference_latitude,
        warnings=warnings,
        cost_matrix=matrix,
        name_field=_field_name(fields, "attraction_name"),
        duration_field=duration_field,
        duration_multiplier=duration_multiplier,
        weight_field=weight_field,
        image_field=_field_name(fields, "attraction_image"),
        description_field=_field_name(fields, "attraction_description"),
    )


def build_cost_matrix(graph: Graph, progress: ProgressCallback = None) -> CostMatrix:
    """Calculate shortest walking times between attraction vertices."""

    matrix = CostMatrix()
    targets = set(graph.attractions)
    for source_index, source_id in enumerate(targets, start=1):
        report_progress(
            progress,
            25 + round(70 * (source_index - 1) / max(len(targets), 1)),
            f"Obliczam macierz kosztów: {source_index}/{len(targets)}.",
        )
        distances = {source_id: 0.0}
        queue = [(0.0, source_id)]
        remaining_targets = targets - {source_id}
        while queue and remaining_targets:
            current_distance, node_id = heapq.heappop(queue)
            if current_distance != distances.get(node_id):
                continue
            remaining_targets.discard(node_id)
            for neighbor_id, edge in graph.neighbors(node_id):
                candidate = current_distance + edge.time
                if candidate < distances.get(neighbor_id, float("inf")):
                    distances[neighbor_id] = candidate
                    heapq.heappush(queue, (candidate, neighbor_id))
        for target_id in targets - {source_id}:
            matrix.add_cost(source_id, target_id, distances.get(target_id, -1.0))
    report_progress(progress, 98, "Zapisuję gotowy graf i macierz kosztów.")
    return matrix


def attraction_duration_seconds(
    network: PreparedNetwork, properties: Mapping[str, Any]
) -> float:
    """Return normalized visit duration from current or legacy metadata."""

    if "_visit_duration_seconds" in properties:
        return number(properties.get("_visit_duration_seconds"), "czas zwiedzania", default=0)
    return number(
        properties.get(network.duration_field),
        network.duration_field or "czas zwiedzania",
        default=0,
    ) * network.duration_multiplier


def _ensure_attraction_metadata(network: PreparedNetwork) -> None:
    """Backfill editable metadata in networks saved by older versions."""

    for vertex_id, features in network.attractions_by_vertex.items():
        for index, feature in enumerate(features, start=1):
            properties = feature.setdefault("properties", {})
            properties.setdefault("_planner_id", f"{vertex_id}-{index}")
            properties.setdefault("_enabled", True)
            properties.setdefault(
                "_visit_duration_seconds",
                round(attraction_duration_seconds(network, properties), 2),
            )
            properties.setdefault(
                "_attraction_weight",
                number(
                    properties.get(network.weight_field),
                    network.weight_field or "waga atrakcji",
                    default=0,
                ),
            )


def recalculate_attraction_nodes(network: PreparedNetwork) -> None:
    """Rebuild graph attraction aggregates after editor changes."""

    _ensure_attraction_metadata(network)
    for vertex_id, features in network.attractions_by_vertex.items():
        weight = 0.0
        duration = 0.0
        for feature in features:
            properties = feature.get("properties") or {}
            if not properties.get("_enabled", True):
                continue
            weight += number(properties.get("_attraction_weight"), "waga atrakcji", default=0)
            duration += attraction_duration_seconds(network, properties)
        network.graph.set_attraction_values(vertex_id, int(round(weight)), duration)


def list_prepared_attractions(network: PreparedNetwork) -> List[Dict[str, Any]]:
    """Return normalized attraction rows for the browser editor."""

    _ensure_attraction_metadata(network)
    result = []
    for vertex_id, features in network.attractions_by_vertex.items():
        for feature in features:
            properties = feature.get("properties") or {}
            result.append(
                {
                    "id": str(properties["_planner_id"]),
                    "name": str(
                        properties.get(network.name_field)
                        or f"Atrakcja {properties['_planner_id']}"
                    ),
                    "duration_seconds": round(
                        attraction_duration_seconds(network, properties), 2
                    ),
                    "weight": number(
                        properties.get("_attraction_weight"),
                        "waga atrakcji",
                        default=0,
                    ),
                    "enabled": bool(properties.get("_enabled", True)),
                    "vertex_id": vertex_id,
                }
            )
    return sorted(
        result,
        key=lambda item: (-item["weight"], item["name"].casefold()),
    )


def update_prepared_attractions(
    network: PreparedNetwork, edits: Any
) -> List[Dict[str, Any]]:
    """Apply browser editor changes and return the updated rows."""

    if not isinstance(edits, list) or not edits:
        raise PlanningError("Nie przekazano zmian atrakcji do zapisania.")
    _ensure_attraction_metadata(network)
    features_by_id = {
        str((feature.get("properties") or {})["_planner_id"]): feature
        for features in network.attractions_by_vertex.values()
        for feature in features
    }
    unknown_ids = []
    for edit in edits:
        attraction_id = str((edit or {}).get("id") or "")
        feature = features_by_id.get(attraction_id)
        if feature is None:
            unknown_ids.append(attraction_id)
            continue
        duration = number((edit or {}).get("duration_seconds"), "czas zwiedzania")
        if duration < 0:
            raise PlanningError("Czas zwiedzania nie może być ujemny.")
        enabled = (edit or {}).get("enabled")
        if not isinstance(enabled, bool):
            raise PlanningError("Pole aktywności atrakcji musi mieć wartość logiczną.")
        properties = feature.setdefault("properties", {})
        properties["_visit_duration_seconds"] = round(duration, 2)
        properties["_enabled"] = enabled
    if unknown_ids:
        raise PlanningError(f"Nie znaleziono atrakcji: {', '.join(unknown_ids)}.")
    recalculate_attraction_nodes(network)
    return list_prepared_attractions(network)


def prepared_network_preview(network: PreparedNetwork) -> GeoJSON:
    """Return source geometry needed to select a start point in the browser."""

    edge_features = [
        {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": geometry.coordinates},
            "properties": {**geometry.properties, "prepared_edge_id": edge_id},
        }
        for edge_id, geometry in network.edges.items()
    ]
    attractions = [
        feature
        for features in network.attractions_by_vertex.values()
        for feature in features
    ]
    return {
        "edges": {"type": "FeatureCollection", "features": edge_features},
        "attractions": {"type": "FeatureCollection", "features": attractions},
    }
