import math
import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy.QtCore import QPointF

from ballontranslator.ui.text_transform import (
    compensated_text_transform_matrix,
    text_transform_matrix,
)


class CompensatedTextTransformMatrixTest(unittest.TestCase):

    @staticmethod
    def box_point(point, pivot, horizontal_scale, vertical_scale, slant_angle):
        """Independent canonical Box formula; do not call production helpers."""
        shear = -math.tan(math.radians(slant_angle))
        dx = point.x() - pivot.x()
        dy = point.y() - pivot.y()
        return QPointF(
            pivot.x()
            + horizontal_scale * dx
            + shear * vertical_scale * dy,
            pivot.y() + vertical_scale * dy,
        )

    @staticmethod
    def rotate_point(point, pivot, angle):
        """Independent clockwise-positive rotation in Qt's y-down plane."""
        radians = math.radians(angle)
        cosine = math.cos(radians)
        sine = math.sin(radians)
        dx = point.x() - pivot.x()
        dy = point.y() - pivot.y()
        return QPointF(
            pivot.x() + cosine * dx - sine * dy,
            pivot.y() + sine * dx + cosine * dy,
        )

    def assertPointAlmostEqual(self, actual, expected, places=9):
        self.assertAlmostEqual(actual.x(), expected.x(), places=places)
        self.assertAlmostEqual(actual.y(), expected.y(), places=places)

    def test_compensation_maps_as_box_then_rotation_across_extremes(self):
        box_pivots = (QPointF(), QPointF(123.456, -78.9))
        rotation_pivots = (QPointF(), QPointF(-17.25, 81.5))
        points = (
            QPointF(),
            QPointF(19.75, -31.5),
            QPointF(-10000.0, 20000.0),
        )
        transforms = (
            (1.0, 4.0, 0.0),
            (4.0, 1.0, 0.0),
            (0.1, 4.0, -85.0),
            (4.0, 0.1, 85.0),
            (1.7, 0.6, 23.0),
            (1.0, 1.0, 0.0),
        )

        for box_pivot in box_pivots:
            for rotation_pivot in rotation_pivots:
                for horizontal_scale, vertical_scale, slant_angle in transforms:
                    for angle in (0.0, 41.07, 90.0, -137.0, 359.5):
                        with self.subTest(
                            box_pivot=box_pivot,
                            rotation_pivot=rotation_pivot,
                            horizontal_scale=horizontal_scale,
                            vertical_scale=vertical_scale,
                            slant_angle=slant_angle,
                            angle=angle,
                        ):
                            base = compensated_text_transform_matrix(
                                horizontal_scale,
                                vertical_scale,
                                slant_angle,
                                box_pivot,
                                angle,
                                rotation_pivot,
                            )
                            inverse, invertible = base.inverted()
                            self.assertTrue(invertible)
                            self.assertAlmostEqual(
                                base.determinant(),
                                horizontal_scale * vertical_scale,
                                places=9,
                            )
                            self.assertTrue(
                                all(
                                    math.isfinite(value)
                                    for value in (
                                        base.m11(),
                                        base.m12(),
                                        base.m21(),
                                        base.m22(),
                                        base.dx(),
                                        base.dy(),
                                    )
                                )
                            )

                            for point in points:
                                rotated_input = self.rotate_point(
                                    point, rotation_pivot, angle
                                )
                                actual = base.map(rotated_input)
                                expected = self.rotate_point(
                                    self.box_point(
                                        point,
                                        box_pivot,
                                        horizontal_scale,
                                        vertical_scale,
                                        slant_angle,
                                    ),
                                    rotation_pivot,
                                    angle,
                                )
                                self.assertPointAlmostEqual(
                                    actual, expected, places=7
                                )
                                self.assertPointAlmostEqual(
                                    inverse.map(base.map(rotated_input)),
                                    rotated_input,
                                    places=7,
                                )

    def test_default_rotation_pivot_is_box_pivot(self):
        pivot = QPointF(7.25, -3.5)
        default = compensated_text_transform_matrix(
            2.0, 0.75, 31.0, pivot, 52.0
        )
        explicit = compensated_text_transform_matrix(
            2.0, 0.75, 31.0, pivot, 52.0, pivot
        )

        for point in (QPointF(), pivot, QPointF(-19.0, 27.0)):
            self.assertPointAlmostEqual(default.map(point), explicit.map(point))

    def test_neutral_and_full_turn_fast_paths_are_exact(self):
        box_pivot = QPointF(1.25, -9.5)
        rotation_pivot = QPointF(-300.0, 700.0)

        neutral = compensated_text_transform_matrix(
            1.0, 1.0, 0.0, box_pivot, 41.07, rotation_pivot
        )
        self.assertTrue(neutral.isIdentity())

        canonical = text_transform_matrix(2.0, 0.5, 22.0, box_pivot)
        for angle in (0.0, -0.0, 360.0, -720.0):
            with self.subTest(angle=angle):
                compensated = compensated_text_transform_matrix(
                    2.0,
                    0.5,
                    22.0,
                    box_pivot,
                    angle,
                    rotation_pivot,
                )
                self.assertEqual(compensated, canonical)

        isotropic = text_transform_matrix(2.0, 2.0, 0.0, box_pivot)
        compensated = compensated_text_transform_matrix(
            2.0, 2.0, 0.0, box_pivot, 41.07, box_pivot
        )
        self.assertEqual(compensated, isotropic)

    def test_rejects_nonfinite_angle_and_pivots(self):
        for angle in (True, math.nan, math.inf, -math.inf, object()):
            with self.subTest(angle=angle):
                with self.assertRaisesRegex(ValueError, 'rotation angle'):
                    compensated_text_transform_matrix(
                        1.0, 1.0, 0.0, QPointF(), angle
                    )

        for coordinate in (math.nan, math.inf, -math.inf):
            with self.subTest(box_coordinate=coordinate):
                with self.assertRaisesRegex(ValueError, 'box pivot'):
                    compensated_text_transform_matrix(
                        1.0,
                        1.0,
                        0.0,
                        QPointF(coordinate, 0.0),
                        10.0,
                    )
            with self.subTest(rotation_coordinate=coordinate):
                with self.assertRaisesRegex(ValueError, 'rotation pivot'):
                    compensated_text_transform_matrix(
                        1.0,
                        1.0,
                        0.0,
                        QPointF(),
                        10.0,
                        QPointF(0.0, coordinate),
                    )


if __name__ == '__main__':
    unittest.main()
