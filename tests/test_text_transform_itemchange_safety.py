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

    from qtpy.QtCore import QPointF
    from qtpy.QtWidgets import QApplication, QGraphicsScene

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
    scene = QGraphicsScene()
    scene.addItem(item)
    item.setRotation(17.0)


    def transform_signature(target):
        transform = target.transform()
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


    def geometry_signature(target):
        rect = target.logical_unpadded_rect()
        return tuple(
            (point.x(), point.y())
            for point in (
                target.mapToScene(rect.topLeft()),
                target.mapToScene(rect.topRight()),
                target.mapToScene(rect.bottomRight()),
                target.mapToScene(rect.bottomLeft()),
            )
        )


    def assert_finite(values):
        if not all(math.isfinite(value) for value in values):
            raise AssertionError(values)


    def safely_rejected(action, label):
        rotation_before = item.rotation()
        model_angle_before = block.angle
        origin_before = QPointF(item.transformOriginPoint())
        transform_before = transform_signature(item)
        geometry_before = geometry_signature(item)
        print(label, flush=True)
        try:
            action()
        except (ValueError, RuntimeError):
            pass
        if item.rotation() != rotation_before:
            raise AssertionError(
                (label, 'rotation', item.rotation(), rotation_before)
            )
        if block.angle != model_angle_before:
            raise AssertionError(
                (label, 'model angle', block.angle, model_angle_before)
            )
        if item.transformOriginPoint() != origin_before:
            raise AssertionError(
                (label, 'origin', item.transformOriginPoint(), origin_before)
            )
        if transform_signature(item) != transform_before:
            raise AssertionError((label, 'base transform changed'))
        geometry_after = geometry_signature(item)
        assert_finite(value for point in geometry_after for value in point)
        for actual, expected in zip(geometry_after, geometry_before):
            for actual_value, expected_value in zip(actual, expected):
                if abs(actual_value - expected_value) > 1e-9:
                    raise AssertionError(
                        (label, 'geometry', actual, expected)
                    )


    rotation_property = item.metaObject().property(
        item.metaObject().indexOfProperty('rotation')
    )

    def reject_model_angle(value):
        try:
            item.setAngle(value)
        except ValueError:
            return
        raise AssertionError(('setAngle did not raise ValueError', value))


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
            lambda value=value: rotation_property.write(item, value),
            'meta-rotation-' + value_name,
        )
        safely_rejected(
            lambda value=value: reject_model_angle(value),
            'model-angle-' + value_name,
        )

    for value_name, value in (
        ('bool', True),
        ('non-convertible', object()),
    ):
        safely_rejected(
            lambda value=value: reject_model_angle(value),
            'model-angle-' + value_name,
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

    item.setRotation(41.07)
    if item.rotation() != 41.07:
        raise AssertionError(('valid rotation after rejection', item.rotation()))
    assert_finite(transform_signature(item))
    assert_finite(
        value for point in geometry_signature(item) for value in point
    )

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
        scene.addItem(invalid_item)
        if invalid_block.angle != 0.0:
            raise AssertionError(
                (value_name, 'repaired model angle', invalid_block.angle)
            )
        if invalid_item.rotation() != 0.0:
            raise AssertionError(
                (value_name, 'repaired item rotation', invalid_item.rotation())
            )
        assert_finite(transform_signature(invalid_item))
        assert_finite(
            coordinate
            for point in geometry_signature(invalid_item)
            for coordinate in point
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
