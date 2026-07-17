import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy import API_NAME, QT_VERSION

from ballontranslator.utils import shared as C

C.FLAG_QT6 = QT_VERSION.startswith('6')
C.USE_PYSIDE6 = API_NAME == 'PySide6'

try:
    from qtpy.QtWidgets import QApplication, QGraphicsScene, QUndoStack
except ImportError:
    from qtpy.QtGui import QUndoStack
    from qtpy.QtWidgets import QApplication, QGraphicsScene

from qtpy.QtCore import QPointF, QRectF
from qtpy.QtGui import QTextDocument

from ballontranslator.ui.canvas import MoveByKeyCommand
from ballontranslator.ui.textedit_commands import (
    ApplyFontformatCommand,
    AutoLayoutCommand,
    MoveBlkItemsCommand,
    ResetAngleCommand,
    SetTextTransformCommand,
    SqueezeCommand,
)
from ballontranslator.ui.textitem import TextBlkItem
from ballontranslator.utils.fontformat import FontFormat
from ballontranslator.utils.textblock import TextBlock


_APP = QApplication.instance() or QApplication([])


class FakeTextItem:
    def __init__(self, transform):
        self.transform = transform
        self.calls = []
        self.html = '<b>unchanged</b>'
        self.rect = (1, 2, 3, 4)
        self.pos = (5, 6)

    def set_text_transform(
        self,
        horizontal_scale,
        vertical_scale,
        slant_angle,
        glyph_slant_angle=None,
        *,
        preview=False,
    ):
        self.transform = (
            horizontal_scale,
            vertical_scale,
            slant_angle,
            0.0 if glyph_slant_angle is None else glyph_slant_angle,
        )
        self.calls.append((self.transform, preview))


class FakeTransEdit:
    def __init__(self):
        self._document = QTextDocument()

    def document(self):
        return self._document


class TrackingShapeControl:
    def __init__(self, item):
        self.blk_item = item
        self.refresh_count = 0

    def updateBoundingRect(self):
        self.refresh_count += 1


class SetTextTransformCommandTest(unittest.TestCase):
    def test_multi_item_command_is_atomic_and_restores_only_transforms(self):
        items = [
            FakeTextItem((1.0, 1.0, 0.0, 3.0)),
            FakeTextItem((0.5, 2.0, -10.0, -4.0)),
        ]
        snapshots = [(item.html, item.rect, item.pos) for item in items]
        refreshes = []
        command = SetTextTransformCommand(
            items,
            [(1.0, 1.0, -0.0, 3.0), (0.5, 2.0, -10.0, -4.0)],
            [(1.23456789, 0.01, 90.0, 99.0), (5.0, 3.0, -50.0, -99.0)],
            lambda: refreshes.append('refresh'),
        )

        stack = QUndoStack()
        stack.push(command)

        self.assertEqual(stack.count(), 1)
        self.assertEqual(command.childCount(), 0)
        self.assertEqual(items[0].transform, (1.234568, 0.1, 85.0, 45.0))
        self.assertEqual(items[1].transform, (4.0, 3.0, -50.0, -45.0))
        self.assertEqual(refreshes, ['refresh'])

        stack.undo()
        self.assertEqual(items[0].transform, (1.0, 1.0, 0.0, 3.0))
        self.assertEqual(items[1].transform, (0.5, 2.0, -10.0, -4.0))
        self.assertEqual(refreshes, ['refresh', 'refresh'])

        stack.redo()
        self.assertEqual(items[0].transform, (1.234568, 0.1, 85.0, 45.0))
        self.assertEqual(items[1].transform, (4.0, 3.0, -50.0, -45.0))
        self.assertEqual(refreshes, ['refresh', 'refresh', 'refresh'])
        self.assertTrue(all(not preview for item in items for _, preview in item.calls))
        self.assertEqual(
            [(item.html, item.rect, item.pos) for item in items], snapshots
        )

    def test_create_returns_none_when_normalized_values_match(self):
        item = FakeTextItem((4.0, 0.1, -85.0, 45.0))

        command = SetTextTransformCommand.create(
            [item],
            [(4.0, 0.1, -85.0, 45.0)],
            [(99.0, 0.0, -99.0, 99.0)],
        )

        self.assertIsNone(command)
        self.assertEqual(item.calls, [])

    def test_rejects_mismatched_per_item_state(self):
        with self.assertRaisesRegex(ValueError, 'same length'):
            SetTextTransformCommand(
                [FakeTextItem((1.0, 1.0, 0.0, 0.0))],
                [],
                [(1.0, 1.0, 0.0, 0.0)],
            )

    def test_undo_then_new_transform_clears_redo_branch(self):
        item = FakeTextItem((1.0, 1.0, 0.0, 0.0))
        stack = QUndoStack()
        stack.push(
            SetTextTransformCommand(
                [item], [(1.0, 1.0, 0.0, 0.0)], [(1.5, 1.0, 0.0, 7.0)]
            )
        )
        stack.undo()
        self.assertTrue(stack.canRedo())

        stack.push(
            SetTextTransformCommand(
                [item], [(1.0, 1.0, 0.0, 0.0)], [(1.0, 0.75, 5.0, -12.0)]
            )
        )
        self.assertFalse(stack.canRedo())
        self.assertEqual(stack.count(), 1)
        self.assertEqual(item.transform, (1.0, 0.75, 5.0, -12.0))

    def test_normalized_noop_never_enters_stack_or_calls_item(self):
        item = FakeTextItem((1.2, 1.0, 0.0, 0.0))
        stack = QUndoStack()
        command = SetTextTransformCommand.create(
            [item],
            [(1.2, 1.0, 0.0, 0.0)],
            [(1.20000001, 1.0, -0.0, -0.0)],
        )
        self.assertIsNone(command)
        self.assertEqual(stack.count(), 0)
        self.assertEqual(item.calls, [])


