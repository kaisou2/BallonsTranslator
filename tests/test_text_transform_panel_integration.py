import os
import sys
import unittest
from contextlib import nullcontext
from types import MethodType, SimpleNamespace
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy.QtCore import QEvent, QPoint, QPointF, Qt
from qtpy.QtGui import QCloseEvent, QKeySequence, QMouseEvent
from qtpy.QtTest import QTest
from qtpy.QtWidgets import (
    QApplication,
    QLineEdit,
    QMenu,
    QShortcut,
    QToolButton,
    QWidget,
)

try:
    from qtpy.QtGui import QAction
except ImportError:
    from qtpy.QtWidgets import QAction

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


def send_held_mouse_move(widget, pos):
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(pos),
        QPointF(widget.mapTo(widget.window(), pos)),
        QPointF(widget.mapToGlobal(pos)),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)


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

        old_font_families = app_shared.FONT_FAMILIES
        self.addCleanup(
            setattr,
            app_shared,
            'FONT_FAMILIES',
            old_font_families,
        )
        if old_font_families is None:
            app_shared.FONT_FAMILIES = set()

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
            QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
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

    def save_current_page(
        self,
        item,
        *,
        update_scene_text=True,
        save_proj=True,
    ):
        from ballontranslator.ui import mainwindow as mainwindow_module

        events = []

        class Project:
            img_valid = True
            current_img = 'page.png'

            @staticmethod
            def result_dir():
                return os.getcwd()

            @staticmethod
            def get_result_path(_name):
                return os.path.join(os.getcwd(), 'unused-result.png')

            @staticmethod
            def current_has_alpha():
                return True

            @staticmethod
            def save(**_kwargs):
                events.append(
                    (
                        'project-save',
                        item.blk.fontformat.horizontal_scale,
                        item._effective_text_transform().horizontal_scale,
                    )
                )

        class Manager:
            formatpanel = self.panel
            txtblkShapeControl = SimpleNamespace(isVisible=lambda: False)

            @staticmethod
            def updateTextBlkList():
                item.updateBlkFormat()
                events.append(
                    (
                        'update-blocks',
                        item.blk.fontformat.horizontal_scale,
                        item._effective_text_transform().horizontal_scale,
                    )
                )

        class Canvas:
            @staticmethod
            def render_result_img():
                events.append(
                    (
                        'render-result',
                        item.blk.fontformat.horizontal_scale,
                        item._effective_text_transform().horizontal_scale,
                    )
                )
                return object()

            @staticmethod
            def setProjSaveState(_state):
                pass

            @staticmethod
            def update_saved_undostep():
                pass

        harness = SimpleNamespace(
            imgtrans_proj=Project(),
            st_manager=Manager(),
            canvas=Canvas(),
            rightComicTransStackPanel=SimpleNamespace(isHidden=lambda: False),
            bottomBar=SimpleNamespace(),
            imsave_thread=SimpleNamespace(
                saveImg=lambda *_args, **_kwargs: None
            ),
        )
        with mock.patch.object(
            mainwindow_module.pcfg,
            'imgtrans_textblock',
            False,
        ):
            mainwindow_module.MainWindow.saveCurrentPage(
                harness,
                update_scene_text=update_scene_text,
                save_proj=save_proj,
                restore_interface=False,
                save_rst_only=True,
            )
        return events

    def make_page_shortcut_harness(self, tracked_item):
        from ballontranslator.ui import mainwindow as mainwindow_module

        events = []
        page_state = {'row': 0}
        canvas = self.canvas
        canvas.projstate_unsaved = False
        original_push = canvas.push_undo_command

        def push_undo_command(command):
            original_push(command)
            canvas.projstate_unsaved = True
            events.append(
                (
                    'push',
                    tracked_item.blk.fontformat.horizontal_scale,
                    bool(harness.page_changing),
                )
            )
            if not harness.page_changing:
                harness.global_search_widget.set_document_edited()

        def clear_undostack(update_saved_step=False):
            events.append(('clear-undo', bool(update_saved_step)))
            canvas.undo_stack.clear()

        canvas.push_undo_command = push_undo_command
        canvas.clear_undostack = clear_undostack
        canvas.text_change_unsaved = lambda: canvas.projstate_unsaved
        canvas.draw_change_unsaved = lambda: False
        def update_canvas():
            events.append(('canvas-update',))
            canvas.projstate_unsaved = False

        canvas.updateCanvas = update_canvas

        class PageIndex:
            @staticmethod
            def isValid():
                return True

            @staticmethod
            def row():
                return page_state['row']

        class PageItem:
            def __init__(self, name):
                self.name = name

            def text(self):
                return self.name

        class PageList:
            @staticmethod
            def currentIndex():
                return PageIndex()

            @staticmethod
            def count():
                return 2

            @staticmethod
            def currentItem():
                return PageItem(('page-a.png', 'page-b.png')[page_state['row']])

            @staticmethod
            def setCurrentRow(row):
                row = int(row)
                events.append(('shortcut-row', row))
                if row == page_state['row']:
                    return
                page_state['row'] = row
                mainwindow_module.MainWindow.pageListCurrentItemChanged(harness)

        class Project:
            current_img = 'page-a.png'

            @staticmethod
            def pagename2idx(name):
                return {'page-a.png': 0, 'page-b.png': 1}[name]

            def set_current_img(self, name):
                events.append(('set-current', name))
                self.current_img = name

        def save_current_page(*_args, **_kwargs):
            harness.save_calls.append(dict(_kwargs))
            events.append(
                (
                    'save',
                    tracked_item.blk.fontformat.horizontal_scale,
                    tracked_item._effective_text_transform().horizontal_scale,
                )
            )
            canvas.projstate_unsaved = False

        manager = SimpleNamespace(
            formatpanel=self.panel,
            is_editting=lambda: False,
            on_switch_textitem=lambda *_args, **_kwargs: None,
            updateSceneTextitems=lambda: events.append(
                (
                    'scene-update',
                    self.panel.textblk_item is None,
                    len(self.panel._transform_items),
                )
            ),
        )
        harness = SimpleNamespace(
            app=_APP,
            sender=lambda: shortcut,
            centralStackWidget=SimpleNamespace(currentIndex=lambda: 0),
            pageList=PageList(),
            page_changing=False,
            save_on_page_changed=True,
            opening_dir=False,
            canvas=canvas,
            st_manager=manager,
            imgtrans_proj=Project(),
            global_search_widget=SimpleNamespace(
                page_set=set(),
                set_document_edited=lambda: events.append(('document-edited',)),
            ),
            titleBar=SimpleNamespace(setTitleContent=lambda **_kwargs: None),
            module_manager=SimpleNamespace(handle_page_changed=lambda: None),
            drawingPanel=SimpleNamespace(handle_page_changed=lambda: None),
            saveCurrentPage=save_current_page,
            save_calls=[],
        )
        harness.conditional_save = MethodType(
            mainwindow_module.MainWindow.conditional_save,
            harness,
        )
        shortcut = QShortcut(
            QKeySequence(QKeySequence.StandardKey.MoveToNextPage),
            self.panel,
        )
        shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        shortcut.activated.connect(
            lambda: mainwindow_module.MainWindow.shortcutNext(harness)
        )
        harness.page_shortcut = shortcut
        self.addCleanup(shortcut.deleteLater)
        return harness, events

    def make_close_harness(self, *, project_empty=False):
        from ballontranslator.ui import mainwindow as mainwindow_module

        events = []
        canvas = self.canvas
        canvas.projstate_unsaved = False
        original_push = canvas.push_undo_command

        def push_undo_command(command):
            original_push(command)
            canvas.projstate_unsaved = True
            events.append(('push',))

        canvas.push_undo_command = push_undo_command
        canvas.text_change_unsaved = lambda: canvas.projstate_unsaved
        canvas.draw_change_unsaved = lambda: False
        canvas.prepareClose = lambda: events.append(('prepare-close',))

        window = mainwindow_module.MainWindow.__new__(mainwindow_module.MainWindow)
        QWidget.__init__(window)

        def cleanup_window():
            window.deleteLater()
            _APP.processEvents()

        self.addCleanup(cleanup_window)

        def save_current_page(*_args, **_kwargs):
            events.append(
                (
                    'save',
                    None
                    if self.panel.textblk_item is None
                    else self.panel.textblk_item.blk.fontformat.horizontal_scale,
                )
            )
            canvas.projstate_unsaved = False

        window.opening_dir = False
        window.canvas = canvas
        window.imgtrans_proj = SimpleNamespace(is_empty=project_empty)
        window.imsave_thread = SimpleNamespace(isRunning=lambda: False)
        window.st_manager = SimpleNamespace(
            formatpanel=self.panel,
            hovering_transwidget=object(),
            blockSignals=lambda blocked: events.append(
                ('block-signals', bool(blocked))
            ),
        )
        window.saveCurrentPage = save_current_page
        window.conditional_save = MethodType(
            mainwindow_module.MainWindow.conditional_save,
            window,
        )
        window.save_config = lambda: events.append(
            ('save-config', self.panel.global_format.horizontal_scale)
        )
        window.dispatch_close = lambda: self._dispatch_close_event(
            mainwindow_module,
            window,
        )
        self.panel.show()
        _APP.processEvents()
        return window, events

    def make_shortcut_host(self):
        host = QWidget()
        self.panel.setParent(host)

        def cleanup_host():
            if self.panel.parent() is host:
                self.panel.setParent(None)
            host.close()
            host.deleteLater()
            _APP.processEvents()

        self.addCleanup(cleanup_host)
        host.show()
        self.panel.show()
        _APP.processEvents()
        return host

    @staticmethod
    def _dispatch_close_event(mainwindow_module, window):
        event = QCloseEvent()
        mainwindow_module.MainWindow.closeEvent(window, event)
        return event

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

    def test_window_escape_shortcut_yields_to_held_transform_drag(self):
        from ballontranslator.ui import mainwindow as mainwindow_module

        item = make_item(horizontal=1.0)
        self.select_one(item)
        host = self.make_shortcut_host()
        shortcut_hits = []
        harness = SimpleNamespace(
            canvas=SimpleNamespace(
                search_widget=SimpleNamespace(
                    isVisible=lambda: False,
                    hide=lambda: None,
                ),
                editing_textblkitem=None,
            )
        )

        def activate_window_escape():
            shortcut_hits.append('escape')
            mainwindow_module.MainWindow.shortcutEscape(harness)

        shortcut = QShortcut(QKeySequence('Escape'), host)
        shortcut.activated.connect(activate_window_escape)
        self.addCleanup(shortcut.deleteLater)
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        center = control.label.rect().center()
        QTest.mousePress(
            control.label,
            Qt.MouseButton.LeftButton,
            pos=center,
        )
        send_held_mouse_move(control.label, center + QPoint(25, 0))
        _APP.processEvents()
        self.assertTrue(control.label.mouse_pressed)
        self.assertEqual(control.state, control.DRAG_PREVIEW)
        self.assertEqual(
            item._effective_text_transform().horizontal_scale,
            1.25,
        )

        QTest.keyClick(control.label, Qt.Key.Key_Escape)
        _APP.processEvents()

        self.assertEqual(shortcut_hits, [])
        self.assertFalse(control.label.mouse_pressed)
        self.assertEqual(control.state, control.IDLE)
        self.assertIsNone(item._text_transform_preview)
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(self.canvas.undo_stack.count(), 0)

        send_held_mouse_move(control.label, center + QPoint(40, 0))
        QTest.mouseRelease(
            control.label,
            Qt.MouseButton.LeftButton,
            pos=center + QPoint(40, 0),
        )
        _APP.processEvents()
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(self.canvas.undo_stack.count(), 0)

        QTest.keyClick(control.label, Qt.Key.Key_Escape)
        _APP.processEvents()
        self.assertEqual(shortcut_hits, ['escape'])

    def test_window_escape_shortcut_yields_to_pending_transform_text(self):
        item = make_item(horizontal=1.0)
        self.select_one(item)
        host = self.make_shortcut_host()
        shortcut_hits = []
        shortcut = QShortcut(QKeySequence('Escape'), host)
        shortcut.activated.connect(lambda: shortcut_hits.append('escape'))
        self.addCleanup(shortcut.deleteLater)
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        control.editor.setFocus()
        control.editor.setText('150%')
        control._on_text_edited()
        _APP.processEvents()
        self.assertEqual(control.state, control.PENDING_TEXT)

        QTest.keyClick(control.editor, Qt.Key.Key_Escape)
        _APP.processEvents()

        self.assertEqual(shortcut_hits, [])
        self.assertEqual(control.state, control.IDLE)
        self.assertEqual(control.editor.text(), '100.0%')
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(self.canvas.undo_stack.count(), 0)

        QTest.keyClick(control.editor, Qt.Key.Key_Escape)
        _APP.processEvents()
        self.assertEqual(shortcut_hits, ['escape'])

    def test_select_all_shortcut_aborts_held_drag_before_target_change(self):
        from ballontranslator.ui import mainwindow as mainwindow_module

        first = make_item(horizontal=1.0, idx=0)
        second = make_item(horizontal=1.0, idx=1)
        self.select_one(first)
        host = self.make_shortcut_host()

        def select_all(selected):
            self.assertTrue(selected)
            self.canvas.selection = [first, second]
            self.panel.set_textblk_item(None, multi_select=True)

        harness = SimpleNamespace(
            centralStackWidget=SimpleNamespace(currentIndex=lambda: 0),
            textPanel=SimpleNamespace(isVisible=lambda: True),
            st_manager=SimpleNamespace(set_blkitems_selection=select_all),
        )
        shortcut = QShortcut(QKeySequence.StandardKey.SelectAll, host)
        shortcut.activated.connect(
            lambda: mainwindow_module.MainWindow.shortcutSelectAll(harness)
        )
        self.addCleanup(shortcut.deleteLater)
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        center = control.label.rect().center()
        QTest.mousePress(
            control.label,
            Qt.MouseButton.LeftButton,
            pos=center,
        )
        send_held_mouse_move(control.label, center + QPoint(50, 0))
        _APP.processEvents()
        self.assertTrue(control.label.mouse_pressed)
        self.assertEqual(control.state, control.DRAG_PREVIEW)
        self.assertEqual(
            first._effective_text_transform().horizontal_scale,
            1.5,
        )

        QTest.keyClick(
            control.label,
            Qt.Key.Key_A,
            Qt.KeyboardModifier.ControlModifier,
        )
        _APP.processEvents()

        self.assertEqual(self.panel._transform_items, [first, second])
        self.assertFalse(control.label.mouse_pressed)
        self.assertEqual(control.state, control.IDLE)
        self.assertIsNone(first._text_transform_preview)
        self.assertIsNone(second._text_transform_preview)
        self.assertEqual(first.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(second.blk.fontformat.horizontal_scale, 1.0)

        send_held_mouse_move(control.label, center + QPoint(60, 0))
        QTest.mouseRelease(
            control.label,
            Qt.MouseButton.LeftButton,
            pos=center + QPoint(60, 0),
        )
        _APP.processEvents()
        self.assertEqual(first.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(second.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(self.canvas.undo_stack.count(), 0)

    def test_same_owner_refresh_clears_preview_but_preserves_press_latch(self):
        item = make_item(horizontal=1.0)
        self.select_one(item)
        self.panel.show()
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        center = control.label.rect().center()
        QTest.mousePress(
            control.label,
            Qt.MouseButton.LeftButton,
            pos=center,
        )
        send_held_mouse_move(control.label, center + QPoint(50, 0))
        _APP.processEvents()
        self.assertTrue(control.label.mouse_pressed)
        self.assertEqual(control.state, control.DRAG_PREVIEW)
        self.assertEqual(
            item._effective_text_transform().horizontal_scale,
            1.5,
        )

        self.canvas.selection = []
        self.panel.set_textblk_item(None)
        _APP.processEvents()

        self.assertIs(self.panel.textblk_item, item)
        self.assertEqual(self.panel._transform_items, [item])
        self.assertTrue(control.label.mouse_pressed)
        self.assertEqual(control.state, control.IDLE)
        self.assertIsNone(item._text_transform_preview)
        self.assertIsNone(self.panel._transform_drag_before)
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(self.canvas.undo_stack.count(), 0)

        QTest.mouseRelease(
            control.label,
            Qt.MouseButton.LeftButton,
            pos=center + QPoint(50, 0),
        )
        _APP.processEvents()
        self.assertFalse(control.label.mouse_pressed)
        self.assertIsNone(item._text_transform_preview)
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(self.canvas.undo_stack.count(), 0)

    def test_history_actions_cancel_held_drag_before_stack_move(self):
        from ballontranslator.ui import mainwindow as mainwindow_module

        item = make_item(horizontal=1.0)
        self.select_one(item)
        self.panel.on_text_transform_commit('horizontal_scale', 1.2)
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.2)
        self.assertEqual(self.canvas.undo_stack.index(), 1)
        self.assertEqual(self.canvas.undo_stack.count(), 1)

        host = self.make_shortcut_host()
        edit_button = QToolButton(host)
        edit_menu = QMenu(edit_button)
        undo_action = QAction('Undo', host)
        undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        redo_action = QAction('Redo', host)
        redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        edit_menu.addActions([undo_action, redo_action])
        edit_button.setMenu(edit_menu)
        edit_button.show()

        history_canvas = SimpleNamespace(
            undo=self.canvas.undo_stack.undo,
            redo=self.canvas.undo_stack.redo,
        )
        harness = SimpleNamespace(
            canvas=history_canvas,
            st_manager=SimpleNamespace(formatpanel=self.panel),
        )
        undo_action.triggered.connect(
            lambda _checked=False: mainwindow_module.MainWindow.on_undo(harness)
        )
        redo_action.triggered.connect(
            lambda _checked=False: mainwindow_module.MainWindow.on_redo(harness)
        )

        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        center = control.label.rect().center()

        QTest.mousePress(
            control.label,
            Qt.MouseButton.LeftButton,
            pos=center,
        )
        send_held_mouse_move(control.label, center + QPoint(30, 0))
        _APP.processEvents()
        self.assertTrue(control.label.mouse_pressed)
        self.assertEqual(control.state, control.DRAG_PREVIEW)
        self.assertEqual(
            item._effective_text_transform().horizontal_scale,
            1.5,
        )

        undo_action.trigger()
        _APP.processEvents()

        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.0)
        self.assertIsNone(item._text_transform_preview)
        self.assertFalse(control.label.mouse_pressed)
        self.assertEqual(control.state, control.IDLE)
        self.assertIsNone(self.panel._transform_drag_before)
        self.assertIsNone(self.panel._transform_drag_after)
        self.assertIsNone(self.panel._transform_drag_param)
        self.assertEqual(self.canvas.undo_stack.index(), 0)
        self.assertEqual(self.canvas.undo_stack.count(), 1)
        self.assertTrue(self.canvas.undo_stack.canRedo())

        send_held_mouse_move(control.label, center + QPoint(40, 0))
        QTest.mouseRelease(
            control.label,
            Qt.MouseButton.LeftButton,
            pos=center + QPoint(40, 0),
        )
        _APP.processEvents()
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(self.canvas.undo_stack.index(), 0)
        self.assertEqual(self.canvas.undo_stack.count(), 1)
        self.assertTrue(self.canvas.undo_stack.canRedo())

        QTest.mousePress(
            control.label,
            Qt.MouseButton.LeftButton,
            pos=center,
        )
        send_held_mouse_move(control.label, center + QPoint(50, 0))
        _APP.processEvents()
        self.assertTrue(control.label.mouse_pressed)
        self.assertEqual(control.state, control.DRAG_PREVIEW)
        self.assertEqual(
            item._effective_text_transform().horizontal_scale,
            1.5,
        )

        redo_action.trigger()
        _APP.processEvents()

        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.2)
        self.assertIsNone(item._text_transform_preview)
        self.assertFalse(control.label.mouse_pressed)
        self.assertEqual(control.state, control.IDLE)
        self.assertIsNone(self.panel._transform_drag_before)
        self.assertIsNone(self.panel._transform_drag_after)
        self.assertIsNone(self.panel._transform_drag_param)
        self.assertEqual(self.canvas.undo_stack.index(), 1)
        self.assertEqual(self.canvas.undo_stack.count(), 1)

        send_held_mouse_move(control.label, center + QPoint(60, 0))
        QTest.mouseRelease(
            control.label,
            Qt.MouseButton.LeftButton,
            pos=center + QPoint(60, 0),
        )
        _APP.processEvents()
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.2)
        self.assertEqual(self.canvas.undo_stack.index(), 1)
        self.assertEqual(self.canvas.undo_stack.count(), 1)

        undo_action.trigger()
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(self.canvas.undo_stack.index(), 0)
        self.assertEqual(self.canvas.undo_stack.count(), 1)

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

    def test_nested_transform_editor_focus_keeps_local_owner(self):
        item = make_item(horizontal=1.0)
        self.select_one(item)
        global_before = self.panel.global_format.text_transform
        self.panel.show()
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        control.editor.setFocus()
        _APP.processEvents()
        self.assertIs(_APP.focusWidget(), control.editor)

        self.canvas.selection = []
        self.panel.set_textblk_item(None)
        self.assertFalse(self.panel.global_mode())
        self.assertIs(self.panel.textblk_item, item)
        self.assertEqual(self.panel._transform_items, [item])

        control.editor.setText('150%')
        control._on_text_edited()
        self.assertTrue(control.commit_pending())
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.5)
        self.assertEqual(self.panel.global_format.text_transform, global_before)
        self.assertEqual(self.canvas.undo_stack.count(), 1)

    def test_nested_drag_label_focus_keeps_preview_and_commit_local(self):
        item = make_item(horizontal=1.0)
        self.select_one(item)
        global_before = self.panel.global_format.text_transform
        self.panel.show()
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        QTest.mousePress(control.label, Qt.MouseButton.LeftButton)
        _APP.processEvents()
        self.assertIs(_APP.focusWidget(), control.label)

        self.canvas.selection = []
        self.panel.set_textblk_item(None)
        control._move_drag(50)
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(item._effective_text_transform().horizontal_scale, 1.5)
        QTest.keyClick(control.label, Qt.Key.Key_Escape)
        _APP.processEvents()
        self.assertEqual(item._effective_text_transform().horizontal_scale, 1.0)

        QTest.mousePress(control.label, Qt.MouseButton.LeftButton)
        control._move_drag(50)
        QTest.mouseRelease(control.label, Qt.MouseButton.LeftButton)
        _APP.processEvents()
        self.assertIsNone(item._text_transform_preview)
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.5)
        self.assertEqual(self.panel.global_format.text_transform, global_before)
        self.assertEqual(self.canvas.undo_stack.count(), 1)

    def test_render_only_save_does_not_resolve_live_preview(self):
        item = make_item(horizontal=1.0)
        self.select_one(item)
        self.panel.show()
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        QTest.mousePress(control.label, Qt.MouseButton.LeftButton)
        _APP.processEvents()
        self.canvas.selection = []
        self.panel.set_textblk_item(None)
        control._move_drag(50)

        events = self.save_current_page(
            item,
            update_scene_text=False,
            save_proj=False,
        )

        self.assertEqual(control.state, control.DRAG_PREVIEW)
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(
            item._effective_text_transform().horizontal_scale,
            1.5,
        )
        self.assertEqual(self.canvas.undo_stack.count(), 0)
        self.assertEqual(events, [('render-result', 1.0, 1.5)])
        QTest.keyClick(control.label, Qt.Key.Key_Escape)
        QTest.mouseRelease(control.label, Qt.MouseButton.LeftButton)
        _APP.processEvents()
        self.assertIsNone(item._text_transform_preview)
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(
            item._effective_text_transform().horizontal_scale,
            1.0,
        )
        self.assertEqual(self.canvas.undo_stack.count(), 0)

    def test_save_commits_pending_numeric_before_snapshot_and_render(self):
        item = make_item(horizontal=1.0)
        self.select_one(item)
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        control.editor.setText('175%')
        control._on_text_edited()

        events = self.save_current_page(item)

        self.assertEqual(control.state, control.IDLE)
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.75)
        self.assertEqual(
            item._effective_text_transform().horizontal_scale,
            1.75,
        )
        self.assertEqual(self.canvas.undo_stack.count(), 1)
        self.assertEqual(
            events,
            [
                ('update-blocks', 1.75, 1.75),
                ('project-save', 1.75, 1.75),
                ('render-result', 1.75, 1.75),
            ],
        )

    def test_save_cancels_unreleased_drag_before_snapshot_and_render(self):
        item = make_item(horizontal=1.0)
        self.select_one(item)
        self.panel.show()
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        QTest.mousePress(control.label, Qt.MouseButton.LeftButton)
        _APP.processEvents()
        self.assertIs(_APP.focusWidget(), control.label)
        self.canvas.selection = []
        self.panel.set_textblk_item(None)
        send_held_mouse_move(
            control.label,
            control.label.rect().center() + QPoint(50, 0),
        )
        _APP.processEvents()
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(
            item._effective_text_transform().horizontal_scale,
            1.5,
        )

        events = self.save_current_page(item)

        self.assertEqual(control.state, control.IDLE)
        self.assertFalse(control.label.mouse_pressed)
        self.assertIsNone(item._text_transform_preview)
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(
            item._effective_text_transform().horizontal_scale,
            1.0,
        )
        self.assertEqual(self.canvas.undo_stack.count(), 0)
        self.assertEqual(
            events,
            [
                ('update-blocks', 1.0, 1.0),
                ('project-save', 1.0, 1.0),
                ('render-result', 1.0, 1.0),
            ],
        )
        send_held_mouse_move(
            control.label,
            control.label.rect().center() + QPoint(10, 0),
        )
        _APP.processEvents()
        self.assertFalse(control.label.mouse_pressed)
        self.assertEqual(control.state, control.IDLE)
        self.assertIsNone(item._text_transform_preview)
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(self.canvas.undo_stack.count(), 0)
        QTest.mouseRelease(control.label, Qt.MouseButton.LeftButton)
        _APP.processEvents()
        self.assertIsNone(item._text_transform_preview)
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(self.canvas.undo_stack.count(), 0)

    def test_save_aborts_pressed_label_after_control_refresh(self):
        item = make_item(horizontal=1.0)
        self.select_one(item)
        self.panel.show()
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        QTest.mousePress(control.label, Qt.MouseButton.LeftButton)
        _APP.processEvents()
        self.assertTrue(control.label.mouse_pressed)
        self.assertEqual(control.state, control.DRAG_PREVIEW)

        self.canvas.selection = []
        self.panel.set_textblk_item(None)
        self.assertTrue(control.label.mouse_pressed)
        self.assertEqual(control.state, control.IDLE)

        events = self.save_current_page(item)

        self.assertFalse(control.label.mouse_pressed)
        self.assertEqual(control.state, control.IDLE)
        self.assertEqual(
            events,
            [
                ('update-blocks', 1.0, 1.0),
                ('project-save', 1.0, 1.0),
                ('render-result', 1.0, 1.0),
            ],
        )
        send_held_mouse_move(
            control.label,
            control.label.rect().center() + QPoint(50, 0),
        )
        QTest.mouseRelease(control.label, Qt.MouseButton.LeftButton)
        _APP.processEvents()
        self.assertFalse(control.label.mouse_pressed)
        self.assertEqual(control.state, control.IDLE)
        self.assertIsNone(item._text_transform_preview)
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(
            item._effective_text_transform().horizontal_scale,
            1.0,
        )
        self.assertEqual(self.canvas.undo_stack.count(), 0)

    def test_close_commits_focused_local_numeric_before_dirty_check(self):
        item = make_item(horizontal=1.0)
        self.select_one(item)
        window, events = self.make_close_harness(project_empty=False)
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        control.editor.setFocus()
        control.editor.selectAll()
        QTest.keyClicks(control.editor, '150%')
        _APP.processEvents()

        self.assertIs(_APP.focusWidget(), control.editor)
        self.assertEqual(control.state, control.PENDING_TEXT)
        self.assertFalse(self.canvas.projstate_unsaved)
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.0)

        close_event = window.dispatch_close()
        _APP.processEvents()

        self.assertTrue(close_event.isAccepted())
        self.assertEqual(control.state, control.IDLE)
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.5)
        self.assertEqual(self.canvas.undo_stack.count(), 1)
        self.assertIn(('save', 1.5), events)
        event_names = [event[0] for event in events]
        self.assertLess(event_names.index('push'), event_names.index('save'))
        self.assertLess(event_names.index('save'), event_names.index('save-config'))

    def test_close_commits_focused_global_numeric_before_config_save(self):
        self.canvas.selection = []
        self.panel.set_textblk_item(None)
        window, events = self.make_close_harness(project_empty=True)
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        control.editor.setFocus()
        control.editor.selectAll()
        QTest.keyClicks(control.editor, '150%')
        _APP.processEvents()

        self.assertIs(_APP.focusWidget(), control.editor)
        self.assertEqual(control.state, control.PENDING_TEXT)
        self.assertEqual(self.panel.global_format.horizontal_scale, 1.0)

        close_event = window.dispatch_close()
        _APP.processEvents()

        self.assertTrue(close_event.isAccepted())
        self.assertEqual(control.state, control.IDLE)
        self.assertEqual(self.panel.global_format.horizontal_scale, 1.5)
        self.assertNotIn('save', [event[0] for event in events])
        self.assertIn(('save-config', 1.5), events)

    def test_close_cancels_held_transform_preview(self):
        item = make_item(horizontal=1.0)
        self.select_one(item)
        window, events = self.make_close_harness(project_empty=False)
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        center = control.label.rect().center()
        QTest.mousePress(
            control.label,
            Qt.MouseButton.LeftButton,
            pos=center,
        )
        send_held_mouse_move(control.label, center + QPoint(50, 0))
        _APP.processEvents()

        self.assertTrue(control.label.mouse_pressed)
        self.assertEqual(control.state, control.DRAG_PREVIEW)
        self.assertEqual(
            item._effective_text_transform().horizontal_scale,
            1.5,
        )

        close_event = window.dispatch_close()
        _APP.processEvents()

        self.assertTrue(close_event.isAccepted())
        self.assertFalse(control.label.mouse_pressed)
        self.assertEqual(control.state, control.IDLE)
        self.assertIsNone(item._text_transform_preview)
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(self.canvas.undo_stack.count(), 0)
        self.assertNotIn('save', [event[0] for event in events])

    def test_page_shortcut_commits_pending_transform_before_old_owner_detaches(self):
        old_item = make_item(horizontal=1.0, idx=0)
        new_item = make_item(horizontal=1.0, idx=0)
        self.select_one(old_item)
        self.panel.show()
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        harness, events = self.make_page_shortcut_harness(old_item)
        control.editor.setFocus()
        control.editor.setText('150%')
        control._on_text_edited()
        self.assertEqual(control.state, control.PENDING_TEXT)
        self.assertFalse(self.canvas.projstate_unsaved)

        harness.page_shortcut.activated.emit()
        _APP.processEvents()

        self.assertEqual(harness.imgtrans_proj.current_img, 'page-b.png')
        self.assertEqual(old_item.blk.fontformat.horizontal_scale, 1.5)
        self.assertEqual(new_item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(control.state, control.IDLE)
        self.assertIsNone(self.panel.textblk_item)
        self.assertEqual(self.panel._transform_items, [])
        self.assertEqual(self.canvas.undo_stack.count(), 0)
        self.assertFalse(self.canvas.projstate_unsaved)
        event_names = [event[0] for event in events]
        self.assertLess(event_names.index('push'), event_names.index('save'))
        self.assertLess(event_names.index('save'), event_names.index('set-current'))
        self.assertIn(('save', 1.5, 1.5), events)
        self.assertIn(('scene-update', True, 0), events)

        self.canvas.selection = [new_item]
        self.panel.set_textblk_item(new_item)
        self.assertEqual(old_item.blk.fontformat.horizontal_scale, 1.5)
        self.assertEqual(new_item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(self.canvas.undo_stack.count(), 0)

    def test_global_search_page_move_saves_pending_transform_before_switch(self):
        old_item = make_item(horizontal=1.0, idx=0)
        new_item = make_item(horizontal=1.0, idx=0)
        self.select_one(old_item)
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        harness, events = self.make_page_shortcut_harness(old_item)
        harness.global_search_widget.page_set = {'page-b.png'}
        control.editor.setFocus()
        control.editor.setText('150%')
        control._on_text_edited()
        self.assertEqual(control.state, control.PENDING_TEXT)
        self.assertFalse(self.canvas.projstate_unsaved)

        from ballontranslator.ui import mainwindow as mainwindow_module

        mainwindow_module.MainWindow.on_req_move_page(harness, 'page-b.png')
        _APP.processEvents()

        self.assertEqual(harness.imgtrans_proj.current_img, 'page-b.png')
        self.assertEqual(old_item.blk.fontformat.horizontal_scale, 1.5)
        self.assertEqual(new_item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(control.state, control.IDLE)
        self.assertIsNone(self.panel.textblk_item)
        self.assertEqual(self.panel._transform_items, [])
        self.assertEqual(self.canvas.undo_stack.count(), 0)
        self.assertFalse(self.canvas.projstate_unsaved)
        self.assertTrue(harness.save_on_page_changed)
        self.assertFalse(harness.page_changing)
        self.assertEqual(harness.save_calls, [{}])
        event_names = [event[0] for event in events]
        self.assertLess(event_names.index('push'), event_names.index('save'))
        self.assertLess(event_names.index('save'), event_names.index('set-current'))
        self.assertIn(('push', 1.5, True), events)
        self.assertIn(('save', 1.5, 1.5), events)
        self.assertNotIn('document-edited', event_names)

        self.canvas.selection = [new_item]
        self.panel.set_textblk_item(new_item)
        self.assertEqual(old_item.blk.fontformat.horizontal_scale, 1.5)
        self.assertEqual(new_item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(self.canvas.undo_stack.count(), 0)

    def test_global_search_force_save_same_page_keeps_transform_owner(self):
        item = make_item(horizontal=1.0, idx=0)
        self.select_one(item)
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        harness, events = self.make_page_shortcut_harness(item)
        control.editor.setText('150%')
        control._on_text_edited()

        from ballontranslator.ui import mainwindow as mainwindow_module

        mainwindow_module.MainWindow.on_req_move_page(
            harness,
            'page-a.png',
            force_save=True,
        )
        _APP.processEvents()

        self.assertEqual(harness.imgtrans_proj.current_img, 'page-a.png')
        self.assertEqual(item.blk.fontformat.horizontal_scale, 1.5)
        self.assertEqual(control.state, control.IDLE)
        self.assertIs(self.panel.textblk_item, item)
        self.assertEqual(self.panel._transform_items, [item])
        self.assertEqual(self.canvas.undo_stack.count(), 1)
        self.assertFalse(self.canvas.projstate_unsaved)
        self.assertTrue(harness.save_on_page_changed)
        self.assertFalse(harness.page_changing)
        self.assertEqual(harness.save_calls, [{}])
        self.assertIn(('push', 1.5, True), events)
        self.assertIn(('save', 1.5, 1.5), events)
        self.assertNotIn('set-current', [event[0] for event in events])
        self.assertNotIn('scene-update', [event[0] for event in events])
        self.assertNotIn('document-edited', [event[0] for event in events])

    def test_global_search_page_move_cancels_held_transform_preview(self):
        old_item = make_item(horizontal=1.0, idx=0)
        self.select_one(old_item)
        self.panel.show()
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        harness, events = self.make_page_shortcut_harness(old_item)
        harness.global_search_widget.page_set = {'page-b.png'}
        center = control.label.rect().center()
        QTest.mousePress(
            control.label,
            Qt.MouseButton.LeftButton,
            pos=center,
        )
        send_held_mouse_move(control.label, center + QPoint(50, 0))
        _APP.processEvents()
        self.assertTrue(control.label.mouse_pressed)
        self.assertEqual(control.state, control.DRAG_PREVIEW)

        from ballontranslator.ui import mainwindow as mainwindow_module

        mainwindow_module.MainWindow.on_req_move_page(harness, 'page-b.png')
        _APP.processEvents()

        self.assertEqual(harness.imgtrans_proj.current_img, 'page-b.png')
        self.assertFalse(control.label.mouse_pressed)
        self.assertEqual(control.state, control.IDLE)
        self.assertIsNone(old_item._text_transform_preview)
        self.assertEqual(old_item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(self.canvas.undo_stack.count(), 0)
        self.assertEqual(harness.save_calls, [])
        self.assertNotIn('save', [event[0] for event in events])
        self.assertNotIn('document-edited', [event[0] for event in events])

    def test_page_shortcut_cancels_held_transform_before_scene_replacement(self):
        old_item = make_item(horizontal=1.0, idx=0)
        new_item = make_item(horizontal=1.0, idx=0)
        self.select_one(old_item)
        self.panel.show()
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        harness, events = self.make_page_shortcut_harness(old_item)
        center = control.label.rect().center()
        QTest.mousePress(
            control.label,
            Qt.MouseButton.LeftButton,
            pos=center,
        )
        send_held_mouse_move(control.label, center + QPoint(50, 0))
        _APP.processEvents()
        self.assertTrue(control.label.mouse_pressed)
        self.assertEqual(control.state, control.DRAG_PREVIEW)
        self.assertEqual(
            old_item._effective_text_transform().horizontal_scale,
            1.5,
        )
        self.assertFalse(self.canvas.projstate_unsaved)

        QTest.keyClick(control.label, Qt.Key.Key_PageDown)
        _APP.processEvents()

        self.assertEqual(harness.imgtrans_proj.current_img, 'page-b.png')
        self.assertFalse(control.label.mouse_pressed)
        self.assertEqual(control.state, control.IDLE)
        self.assertIsNone(old_item._text_transform_preview)
        self.assertEqual(old_item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(new_item.blk.fontformat.horizontal_scale, 1.0)
        self.assertIsNone(self.panel.textblk_item)
        self.assertEqual(self.panel._transform_items, [])
        self.assertEqual(self.canvas.undo_stack.count(), 0)
        self.assertNotIn('save', [event[0] for event in events])
        self.assertIn(('scene-update', True, 0), events)

        send_held_mouse_move(control.label, center + QPoint(70, 0))
        QTest.mouseRelease(
            control.label,
            Qt.MouseButton.LeftButton,
            pos=center + QPoint(70, 0),
        )
        _APP.processEvents()
        self.assertFalse(control.label.mouse_pressed)
        self.assertEqual(control.state, control.IDLE)
        self.assertIsNone(old_item._text_transform_preview)
        self.assertEqual(old_item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(new_item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(self.canvas.undo_stack.count(), 0)

    def test_scene_change_discards_pending_transform_without_late_commit(self):
        from ballontranslator.ui.scenetext_manager import SceneTextManager

        old_item = make_item(horizontal=1.0, idx=0)
        self.select_one(old_item)
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        control.editor.setText('150%')
        control._on_text_edited()
        self.assertEqual(control.state, control.PENDING_TEXT)

        manager = SimpleNamespace(
            formatpanel=self.panel,
            text_overlay_manager=SimpleNamespace(
                batch_update=nullcontext,
                clear=lambda: None,
            ),
            hovering_transwidget=object(),
            txtblkShapeControl=SimpleNamespace(setBlkItem=lambda _item: None),
            textblk_item_list=[old_item],
            canvas=SimpleNamespace(removeItem=lambda _item: None),
            textEditList=SimpleNamespace(
                clearAllSelected=lambda: None,
                removeWidget=lambda _widget: None,
            ),
            pairwidget_list=[],
        )
        SceneTextManager.clearSceneTextitems(manager)

        self.assertEqual(control.state, control.IDLE)
        self.assertEqual(old_item.blk.fontformat.horizontal_scale, 1.0)
        self.assertEqual(self.canvas.undo_stack.count(), 0)
        self.assertIsNone(self.panel.textblk_item)
        self.assertEqual(self.panel._transform_items, [])
        self.assertEqual(manager.textblk_item_list, [])

    def test_panel_external_focus_enters_global_mode(self):
        item = make_item(horizontal=1.0)
        self.select_one(item)
        outside = QLineEdit()
        self.addCleanup(outside.deleteLater)
        outside.show()
        outside.setFocus()
        _APP.processEvents()
        self.assertIs(_APP.focusWidget(), outside)

        self.canvas.selection = []
        self.panel.set_textblk_item(None)
        self.assertTrue(self.panel.global_mode())
        self.assertIsNone(self.panel.textblk_item)
        self.assertEqual(self.panel._transform_items, [])

    def test_descendant_focus_does_not_override_multi_selection(self):
        first = make_item(horizontal=1.0, idx=0)
        second = make_item(horizontal=0.8, idx=1)
        self.select_one(first)
        global_before = self.panel.global_format.text_transform
        self.panel.show()
        control = self.panel.textadvancedfmt_panel.horizontal_scale_control
        control.editor.setFocus()
        _APP.processEvents()
        self.assertIs(_APP.focusWidget(), control.editor)

        self.canvas.selection = [first, second]
        self.panel.set_textblk_item(None, multi_select=True)
        self.assertEqual(self.panel._transform_items, [first, second])
        self.assertTrue(self.panel.global_mode())
        self.assertIsNone(self.panel.textblk_item)
        self.panel.on_text_transform_commit('horizontal_scale', 1.5)
        self.assertEqual(first.blk.fontformat.horizontal_scale, 1.5)
        self.assertEqual(second.blk.fontformat.horizontal_scale, 1.5)
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
