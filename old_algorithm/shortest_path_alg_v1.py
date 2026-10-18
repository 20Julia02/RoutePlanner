"""Shortest-path search retained by the legacy route optimizer."""

import heapq
import math
from typing import Collection, Dict, List, Optional, Tuple

from models import Edge, Graph, PathResult


class PathAlgorithm:
    """Legacy A* search with its original node-reuse penalty."""

    def __init__(self, graph: Graph):
        self._graph = graph
        time_per_meter = []
        for edge in graph.edges.values():
            endpoint_distance = math.hypot(
                edge.end.x - edge.start.x,
                edge.end.y - edge.start.y,
            )
            if endpoint_distance > 0 and edge.time > 0:
                time_per_meter.append(edge.time / endpoint_distance)
        self._minimum_time_per_meter = min(time_per_meter, default=0.0)

    def heuristic(self, node_id: int, goal_id: int) -> float:
        """Return an admissible lower bound of travel time to the goal."""

        node = self._graph.nodes[node_id]
        goal = self._graph.nodes[goal_id]
        distance = math.hypot(goal.x - node.x, goal.y - node.y)
        return distance * self._minimum_time_per_meter

    def solve_a_star(
        self,
        start_id: int,
        end_id: int,
        penalties: Collection[int],
    ) -> Optional[PathResult]:
        """Find the fastest path while penalizing previously used nodes."""

        penalized_nodes = set(penalties)
        queue = [(self.heuristic(start_id, end_id), start_id)]
        visited = set()
        cost = {start_id: 0.0}
        real_time = {start_id: 0.0}
        previous: Dict[int, Tuple[int, Edge]] = {}

        while queue:
            _, node_id = heapq.heappop(queue)
            if node_id in visited:
                continue
            visited.add(node_id)
            if node_id == end_id:
                break

            for neighbor_id, edge in self._graph.neighbors(node_id):
                candidate_cost = cost[node_id] + edge.time
                if neighbor_id in penalized_nodes:
                    candidate_cost += edge.time * 10
                if candidate_cost >= cost.get(neighbor_id, float("inf")):
                    continue

                cost[neighbor_id] = candidate_cost
                real_time[neighbor_id] = real_time[node_id] + edge.time
                previous[neighbor_id] = (node_id, edge)
                estimated_total = candidate_cost + self.heuristic(neighbor_id, end_id)
                heapq.heappush(queue, (estimated_total, neighbor_id))

        if end_id not in cost:
            return None
        total_length, nodes, edges = self._reconstruct_path(end_id, previous)
        return PathResult(
            time=real_time[end_id],
            length=total_length,
            nodes=nodes,
            edges=edges,
        )

    @staticmethod
    def _reconstruct_path(
        end_id: int,
        previous: Dict[int, Tuple[int, Edge]],
    ) -> Tuple[float, List[int], List[int]]:
        path_nodes = [end_id]
        path_edges = []
        current = end_id
        while current in previous:
            current, edge = previous[current]
            path_edges.append(edge)
            path_nodes.append(current)
        path_nodes.reverse()
        path_edges.reverse()
        return (
            sum(edge.length for edge in path_edges),
            path_nodes,
            [edge.id for edge in path_edges],
        )
