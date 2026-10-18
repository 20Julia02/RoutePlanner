import unittest

from shapely.geometry import LineString

from models import CostMatrix, Edge, Graph, Node
from optimizer import RouteOptimizer
from planning_models import EdgeGeometry, PreparedNetwork
from route_planning import _add_nearby_attractions
from shortest_path_alg import PathAlgorithm


class SpatialAssignmentTests(unittest.TestCase):
    def _clustered_optimizer(self):
        graph = Graph()
        nodes = {
            0: Node(0, 0.0, 0.0, 0, 0),
            1: Node(1, 10.0, 0.0, 10, 600),
            2: Node(2, 11.0, 0.0, 8, 600),
            3: Node(3, -10.0, 0.0, 9, 600),
            4: Node(4, -11.0, 0.0, 7, 600),
        }
        for node in nodes.values():
            graph.add_node(node)

        matrix = CostMatrix()
        for source in nodes.values():
            for target in nodes.values():
                if source.id != target.id:
                    matrix.add_cost(
                        source.id,
                        target.id,
                        abs(source.x - target.x) * 100,
                    )
        optimizer = RouteOptimizer(
            graph,
            matrix,
            PathAlgorithm(graph),
            start_id=0,
            duration_hours=8,
        )
        return optimizer

    def test_spatial_split_keeps_nearby_attractions_in_the_same_day(self):
        optimizer = self._clustered_optimizer()

        day_routes = optimizer.split_attractions_between_days_spatially([1, 2, 3, 4], days=2)

        self.assertEqual(
            {frozenset({1, 2}), frozenset({3, 4})}, {frozenset(route) for route in day_routes}
        )

    def test_spatial_split_prefers_the_less_filled_equally_close_day(self):
        graph = Graph()
        nodes = {
            0: Node(0, 0.0, 0.0, 0, 0),
            1: Node(1, -10.0, 0.0, 10, 7200),
            2: Node(2, 10.0, 0.0, 9, 600),
            3: Node(3, 0.0, 0.0, 8, 600),
        }
        for node in nodes.values():
            graph.add_node(node)
        matrix = CostMatrix()
        for source in nodes.values():
            for target in nodes.values():
                if source.id != target.id:
                    matrix.add_cost(
                        source.id,
                        target.id,
                        abs(source.x - target.x) * 100,
                    )
        optimizer = RouteOptimizer(
            graph,
            matrix,
            PathAlgorithm(graph),
            start_id=0,
            duration_hours=4,
        )

        day_routes = optimizer.split_attractions_between_days_spatially([1, 2, 3], days=2)

        self.assertIn(3, next(route for route in day_routes if 2 in route))
        self.assertNotIn(3, next(route for route in day_routes if 1 in route))

    def test_spatial_split_allows_up_to_fifteen_percent_over_day_limit(self):
        graph = Graph()
        nodes = {
            0: Node(0, 0.0, 0.0, 0, 0),
            1: Node(1, 1.0, 0.0, 10, 0),
            2: Node(2, 2.0, 0.0, 9, 0),
        }
        for node in nodes.values():
            graph.add_node(node)

        matrix = CostMatrix()
        costs = {
            frozenset({0, 1}): 400,
            frozenset({0, 2}): 450,
            frozenset({1, 2}): 200,
        }
        for source in nodes:
            for target in nodes:
                if source != target:
                    matrix.add_cost(source, target, costs[frozenset({source, target})])

        optimizer = RouteOptimizer(
            graph,
            matrix,
            PathAlgorithm(graph),
            start_id=0,
            duration_hours=1000 / 3600,
        )

        day_routes = optimizer.split_attractions_between_days_spatially([1, 2], days=1)

        self.assertEqual({1, 2}, set(day_routes[0]))
        self.assertEqual(1050, optimizer.estimate_total_route_time(day_routes[0]))

    def test_spatial_split_rejects_more_than_fifteen_percent_over_day_limit(self):
        graph = Graph()
        nodes = {
            0: Node(0, 0.0, 0.0, 0, 0),
            1: Node(1, 1.0, 0.0, 10, 0),
            2: Node(2, 2.0, 0.0, 9, 0),
        }
        for node in nodes.values():
            graph.add_node(node)

        matrix = CostMatrix()
        costs = {
            frozenset({0, 1}): 400,
            frozenset({0, 2}): 550,
            frozenset({1, 2}): 250,
        }
        for source in nodes:
            for target in nodes:
                if source != target:
                    matrix.add_cost(source, target, costs[frozenset({source, target})])

        optimizer = RouteOptimizer(
            graph,
            matrix,
            PathAlgorithm(graph),
            start_id=0,
            duration_hours=1000 / 3600,
        )

        day_routes = optimizer.split_attractions_between_days_spatially([1, 2], days=1)

        self.assertEqual([1], day_routes[0])

    def test_repair_moves_cross_assigned_attractions_to_neighboring_regions(self):
        optimizer = self._clustered_optimizer()

        repaired = optimizer.repair_day_assignments([[1, 3], [4, 2]])

        self.assertEqual(
            {frozenset({1, 2}), frozenset({3, 4})}, {frozenset(route) for route in repaired}
        )

    def test_repair_preserves_empty_days_without_dividing_by_zero(self):
        optimizer = self._clustered_optimizer()

        repaired = optimizer.repair_day_assignments([[1], [], [3]])

        self.assertEqual([[1], [], [3]], repaired)

    def test_assignment_objective_is_total_walking_time(self):
        optimizer = self._clustered_optimizer()

        compact = optimizer._assignment_objective([[1, 2], [3, 4]])
        crossed = optimizer._assignment_objective([[1, 3], [2, 4]])

        self.assertEqual(4400, compact)
        self.assertEqual(8400, crossed)

    def test_repair_swaps_attractions_when_moves_cannot_fit(self):
        graph = Graph()
        nodes = {0: Node(0, 0, 0, 0, 0)}
        nodes.update({node_id: Node(node_id, node_id, 0, 1, 100) for node_id in range(1, 5)})
        for node in nodes.values():
            graph.add_node(node)

        compact_pairs = {frozenset({1, 2}), frozenset({3, 4})}
        matrix = CostMatrix()
        for source in nodes:
            for target in nodes:
                if source == target:
                    continue
                cost = (
                    10
                    if 0 in (source, target)
                    else (1 if frozenset({source, target}) in compact_pairs else 100)
                )
                matrix.add_cost(source, target, cost)
        optimizer = RouteOptimizer(
            graph,
            matrix,
            PathAlgorithm(graph),
            start_id=0,
            duration_hours=350 / 3600,
        )

        repaired = optimizer.repair_day_assignments([[1, 3], [4, 2]])

        self.assertEqual(
            {frozenset({1, 2}), frozenset({3, 4})},
            {frozenset(route) for route in repaired},
        )
        self.assertEqual([2, 2], [len(route) for route in repaired])

    def test_repair_accepts_combined_improvement_with_one_longer_day(self):
        optimizer = self._transfer_optimizer(target_pair_cost=550)
        initial = [[1, 2], [3]]

        repaired = optimizer.repair_day_assignments(initial)

        self.assertEqual([1], repaired[0])
        self.assertEqual({2, 3}, set(repaired[1]))
        self.assertLess(
            optimizer._assignment_objective(repaired),
            optimizer._assignment_objective(initial),
        )
        self.assertGreater(
            optimizer.estimate_total_route_time(repaired[1]),
            optimizer.duration_sec,
        )
        self.assertLessEqual(
            optimizer.estimate_total_route_time(repaired[1]),
            optimizer.duration_sec * 1.15,
        )

    def test_repair_rejects_a_day_over_fifteen_percent_above_limit(self):
        optimizer = self._transfer_optimizer(target_pair_cost=700)
        initial = [[1, 2], [3]]

        repaired = optimizer.repair_day_assignments(initial)

        self.assertEqual(initial, repaired)

    @staticmethod
    def _transfer_optimizer(target_pair_cost):
        graph = Graph()
        nodes = {0: Node(0, 0, 0, 0, 0)}
        nodes.update({node_id: Node(node_id, node_id, 0, 1, 0) for node_id in range(1, 4)})
        for node in nodes.values():
            graph.add_node(node)

        matrix = CostMatrix()
        for source in nodes:
            for target in nodes:
                if source == target:
                    continue
                if 0 in (source, target):
                    attraction = target if source == 0 else source
                    cost = 450 if attraction == 3 else 100
                elif frozenset({source, target}) == frozenset({1, 2}):
                    cost = 800
                elif frozenset({source, target}) == frozenset({2, 3}):
                    cost = target_pair_cost
                else:
                    cost = 5000
                matrix.add_cost(source, target, cost)
        return RouteOptimizer(
            graph,
            matrix,
            PathAlgorithm(graph),
            start_id=0,
            duration_hours=1000 / 3600,
        )

    def test_cheap_insertion_uses_walking_detour_not_visit_duration(self):
        optimizer = self._clustered_optimizer()

        self.assertTrue(optimizer.is_cheap_insertion([1], 2))
        self.assertFalse(optimizer.is_cheap_insertion([1], 3))

    def test_nearby_future_attraction_is_moved_to_the_current_day(self):
        graph = Graph()
        start = Node(0, 0.0, 0.0, 0, 0)
        first = Node(1, 10.0, 0.0, 10, 60)
        future = Node(2, 11.0, 0.0, 9, 60)
        future_anchor = Node(3, -20.0, 0.0, 8, 60)
        for node in (start, first, future, future_anchor):
            graph.add_node(node)
        graph.add_edge(Edge(1, start, first, 10, 10))
        graph.add_edge(Edge(2, first, future, 1, 1))
        graph.add_edge(Edge(3, start, future_anchor, 20, 20))

        matrix = CostMatrix()
        matrix.add_cost(1, 2, 1)
        matrix.add_cost(2, 1, 1)
        optimizer = RouteOptimizer(
            graph,
            matrix,
            PathAlgorithm(graph),
            start_id=0,
            duration_hours=1,
        )
        network = PreparedNetwork(
            graph=graph,
            edges={
                1: EdgeGeometry([[0, 0], [10, 0]], LineString([(0, 0), (10, 0)]), 0, 1, 1, {}),
                2: EdgeGeometry([[10, 0], [11, 0]], LineString([(10, 0), (11, 0)]), 1, 2, 2, {}),
                3: EdgeGeometry([[0, 0], [-20, 0]], LineString([(0, 0), (-20, 0)]), 0, 3, 3, {}),
            },
            attractions_by_vertex={1: [], 2: [], 3: []},
            reference_latitude=0,
            warnings=[],
            cost_matrix=matrix,
            name_field="name",
        )
        current_result = optimizer.build_exact_route([1])
        self.assertIsNotNone(current_result)
        day_routes = [[1], [2, 3]]
        visited = set()

        current_route, _ = _add_nearby_attractions(
            network,
            optimizer,
            [1],
            current_result,
            visited,
            maximum_distance=2,
            day_routes=day_routes,
            day_index=0,
        )

        self.assertEqual({1, 2}, set(current_route))
        self.assertEqual([3], day_routes[1])
        self.assertEqual(set(), visited)

    def test_unused_nearby_attraction_fills_time_left_in_day(self):
        graph = Graph()
        start = Node(0, 0.0, 0.0, 0, 0)
        first = Node(1, 10.0, 0.0, 10, 60)
        extra = Node(2, 11.0, 0.0, 5, 60)
        for node in (start, first, extra):
            graph.add_node(node)
        graph.add_edge(Edge(1, start, first, 10, 10))
        graph.add_edge(Edge(2, first, extra, 1, 1))

        matrix = CostMatrix()
        matrix.add_cost(1, 2, 1)
        matrix.add_cost(2, 1, 1)
        optimizer = RouteOptimizer(
            graph,
            matrix,
            PathAlgorithm(graph),
            start_id=0,
            duration_hours=1,
        )
        network = PreparedNetwork(
            graph=graph,
            edges={
                1: EdgeGeometry([[0, 0], [10, 0]], LineString([(0, 0), (10, 0)]), 0, 1, 1, {}),
                2: EdgeGeometry([[10, 0], [11, 0]], LineString([(10, 0), (11, 0)]), 1, 2, 2, {}),
            },
            attractions_by_vertex={1: [], 2: []},
            reference_latitude=0,
            warnings=[],
            cost_matrix=matrix,
            name_field="name",
        )
        current_result = optimizer.build_exact_route([1])
        self.assertIsNotNone(current_result)
        day_routes = [[1]]

        current_route, result = _add_nearby_attractions(
            network,
            optimizer,
            [1],
            current_result,
            visited=set(),
            maximum_distance=2,
            day_routes=day_routes,
            day_index=0,
        )

        self.assertEqual({1, 2}, set(current_route))
        self.assertLessEqual(result.total_time, optimizer.duration_sec)


if __name__ == "__main__":
    unittest.main()
