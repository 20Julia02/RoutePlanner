"""Regression tests for current and legacy route selection."""

import random
import unittest
from itertools import pairwise
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

import main
from models import CostMatrix, Edge, Graph, Node
from network_preparation import prepare_network
from old_algorithm.optimizer_v1 import RouteOptimizer as LegacyRouteOptimizer
from old_algorithm.shortest_path_alg_v1 import PathAlgorithm as LegacyPathAlgorithm
from planning_models import PlanningError
from route_planning import plan_prepared_network


def _network():
    points = [(21.0, 52.0), (21.001, 52.0), (21.002, 52.0)]
    return prepare_network(
        {
            "edges": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [a, b],
                        },
                        "properties": {},
                    }
                    for a, b in pairwise(points)
                ],
            },
            "attractions": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": point},
                        "properties": {"name": str(index), "weight": 10, "visit": 25},
                    }
                    for index, point in enumerate(points[1:], 1)
                ],
            },
            "fields": {
                "attraction_weight": "weight",
                "attraction_duration": "visit",
                "attraction_name": "name",
            },
        },
        calculate_cost_matrix=True,
    )


class AlgorithmSelectionTests(unittest.TestCase):
    def test_both_algorithms_return_continuous_routes_and_preserve_network(self):
        network = _network()
        before = set(network.graph.attractions)
        for algorithm in ("legacy", "current"):
            payload = {
                "algorithm": algorithm,
                "start": {"lon": 21.0, "lat": 52.0},
                "options": {
                    "days": 2,
                    "max_hours_per_day": 1,
                    "must_see_per_day": 1,
                },
            }
            random_state = random.getstate()
            try:
                random.seed(1)
                result = plan_prepared_network(network, payload)
            finally:
                random.setstate(random_state)
            self.assertEqual(algorithm, result["meta"]["algorithm"])
            self.assertTrue(result["routes"]["features"])
            self.assertEqual(before, set(network.graph.attractions))
            for day in result["days"]:
                features = day["route"]["features"]
                for previous, following in pairwise(features):
                    self.assertEqual(
                        previous["geometry"]["coordinates"][-1][:2],
                        following["geometry"]["coordinates"][0][:2],
                    )

    def test_public_client_cannot_select_legacy_algorithm(self):
        request = Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/plan",
                "raw_path": b"/api/plan",
                "query_string": b"",
                "headers": [(b"content-type", b"application/json")],
                "client": ("203.0.113.10", 12345),
                "server": ("localhost", 8000),
            }
        )
        with (
            patch.object(main, "ALLOW_LOCAL_ADMIN", True),
            self.assertRaises(HTTPException) as error,
        ):
            main.create_plan({"network_id": "published", "algorithm": "legacy"}, request)
        self.assertEqual(403, error.exception.status_code)

    def test_attraction_at_start_is_a_valid_plan_without_walking_segments(self):
        network = _network()

        result = plan_prepared_network(
            network,
            {
                "start": {"lon": 21.001, "lat": 52.0},
                "options": {
                    "days": 1,
                    "max_hours_per_day": 1,
                    "must_see_per_day": 1,
                },
            },
        )

        self.assertEqual([], result["routes"]["features"])
        self.assertEqual(1, len(result["selected_attractions"]["features"]))
        self.assertEqual(0, result["days"][0]["walking_time_seconds"])

    def test_legacy_path_attractions_are_added_only_once(self):
        graph = Graph()
        for node_id in range(4):
            graph.add_node(Node(node_id, float(node_id), 0.0, int(node_id > 0), 60))
        costs = CostMatrix()
        for source in graph.nodes:
            for target in graph.nodes:
                if source != target:
                    costs.add_cost(source, target, 10)
        optimizer = LegacyRouteOptimizer(
            graph,
            costs,
            LegacyPathAlgorithm(graph),
            start_id=0,
            duration_h=1,
        )

        route = optimizer._add_attractions_from_path([1], [0, 2, 3, 2, 0])

        self.assertEqual([1, 2, 3], route)

    def test_legacy_a_star_uses_an_admissible_time_heuristic(self):
        graph = Graph()
        start = Node(0, 0.0, 0.0, 0, 0)
        detour = Node(1, 0.0, 0.0, 0, 0)
        destination = Node(2, 110.0, 0.0, 0, 0)
        for node in (start, detour, destination):
            graph.add_node(node)
        graph.add_edge(Edge(1, start, destination, 110, 100))
        graph.add_edge(Edge(2, start, detour, 1, 1))
        graph.add_edge(Edge(3, detour, destination, 110, 85))

        result = LegacyPathAlgorithm(graph).solve_a_star(0, 2, [])

        self.assertIsNotNone(result)
        self.assertEqual(86, result.time)
        self.assertEqual([2, 3], result.edges)

    def test_options_must_be_a_json_object(self):
        network = _network()
        for invalid_options in ([], "", 0):
            with (
                self.subTest(value=invalid_options),
                self.assertRaisesRegex(PlanningError, "obiektem JSON"),
            ):
                plan_prepared_network(
                    network,
                    {
                        "start": {"lon": 21.0, "lat": 52.0},
                        "options": invalid_options,
                    },
                )

    def test_attraction_edits_must_contain_json_objects(self):
        with self.assertRaisesRegex(PlanningError, "format zmian atrakcji"):
            plan_prepared_network(
                _network(),
                {
                    "start": {"lon": 21.0, "lat": 52.0},
                    "attraction_edits": ["bad"],
                },
            )


if __name__ == "__main__":
    unittest.main()
