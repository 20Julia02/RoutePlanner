"""Stable public API for route planning and network preparation.

Implementation details live in focused modules.  This facade keeps existing
imports used by the HTTP layer, command-line scripts and external callers.
"""

from network_preparation import (
    build_cost_matrix,
    list_prepared_attractions,
    prepare_network,
    prepared_network_preview,
    recalculate_attraction_nodes,
    update_prepared_attractions,
)
from planning_models import EdgeGeometry, PlanningError, PreparedNetwork
from route_planning import plan_prepared_network, plan_route


__all__ = [
    "EdgeGeometry",
    "PlanningError",
    "PreparedNetwork",
    "build_cost_matrix",
    "list_prepared_attractions",
    "plan_prepared_network",
    "plan_route",
    "prepare_network",
    "prepared_network_preview",
    "recalculate_attraction_nodes",
    "update_prepared_attractions",
]
