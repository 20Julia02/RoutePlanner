from __future__ import annotations

import math
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional, Sequence, Tuple

from planning_models import Coordinate, GeoJSON, PlanningError, ProgressCallback


EARTH_RADIUS_M = 6_371_008.8


def number(value: Any, label: str, *, default: Optional[float] = None) -> float:
    """Parse a finite number and produce a user-facing validation error."""

    if value in (None, ""):
        if default is not None:
            return default
        raise PlanningError(f"Brak wartości pola „{label}”.")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise PlanningError(f"Pole „{label}” musi zawierać liczbę.") from exc
    if not math.isfinite(result):
        raise PlanningError(f"Pole „{label}” musi zawierać skończoną liczbę.")
    return result


def positive_number(value: Any, label: str, *, minimum: float = 0.0) -> float:
    """Parse a number that must be greater than the supplied minimum."""

    result = number(value, label)
    if result <= minimum:
        raise PlanningError(f"Pole „{label}” musi być większe od {minimum:g}.")
    return result


def unit_multiplier(unit: str, allowed: Mapping[str, float], label: str) -> float:
    """Resolve a supported unit to its multiplier."""

    try:
        return allowed[str(unit).lower()]
    except KeyError as exc:
        options = ", ".join(allowed)
        raise PlanningError(f"Nieznana jednostka pola „{label}”. Dostępne: {options}.") from exc


def feature_collection(
    value: Any, label: str, *, maximum_features: Optional[int] = None
) -> list[GeoJSON]:
    """Validate and return features from a non-empty GeoJSON collection."""

    if not isinstance(value, dict) or value.get("type") != "FeatureCollection":
        raise PlanningError(f"{label} muszą być kolekcją GeoJSON FeatureCollection.")
    features = value.get("features")
    if not isinstance(features, list) or not features:
        raise PlanningError(f"{label} nie zawierają żadnych obiektów.")
    if maximum_features is not None and len(features) > maximum_features:
        raise PlanningError(
            f"{label} przekraczają limit {maximum_features} obiektów."
        )
    if any(not isinstance(feature, dict) for feature in features):
        raise PlanningError(f"{label} zawierają obiekt w nieprawidłowym formacie.")
    return features


def iter_lines(features: Iterable[GeoJSON]) -> Iterator[Tuple[GeoJSON, list[list[float]]]]:
    """Yield individual lines from LineString and MultiLineString features."""

    for feature in features:
        if not isinstance(feature, dict):
            raise PlanningError("Obiekt sieci ma nieprawidłowy format.")
        geometry = feature.get("geometry") or {}
        if not isinstance(geometry, dict):
            raise PlanningError("Obiekt sieci ma nieprawidłową geometrię.")
        geometry_type = geometry.get("type")
        coordinates = geometry.get("coordinates")
        if geometry_type == "LineString":
            yield feature, coordinates
        elif geometry_type == "MultiLineString":
            for part in coordinates or []:
                yield feature, part
        elif geometry_type is None and not coordinates:
            continue
        else:
            raise PlanningError(
                "Warstwa sieci może zawierać wyłącznie geometrie LineString lub MultiLineString."
            )


def point_coordinates(feature: GeoJSON) -> Coordinate:
    """Return longitude and latitude from an attraction feature."""

    geometry = feature.get("geometry") or {}
    if not isinstance(geometry, dict):
        raise PlanningError("Atrakcja zawiera nieprawidłową geometrię.")
    if geometry.get("type") != "Point":
        raise PlanningError("Warstwa atrakcji może zawierać wyłącznie geometrie Point.")
    coordinates = geometry.get("coordinates") or []
    if len(coordinates) < 2:
        raise PlanningError("Atrakcja zawiera nieprawidłową geometrię Point.")
    lon = number(coordinates[0], "długość geograficzna atrakcji")
    lat = number(coordinates[1], "szerokość geograficzna atrakcji")
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise PlanningError("Dane atrakcji muszą być zapisane w WGS 84 (EPSG:4326).")
    return lon, lat