class LogicalMoveCommandTest(unittest.TestCase):
    @staticmethod
    def make_item():
        block = TextBlock(
            xyxy=[25, 35, 165, 105],
            _bounding_rect=[25, 35, 140, 70],
            translation='logical move',
            fontformat=FontFormat(stroke_width=0.08),
        )
        item = TextBlkItem(block)
        scene = QGraphicsScene()
        scene.addItem(item)
        return scene, item

    def assertPointAlmostEqual(self, actual, expected):
        self.assertAlmostEqual(actual.x(), expected.x(), places=6)
        self.assertAlmostEqual(actual.y(), expected.y(), places=6)

    def test_drag_command_is_padding_independent_and_refreshes_once(self):
        _scene, item = self.make_item()
        before = QPointF(item.logical_position())
        after = before + QPointF(37, -19)
        refreshes = []
        stack = QUndoStack()
        stack.push(
            MoveBlkItemsCommand(
                [item],
                before_positions=[before],
                after_positions=[after],
                overlay_sync=lambda: refreshes.append('sync'),
            )
        )
        self.assertPointAlmostEqual(item.logical_position(), after)
        self.assertEqual(refreshes, ['sync'])

        item.setPadding(item.padding() + 11.0)
        self.assertPointAlmostEqual(item.logical_position(), after)
        stack.undo()
        self.assertPointAlmostEqual(item.logical_position(), before)
        stack.redo()
        self.assertPointAlmostEqual(item.logical_position(), after)
        self.assertEqual(refreshes, ['sync', 'sync', 'sync'])

    def test_arrow_command_is_padding_independent(self):
        _scene, item = self.make_item()
        before = QPointF(item.logical_position())
        delta = QPointF(-4, 7)
        refreshes = []
        stack = QUndoStack()
        stack.push(
            MoveByKeyCommand(
                [item],
                delta,
                TrackingShapeControl(item),
                lambda: refreshes.append('sync'),
            )
        )
        self.assertPointAlmostEqual(item.logical_position(), before + delta)
        item.setPadding(item.padding() + 9.0)
        stack.undo()
        self.assertPointAlmostEqual(item.logical_position(), before)
        stack.redo()
        self.assertPointAlmostEqual(item.logical_position(), before + delta)
        self.assertEqual(refreshes, ['sync', 'sync', 'sync'])

    def test_noop_drag_command_does_not_enter_stack(self):
        _scene, item = self.make_item()
        position = QPointF(item.logical_position())
        stack = QUndoStack()
        stack.push(
            MoveBlkItemsCommand(
                [item],
                before_positions=[position],
                after_positions=[position],
            )
        )
        self.assertEqual(stack.count(), 0)


