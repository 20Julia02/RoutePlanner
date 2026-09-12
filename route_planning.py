"""Route planning over an already prepared walking network."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from shapely.geometry import Point
from shapely.ops import unary_union


MAX_SELECTED_ATTRACTIONS = 50
MAX_NEARBY_CANDIDATES_PER_DAY = 25

from geojson_utils import NodeIndex, number, positive_number, report_progress, to_lonlat, to_xy
from models import Graph, RouteResult
from network_preparation import (
    attraction_duration_seconds,
    prepare_network,
    update_prepared_attractions,
)
from optimizer import RouteOptimizer
from planning_models import GeoJSON, PlanningError, PreparedNetwork, ProgressCallback
from shortest_path_alg import PathAlgorithm


@dataclass(frozen=True)
class _RouteOptions:
    """Validated settings used by a single planning request."""

    days: int
    max_hours: float
    nearby_distance: float
    start_snap_distance: float
    must_see_per_day: int

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "_RouteOptions":
        options = payload.get("options") or {}
        if not isinstance(options, Mapping):
            raise PlanningError("Opcje planowania muszą być obiektem JSON.")
        days_value = positive_number(options.get("days", 1), "liczba dni")
        if not days_value.is_integer():
            raise PlanningError("Liczba dni musi być liczbą całkowitą.")
        days = int(days_value)
        if days > 14:
            raise PlanningError("Liczba dni nie może być większa niż 14.")
        max_hours = positive_number(
            options.get("max_hours_per_day", 8), "maksymalny czas dzienny"
        )
        if max_hours > 24:
            raise PlanningError("Maksymalny czas dzienny nie może przekraczać 24 godzin.")
        nearby_distance = positive_number(
            options.get("nearby_distance_m", 500), "promień dodatkowych atrakcji"
        )
        start_snap_distance = positive_number(
            options.get("start_snap_distance_m", 5000),
            "promień dowiązania startu",
        )
        must_see_value = positive_number(
            options.get("must_see_per_day", 2),
            "liczba atrakcji must-see na dzień",
        )
        if not must_see_value.is_integer():
            raise PlanningError("Liczba atrakcji must-see musi być liczbą całkowitą.")
        must_see_per_day = int(must_see_value)
        if nearby_distance > 50_000:
            raise PlanningError("Promień dodatkowych atrakcji nie może przekraczać 50 km.")
        if start_snap_distance > 100_000:
            raise PlanningError("Promień dowiązania startu nie może przekraczać 100 km.")
        maximum_must_see = int(max_hours) + 2
        if must_see_per_day > maximum_must_see:
            raise PlanningError(
                "Liczba najważniejszych atrakcji na dzień nie może przekraczać "
                f"{maximum_must_see} dla wybranego czasu zwiedzania."
            )
        if days * must_see_per_day > MAX_SELECTED_ATTRACTIONS:
            raise PlanningError(
                f"Plan może obejmować najwyżej {MAX_SELECTED_ATTRACTIONS} "
                "atrakcji must-see łącznie."
            )
        return cls(
            days=days,
            max_hours=max_hours,
            nearby_distance=nearby_distance,
            start_snap_distance=start_snap_distance,
            must_see_per_day=must_see_per_day,
        )


def _nearest_start(
    network: PreparedNetwork, start: Mapping[str, Any], maximum_distance: float
) -> Tuple[int, float]:
    """Snap the selected start point to the nearest graph vertex."""

    if not isinstance(start, Mapping):
        raise PlanningError("Punkt startowy musi być obiektem JSON.")
    lon = number(start.get("lon"), "długość geograficzna punktu startowego")
    lat = number(start.get("lat"), "szerokość geograficzna punktu startowego")
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise PlanningError("Punkt startowy musi mieć współrzędne WGS 84.")
    index = NodeIndex(maximum_distance)
    for node_id, node in network.graph.nodes.items():
        index.add(node_id, (node.x, node.y))
    node_id, distance = index.nearest(
        to_xy((lon, lat), network.reference_latitude), maximum_distance
    )
    if node_id is None:
        raise PlanningError(
            f"Punkt startowy leży dalej niż {maximum_distance:g} m od sieci pieszej."
        )
    return node_id, distance


def _reachable_node_ids(graph: Graph, start_id: int) -> set[int]:
    """Return the connected component containing the selected start node."""

    reachable = {start_id}
    pending = [start_id]
    while pending:
        node_id = pending.pop()
        for neighbor_id, _ in graph.neighbors(node_id):
            if neighbor_id not in reachable:
                reachable.add(neighbor_id)
                pending.append(neighbor_id)
    return reachable


def _nearby_attractions(
    network: PreparedNetwork,
    route_edges: Sequence[int],
    excluded: set[int],
    distance: float,
) -> List[int]:
    """Find unused attractions within a metric buffer of the current route."""

    lines = [
        network.edges[edge_id].line_xy
        for edge_id in route_edges
        if edge_id in network.edges
    ]
    if not lines:
        return []
    route = unary_union(lines)
    return [
        node_id
        for node_id, node in network.graph.attractions.items()
        if node_id not in excluded and Point(node.x, node.y).distance(route) <= distance
    ]


def _route_features(network: PreparedNetwork, result: RouteResult, day: int) -> GeoJSON:
    """Convert an ordered graph path to display-ready GeoJSON lines."""

    features = []
    node_pairs = zip(result.nodes_path, result.nodes_path[1:])
    for sequence, ((source_id, target_id), edge_id) in enumerate(
        zip(node_pairs, result.edges_path), start=1
    ):
        geometry = network.edges[edge_id]
        coordinates = [list(point) for point in geometry.coordinates]
        if not (source_id == geometry.start_id and target_id == geometry.end_id):
            coordinates.reverse()
        source_lonlat = to_lonlat(
            (network.graph.nodes[source_id].x, network.graph.nodes[source_id].y),
            network.reference_latitude,
        )
        target_lonlat = to_lonlat(
            (network.graph.nodes[target_id].x, network.graph.nodes[target_id].y),
            network.reference_latitude,
        )
        coordinates[0] = [*source_lonlat, *coordinates[0][2:]]
        coordinates[-1] = [*target_lonlat, *coordinates[-1][2:]]
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coordinates},
                "properties": {
                    "day": day,
                    "sequence": sequence,
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _attraction_features(
    network: PreparedNetwork, vertex_ids: Sequence[int], day: int
) -> Tuple[GeoJSON, List[Dict[str, Any]]]:
    """Build numbered attraction features and matching itinerary rows."""

    features: List[GeoJSON] = []
    itinerary = []
    position = 1
    for vertex_id in vertex_ids:
        vertex = network.graph.nodes[vertex_id]
        vertex_coordinates = to_lonlat(
            (vertex.x, vertex.y), network.reference_latitude
        )
        for source in network.attractions_by_vertex.get(vertex_id, []):
            source_properties = source.get("properties") or {}
            if not isinstance(source_properties, Mapping):
                continue
            properties = dict(source_properties)
            if not properties.get("_enabled", True):
                continue
            name = str(
                properties.get(network.name_field) or f"Atrakcja {position}"
            )[:300]
            image = properties.get(network.image_field) if network.image_field else None
            description = (
                properties.get(network.description_field)
                if network.description_field
                else None
            )
            safe_properties = {
                "day": day,
                "visit_order": position,
                "name": name,
                "vertex_id": vertex_id,
                "vertex_coordinates": [round(value, 7) for value in vertex_coordinates],
                "image": str(image)[:2048] if image is not None else None,
                "description": str(description)[:5000] if description is not None else None,
            }
            source_coordinates = (source.get("geometry") or {}).get("coordinates") or []
            feature = {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(value) for value in source_coordinates[:2]],
                },
                "properties": safe_properties,
            }
            features.append(feature)
            itinerary.append(
                {
                    "order": position,
                    "name": name,
                    "vertex_id": vertex_id,
                    "coordinates": feature["geometry"]["coordinates"][:2],
                    "duration_seconds": round(
                        attraction_duration_seconds(network, properties), 2
                    ),
                }
            )
            position += 1
    return {"type": "FeatureCollection", "features": features}, itinerary


def _attraction_names(network: PreparedNetwork, vertex_ids: Iterable[int]) -> List[str]:
    """Resolve graph attraction IDs to active source attraction names."""

    names: List[str] = []
    for vertex_id in vertex_ids:
        active_features = [
            feature
            for feature in network.attractions_by_vertex.get(vertex_id, [])
            if (feature.get("properties") or {}).get("_enabled", True)
        ]
        if not active_features:
            names.append("Atrakcja bez nazwy")
            continue
        for feature in active_features:
            properties = feature.get("properties") or {}
            raw_name = properties.get(network.name_field) if network.name_field else None
            name = str(raw_name).strip()[:300] if raw_name is not None else ""
            names.append(name or "Atrakcja bez nazwy")
    return names


def _empty_day(day_number: int) -> Dict[str, Any]:
    """Create a consistent empty-day response object."""

    empty_collection = {"type": "FeatureCollection", "features": []}
    return {
        "day": day_number,
        "total_time_seconds": 0,
        "walking_time_seconds": 0,
        "total_weight": 0,
        "route": copy.deepcopy(empty_collection),
        "attractions": copy.deepcopy(empty_collection),
        "itinerary": [],
    }


def _recover_route(
    optimizer: RouteOptimizer, attraction_ids: Sequence[int]
) -> tuple[List[int], Optional[RouteResult], List[int]]:
    """Keep reachable attractions when the full closed route cannot be built."""

    recovered_ids: List[int] = []
    recovered_result: Optional[RouteResult] = None
    skipped_ids = []
    for attraction_id in attraction_ids:
        candidate_ids = [*recovered_ids, attraction_id]
        candidate_result = optimizer.build_exact_route(candidate_ids)
        if candidate_result is None:
            skipped_ids.append(attraction_id)
            continue
        recovered_ids = candidate_ids
        recovered_result = candidate_result
    return recovered_ids, recovered_result, skipped_ids


def _add_nearby_attractions(
    network: PreparedNetwork,
    optimizer: RouteOptimizer,
    attraction_ids: List[int],
    route_result: RouteResult,
    visited: set[int],
    maximum_distance: float,
    day_routes: List[List[int]],
    day_index: int,
) -> tuple[List[int], RouteResult]:
    """Insert nearby places and cheaply take them from future daily regions."""

    excluded = visited | set(attraction_ids)
    candidates = _nearby_attractions(
        network, route_result.edges_path, excluded, maximum_distance
    )

    def future_day(candidate: int) -> Optional[int]:
        return next(
            (
                future_index
                for future_index in range(day_index + 1, len(day_routes))
                if candidate in day_routes[future_index]
            ),
            None,
        )

    candidates.sort(
        key=lambda candidate: (
            future_day(candidate) is None,
            -network.graph.nodes[candidate].weight,
            network.graph.nodes[candidate].time,
        )
    )
    for candidate in candidates[:MAX_NEARBY_CANDIDATES_PER_DAY]:
        future_index = future_day(candidate)
        if future_index is not None:
            if not optimizer.should_move_from_future_day(
                attraction_ids,
                day_routes[future_index],
                candidate,
            ):
                continue
        else:
            closest_region = optimizer.closest_day_region(
                candidate,
                day_routes,
                range(day_index, len(day_routes)),
            )
            if closest_region != day_index:
                continue
        inserted, candidate_ids, candidate_result = optimizer.try_insert_attraction_a_star(
            attraction_ids, route_result, candidate
        )
        if inserted:
            attraction_ids, route_result = candidate_ids, candidate_result
            day_routes[day_index] = list(attraction_ids)
            if future_index is not None:
                day_routes[future_index] = [
                    attraction_id
                    for attraction_id in day_routes[future_index]
                    if attraction_id != candidate
                ]
    return attraction_ids, route_result


def plan_route(payload: Mapping[str, Any], progress: ProgressCallback = None) -> GeoJSON:
    """Prepare raw input and calculate a route in one request."""

    preparation_progress = None
    if progress:
        preparation_progress = lambda value, message: progress(
            min(7, round(value * 0.07)), message
        )
    network = prepare_network(payload, progress=preparation_progress)
    return plan_prepared_network(network, payload, progress=progress)


def plan_prepared_network(
    network: PreparedNetwork,
    payload: Mapping[str, Any],
    progress: ProgressCallback = None,
) -> GeoJSON:
    """Calculate a multi-day plan using a reusable prepared network."""

    edits = payload.get("attraction_edits")
    if edits:
        # Cached public networks are shared by requests, so customize a private
        # copy of only the mutable data used by the optimizer.
        customized = copy.copy(network)
        customized.graph = network.graph.copy_for_attraction_edits()
        customized.attractions_by_vertex = copy.deepcopy(network.attractions_by_vertex)
        customized.warnings = list(network.warnings)
        update_prepared_attractions(customized, edits)
        network = customized
    elif edits is not None and not isinstance(edits, list):
        raise PlanningError("Nieprawidłowy format zmian atrakcji.")

    report_progress(progress, 8, "Wczytuję przygotowany graf i macierz kosztów.")
    options = _RouteOptions.from_payload(payload)
    start_id, start_distance = _nearest_start(
        network, payload.get("start") or {}, options.start_snap_distance
    )
    report_progress(progress, 18, "Dowiązuję punkt startowy do sieci.")
    optimizer = RouteOptimizer(
        network.graph,
        network.cost_matrix,
        PathAlgorithm(network.graph),
        start_id,
        options.max_hours,
    )

    reachable_nodes = _reachable_node_ids(network.graph, start_id)
    unreachable_attractions = set(network.graph.attractions) - reachable_nodes
    must_see = optimizer.choose_best_attractions(
        options.days,
        multiplier=options.must_see_per_day,
        candidate_ids=reachable_nodes,
    )
    day_routes = optimizer.split_attractions_between_days_spatially(
        must_see, options.days
    )
    day_routes = optimizer.repair_day_assignments(day_routes)
    report_progress(progress, 30, "Skracam łączny czas przemieszczania między atrakcjami.")

    assigned = {attraction_id for route in day_routes for attraction_id in route}
    visited: set[int] = set()
    warnings = list(network.warnings)
    if unreachable_attractions:
        warnings.append(
            "Pominięto atrakcje nieosiągalne z wybranego punktu startowego "
            f"({len(unreachable_attractions)})."
        )
    missing = [attraction_id for attraction_id in must_see if attraction_id not in assigned]
    if missing:
        warnings.append(
            "Nie udało się przydzielić atrakcji must-see: "
            f"{', '.join(_attraction_names(network, missing))}."
        )

    result_days = []
    combined_routes: List[GeoJSON] = []
    combined_attractions: List[GeoJSON] = []
    for day_index, initial_route in enumerate(day_routes):
        day_number = day_index + 1
        report_progress(
            progress,
            35 + round(55 * day_index / max(options.days, 1)),
            f"Wyznaczam i uzupełniam trasę dla dnia {day_number}/{options.days}.",
        )
        if not initial_route:
            warnings.append(f"Dzień {day_number} nie ma przypisanych atrakcji.")
            result_days.append(_empty_day(day_number))
            continue

        current_route = [
            attraction_id
            for attraction_id in initial_route
            if attraction_id not in visited
        ]
        if not current_route:
            warnings.append(f"Dzień {day_number} nie ma przypisanych atrakcji.")
            result_days.append(_empty_day(day_number))
            continue
        current_result = optimizer.build_exact_route(current_route)
        if current_result is None:
            current_route, current_result, skipped = _recover_route(
                optimizer, current_route
            )
            if skipped:
                warnings.append(
                    f"Dzień {day_number}: pominięto nieosiągalne atrakcje "
                    f"{', '.join(_attraction_names(network, skipped))}, "
                    "ale zachowano pozostałą trasę dnia."
                )
            if current_result is None:
                warnings.append(
                    f"Nie znaleziono przejezdnej trasy dla dnia {day_number}."
                )
                result_days.append(_empty_day(day_number))
                continue

        if current_result.total_time > optimizer.duration_sec:
            warnings.append(
                f"Dzień {day_number}: atrakcje must-see przekraczają ustawiony limit czasu."
            )
        else:
            current_route, current_result = _add_nearby_attractions(
                network,
                optimizer,
                current_route,
                current_result,
                visited,
                options.nearby_distance,
                day_routes,
                day_index,
            )

        day_routes[day_index] = list(current_route)
        visited.update(current_route)

        route_collection = _route_features(network, current_result, day_number)
        attraction_collection, itinerary = _attraction_features(
            network, current_route, day_number
        )
        combined_routes.extend(route_collection["features"])
        combined_attractions.extend(attraction_collection["features"])
        result_days.append(
            {
                "day": day_number,
                "total_time_seconds": round(current_result.total_time, 2),
                "walking_time_seconds": round(
                    current_result.total_time
                    - network.graph.get_attr_time(current_route),
                    2,
                ),
                "total_weight": network.graph.get_attr_weight(current_route),
                "route": route_collection,
                "attractions": attraction_collection,
                "itinerary": itinerary,
            }
        )

    if not combined_routes:
        raise PlanningError(
            "Nie udało się wyznaczyć żadnej trasy. "
            "Sprawdź spójność sieci i parametry dowiązania."
        )

    report_progress(progress, 94, "Przygotowuję geometrie i plan do wyświetlenia.")
    start = payload.get("start") or {}
    return {
        "days": result_days,
        "routes": {"type": "FeatureCollection", "features": combined_routes},
        "selected_attractions": {
            "type": "FeatureCollection",
            "features": combined_attractions,
        },
        "start": {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [float(start["lon"]), float(start["lat"])],
            },
            "properties": {
                "snapped_vertex_id": start_id,
                "snap_distance_m": round(start_distance, 2),
            },
        },
        "warnings": warnings,
        "meta": {
            "nodes": len(network.graph.nodes),
            "edges": len(network.edges),
            "attraction_vertices": len(network.graph.attractions),
            "cost_matrix_entries": len(network.cost_matrix),
            "crs": "EPSG:4326",
        },
    }
