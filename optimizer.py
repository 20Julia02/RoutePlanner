"""Heuristics for selecting, distributing and ordering attractions."""

import logging
import math
from itertools import pairwise
from typing import Collection, Dict, List, Optional, Tuple

from models import CostMatrix, Graph, RouteResult
from shortest_path_alg import PathAlgorithm


logger = logging.getLogger(__name__)


class RouteOptimizer:
    """Build time-limited daily routes from attraction vertex IDs."""

    ASSIGNMENT_LIMIT_OVERAGE_RATE = 0.15

    def __init__(
        self,
        graph: Graph,
        cost_matrix: CostMatrix,
        algorithm: PathAlgorithm,
        start_id: int,
        duration_hours: float,
    ):
        self.graph = graph
        self.cost_matrix = cost_matrix
        self.alg = algorithm
        self.start_id = start_id
        self.duration_sec = duration_hours * 3600
        self._a_star_time_cache: Dict[Tuple[int, int], float] = {}

    def _estimate_walk_time(self, attraction_ids: List[int]) -> float:
        """Estimate closed-route walking time using cached pairwise costs."""

        if not attraction_ids:
            return 0.0
        total = 0.0
        route_points = [self.start_id, *attraction_ids, self.start_id]
        for source_id, target_id in pairwise(route_points):
            cost = self._travel_time(source_id, target_id)
            if math.isinf(cost):
                return float("inf")
            total += cost
        return total

    def estimate_total_route_time(self, attraction_ids: List[int]) -> float:
        """Estimate walking plus visit time for an ordered attraction list."""

        walk_time = self._estimate_walk_time(attraction_ids)
        if math.isinf(walk_time):
            return float("inf")
        return walk_time + self.graph.get_attr_time(attraction_ids)

    def _get_a_star_time(self, source_id: int, target_id: int) -> float:
        """Return an unpenalized pairwise travel time from the local cache."""

        if source_id == target_id:
            return 0.0
        key = (source_id, target_id)
        if key not in self._a_star_time_cache:
            result = self.alg.solve_a_star(source_id, target_id)
            travel_time = float("inf") if result is None else result.time
            self._a_star_time_cache[key] = travel_time
            # Walking edges are undirected, so the reverse query is identical.
            self._a_star_time_cache[(target_id, source_id)] = travel_time
        return self._a_star_time_cache[key]

    def _travel_time(self, source_id: int, target_id: int) -> float:
        """Return the prepared pairwise cost or calculate it when unavailable."""

        if source_id == target_id:
            return 0.0
        matrix_cost = self.cost_matrix.get_cost(source_id, target_id)
        if matrix_cost is not None and matrix_cost >= 0:
            return matrix_cost
        return self._get_a_star_time(source_id, target_id)

    def build_exact_route(self, attractions: List[int]) -> Optional[RouteResult]:
        """Build a closed A* route while discouraging repeated edge use."""

        if not attractions:
            return None
        route_points = [self.start_id, *attractions, self.start_id]
        total_walk_time = 0.0
        all_nodes = []
        all_edges = []
        used_edges = set()

        for source_id, target_id in pairwise(route_points):
            result = self.alg.solve_a_star(source_id, target_id, used_edges)
            if result is None:
                logger.warning("Nie znaleziono trasy A*: %s -> %s", source_id, target_id)
                return None
            total_walk_time += result.time
            all_edges.extend(result.edges)
            used_edges.update(result.edges)
            if all_nodes and result.nodes and all_nodes[-1] == result.nodes[0]:
                all_nodes.extend(result.nodes[1:])
            else:
                all_nodes.extend(result.nodes)

        total_time = total_walk_time + self.graph.get_attr_time(attractions)
        weight = self.graph.get_attr_weight(attractions)
        return RouteResult(
            score=weight / max(total_time, 1e-9),
            total_time=total_time,
            attractions=list(attractions),
            nodes_path=all_nodes,
            edges_path=all_edges,
        )

    def find_best_insertion_position_a_star(
        self, current_route: List[int], attraction_id: int
    ) -> Tuple[Optional[int], float]:
        """Find the insertion position with the smallest estimated time increase."""

        full_route = [self.start_id, *current_route, self.start_id]
        best_position = None
        best_added_time = float("inf")
        visit_time = self.graph.get_attraction_duration(attraction_id) or 0.0
        for position in range(len(current_route) + 1):
            previous_id = full_route[position]
            next_id = full_route[position + 1]
            old_walk = self._travel_time(previous_id, next_id)
            walk_to = self._travel_time(previous_id, attraction_id)
            walk_from = self._travel_time(attraction_id, next_id)
            if any(math.isinf(value) for value in (old_walk, walk_to, walk_from)):
                continue
            added_time = walk_to + walk_from - old_walk + visit_time
            if added_time < best_added_time:
                best_added_time = added_time
                best_position = position
        return best_position, best_added_time

    def choose_best_attractions(
        self,
        days: int,
        multiplier: int = 2,
        candidate_ids: Optional[Collection[int]] = None,
    ) -> List[int]:
        """Select the highest-weight reachable attractions as must-see places."""

        allowed_ids = set(candidate_ids) if candidate_ids is not None else None
        candidates = [
            attraction
            for attraction in self.graph.attractions.values()
            if allowed_ids is None or attraction.id in allowed_ids
        ]
        candidates.sort(key=lambda attraction: (-attraction.weight, attraction.time))
        return [attraction.id for attraction in candidates[: days * multiplier]]

    def _region_travel_time(
        self, attraction_id: int, day_route: List[int]
    ) -> float:
        """Return travel time from an attraction to the nearest place in a day."""

        if not day_route:
            return self._travel_time(self.start_id, attraction_id)
        return min(
            self._travel_time(attraction_id, member_id) for member_id in day_route
        )

    def closest_day_region(
        self,
        attraction_id: int,
        day_routes: List[List[int]],
        day_indices: Optional[Collection[int]] = None,
    ) -> Optional[int]:
        """Return the eligible day whose attractions are nearest by travel time."""

        eligible = set(day_indices) if day_indices is not None else set(range(len(day_routes)))
        candidates = [
            day_index
            for day_index, day_route in enumerate(day_routes)
            if day_index in eligible and day_route
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda day_index: (
                self._region_travel_time(attraction_id, day_routes[day_index]),
                self.estimate_total_route_time(day_routes[day_index]),
                day_index,
            ),
        )

    def _spatial_seeds(self, attraction_ids: List[int], count: int) -> List[int]:
        """Choose important, mutually distant attractions as daily region seeds."""

        ranked = sorted(
            dict.fromkeys(attraction_ids),
            key=lambda attraction_id: (
                -self.graph.nodes[attraction_id].weight,
                self.graph.nodes[attraction_id].time,
                attraction_id,
            ),
        )
        if not ranked or count <= 0:
            return []
        seeds = [ranked[0]]
        while len(seeds) < min(count, len(ranked)):
            remaining = [item for item in ranked if item not in seeds]
            next_seed = max(
                remaining,
                key=lambda attraction_id: (
                    min(
                        self._travel_time(attraction_id, seed_id)
                        for seed_id in seeds
                    ),
                    self.graph.nodes[attraction_id].weight,
                    -self.graph.nodes[attraction_id].time,
                ),
            )
            seeds.append(next_seed)
        return seeds

    def split_attractions_between_days_spatially(
        self, attraction_ids: List[int], days: int
    ) -> List[List[int]]:
        """Build compact daily regions while respecting route time and day load."""

        day_routes: List[List[int]] = [[] for _ in range(days)]
        assignment_time_limit = self.duration_sec * (
            1 + self.ASSIGNMENT_LIMIT_OVERAGE_RATE
        )
        valid_ids = [
            attraction_id
            for attraction_id in dict.fromkeys(attraction_ids)
            if attraction_id in self.graph.attractions
        ]
        seeds = self._spatial_seeds(valid_ids, days)
        for day_index, seed_id in enumerate(seeds):
            day_routes[day_index].append(seed_id)

        remaining = [item for item in valid_ids if item not in seeds]
        remaining.sort(
            key=lambda attraction_id: (
                -(self.graph.get_attraction_duration(attraction_id) or 0.0),
                -self.graph.nodes[attraction_id].weight,
            )
        )
        for attraction_id in remaining:
            best_day_index = None
            best_route = None
            best_score = float("inf")
            for day_index, day_route in enumerate(day_routes):
                if not day_route:
                    continue
                current_time = self.estimate_total_route_time(day_route)
                position, added_time = self.find_best_insertion_position_a_star(
                    day_route, attraction_id
                )
                if position is None or math.isinf(current_time):
                    continue
                candidate_time = current_time + added_time
                if candidate_time > assignment_time_limit:
                    continue
                travel_to_region = self._region_travel_time(
                    attraction_id, day_route
                )
                fill_ratio = candidate_time / max(self.duration_sec, 1.0)
                score = (
                    0.5 * travel_to_region
                    + 0.35 * added_time
                    + 0.15 * self.duration_sec * fill_ratio**2
                )
                if score < best_score:
                    best_score = score
                    best_day_index = day_index
                    best_route = [
                        *day_route[:position],
                        attraction_id,
                        *day_route[position:],
                    ]
            if best_day_index is not None and best_route is not None:
                day_routes[best_day_index] = best_route
        return day_routes

    def _day_centroid(self, attraction_ids: List[int]) -> Tuple[float, float]:
        """Return the planar centroid of a day's attraction vertices."""

        nodes = [self.graph.nodes[attraction_id] for attraction_id in attraction_ids]
        return (
            sum(node.x for node in nodes) / len(nodes),
            sum(node.y for node in nodes) / len(nodes),
        )

    def _neighboring_days(
        self, day_routes: List[List[int]], source_index: int
    ) -> List[int]:
        """Return at most two daily regions nearest to the source region."""

        if not day_routes[source_index]:
            return []
        source_x, source_y = self._day_centroid(day_routes[source_index])
        neighbors = []
        for day_index, day_route in enumerate(day_routes):
            if day_index == source_index or not day_route:
                continue
            target_x, target_y = self._day_centroid(day_route)
            distance_sq = (target_x - source_x) ** 2 + (target_y - source_y) ** 2
            neighbors.append((distance_sq, day_index))
        neighbors.sort()
        return [day_index for _, day_index in neighbors[:2]]

    def _assignment_objective(self, day_routes: List[List[int]]) -> float:
        """Return the total walking time of all non-empty daily routes."""

        active_routes = [route for route in day_routes if route]
        if not active_routes:
            return 0.0
        walking_times = [self._estimate_walk_time(route) for route in active_routes]
        if any(math.isinf(walking_time) for walking_time in walking_times):
            return float("inf")
        return sum(walking_times)

    def repair_day_assignments(
        self, day_routes: List[List[int]], max_moves: Optional[int] = None
    ) -> List[List[int]]:
        """Greedily move or swap attractions to reduce total walking time."""

        repaired = [list(day_route) for day_route in day_routes]
        move_limit = max_moves or max(1, sum(map(len, repaired)) * 2)
        assignment_time_limit = self.duration_sec * (
            1 + self.ASSIGNMENT_LIMIT_OVERAGE_RATE
        )
        for _ in range(move_limit):
            current_score = self._assignment_objective(repaired)
            best_score = current_score
            best_routes = None
            for source_index, source_route in enumerate(repaired):
                for target_index in self._neighboring_days(repaired, source_index):
                    target_route = repaired[target_index]
                    for source_position, attraction_id in enumerate(source_route):
                        if len(source_route) <= 1:
                            break
                        reduced_source = [
                            *source_route[:source_position],
                            *source_route[source_position + 1 :],
                        ]
                        position, _ = self.find_best_insertion_position_a_star(
                            target_route, attraction_id
                        )
                        if position is None:
                            continue
                        expanded_target = [
                            *target_route[:position],
                            attraction_id,
                            *target_route[position:],
                        ]
                        target_time = self.estimate_total_route_time(expanded_target)
                        if target_time > assignment_time_limit:
                            continue
                        candidate_routes = [list(route) for route in repaired]
                        candidate_routes[source_index] = reduced_source
                        candidate_routes[target_index] = expanded_target
                        candidate_score = self._assignment_objective(candidate_routes)
                        if candidate_score + 1e-6 < best_score:
                            best_score = candidate_score
                            best_routes = candidate_routes

                    for source_position, source_id in enumerate(source_route):
                        for target_position, target_id in enumerate(target_route):
                            source_without = [
                                *source_route[:source_position],
                                *source_route[source_position + 1 :],
                            ]
                            target_without = [
                                *target_route[:target_position],
                                *target_route[target_position + 1 :],
                            ]
                            source_insert, _ = self.find_best_insertion_position_a_star(
                                source_without, target_id
                            )
                            target_insert, _ = self.find_best_insertion_position_a_star(
                                target_without, source_id
                            )
                            if source_insert is None or target_insert is None:
                                continue
                            exchanged_source = list(source_without)
                            exchanged_source.insert(source_insert, target_id)
                            exchanged_target = list(target_without)
                            exchanged_target.insert(target_insert, source_id)
                            if (
                                self.estimate_total_route_time(exchanged_source)
                                > assignment_time_limit
                                or self.estimate_total_route_time(exchanged_target)
                                > assignment_time_limit
                            ):
                                continue
                            candidate_routes = [list(route) for route in repaired]
                            candidate_routes[source_index] = exchanged_source
                            candidate_routes[target_index] = exchanged_target
                            candidate_score = self._assignment_objective(candidate_routes)
                            if candidate_score + 1e-6 < best_score:
                                best_score = candidate_score
                                best_routes = candidate_routes
            if best_routes is None:
                break
            repaired = best_routes
        return repaired

    def is_cheap_insertion(
        self,
        current_route: List[int],
        attraction_id: int,
        maximum_detour_seconds: float = 20 * 60,
        maximum_detour_fraction: float = 0.08,
    ) -> bool:
        """Return whether an attraction adds only a small walking detour."""

        position, added_time = self.find_best_insertion_position_a_star(
            current_route, attraction_id
        )
        if position is None:
            return False
        visit_time = self.graph.get_attraction_duration(attraction_id) or 0.0
        walking_detour = max(0.0, added_time - visit_time)
        allowed_detour = min(
            maximum_detour_seconds,
            self.duration_sec * maximum_detour_fraction,
        )
        return walking_detour <= allowed_detour

    def should_move_from_future_day(
        self,
        current_route: List[int],
        future_route: List[int],
        attraction_id: int,
    ) -> bool:
        """Allow a cheap transfer only when it reduces combined walking time."""

        if len(future_route) <= 1 or not self.is_cheap_insertion(
            current_route, attraction_id
        ):
            return False
        position, added_time = self.find_best_insertion_position_a_star(
            current_route, attraction_id
        )
        current_time = self.estimate_total_route_time(current_route)
        if position is None or current_time + added_time > self.duration_sec:
            return False
        current_with = list(current_route)
        current_with.insert(position, attraction_id)
        future_without = [item for item in future_route if item != attraction_id]
        previous_walk = (
            self._estimate_walk_time(current_route)
            + self._estimate_walk_time(future_route)
        )
        transferred_walk = (
            self._estimate_walk_time(current_with)
            + self._estimate_walk_time(future_without)
        )
        if math.isinf(previous_walk) or math.isinf(transferred_walk):
            return False
        return transferred_walk + 1e-6 < previous_walk

    def try_insert_attraction_a_star(
        self,
        current_route: List[int],
        current_result: RouteResult,
        attraction_id: int,
    ) -> Tuple[bool, List[int], RouteResult]:
        """Insert a nearby attraction if the exact route stays within the limit."""

        best_position, added_time = self.find_best_insertion_position_a_star(
            current_route, attraction_id
        )
        if best_position is None or current_result.total_time + added_time > self.duration_sec:
            return False, current_route, current_result
        candidate_route = list(current_route)
        candidate_route.insert(best_position, attraction_id)
        candidate_result = self.build_exact_route(candidate_route)
        if candidate_result is None or candidate_result.total_time > self.duration_sec:
            return False, current_route, current_result
        return True, candidate_route, candidate_result
