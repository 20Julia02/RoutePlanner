import unittest

from geojson_utils import NodeIndex, line_length, to_lonlat, to_xy


class GeoJSONUtilityTests(unittest.TestCase):
    def test_node_index_finds_only_nodes_inside_the_search_radius(self):
        index = NodeIndex(cell_size=50)
        index.add(1, (0.0, 0.0))
        index.add(2, (80.0, 0.0))

        self.assertEqual(2, index.nearest((70.0, 0.0), 20)[0])
        self.assertIsNone(index.nearest((40.0, 0.0), 20)[0])

    def test_local_projection_round_trip_preserves_coordinates(self):
        source = (21.0122, 52.2297)
        projected = to_xy(source, reference_latitude=52.0)
        restored = to_lonlat(projected, reference_latitude=52.0)

        self.assertAlmostEqual(source[0], restored[0], places=10)
        self.assertAlmostEqual(source[1], restored[1], places=10)

    def test_line_length_is_reported_in_metres(self):
        length = line_length([(21.0, 52.0), (21.0, 52.001)])
        self.assertGreater(length, 110)
        self.assertLess(length, 112)


if __name__ == "__main__":
    unittest.main()
