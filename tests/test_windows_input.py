"""Require exact window geometry and ownership before routed desktop input."""

import unittest
from unittest.mock import Mock

from scripts.native_ui import ProtocolError
from scripts.windows_input import RoutedInput, node_path, physical_point


class TestInputGeometry(unittest.TestCase):
    """Exercise DPI conversion without moving a cursor or touching a desktop."""

    def test_scaled_window_uses_relative_coordinates(self):
        """A 125 percent display must not use DIP positions as physical pixels."""
        point = physical_point(
            {"x": 160, "y": 160, "width": 80, "height": 32},
            {"x": 80, "y": 80, "width": 800, "height": 600},
            (100, 100, 1100, 850),
            1.25,
        )
        self.assertEqual(point, (250, 220))

    def test_negative_display_origin_is_preserved(self):
        """A second display left of the primary can contain the fixture window."""
        point = physical_point(
            {"x": -1200, "y": 80, "width": 100, "height": 30},
            {"x": -1280, "y": 0, "width": 1280, "height": 720},
            (-1920, 0, 0, 1080),
            1.5,
        )
        self.assertEqual(point, (-1725, 142))

    def test_disagreeing_window_bounds_refuse(self):
        """Do not compensate for unknown frame differences with guessed offsets."""
        with self.assertRaisesRegex(ValueError, "bounds disagree"):
            physical_point(
                {"x": 0, "y": 0, "width": 20, "height": 20}, {"x": 0, "y": 0, "width": 100, "height": 100}, (0, 0, 300, 300), 1
            )

    def test_off_window_targets_refuse(self):
        """A clipped or stale row must not redirect input beyond the fixture frame."""
        with self.assertRaisesRegex(ValueError, "outside"):
            physical_point(
                {"x": 500, "y": 0, "width": 20, "height": 20}, {"x": 0, "y": 0, "width": 100, "height": 100}, (0, 0, 100, 100), 1
            )

    def test_foreground_failure_never_sends_input(self):
        """Another program retaining focus is a test failure, not permission to click."""
        driver = RoutedInput.__new__(RoutedInput)
        driver.api = Mock()
        driver.process = Mock(side_effect=[9, 10, 10])
        with self.assertRaisesRegex(ProtocolError, "foreground input"):
            driver.guard(1, {9})
        driver.api.SendInput.assert_not_called()

    def test_node_ancestry_is_specific_to_the_target(self):
        """Mapping may use only windows actually containing this inspected node."""
        tree = {"nodeId": 1, "children": [{"nodeId": 2}, {"nodeId": 3, "children": [{"nodeId": 4}]}]}
        self.assertEqual([node["nodeId"] for node in node_path(tree, 4)], [1, 3, 4])
        self.assertEqual(node_path(tree, 5), [])