def valid_line(coordinates: Any) -> list[list[float]]:
    """Validate a WGS84 line and normalize its coordinates to floats."""

    if not isinstance(coordinates, list) or len(coordinates) < 2:
        raise PlanningError("Każda krawędź sieci musi mieć co najmniej dwa punkty.")
    result = []
    for coordinate in coordinates:
        if not isinstance(coordinate, (list, tuple)) or len(coordinate) < 2:
            raise PlanningError("Krawędź zawiera nieprawidłową współrzędną.")
        lon = number(coordinate[0], "długość geograficzna")
        lat = number(coordinate[1], "szerokość geograficzna")
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise PlanningError("Dane muszą być zapisane w WGS 84 (EPSG:4326).")
        result.append([lon, lat])
    return result


def to_xy(coordinate: Sequence[float], reference_latitude: float) -> Coordinate:
    """Project WGS84 coordinates to a local metric equirectangular plane."""

    lon, lat = coordinate[:2]
    x = math.radians(float(lon)) * EARTH_RADIUS_M * math.cos(math.radians(reference_latitude))
    y = math.radians(float(lat)) * EARTH_RADIUS_M
    return x, y


def to_lonlat(coordinate: Sequence[float], reference_latitude: float) -> Coordinate:
    """Convert local metric coordinates back to longitude and latitude."""

    x, y = coordinate[:2]
    lon = math.degrees(float(x) / (EARTH_RADIUS_M * math.cos(math.radians(reference_latitude))))
    lat = math.degrees(float(y) / EARTH_RADIUS_M)
    return lon, lat


def haversine_distance(first: Sequence[float], second: Sequence[float]) -> float:
    """Calculate great-circle distance in metres between WGS84 points."""

    lon1, lat1, lon2, lat2 = map(math.radians, (first[0], first[1], second[0], second[1]))
    delta_lon = lon2 - lon1
    delta_lat = lat2 - lat1
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(value)))


def line_length(coordinates: Sequence[Sequence[float]]) -> float:
    """Calculate the geodesic length of a line in metres."""

    return sum(haversine_distance(first, second) for first, second in zip(coordinates, coordinates[1:]))


class NodeIndex:
    """Uniform-grid index used for fast nearest-node lookups."""

    def __init__(self, cell_size: float):
        self.cell_size = max(float(cell_size), 0.01)
        self.points: Dict[int, Coordinate] = {}
        self.cells: Dict[Tuple[int, int], list[int]] = {}

    def _cell(self, point: Coordinate) -> Tuple[int, int]:
        return math.floor(point[0] / self.cell_size), math.floor(point[1] / self.cell_size)

    def add(self, node_id: int, point: Coordinate) -> None:
        """Add a node to the index."""

        self.points[node_id] = point
        self.cells.setdefault(self._cell(point), []).append(node_id)

    def nearest(self, point: Coordinate, maximum_distance: float) -> Tuple[Optional[int], float]:
        """Return the nearest indexed node within the maximum distance."""

        cell_x, cell_y = self._cell(point)
        cell_radius = max(1, math.ceil(maximum_distance / self.cell_size))
        best_id: Optional[int] = None
        best_distance = float("inf")
        for offset_x in range(-cell_radius, cell_radius + 1):
            for offset_y in range(-cell_radius, cell_radius + 1):
                for node_id in self.cells.get((cell_x + offset_x, cell_y + offset_y), []):
                    candidate = self.points[node_id]
                    distance = math.hypot(candidate[0] - point[0], candidate[1] - point[1])
                    if distance < best_distance:
                        best_id, best_distance = node_id, distance
        if best_distance > maximum_distance:
            return None, best_distance
        return best_id, best_distance


def property_value(feature: GeoJSON, field: str, label: str, *, default: Any = None) -> Any:
    """Read a configured property and report missing fields consistently."""

    properties = feature.get("properties") or {}
    if not isinstance(properties, Mapping):
        raise PlanningError("Obiekt GeoJSON zawiera nieprawidłowe właściwości.")
    if not field:
        return default
    if field not in properties:
        raise PlanningError(f"Nie znaleziono pola „{field}” ({label}) w jednym z obiektów.")
    return properties.get(field)


def report_progress(progress: ProgressCallback, value: int, message: str) -> None:
    """Invoke an optional progress callback."""

    if progress:
        progress(value, message)
