"""Unit tests for humanized mouse input trajectories and Bezier curve math."""
from __future__ import annotations

import unittest

from Controller.Utilities.input_controller import bezier_point, generate_human_path


class HumanInputTests(unittest.TestCase):
    def test_bezier_point_endpoints(self):
        p0 = (100.0, 100.0)
        p1 = (150.0, 120.0)
        p2 = (200.0, 180.0)
        p3 = (300.0, 300.0)
        start = bezier_point(p0, p1, p2, p3, 0.0)
        end = bezier_point(p0, p1, p2, p3, 1.0)
        self.assertEqual(start, (100, 100))
        self.assertEqual(end, (300, 300))

    def test_generate_human_path_short_distance(self):
        path = generate_human_path((50, 50), (52, 51))
        self.assertEqual(path, [(52, 51)])

    def test_generate_human_path_generates_points_to_destination(self):
        path = generate_human_path((100, 100), (800, 600), num_steps=15)
        self.assertEqual(len(path), 15)
        self.assertEqual(path[-1], (800, 600))
