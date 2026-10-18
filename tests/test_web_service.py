import tempfile
import unittest
from itertools import pairwise
from pathlib import Path
from unittest.mock import patch

from models import Edge, Graph, Node
from optimizer import RouteOptimizer
from prepared_store import PreparedNetworkStore
from shortest_path_alg import PathAlgorithm
from web_service import (
    PlanningError,
    build_cost_matrix,
    list_prepared_attractions,
    plan_prepared_network,
    plan_route,
    prepare_network,
    update_prepared_attractions,
)


def sample_payload():
    longitudes = [21.008, 21.011, 21.014, 21.017]
    latitudes = [52.228, 52.230, 52.232]
    segments = [
        ([longitudes[column], latitude], [longitudes[column + 1], latitude], 155)
        for latitude in latitudes
        for column in range(len(longitudes) - 1)
    ] + [
        ([longitude, latitudes[row]], [longitude, latitudes[row + 1]], 165)
        for longitude in longitudes
        for row in range(len(latitudes) - 1)
    ]
    edges = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": edge_id,
                "properties": {"walk_time": walk_time},
                "geometry": {"type": "LineString", "coordinates": [start, end]},
            }
            for edge_id, (start, end, walk_time) in enumerate(segments, start=1)
        ],
    }
    attraction_rows = [
        (101, "Ogród Saski", 8, 45, [21.008, 52.232]),
        (102, "Galeria miejska", 7, 60, [21.014, 52.232]),
        (103, "Historyczny plac", 10, 50, [21.017, 52.230]),
        (104, "Muzeum rzemiosła", 9, 80, [21.014, 52.228]),
        (105, "Punkt widokowy", 6, 30, [21.017, 52.232]),
        (106, "Kamienica Pod Gwiazdą", 5, 35, [21.008, 52.228]),
        (107, "Pasaż artystów", 4, 25, [21.011, 52.230]),
    ]
    attractions = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": attraction_id,
                "properties": {"name": name, "weight": weight, "duration": duration},
                "geometry": {"type": "Point", "coordinates": coordinates},
            }
            for attraction_id, name, weight, duration, coordinates in attraction_rows
        ],
    }
    return {
        "edges": edges,
        "attractions": attractions,
        "start": {"lat": 52.2302, "lon": 21.0113},
        "fields": {
            "edge_time": "walk_time",
            "edge_time_unit": "seconds",
            "attraction_weight": "weight",
            "attraction_duration": "duration",
            "attraction_duration_unit": "minutes",
            "attraction_name": "name",
            "attraction_image": "image",
            "attraction_description": "description",
        },
        "options": {
            "days": 2,
            "max_hours_per_day": 8,
            "must_see_per_day": 2,
            "nearby_distance_m": 300,
            "topology_tolerance_m": 1,
            "snap_distance_m": 500,
            "start_snap_distance_m": 5000,
            "walking_speed_mps": 1.4,
        },
    }


