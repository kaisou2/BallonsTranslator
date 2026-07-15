import importlib.util
import os
import subprocess
import sys
import textwrap
import unittest


_BINDINGS = (
    ('pyqt5', 'PyQt5'),
    ('pyqt6', 'PyQt6'),
    ('pyside6', 'PySide6'),
)


_CHILD_PROBE = textwrap.dedent(
    r'''
    import math
    import sys

    from qtpy.QtCore import QPointF
    from qtpy.QtWidgets import QApplication, QGraphicsScale

    from ballontranslator.ui.textitem import TextBlkItem
    from ballontranslator.utils.fontformat import FontFormat
    from ballontranslator.utils.textblock import TextBlock


    app = QApplication.instance() or QApplication([])
    block = TextBlock(
        xyxy=[10, 20, 110, 70],
        _bounding_rect=[10, 20, 100, 50],
        translation='x',
        fontformat=FontFormat(horizontal_scale=1.0, vertical_scale=4.0),
    )
    item = TextBlkItem(block)
    item.setRotation(17.0)


    def transform_signature():
        transform = item.transform()
        return (
            transform.m11(),
            transform.m12(),
            transform.m13(),
            transform.m21(),
            transform.m22(),
            transform.m23(),
            transform.m31(),
            transform.m32(),
            transform.m33(),
        )


    def geometry_signature():
        rect = item.logical_unpadded_rect()
        return tuple(
            (point.x(), point.y())
            for point in (
                item.mapToScene(rect.topLeft()),
                item.mapToScene(rect.topRight()),
                item.mapToScene(rect.bottomRight()),
                item.mapToScene(rect.bottomLeft()),
            )
        )


    def assert_finite(values):
        if not all(math.isfinite(value) for value in values):
            raise AssertionError(values)


    def safely_rejected(action, label):
        rotation_before = item.rotation()
        model_angle_before = block.angle
        origin_before = QPointF(item.transformOriginPoint())
        transform_before = transform_signature()
        geometry_before = geometry_signature()
        print(label, flush=True)
        try:
            action()
        except (ValueError, RuntimeError):
            pass
        if item.rotation() != rotation_before:
            raise AssertionError((label, 'rotation', item.rotation(), rotation_before))
        if block.angle != model_angle_before:
            raise AssertionError(
                (label, 'model angle', block.angle, model_angle_before)
            )
        if item.transformOriginPoint() != origin_before:
            raise AssertionError(
                (label, 'origin', item.transformOriginPoint(), origin_before)
            )
        if transform_signature() != transform_before:
            raise AssertionError((label, 'base transform changed'))
        geometry_after = geometry_signature()
        assert_finite(value for point in geometry_after for value in point)
        for actual, expected in zip(geometry_after, geometry_before):
            for actual_value, expected_value in zip(actual, expected):
                if abs(actual_value - expected_value) > 1e-9:
                    raise AssertionError(
                        (label, 'geometry', actual, expected)
                    )


    for value_name, value in (
        ('nan', float('nan')),
        ('positive-inf', float('inf')),
        ('negative-inf', float('-inf')),
    ):
        safely_rejected(
            lambda value=value: item.setRotation(value),
            'direct-rotation-' + value_name,
        )
        safely_rejected(
            lambda value=value: item.setProperty('rotation', value),
            'property-rotation-' + value_name,
        )
        safely_rejected(
            lambda value=value: item.setAngle(value),
            'model-angle-' + value_name,
        )

    rotation_property = item.metaObject().property(
        item.metaObject().indexOfProperty('rotation')
    )
    safely_rejected(
        lambda: rotation_property.write(item, float('nan')),
        'meta-rotation-nan',
    )

    valid_origin = QPointF(item.transformOriginPoint())
    for value_name, point in (
        ('nan-x', QPointF(float('nan'), valid_origin.y())),
        ('positive-inf-y', QPointF(valid_origin.x(), float('inf'))),
        ('negative-inf-x', QPointF(float('-inf'), valid_origin.y())),
    ):
        safely_rejected(
            lambda point=point: item.setTransformOriginPoint(point),
            'direct-origin-' + value_name,
        )
        safely_rejected(
            lambda point=point: item.setProperty('transformOriginPoint', point),
            'property-origin-' + value_name,
        )

    print('nonempty-transformations-list', flush=True)
    graphics_scale = QGraphicsScale()
    try:
        item.setTransformations([graphics_scale])
    except (ValueError, RuntimeError):
        pass
    if item.transformations():
        raise AssertionError('nonempty transformations list was accepted')

    item.setRotation(41.07)
    if item.rotation() != 41.07:
        raise AssertionError(('valid rotation after rejection', item.rotation()))
    assert_finite(transform_signature())
    assert_finite(value for point in geometry_signature() for value in point)

    for value_name, value in (
        ('nan', float('nan')),
        ('positive-inf', float('inf')),
        ('negative-inf', float('-inf')),
    ):
        print('constructor-rotation-' + value_name, flush=True)
        invalid_block = TextBlock(
            xyxy=[0, 0, 80, 40],
            _bounding_rect=[0, 0, 80, 40],
            translation='load',
            angle=value,
            fontformat=FontFormat(horizontal_scale=1.0, vertical_scale=4.0),
        )
        invalid_item = TextBlkItem(invalid_block)
        if invalid_block.angle != 0.0:
            raise AssertionError(
                (value_name, 'repaired model angle', invalid_block.angle)
            )
        if invalid_item.rotation() != 0.0:
            raise AssertionError(
                (value_name, 'repaired item rotation', invalid_item.rotation())
            )
        invalid_transform = invalid_item.transform()
        assert_finite(
            (
                invalid_transform.m11(),
                invalid_transform.m12(),
                invalid_transform.m13(),
                invalid_transform.m21(),
                invalid_transform.m22(),
                invalid_transform.m23(),
                invalid_transform.m31(),
                invalid_transform.m32(),
                invalid_transform.m33(),
            )
        )
        invalid_rect = invalid_item.logical_unpadded_rect()
        invalid_corners = (
            invalid_item.mapToScene(invalid_rect.topLeft()),
            invalid_item.mapToScene(invalid_rect.topRight()),
            invalid_item.mapToScene(invalid_rect.bottomRight()),
            invalid_item.mapToScene(invalid_rect.bottomLeft()),
        )
        assert_finite(
            coordinate
            for point in invalid_corners
            for coordinate in (point.x(), point.y())
        )
    print('probe-ok', flush=True)
    '''
)


class TextTransformItemChangeSafetyTests(unittest.TestCase):
    def test_invalid_qt_properties_are_rejected_without_process_termination(self):
        available_bindings = [
            (api_name, module_name)
            for api_name, module_name in _BINDINGS
            if importlib.util.find_spec(module_name) is not None
        ]
        self.assertTrue(available_bindings, 'no supported Qt binding is installed')

        repository_root = os.path.dirname(os.path.dirname(__file__))
        for api_name, module_name in available_bindings:
            with self.subTest(binding=module_name):
                environment = os.environ.copy()
                environment.update(
                    QT_API=api_name,
                    QT_QPA_PLATFORM='offscreen',
                    PYTHONFAULTHANDLER='1',
                )
                completed = subprocess.run(
                    [sys.executable, '-c', _CHILD_PROBE],
                    cwd=repository_root,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    msg=(
                        f'{module_name} safety probe exited with '
                        f'{completed.returncode}.\n'
                        f'stdout:\n{completed.stdout}\n'
                        f'stderr:\n{completed.stderr}'
                    ),
                )
                self.assertIn('probe-ok', completed.stdout)


if __name__ == '__main__':
    unittest.main()
