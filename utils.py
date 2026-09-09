"""Small geometry-independent graph utilities."""

from __future__ import annotations

import math
from typing import Optional, Tuple

from models import Graph


def get_nearest_vertex_id(point_xy: Tuple[float, float], graph: Graph, maximum_distance: float) -> Optional[int]:
    """Return the closest graph node in the graph coordinate system."""
    closest_id = None
    closest_distance = float("inf")
    for node_id, node in graph.nodes.items():
        distance = math.hypot(node.x - point_xy[0], node.y - point_xy[1])
        if distance < closest_distance:
            closest_id, closest_distance = node_id, distance
    return closest_id if closest_distance <= maximum_distance else None