class RoutePlanningTests(unittest.TestCase):
    def test_public_store_rejects_every_write(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PreparedNetworkStore(Path(directory), read_only=True)
            with self.assertRaisesRegex(PlanningError, "tylko do odczytu"):
                store.save("Publiczny", prepare_network(sample_payload()))

    def test_response_does_not_expose_arbitrary_source_properties(self):
        payload = sample_payload()
        payload["edges"]["features"][0]["properties"]["internal_secret"] = "edge-secret"
        payload["attractions"]["features"][0]["properties"]["internal_secret"] = "poi-secret"

        result = plan_route(payload)

        route_keys = {
            key for feature in result["routes"]["features"] for key in feature["properties"]
        }
        attraction_keys = {
            key
            for feature in result["selected_attractions"]["features"]
            for key in feature["properties"]
        }
        self.assertNotIn("internal_secret", route_keys)
        self.assertNotIn("internal_secret", attraction_keys)

    def test_total_must_see_limit_bounds_expensive_planning(self):
        payload = sample_payload()
        payload["options"].update({"days": 14, "must_see_per_day": 10})
        with self.assertRaisesRegex(PlanningError, "50"):
            plan_route(payload)

    def test_sample_payload_returns_two_closed_daily_routes(self):
        result = plan_route(sample_payload())
        self.assertEqual(2, len(result["days"]))
        self.assertGreater(len(result["routes"]["features"]), 0)
        self.assertGreater(len(result["selected_attractions"]["features"]), 0)
        for day in result["days"]:
            self.assertLessEqual(day["total_time_seconds"], 8 * 3600)
            coordinates = [
                feature["geometry"]["coordinates"] for feature in day["route"]["features"]
            ]
            self.assertTrue(coordinates)
            self.assertEqual(coordinates[0][0], coordinates[-1][-1])

    def test_walk_time_can_be_calculated_from_geometry(self):
        payload = sample_payload()
        payload["fields"]["edge_time"] = ""
        result = plan_route(payload)
        self.assertGreater(result["days"][0]["walking_time_seconds"], 0)

    def test_geometry_time_accepts_vehicle_speed(self):
        payload = sample_payload()
        payload["fields"]["edge_time"] = ""
        payload["options"]["walking_speed_mps"] = 25

        network = prepare_network(payload)

        for edge in network.graph.edges.values():
            self.assertAlmostEqual(edge.length / 25, edge.time)

    def test_empty_attraction_values_are_treated_as_zero(self):
        payload = sample_payload()
        payload["attractions"]["features"][0]["properties"]["weight"] = None
        payload["attractions"]["features"][0]["properties"]["duration"] = None
        result = plan_route(payload)
        self.assertEqual(2, len(result["days"]))

    def test_invalid_and_empty_edges_are_skipped(self):
        payload = sample_payload()
        payload["edges"]["features"].extend(
            [
                {
                    "type": "Feature",
                    "properties": {"walk_time": 10},
                    "geometry": {"type": "LineString", "coordinates": [[21.0, 52.0]]},
                },
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": None,
                },
            ]
        )
        result = plan_route(payload)
        self.assertTrue(any("Pominięto krawędź" in warning for warning in result["warnings"]))
        self.assertEqual(17, result["meta"]["edges"])

    def test_missing_required_attraction_field_has_clear_error(self):
        payload = sample_payload()
        payload["fields"]["attraction_weight"] = "does_not_exist"
        with self.assertRaisesRegex(PlanningError, "does_not_exist"):
            plan_route(payload)

    def test_start_outside_network_is_rejected(self):
        payload = sample_payload()
        payload["start"] = {"lat": 50.0, "lon": 19.0}
        payload["options"]["start_snap_distance_m"] = 100
        with self.assertRaisesRegex(PlanningError, "Punkt startowy"):
            plan_route(payload)

    def test_disconnected_attraction_does_not_remove_daily_route(self):
        payload = sample_payload()
        payload["options"].update({"days": 1, "must_see_per_day": 1})
        network = prepare_network(payload, calculate_cost_matrix=True)
        disconnected_id = max(network.graph.nodes) + 1
        network.graph.add_node(Node(disconnected_id, 100_000, 100_000, 10_000, 600))
        network.attractions_by_vertex[disconnected_id] = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [22.0, 53.0]},
                "properties": {"name": "Odłączona atrakcja", "weight": 10_000, "duration": 10},
            }
        ]

        result = plan_prepared_network(
            network, {"start": payload["start"], "options": payload["options"]}
        )

        self.assertEqual(1, len(result["days"]))
        self.assertTrue(result["days"][0]["route"]["features"])
        self.assertNotIn(
            disconnected_id, [item["vertex_id"] for item in result["days"][0]["itinerary"]]
        )
        self.assertTrue(any("nieosiągalne" in warning for warning in result["warnings"]))

    def test_large_line_is_not_rejected_by_a_coordinate_count_limit(self):
        payload = sample_payload()
        coordinates = [[21.0 + index / 10_000_000, 52.0] for index in range(25_001)]
        payload["edges"]["features"] = [
            {
                "type": "Feature",
                "properties": {"walk_time": 155},
                "geometry": {"type": "LineString", "coordinates": coordinates},
            }
        ]
        payload["attractions"]["features"][0]["geometry"]["coordinates"] = coordinates[-1]
        payload["attractions"]["features"] = payload["attractions"]["features"][:1]

        network = prepare_network(payload)

        self.assertEqual(25_001, len(network.edges[1].coordinates))

    def test_failed_a_star_segment_keeps_recoverable_part_of_day(self):
        payload = sample_payload()
        payload["options"].update({"days": 1, "must_see_per_day": 2})
        network = prepare_network(payload, calculate_cost_matrix=True)
        original_build = RouteOptimizer.build_exact_route

        def fail_routes_with_multiple_attractions(optimizer, attractions):
            if len(attractions) > 1:
                return None
            return original_build(optimizer, attractions)

        with patch.object(
            RouteOptimizer, "build_exact_route", fail_routes_with_multiple_attractions
        ):
            result = plan_prepared_network(
                network,
                {"start": payload["start"], "options": payload["options"]},
            )

        self.assertEqual(1, len(result["days"]))
        self.assertTrue(result["days"][0]["route"]["features"])
        self.assertEqual(1, len(result["days"][0]["itinerary"]))
        self.assertTrue(
            any("zachowano pozostałą trasę dnia" in warning for warning in result["warnings"])
        )

    def test_unassigned_must_see_warning_uses_attraction_name(self):
        payload = sample_payload()
        payload["options"].update({"days": 1, "must_see_per_day": 2})
        network = prepare_network(payload, calculate_cost_matrix=True)
        assigned_id, missing_id = list(network.graph.attractions)[:2]
        feature = network.attractions_by_vertex[missing_id][0]
        expected_name = feature["properties"][network.name_field]

        with (
            patch.object(
                RouteOptimizer, "choose_best_attractions", return_value=[assigned_id, missing_id]
            ),
            patch.object(
                RouteOptimizer,
                "split_attractions_between_days_spatially",
                return_value=[[assigned_id]],
            ),
            patch.object(RouteOptimizer, "repair_day_assignments", return_value=[[assigned_id]]),
        ):
            result = plan_prepared_network(
                network,
                {"start": payload["start"], "options": payload["options"]},
            )

        warning = next(item for item in result["warnings"] if "must-see" in item)
        self.assertIn(expected_name, warning)
        self.assertNotIn(f"[{missing_id}]", warning)

    def test_prepared_network_and_cost_matrix_can_be_reused(self):
        payload = sample_payload()
        network = prepare_network(payload, calculate_cost_matrix=True)
        self.assertEqual(42, len(list(network.cost_matrix.items())))

        with tempfile.TemporaryDirectory() as directory:
            store = PreparedNetworkStore(Path(directory))
            metadata = store.save("Testowa sieć", network)
            self.assertEqual(4, len(metadata["bounds"]))
            self.assertTrue((Path(directory) / f"{metadata['id']}.meta.json").exists())
            self.assertTrue((Path(directory) / f"{metadata['id']}.attractions.json").exists())
            self.assertEqual("Testowa sieć", store.list()[0]["name"])
            restored = store.load(metadata["id"])

            lightweight_request = {
                "start": payload["start"],
                "options": payload["options"],
            }
            result = plan_prepared_network(restored, lightweight_request)
            self.assertEqual(42, result["meta"]["cost_matrix_entries"])
            self.assertEqual(2, len(result["days"]))
            selected_names = {
                feature["properties"]["name"]
                for feature in result["selected_attractions"]["features"]
            }
            self.assertIn("Ogród Saski", selected_names)

    def test_data_preparation_reports_matrix_progress(self):
        updates = []
        prepare_network(
            sample_payload(),
            calculate_cost_matrix=True,
            progress=lambda value, message: updates.append((value, message)),
        )
        values = [value for value, _ in updates]
        self.assertEqual(values, sorted(values))
        self.assertEqual(98, values[-1])
        self.assertTrue(any("macierz kosztów" in message for _, message in updates))

    def test_image_and_description_are_preserved_for_map_popups(self):
        payload = sample_payload()
        properties = payload["attractions"]["features"][0]["properties"]
        properties["photo_address"] = "https://example.org/photo.jpg"
        properties["place_details"] = "Opis miejsca"
        payload["fields"]["attraction_image"] = "photo_address"
        payload["fields"]["attraction_description"] = "place_details"
        result = plan_route(payload)
        selected = result["selected_attractions"]["features"]
        feature = next(item for item in selected if item["properties"].get("name") == "Ogród Saski")
        self.assertEqual("https://example.org/photo.jpg", feature["properties"]["image"])
        self.assertEqual("Opis miejsca", feature["properties"]["description"])

        with tempfile.TemporaryDirectory() as directory:
            store = PreparedNetworkStore(Path(directory))
            metadata = store.save(
                "Pola opisowe", prepare_network(payload, calculate_cost_matrix=True)
            )
            restored = PreparedNetworkStore(Path(directory)).load(metadata["id"])
            self.assertEqual("photo_address", restored.image_field)
            self.assertEqual("place_details", restored.description_field)
            restored_result = plan_prepared_network(
                restored,
                {"start": payload["start"], "options": payload["options"]},
            )
            restored_feature = next(
                item
                for item in restored_result["selected_attractions"]["features"]
                if item["properties"].get("name") == "Ogród Saski"
            )
            self.assertEqual(
                "https://example.org/photo.jpg", restored_feature["properties"]["image"]
            )
            self.assertEqual("Opis miejsca", restored_feature["properties"]["description"])

    def test_itinerary_contains_normalized_visit_duration(self):
        result = plan_route(sample_payload())
        itinerary = [item for day in result["days"] for item in day["itinerary"]]
        garden = next(item for item in itinerary if item["name"] == "Ogród Saski")
        self.assertEqual(45 * 60, garden["duration_seconds"])

    def test_selected_attractions_include_their_attached_vertex_coordinates(self):
        result = plan_route(sample_payload())
        for feature in result["selected_attractions"]["features"]:
            coordinates = feature["properties"].get("vertex_coordinates")
            self.assertIsNotNone(coordinates)
            self.assertEqual(2, len(coordinates))
            self.assertTrue(all(isinstance(value, (int, float)) for value in coordinates))

    def test_a_star_uses_positive_admissible_heuristic(self):
        network = prepare_network(sample_payload())
        algorithm = PathAlgorithm(network.graph)
        for edge in network.graph.edges.values():
            self.assertGreater(algorithm.heuristic(edge.start.id, edge.end.id), 0)
            self.assertLessEqual(algorithm.heuristic(edge.start.id, edge.end.id), edge.time + 1e-9)

        source_id, target_id = list(network.graph.attractions)[:2]
        result = algorithm.solve_a_star(source_id, target_id)
        exact_cost = build_cost_matrix(network.graph).get_cost(source_id, target_id)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(exact_cost, result.time)

    def test_a_star_prefers_a_slight_detour_over_a_reused_edge(self):
        graph = self._alternative_paths_graph(alternative_edge_time=55)
        result = PathAlgorithm(graph).solve_a_star(1, 2, penalized_edges={1})

        self.assertIsNotNone(result)
        self.assertEqual([2, 3], result.edges)
        self.assertEqual(110, result.time)

    def test_a_star_reuses_an_edge_instead_of_taking_a_long_detour(self):
        alternative_edge_time = 60 * (1 + PathAlgorithm.REUSED_EDGE_PENALTY_RATE)
        graph = self._alternative_paths_graph(alternative_edge_time=alternative_edge_time)
        result = PathAlgorithm(graph).solve_a_star(1, 2, penalized_edges={1})

        self.assertIsNotNone(result)
        self.assertEqual([1], result.edges)
        self.assertEqual(100, result.time)

    @staticmethod
    def _alternative_paths_graph(alternative_edge_time):
        graph = Graph()
        nodes = {
            1: Node(1, 0.0, 0.0, 0, 0),
            2: Node(2, 1.0, 0.0, 0, 0),
            3: Node(3, 0.5, 0.5, 0, 0),
        }
        for node in nodes.values():
            graph.add_node(node)
        graph.add_edge(Edge(1, nodes[1], nodes[2], 100, 100))
        graph.add_edge(Edge(2, nodes[1], nodes[3], 55, alternative_edge_time))
        graph.add_edge(Edge(3, nodes[3], nodes[2], 55, alternative_edge_time))
        return graph

    def test_route_segments_are_oriented_and_sequenced(self):
        result = plan_route(sample_payload())
        for day in result["days"]:
            features = day["route"]["features"]
            self.assertEqual(
                list(range(1, len(features) + 1)),
                [feature["properties"]["sequence"] for feature in features],
            )
            for previous, current in pairwise(features):
                self.assertEqual(
                    previous["geometry"]["coordinates"][-1],
                    current["geometry"]["coordinates"][0],
                )

    def test_route_geometry_closes_gaps_joined_by_topology_tolerance(self):
        payload = {
            "edges": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {},
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[21.0, 52.0], [21.001, 52.0]],
                        },
                    },
                    {
                        "type": "Feature",
                        "properties": {},
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[21.00104, 52.0], [21.002, 52.0]],
                        },
                    },
                ],
            },
            "attractions": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"name": "Cel", "weight": 10, "duration": 10},
                        "geometry": {"type": "Point", "coordinates": [21.002, 52.0]},
                    }
                ],
            },
            "start": {"lat": 52.0, "lon": 21.0},
            "fields": {
                "edge_time": "",
                "attraction_weight": "weight",
                "attraction_duration": "duration",
                "attraction_duration_unit": "minutes",
                "attraction_name": "name",
            },
            "options": {
                "days": 1,
                "max_hours_per_day": 2,
                "must_see_per_day": 1,
                "nearby_distance_m": 500,
                "topology_tolerance_m": 5,
                "snap_distance_m": 20,
                "start_snap_distance_m": 20,
                "walking_speed_mps": 1.4,
            },
        }

        result = plan_route(payload)
        features = result["days"][0]["route"]["features"]

        self.assertGreaterEqual(len(features), 2)
        for previous, current in pairwise(features):
            self.assertEqual(
                previous["geometry"]["coordinates"][-1],
                current["geometry"]["coordinates"][0],
            )

    def test_attraction_edits_persist_and_affect_next_plan(self):
        payload = sample_payload()
        network = prepare_network(payload, calculate_cost_matrix=True)
        items = list_prepared_attractions(network)
        edits = [
            {
                "id": item["id"],
                "enabled": item["name"] != "Ogród Saski",
                "duration_seconds": 120 * 60
                if item["name"] == "Muzeum rzemiosła"
                else item["duration_seconds"],
            }
            for item in items
        ]
        updated = update_prepared_attractions(network, edits)
        self.assertFalse(next(item for item in updated if item["name"] == "Ogród Saski")["enabled"])

        with tempfile.TemporaryDirectory() as directory:
            store = PreparedNetworkStore(Path(directory))
            metadata = store.save("Edytowana sieć", network)
            restored = PreparedNetworkStore(Path(directory)).load(metadata["id"])
            restored_items = list_prepared_attractions(restored)
            self.assertFalse(
                next(item for item in restored_items if item["name"] == "Ogród Saski")["enabled"]
            )

            result = plan_prepared_network(
                restored, {"start": payload["start"], "options": payload["options"]}
            )
            itinerary = [item for day in result["days"] for item in day["itinerary"]]
            self.assertNotIn("Ogród Saski", [item["name"] for item in itinerary])
            museum = next(item for item in itinerary if item["name"] == "Muzeum rzemiosła")
            self.assertEqual(120 * 60, museum["duration_seconds"])

    def test_request_edits_customize_a_prepared_network_without_mutating_it(self):
        payload = sample_payload()
        network = prepare_network(payload, calculate_cost_matrix=True)
        items = list_prepared_attractions(network)
        disabled = items[0]
        request_payload = {
            "start": payload["start"],
            "options": payload["options"],
            "attraction_edits": [
                {
                    "id": item["id"],
                    "enabled": item["id"] != disabled["id"],
                    "duration_seconds": item["duration_seconds"],
                }
                for item in items
            ],
        }

        result = plan_prepared_network(network, request_payload)

        itinerary_names = [item["name"] for day in result["days"] for item in day["itinerary"]]
        self.assertNotIn(disabled["name"], itinerary_names)
        original = next(
            item for item in list_prepared_attractions(network) if item["id"] == disabled["id"]
        )
        self.assertTrue(original["enabled"])

    def test_attraction_editor_rows_are_sorted_by_weight(self):
        network = prepare_network(sample_payload())

        items = list_prepared_attractions(network)

        self.assertEqual(
            sorted((item["weight"] for item in items), reverse=True),
            [item["weight"] for item in items],
        )
        self.assertEqual("Historyczny plac", items[0]["name"])

    def test_missing_attraction_sidecar_is_created_for_legacy_network(self):
        network = prepare_network(sample_payload(), calculate_cost_matrix=True)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = PreparedNetworkStore(root).save("Starszy zestaw", network)
            sidecar = root / f"{metadata['id']}.attractions.json"
            sidecar.unlink()
            store = PreparedNetworkStore(root)

            items = store.list_attractions(metadata["id"])

            self.assertTrue(items)
            self.assertTrue(sidecar.exists())
            self.assertIn(metadata["id"], store._cache)

    def test_read_only_store_never_creates_missing_sidecars(self):
        network = prepare_network(sample_payload(), calculate_cost_matrix=True)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = PreparedNetworkStore(root).save("Publiczny", network)
            sidecar = root / f"{metadata['id']}.attractions.json"
            sidecar.unlink()
            store = PreparedNetworkStore(root, read_only=True)

            items = store.list_attractions(metadata["id"])

            self.assertTrue(items)
            self.assertFalse(sidecar.exists())


if __name__ == "__main__":
    unittest.main()
