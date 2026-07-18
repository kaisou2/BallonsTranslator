import os
import sys
import unittest
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy.QtCore import Qt
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QApplication

try:
    from qtpy.QtWidgets import QUndoStack
except ImportError:
    from qtpy.QtGui import QUndoStack

from ballontranslator.ui import shared_widget as SW
from ballontranslator.ui.text_panel import FontFormatPanel
from ballontranslator.ui.textitem import TextBlkItem
from ballontranslator.utils import config as C
from ballontranslator.utils import shared as app_shared
from ballontranslator.utils.fontformat import FontFormat
from ballontranslator.utils.textblock import TextBlock


_APP = QApplication.instance() or QApplication([])


class TrackingTextBlkItem(TextBlkItem):
    def __init__(self, *args, **kwargs):
        self.transform_api_calls = 0
        self.matrix_writes = 0
        self.repaint_calls = 0
        self.update_calls = 0
        super().__init__(*args, **kwargs)

    def set_text_transform(self, *args, **kwargs):
        self.transform_api_calls += 1
        return super().set_text_transform(*args, **kwargs)

    def setTransform(self, matrix, combine=False):
        self.matrix_writes += 1
        return super().setTransform(matrix, combine)

    def repaint_background(self, *args, **kwargs):
        self.repaint_calls += 1
        return super().repaint_background(*args, **kwargs)

    def update(self, *args, **kwargs):
        self.update_calls += 1
        return super().update(*args, **kwargs)


def make_item(
    horizontal=1.0, vertical=1.0, slant=0.0, glyph_slant=0.0, idx=0
):
    block = TextBlock(
        xyxy=[10, 20, 110, 70],
        _bounding_rect=[10, 20, 100, 50],
        translation='panel integration',
        fontformat=FontFormat(
            horizontal_scale=horizontal,
            vertical_scale=vertical,
            slant_angle=slant,
            glyph_slant_angle=glyph_slant,
        ),
    )
    item = TrackingTextBlkItem(block)
    item.idx = idx
    item.transform_api_calls = 0
    item.matrix_writes = 0
    return item


class FakeCanvas:
    def __init__(self):
        self.undo_stack = QUndoStack()
        self.selection = []
        self.txtblkShapeControl = None

    def selected_text_items(self):
        return list(self.selection)

    def push_undo_command(self, command):
        self.undo_stack.push(command)

    def sync_text_overlays(self):
        if self.txtblkShapeControl is not None:
            self.txtblkShapeControl.updateBoundingRect()


class TrackingShapeControl:
    def __init__(self, item):
        self.blk_item = item
        self.refresh_count = 0

    def updateBoundingRect(self):
        self.refresh_count += 1


