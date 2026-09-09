"""Shared data structures used while preparing and planning routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from shapely.geometry import LineString

from models import CostMatrix, Graph


GeoJSON = Dict[str, Any]
Coordinate = tuple[float, float]
ProgressCallback = Optional[Callable[[int, str], None]]


class PlanningError(ValueError):
    """A validation or routing error safe to present to an API client."""


@dataclass
class EdgeGeometry:
    """Source geometry and metadata associated with an internal graph edge."""

    coordinates: List[List[float]]
    line_xy: LineString
    start_id: int
    end_id: int
    source_id: Any
    properties: Dict[str, Any]


@dataclass
class PreparedNetwork:
    """Reusable walking graph together with source attractions and geometries."""

    graph: Graph
    edges: Dict[int, EdgeGeometry]
    attractions_by_vertex: Dict[int, List[GeoJSON]]
    reference_latitude: float
    warnings: List[str]
    cost_matrix: CostMatrix
    name_field: str
    duration_field: str = "duration"
    duration_multiplier: float = 60.0
    weight_field: str = "weight"
    image_field: str = ""
    description_field: str = ""
