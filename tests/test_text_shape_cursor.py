import math
import unittest

from qtpy.QtCore import QPointF, Qt

from ballontranslator.ui.cursor import (
    resizeCursorList,
    resize_handle_scene_angle,
    scene_angle_to_cursor_index,
)


class TextShapeCursorTests(unittest.TestCase):
    def test_axis_aligned_handles_follow_clockwise_cursor_order(self):
        handle_scene_angles = (-135, -90, -45, 0, 45, 90, 135, 180)

        self.assertEqual(
            [scene_angle_to_cursor_index(angle) for angle in handle_scene_angles],
            list(range(8)),
        )

    def test_resize_cursor_matches_scene_axis(self):
        expected_cursors = {
            0: Qt.CursorShape.SizeHorCursor,
            45: Qt.CursorShape.SizeFDiagCursor,
            90: Qt.CursorShape.SizeVerCursor,
            135: Qt.CursorShape.SizeBDiagCursor,
        }

        for scene_angle, expected_cursor in expected_cursors.items():
            with self.subTest(scene_angle=scene_angle):
                cursor_index = scene_angle_to_cursor_index(scene_angle)
                self.assertEqual(resizeCursorList[cursor_index % 4], expected_cursor)

    def test_resize_handle_roles_ignore_box_width_and_scale(self):
        for horizontal_axis in (QPointF(0.1, 0), QPointF(4000, 0)):
            with self.subTest(horizontal_axis=horizontal_axis):
                self.assertEqual(
                    [
                        scene_angle_to_cursor_index(
                            resize_handle_scene_angle(
                                horizontal_axis,
                                handle_index,
                            )
                        )
                        for handle_index in range(8)
                    ],
                    list(range(8)),
                )

    def test_rotation_advances_all_handle_cursor_indices(self):
        for rotation in (-90, 45, 90, 180):
            with self.subTest(rotation=rotation):
                radians = math.radians(rotation)
                horizontal_axis = QPointF(
                    math.cos(radians) * 4000,
                    math.sin(radians) * 4000,
                )
                self.assertEqual(
                    [
                        scene_angle_to_cursor_index(
                            resize_handle_scene_angle(
                                horizontal_axis,
                                handle_index,
                            )
                        )
                        for handle_index in range(8)
                    ],
                    [
                        (handle_index + rotation // 45) % 8
                        for handle_index in range(8)
                    ],
                )


if __name__ == '__main__':
    unittest.main()