class FontFormatPanelTransformIntegrationTest(unittest.TestCase):
    def setUp(self):
        had_register_view_widget = hasattr(app_shared, 'register_view_widget')
        old_register_view_widget = getattr(
            app_shared, 'register_view_widget', None
        )

        def restore_register_view_widget():
            if had_register_view_widget:
                app_shared.register_view_widget = old_register_view_widget
            elif hasattr(app_shared, 'register_view_widget'):
                del app_shared.register_view_widget

        self.addCleanup(restore_register_view_widget)
        app_shared.register_view_widget = lambda *_args, **_kwargs: None

        self.canvas = FakeCanvas()
        self.addCleanup(self.canvas.undo_stack.clear)
        old_canvas = SW.canvas
        self.addCleanup(setattr, SW, 'canvas', old_canvas)
        SW.canvas = self.canvas

        old_active_format = C.active_format
        self.addCleanup(setattr, C, 'active_format', old_active_format)
        self.panel = FontFormatPanel(_APP)

        def cleanup_panel():
            self.panel.close()
            self.panel.deleteLater()
            _APP.processEvents()

        self.addCleanup(cleanup_panel)
        self.panel.global_format = FontFormat()
        self.panel.set_active_format(self.panel.global_format)

    def select_one(self, item):
        self.canvas.selection = [item]
        self.panel.set_textblk_item(item)

    def select_many(self, items):
        self.canvas.selection = list(items)
        self.panel.set_textblk_item(None, multi_select=True)

    def test_selected_numeric_commit_is_atomic_and_undoable(self):
        item = make_item(horizontal=1.0)
        self.select_one(item)
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control

        control.editor.setText('120.0%')
        control._on_text_edited()
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(self.canvas.undo_stack.count(), 0)
        self.assertTrue(control.commit_pending())

        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.2)
        self.assertEqual(control.editor.text(), '120.0%')
        self.assertEqual(self.canvas.undo_stack.count(), 1)
        self.canvas.undo_stack.undo()
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.0)
        self.canvas.undo_stack.redo()
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.2)

    def test_mixed_absolute_commit_sets_all_items_with_one_command(self):
        first = make_item(horizontal=0.8, idx=0)
        second = make_item(horizontal=1.6, idx=1)
        self.select_many([first, second])
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        self.assertEqual(control.editor.text(), '\N{EM DASH}')

        control.editor.setText('120')
        control._on_text_edited()
        self.assertTrue(control.commit_pending())

        self.assertEqual(first.blk.fontformat.horizontal_scale, 1.2)
        self.assertEqual(second.blk.fontformat.horizontal_scale, 1.2)
        self.assertEqual(self.canvas.undo_stack.count(), 1)
        self.canvas.undo_stack.undo()
        self.assertEqual(first.blk.fontformat.horizontal_scale, 0.8)
        self.assertEqual(second.blk.fontformat.horizontal_scale, 1.6)

    def test_many_drag_moves_preview_then_release_one_command(self):
        first = make_item(horizontal=1.1, idx=0)
        second = make_item(horizontal=0.8, idx=1)
        self.select_many([first, second])
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control

        control._start_drag()
        control._move_drag(4)
        control._move_drag(3)
        control._move_drag(-2)
        self.assertEqual(first.blk.fontformat.horizontal_scale, 1.1)
        self.assertEqual(second.blk.fontformat.horizontal_scale, 0.8)
        self.assertEqual(self.canvas.undo_stack.count(), 0)
        control._finish_drag()

        self.assertEqual(first.blk.fontformat.horizontal_scale, 1.15)
        self.assertEqual(second.blk.fontformat.horizontal_scale, 0.85)
        self.assertEqual(self.canvas.undo_stack.count(), 1)
        self.canvas.undo_stack.undo()
        self.assertEqual(first.blk.fontformat.horizontal_scale, 1.1)
        self.assertEqual(second.blk.fontformat.horizontal_scale, 0.8)

    def test_noop_text_and_zero_drag_do_not_touch_item_or_undo(self):
        item = make_item(horizontal=1.2)
        self.select_one(item)
        shape_control = TrackingShapeControl(item)
        self.canvas.txtblkShapeControl = shape_control
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        item.transform_api_calls = 0
        item.matrix_writes = 0
        item.repaint_calls = 0
        item.update_calls = 0
        before_padding = item.padding()
        before_cache_key = (
            None
            if item.background_pixmap is None
            else item.background_pixmap.cacheKey()
        )

        control.editor.setText('120.00%')
        control._on_text_edited()
        self.assertTrue(control.commit_pending())
        control._start_drag()
        control._finish_drag()

        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.2)
        self.assertEqual(item.transform_api_calls, 0)
        self.assertEqual(item.matrix_writes, 0)
        self.assertEqual(item.repaint_calls, 0)
        self.assertEqual(item.update_calls, 0)
        self.assertEqual(shape_control.refresh_count, 0)
        self.assertEqual(item.padding(), before_padding)
        self.assertEqual(
            None
            if item.background_pixmap is None
            else item.background_pixmap.cacheKey(),
            before_cache_key,
        )
        self.assertEqual(self.canvas.undo_stack.count(), 0)

    def test_global_drag_commits_canonical_value_without_item_command(self):
        self.canvas.selection = []
        self.panel.set_textblk_item(None)
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        self.assertEqual(self.panel.global_format.horizontal_scale, 1.0)

        control._start_drag()
        control._move_drag(20)
        self.assertEqual(self.panel.global_format.horizontal_scale, 1.0)
        control._finish_drag()

        self.assertEqual(self.panel.global_format.horizontal_scale, 1.2)
        self.assertEqual(control.editor.text(), '120.0%')
        self.assertEqual(self.canvas.undo_stack.count(), 0)

    def test_outward_drag_at_canonical_limits_is_strict_noop(self):
        cases = (
            ('horizontal_scale', 4.0, 10),
            ('horizontal_scale', 0.1, -10),
            ('vertical_scale', 4.0, 10),
            ('vertical_scale', 0.1, -10),
            ('slant_angle', 85.0, 10),
            ('slant_angle', -85.0, -10),
            ('glyph_slant_angle', 45.0, 10),
            ('glyph_slant_angle', -45.0, -10),
        )
        for param_name, value, display_delta in cases:
            with self.subTest(param_name=param_name, value=value):
                item = make_item(
                    horizontal=value if param_name == 'horizontal_scale' else 1.0,
                    vertical=value if param_name == 'vertical_scale' else 1.0,
                    slant=value if param_name == 'slant_angle' else 0.0,
                    glyph_slant=(
                        value if param_name == 'glyph_slant_angle' else 0.0
                    ),
                )
                self.select_one(item)
                shape_control = TrackingShapeControl(item)
                self.canvas.txtblkShapeControl = shape_control
                control = self.panel.textadvancedfmt_panel.transform_controls[
                    param_name
                ]
                item.transform_api_calls = 0
                item.matrix_writes = 0

                control._start_drag()
                control._move_drag(display_delta)
                control._finish_drag()

                self.assertEqual(
                    getattr(item.blk.fontformat, param_name), value
                )
                self.assertIsNone(item._text_transform_preview)
                self.assertEqual(item.transform_api_calls, 0)
                self.assertEqual(item.matrix_writes, 0)
                self.assertEqual(shape_control.refresh_count, 0)
                self.assertEqual(self.canvas.undo_stack.count(), 0)
                self.canvas.txtblkShapeControl = None

    def test_escape_during_drag_rolls_preview_back_without_command(self):
        item = make_item(horizontal=1.0)
        self.select_one(item)
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        original_matrix = item.transform()

        QTest.mousePress(control.label, Qt.MouseButton.LeftButton)
        control._move_drag(25)
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.0)
        self.assertNotEqual(item.transform(), original_matrix)
        QTest.keyClick(control.label, Qt.Key.Key_Escape)

        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(item.transform(), original_matrix)
        self.assertIsNone(item._text_transform_preview)
        self.assertEqual(self.canvas.undo_stack.count(), 0)

    def test_selection_change_commits_pending_value_to_old_target(self):
        old_item = make_item(horizontal=1.0, idx=0)
        new_item = make_item(horizontal=0.5, idx=1)
        self.select_one(old_item)
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        control.editor.setText('135%')
        control._on_text_edited()
        self.assertEqual(old_item.blk.fontformat.horizontal_scale, 1.0)

        self.panel.set_textblk_item(new_item)

        self.assertEqual(old_item.blk.fontformat.horizontal_scale, 1.35)
        self.assertEqual(new_item.blk.fontformat.horizontal_scale, 0.5)
        self.assertEqual(self.canvas.undo_stack.count(), 1)
        self.assertEqual(control.editor.text(), '50.0%')

    def test_reselected_transform_survives_format_snapshot_and_save_sync(self):
        item = make_item(
            horizontal=1.0,
            vertical=0.8,
            slant=10.0,
            glyph_slant=12.0,
        )
        self.select_one(item)
        self.panel.set_textblk_item(None)
        self.assertIsNot(item.fontformat, item.blk.fontformat)
        self.select_one(item)

        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        control.editor.setText('150%')
        control._on_text_edited()
        self.assertTrue(control.commit_pending())
        expected = (1.5, 0.8, 10.0, 12.0)
        self.assertEqual(item.blk.fontformat.text_transform, expected)
        self.assertEqual(item.get_fontformat().text_transform, expected)

        # Ctrl+S reaches this item boundary through updateTextBlkList().
        item.updateBlkFormat()
        self.assertEqual(item.blk.fontformat.text_transform, expected)

    def test_focus_preserved_empty_selection_keeps_local_transform_owner(self):
        item = make_item(
            horizontal=1.0,
            vertical=0.8,
            slant=10.0,
            glyph_slant=12.0,
        )
        self.select_one(item)
        global_before = self.panel.global_format.text_transform

        self.panel.focusOnColorDialog = True
        self.canvas.selection = []
        self.panel.set_textblk_item(None)
        self.assertFalse(self.panel.global_mode())
        self.assertIs(self.panel.textblk_item, item)
        self.assertEqual(self.panel._transform_items, [item])

        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        control.editor.setText('150%')
        control._on_text_edited()
        self.assertTrue(control.commit_pending())
        self.assertEqual(
            item.blk.fontformat.text_transform,
            (1.5, 0.8, 10.0, 12.0),
        )
        self.assertEqual(self.panel.global_format.text_transform, global_before)
        self.assertEqual(self.canvas.undo_stack.count(), 1)

        self.canvas.undo_stack.undo()
        self.assertEqual(
            item.blk.fontformat.text_transform,
            (1.0, 0.8, 10.0, 12.0),
        )

    def test_focus_preserved_preview_cancel_and_drag_commit_stay_local(self):
        item = make_item(horizontal=1.0)
        self.select_one(item)
        global_before = self.panel.global_format.text_transform

        self.panel.focusOnColorDialog = True
        self.canvas.selection = []
        self.panel.set_textblk_item(None)

        self.panel.on_text_transform_preview('horizontal_scale', 0.5)
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(item._effective_text_transform().horizontal_scale, 1.5)
        self.panel.on_text_transform_cancel('horizontal_scale')
        self.assertEqual(item._effective_text_transform().horizontal_scale, 1.0)
        self.assertEqual(self.canvas.undo_stack.count(), 0)

        self.panel.on_text_transform_preview('horizontal_scale', 0.5)
        self.panel.on_text_transform_drag_commit('horizontal_scale', 0.5)
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.5)
        self.assertEqual(self.panel.global_format.text_transform, global_before)
        self.assertEqual(self.canvas.undo_stack.count(), 1)

    def test_refresh_rounding_never_overwrites_precise_canonical_value(self):
        item = make_item(horizontal=1.234567)
        self.select_one(item)
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        item.transform_api_calls = 0

        for _ in range(3):
            self.panel._refresh_text_transform_controls()
            self.assertEqual(control.editor.text(), '123.5%')

        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.234567)
        self.assertEqual(C.active_format.horizontal_scale, 1.234567)
        self.assertEqual(item.transform_api_calls, 0)
        self.assertEqual(self.canvas.undo_stack.count(), 0)


class FontFormatPanelTransformSetupSafetyTest(unittest.TestCase):
    def test_failed_panel_construction_restores_global_patches(self):
        module = sys.modules[__name__]
        old_canvas = SW.canvas
        old_active_format = C.active_format
        had_register_view_widget = hasattr(app_shared, 'register_view_widget')
        old_register_view_widget = getattr(
            app_shared, 'register_view_widget', None
        )

        with mock.patch.object(
            module,
            'FontFormatPanel',
            side_effect=RuntimeError('forced panel construction failure'),
        ):
            nested = FontFormatPanelTransformIntegrationTest(
                'test_selected_numeric_commit_is_atomic_and_undoable'
            )
            result = unittest.TestResult()
            nested.run(result)

        self.assertEqual(len(result.errors), 1)
        self.assertIs(SW.canvas, old_canvas)
        self.assertIs(C.active_format, old_active_format)
        self.assertEqual(
            hasattr(app_shared, 'register_view_widget'),
            had_register_view_widget,
        )
        if had_register_view_widget:
            self.assertIs(
                app_shared.register_view_widget,
                old_register_view_widget,
            )


if __name__ == '__main__':
    unittest.main()
