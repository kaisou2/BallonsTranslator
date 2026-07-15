import math
import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy.QtCore import QPointF, QPropertyAnimation
from qtpy.QtWidgets import QApplication, QGraphicsItem, QGraphicsScene

from ballontranslator.ui.textitem import TextBlkItem
from ballontranslator.utils.fontformat import FontFormat
from ballontranslator.utils.textblock import TextBlock


_APP = QApplication.instance() or QApplication([])
_HORIZONTAL_SCALE = 1.0
_VERTICAL_SCALE = 4.0
_BOX_SLANT = 0.0
_ANGLE = 41.07


def _make_item():
    fontformat = FontFormat(
        horizontal_scale=_HORIZONTAL_SCALE,
        vertical_scale=_VERTICAL_SCALE,
        slant_angle=_BOX_SLANT,
    )
    block = TextBlock(
        xyxy=[10, 20, 110, 70],
        _bounding_rect=[10, 20, 100, 50],
        translation='x',
        fontformat=fontformat,
    )
    scene = QGraphicsScene()
    item = TextBlkItem(block)
    scene.addItem(item)
    return scene, item


def _rect_corners(item):
    rect = item.logical_unpadded_rect()
    return (
        rect.topLeft(),
        rect.topRight(),
        rect.bottomRight(),
        rect.bottomLeft(),
    )


def _box_then_rotate(point, box_pivot, rotation_pivot, angle):
    """Evaluate the required R(S(point)) mapping without production helpers."""
    dx = point.x() - box_pivot.x()
    dy = point.y() - box_pivot.y()
    shear = -math.tan(math.radians(_BOX_SLANT))
    box_x = (
        box_pivot.x()
        + _HORIZONTAL_SCALE * dx
        + shear * _VERTICAL_SCALE * dy
    )
    box_y = box_pivot.y() + _VERTICAL_SCALE * dy

    radians = math.radians(angle)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    box_dx = box_x - rotation_pivot.x()
    box_dy = box_y - rotation_pivot.y()
    return QPointF(
        rotation_pivot.x() + cosine * box_dx - sine * box_dy,
        rotation_pivot.y() + sine * box_dx + cosine * box_dy,
    )


class TextTransformRotationPropertyTests(unittest.TestCase):
    def assertPointAlmostEqual(self, actual, expected, places=7):
        self.assertAlmostEqual(actual.x(), expected.x(), places=places)
        self.assertAlmostEqual(actual.y(), expected.y(), places=places)

    def assertBoxThenRotationGeometry(
        self,
        item,
        angle,
        box_pivot=None,
        rotation_pivot=None,
    ):
        if box_pivot is None:
            box_pivot = item.logical_unpadded_rect().center()
        if rotation_pivot is None:
            rotation_pivot = item.transformOriginPoint()
        for corner in _rect_corners(item):
            expected_local = _box_then_rotate(
                corner,
                box_pivot,
                rotation_pivot,
                angle,
            )
            expected_scene = item.pos() + expected_local
            self.assertPointAlmostEqual(item.mapToScene(corner), expected_scene)

    def test_direct_set_rotation_composes_box_before_rotation(self):
        _scene, item = _make_item()

        item.setRotation(_ANGLE)

        self.assertAlmostEqual(item.rotation(), _ANGLE)
        self.assertBoxThenRotationGeometry(item, _ANGLE)
        corners = [item.mapToScene(point) for point in _rect_corners(item)]
        top = corners[1] - corners[0]
        left = corners[3] - corners[0]
        self.assertAlmostEqual(
            top.x() * left.x() + top.y() * left.y(),
            0.0,
            places=7,
        )

    def test_qobject_set_property_recomposes_compensated_base(self):
        _scene, item = _make_item()

        self.assertTrue(item.setProperty('rotation', _ANGLE))

        self.assertAlmostEqual(item.rotation(), _ANGLE)
        self.assertBoxThenRotationGeometry(item, _ANGLE)

    def test_qmeta_property_write_recomposes_compensated_base(self):
        _scene, item = _make_item()
        meta_object = item.metaObject()
        property_index = meta_object.indexOfProperty('rotation')
        self.assertGreaterEqual(property_index, 0)
        rotation_property = meta_object.property(property_index)

        self.assertTrue(rotation_property.write(item, _ANGLE))

        self.assertAlmostEqual(item.rotation(), _ANGLE)
        self.assertBoxThenRotationGeometry(item, _ANGLE)

    def test_property_animation_midpoint_has_box_then_rotation_geometry(self):
        _scene, item = _make_item()
        animation = QPropertyAnimation(item, b'rotation')
        animation.setStartValue(0.0)
        animation.setEndValue(_ANGLE * 2.0)
        animation.setDuration(1000)

        animation.start()
        animation.setCurrentTime(500)
        try:
            self.assertAlmostEqual(item.rotation(), _ANGLE)
            self.assertBoxThenRotationGeometry(item, _ANGLE)
        finally:
            animation.stop()

    def test_rotation_changed_observer_sees_only_final_scene_transform(self):
        _scene, item = _make_item()
        observations = []

        def observe_rotation():
            observations.append(
                (
                    item.rotation(),
                    [item.mapToScene(point) for point in _rect_corners(item)],
                )
            )

        item.rotationChanged.connect(observe_rotation)
        self.assertTrue(item.setProperty('rotation', _ANGLE))

        self.assertEqual(len(observations), 1)
        observed_angle, observed_corners = observations[0]
        self.assertAlmostEqual(observed_angle, _ANGLE)
        box_pivot = item.logical_unpadded_rect().center()
        rotation_pivot = item.transformOriginPoint()
        for corner, actual in zip(_rect_corners(item), observed_corners):
            expected = item.pos() + _box_then_rotate(
                corner,
                box_pivot,
                rotation_pivot,
                _ANGLE,
            )
            self.assertPointAlmostEqual(actual, expected)

    def test_transform_origin_property_change_recomposes_before_return(self):
        _scene, item = _make_item()
        item.setRotation(_ANGLE)
        target_pivot = QPointF(19.25, 43.5)

        self.assertTrue(item.setProperty('transformOriginPoint', target_pivot))

        self.assertPointAlmostEqual(item.transformOriginPoint(), target_pivot)
        self.assertBoxThenRotationGeometry(
            item,
            _ANGLE,
            rotation_pivot=target_pivot,
        )

    def test_rotation_compensation_keeps_transformations_list_empty(self):
        _scene, item = _make_item()
        self.assertEqual(item.transformations(), [])

        item.setProperty('rotation', _ANGLE)

        self.assertEqual(item.transformations(), [])
        self.assertTrue(
            bool(
                item.flags()
                & QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
            )
        )


if __name__ == '__main__':
    unittest.main()