class ApplyFontformatCommandTest(unittest.TestCase):
    @staticmethod
    def make_item(transform=(1.15, 0.85, 7.0, -6.0)):
        block = TextBlock(
            xyxy=[0, 0, 140, 70],
            _bounding_rect=[0, 0, 140, 70],
            translation='transform format',
            fontformat=FontFormat(
                horizontal_scale=transform[0],
                vertical_scale=transform[1],
                slant_angle=transform[2],
                glyph_slant_angle=transform[3],
            ),
        )
        item = TextBlkItem(block)
        scene = QGraphicsScene()
        scene.addItem(item)
        return scene, item

    def test_quartet_is_restored_on_undo_and_redo(self):
        _scene, item = self.make_item()
        before = item.fontformat.text_transform
        target = item.fontformat.deepcopy()
        target.horizontal_scale = 1.8
        target.vertical_scale = 0.55
        target.slant_angle = -18.0
        target.glyph_slant_angle = 21.0
        stack = QUndoStack()

        stack.push(ApplyFontformatCommand([item], [FakeTransEdit()], target))
        self.assertEqual(item.fontformat.text_transform, target.text_transform)

        stack.undo()
        self.assertEqual(item.fontformat.text_transform, before)

        stack.redo()
        self.assertEqual(item.fontformat.text_transform, target.text_transform)

    def test_overlay_callback_runs_once_per_apply_undo_redo(self):
        _scene, item = self.make_item()
        refreshes = []
        target = item.fontformat.deepcopy()
        target.slant_angle = 18.0
        stack = QUndoStack()

        stack.push(
            ApplyFontformatCommand(
                [item],
                [FakeTransEdit()],
                target,
                TrackingShapeControl(item),
                lambda: refreshes.append('sync'),
            )
        )
        stack.undo()
        stack.redo()

        self.assertEqual(refreshes, ['sync', 'sync', 'sync'])


class GeometryCommandOverlaySyncTest(unittest.TestCase):
    @staticmethod
    def _item():
        item = Mock()
        item.absBoundingRect.return_value = QRectF(1, 2, 30, 40)
        item.toHtml.return_value = '<p>new</p>'
        item.toPlainText.return_value = 'plain'
        item.fontformat = SimpleNamespace(letter_spacing=1)
        item.rotation.return_value = 27.0
        return item

    def test_geometry_commands_sync_once_at_each_undo_boundary(self):
        factories = (
            lambda item, callback: AutoLayoutCommand(
                [item],
                [QRectF(0, 0, 20, 20)],
                ['<p>old</p>'],
                [Mock()],
                callback,
            ),
            lambda item, callback: SqueezeCommand(
                [item],
                SimpleNamespace(blk_item=None, updateBoundingRect=Mock()),
                callback,
            ),
            lambda item, callback: ResetAngleCommand(
                [item],
                SimpleNamespace(
                    blk_item=None,
                    setAngle=Mock(),
                    updateBoundingRect=Mock(),
                ),
                callback,
            ),
        )
        for factory in factories:
            with self.subTest(command=factory):
                item = self._item()
                refreshes = []
                stack = QUndoStack()
                stack.push(factory(item, lambda: refreshes.append('sync')))
                stack.undo()
                stack.redo()
                self.assertEqual(refreshes, ['sync', 'sync', 'sync'])


if __name__ == '__main__':
    unittest.main()
