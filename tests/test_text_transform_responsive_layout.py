import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy.QtCore import QRect, QSize, Qt
from qtpy.QtGui import QFont
from qtpy.QtWidgets import (
    QApplication,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ballontranslator.ui.text_advanced_format import (
    AdaptiveWrapLayout,
    TextAdvancedFormatPanel,
    _pack_preferred_widths,
)
from ballontranslator.utils import shared as app_shared


_APP = QApplication.instance() or QApplication([])


class HeightForWidthUnit(QWidget):
    def __init__(self, preferred_width=100, minimum_width=40, parent=None):
        super().__init__(parent)
        self._preferred_width = preferred_width
        self.setMinimumSize(minimum_width, 18)

    def sizeHint(self):
        return QSize(self._preferred_width, 24)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return 24 if width >= self._preferred_width else 48


class AdaptiveWrapLayoutTest(unittest.TestCase):
    def test_equivalent_dpr_scaled_metrics_keep_the_same_rows(self):
        for scale in (1.0, 1.5, 2.0):
            with self.subTest(scale=scale):
                widths = [round(value * scale) for value in (80, 60, 70)]
                available = round(150 * scale)
                spacing = round(5 * scale)
                self.assertEqual(
                    _pack_preferred_widths(widths, available, spacing),
                    [(0, 1), (2,)],
                )

    def test_greedy_rows_hidden_items_hfw_and_take_at_contract(self):
        host = QWidget()
        layout = AdaptiveWrapLayout(
            host, horizontal_spacing=5, vertical_spacing=7
        )
        layout.setContentsMargins(2, 3, 2, 3)
        units = [HeightForWidthUnit(parent=host) for _ in range(4)]
        for unit in units:
            layout.addWidget(unit)

        layout.setGeometry(QRect(0, 0, 424, 100))
        self.assertEqual({unit.y() for unit in units}, {3})
        self.assertEqual([unit.x() for unit in units], [2, 107, 212, 317])

        narrow_height = layout.heightForWidth(214)
        layout.setGeometry(QRect(0, 0, 214, narrow_height))
        self.assertEqual([unit.y() for unit in units], [3, 3, 34, 34])
        self.assertEqual(narrow_height, 61)

        units[1].hide()
        hidden_height = layout.heightForWidth(214)
        layout.setGeometry(QRect(0, 0, 214, hidden_height))
        self.assertEqual(units[2].y(), 3)
        self.assertEqual(units[3].y(), 34)

        item = layout.itemAt(0)
        self.assertIs(layout.takeAt(0), item)
        self.assertEqual(layout.count(), 3)
        host.close()

    def test_overwide_item_gets_available_width_and_minimum_is_not_sum(self):
        host = QWidget()
        layout = AdaptiveWrapLayout(
            host, horizontal_spacing=5, vertical_spacing=5
        )
        layout.setContentsMargins(4, 0, 4, 0)
        wide = HeightForWidthUnit(300, 50, host)
        normal = HeightForWidthUnit(80, 40, host)
        layout.addWidget(wide)
        layout.addWidget(normal)

        height = layout.heightForWidth(128)
        layout.setGeometry(QRect(0, 0, 128, height))
        self.assertEqual(wide.geometry().width(), 120)
        self.assertGreater(normal.y(), wide.y())
        self.assertEqual(layout.minimumSize().width(), 58)
        host.close()


class TextAdvancedFormatResponsiveLayoutTest(unittest.TestCase):
    def setUp(self):
        self.old_register_view_widget = getattr(
            app_shared, 'register_view_widget', None
        )
        app_shared.register_view_widget = lambda *_args, **_kwargs: None
        self.hosts = []

    def tearDown(self):
        for host in self.hosts:
            host.close()
        if self.old_register_view_widget is None:
            del app_shared.register_view_widget
        else:
            app_shared.register_view_widget = self.old_register_view_widget
        _APP.processEvents()

    def make_panel(self, width=1200, height=800):
        panel = TextAdvancedFormatPanel(
            'Advanced Text Format',
            config_name='responsive_text_transform_test_panel',
            config_expand_name='responsive_text_transform_test_expand',
            on_format_changed=lambda *_args: None,
        )
        panel.set_expend_area(True, set_config=False)
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(panel.view_widget)
        host.resize(width, height)
        host.show()
        host.activateWindow()
        self.hosts.append(host)
        self.settle(panel)
        return host, panel

    @staticmethod
    def settle(panel):
        for _ in range(6):
            _APP.processEvents()
            if panel._geometry_timer.isActive():
                continue

    @staticmethod
    def assert_no_overlap(testcase, units):
        visible = [unit for unit in units if unit.isVisible()]
        for index, first in enumerate(visible):
            for second in visible[index + 1:]:
                intersection = first.geometry().intersected(second.geometry())
                testcase.assertTrue(
                    intersection.isEmpty(),
                    f'{first} overlaps {second}: {intersection}',
                )

    def test_transform_units_wrap_and_return_without_recreation(self):
        host, panel = self.make_panel()
        controls = tuple(panel.transform_controls.values())
        identities = tuple(map(id, controls))

        self.assertEqual(len({control.y() for control in controls}), 1)
        self.assertEqual(
            [control.x() for control in controls],
            sorted(control.x() for control in controls),
        )

        host.resize(360, 800)
        self.settle(panel)
        self.assertGreater(len({control.y() for control in controls}), 1)
        self.assert_no_overlap(self, controls)
        for control in controls:
            self.assertIs(control.label.parentWidget(), control)
            self.assertIs(control.editor.parentWidget(), control)
            self.assertGreaterEqual(control.editor.width(), 64)

        host.resize(1200, 800)
        self.settle(panel)
        self.assertEqual(tuple(map(id, controls)), identities)
        self.assertEqual(len({control.y() for control in controls}), 1)
        self.assertEqual(
            [control.x() for control in controls],
            sorted(control.x() for control in controls),
        )

    def test_every_section_uses_atomic_units_without_horizontal_overflow(self):
        host, panel = self.make_panel(width=360, height=260)
        section_units = (
            (panel.top_section, panel.top_atomic_units),
            (panel.transform_section, tuple(panel.transform_controls.values())),
            (panel.shadow_group, panel.shadow_group.atomic_units),
            (panel.gradient_group, panel.gradient_group.atomic_units),
        )
        for parent, units in section_units:
            with self.subTest(parent=parent):
                self.assert_no_overlap(self, units)
                for unit in units:
                    self.assertGreaterEqual(unit.x(), 0)
                    self.assertLessEqual(unit.geometry().right(), parent.rect().right())

        self.assertEqual(
            panel.horizontalScrollBarPolicy(),
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        self.assertEqual(
            panel.verticalScrollBarPolicy(),
            Qt.ScrollBarPolicy.ScrollBarAsNeeded,
        )
        self.assertLessEqual(
            panel.scrollContent.width(), panel.viewport().width()
        )
        self.assertGreater(panel.verticalScrollBar().maximum(), 0)

        host.resize(1200, 800)
        self.settle(panel)
        self.assertEqual(panel.verticalScrollBar().maximum(), 0)

    def test_panel_size_hints_and_policies_follow_current_width(self):
        _host, panel = self.make_panel(width=900, height=700)
        content_height = panel._content_preferred_height(
            panel._effective_content_width()
        )
        expected_height = min(
            content_height + 2 * panel.frameWidth(),
            panel._panel_height_cap(),
        )
        self.assertEqual(panel.sizeHint().height(), expected_height)
        self.assertEqual(panel.maximumHeight(), panel._panel_height_cap())
        self.assertEqual(
            panel.minimumSizeHint().height(),
            min(
                expected_height,
                max(96, 6 * panel.fontMetrics().lineSpacing()),
            ),
        )
        self.assertEqual(
            panel.sizePolicy().horizontalPolicy(),
            QSizePolicy.Policy.Expanding,
        )
        self.assertEqual(
            panel.sizePolicy().verticalPolicy(), QSizePolicy.Policy.Preferred
        )
        self.assertEqual(
            panel.scrollContent.sizePolicy().verticalPolicy(),
            QSizePolicy.Policy.Preferred,
        )

    def test_long_labels_and_double_font_wrap_without_clipping_editor(self):
        old_font = QFont(_APP.font())
        font = QFont(old_font)
        point_size = font.pointSizeF()
        font.setPointSizeF(max(1.0, point_size * 2.0))
        _APP.setFont(font)
        try:
            host, panel = self.make_panel(width=340, height=400)
            control = panel.horizontal_scale_control
            control.label.setText(
                'Extremely long localized horizontal scale label that must wrap'
            )
            host.resize(320, 400)
            self.settle(panel)

            self.assertTrue(control.label.wordWrap())
            self.assertGreaterEqual(control.editor.width(), 64)
            self.assertLessEqual(
                control.geometry().right(),
                panel.transform_section.rect().right(),
            )
            self.assertLessEqual(
                panel.scrollContent.width(), panel.viewport().width()
            )
            self.assertLessEqual(
                panel.maximumHeight(), panel._panel_height_cap()
            )
        finally:
            _APP.setFont(old_font)

    def test_resize_preserves_pending_focus_cursor_drag_and_signal_count(self):
        host, panel = self.make_panel(width=900, height=500)
        control = panel.horizontal_scale_control
        commits = []
        panel.transform_commit_requested.connect(lambda *args: commits.append(args))

        control.editor.setFocus()
        control.editor.setText('137.50%')
        control.editor.setCursorPosition(3)
        control._on_text_edited()
        host.resize(350, 500)
        self.settle(panel)
        host.resize(900, 500)
        self.settle(panel)

        self.assertEqual(control.state, control.PENDING_TEXT)
        self.assertEqual(control.editor.text(), '137.50%')
        self.assertEqual(control.editor.cursorPosition(), 3)
        self.assertTrue(control.editor.hasFocus())
        self.assertEqual(commits, [])

        self.assertTrue(control.commit_pending())
        self.assertEqual(commits, [('horizontal_scale', 1.375)])
        drag = panel.glyph_slant_angle_control
        drag._start_drag()
        drag._move_drag(3)
        drag_state = (drag.state, drag._drag_delta, drag.editor.text())
        host.resize(340, 500)
        self.settle(panel)
        self.assertEqual(
            (drag.state, drag._drag_delta, drag.editor.text()), drag_state
        )
        drag.cancel_preview()

    def test_width_only_relayout_preserves_vertical_scroll_position(self):
        host, panel = self.make_panel(width=330, height=220)
        scrollbar = panel.verticalScrollBar()
        self.assertGreater(scrollbar.maximum(), 0)
        requested = max(1, scrollbar.maximum() // 2)
        scrollbar.setValue(requested)

        host.resize(300, 220)
        self.settle(panel)
        self.assertEqual(
            scrollbar.value(), min(requested, scrollbar.maximum())
        )


if __name__ == '__main__':
    unittest.main()
