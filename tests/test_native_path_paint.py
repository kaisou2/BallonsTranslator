"""Native path batching must preserve pixels, shaping, and painter state."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy.QtCore import QPointF, QRectF, Qt
from qtpy.QtGui import (
    QColor, QFont, QImage, QPainter, QPainterPath, QPen,
    QTextCharFormat, QTextCursor, QTextDocument, QTextLayout, QTransform,
)
from qtpy.QtWidgets import QApplication

from ballontranslator.ui.text_engine.rendering.native_paint import (
    _closed_contours, _draw_path_in_strips, draw_native_layout, _CONTOUR_CACHE,
)
from ballontranslator.ui.text_engine import horizontal_layout
from ballontranslator.ui.text_engine.item import TextBlkItem
from ballontranslator.utils.textblock import TextBlock


class NativePathPaintTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _rings(rule: Qt.FillRule) -> QPainterPath:
        path = QPainterPath()
        path.setFillRule(rule)
        for index in range(240):
            left = index * 9.3 - 47.25
            path.addEllipse(QRectF(left, 8, 12.8, 22.5))
            hole = QPainterPath()
            hole.addEllipse(QRectF(left + 3.1, 11.1, 6.6, 16.3))
            path.addPath(hole.toReversed())
        return path

    @staticmethod
    def _image(dpr: float = 1.0) -> QImage:
        image = QImage(2400, 180, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(QColor(25, 35, 55, 90))
        return image

    def test_overlapping_curves_and_holes_match_at_fractional_device_scales(self) -> None:
        for dpr in (0.5, 1.0, 1.25, 2.0):
            for rule in (Qt.FillRule.WindingFill, Qt.FillRule.OddEvenFill):
                with self.subTest(dpr=dpr, rule=rule):
                    path = self._rings(rule)
                    outputs = []
                    for batched in (False, True):
                        image = self._image(dpr)
                        painter = QPainter(image)
                        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                        painter.translate(0.375, 4.125)
                        painter.setPen(QPen(QColor(250, 210, 180, 180), 3.7,
                                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                            Qt.PenJoinStyle.RoundJoin))
                        painter.setBrush(QColor(60, 100, 240, 110))
                        if batched:
                            _draw_path_in_strips(painter, path)
                        else:
                            painter.drawPath(path)
                        painter.end()
                        outputs.append(image)
                    if outputs[0] != outputs[1]:
                        pixels = [
                            np.frombuffer(image.constBits().asstring(image.sizeInBytes()),
                                          dtype=np.uint8).reshape(image.height(), image.width(), 4)
                            for image in outputs
                        ]
                        delta = np.abs(pixels[0].astype(np.int16) - pixels[1])
                        # Qt may round a clipped curve one level differently
                        # at the implicit outer device edge. Strip seams and
                        # every interior pixel must still be identical.
                        self.assertFalse(np.any(delta[:, 1:-1]))
                        self.assertLessEqual(int(delta.max()), 1)

    def test_open_and_sheared_paths_keep_the_native_fallback(self) -> None:
        path = self._rings(Qt.FillRule.WindingFill)
        path.moveTo(0, 40)
        path.lineTo(2000, 50)
        self.assertIsNone(_closed_contours(path))
        for path, transform in (
            (path, QTransform()),
            (self._rings(Qt.FillRule.WindingFill), QTransform().shear(0.15, 0.1)),
        ):
            with self.subTest(transform=transform):
                outputs = []
                for batched in (False, True):
                    image = self._image()
                    painter = QPainter(image)
                    painter.setWorldTransform(transform)
                    painter.setPen(QPen(Qt.GlobalColor.white, 5))
                    if batched:
                        _draw_path_in_strips(painter, path)
                    else:
                        painter.drawPath(path)
                    painter.end()
                    outputs.append(image)
                self.assertEqual(*outputs)

    def test_native_layout_keeps_rich_shaping_selection_clips_and_painter_state(self) -> None:
        document = QTextDocument()
        document.setDefaultFont(QFont('Arial', 20))
        document.setPlainText(('AV office fi 한글 العربية 😀 ' * 16).strip())
        document.setTextWidth(3500)
        cursor = QTextCursor(document)
        cursor.select(QTextCursor.SelectionType.Document)
        format = QTextCharFormat()
        format.setForeground(QColor(30, 60, 90, 180))
        format.setTextOutline(QPen(Qt.GlobalColor.white, 2.7,
                                  Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                  Qt.PenJoinStyle.RoundJoin))
        format.setFontUnderline(True)
        cursor.mergeCharFormat(format)
        document.documentLayout().documentSize()
        layout = document.firstBlock().layout()
        selection = QTextLayout.FormatRange()
        selection.start, selection.length = 14, 250
        selection.format.setForeground(QColor(90, 220, 150, 230))
        selection.format.setBackground(QColor(220, 100, 130, 70))
        before = (document.toHtml(), document.revision(), document.availableUndoSteps())
        for dpr in (0.5, 1.0, 1.25, 2.0):
            with self.subTest(dpr=dpr):
                outputs = []
                for batched in (False, True):
                    image = self._image(dpr)
                    painter = QPainter(image)
                    painter.translate(0.25, 1.5)
                    painter.setOpacity(0.85)
                    painter.setClipRect(QRectF(11.5, 0, 1730.5, 100))
                    painter.setPen(QPen(QColor('magenta'), 6))
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                    state = (painter.worldTransform(), painter.pen(), painter.opacity(),
                             painter.clipPath(), painter.renderHints(), painter.compositionMode())
                    if batched:
                        draw_native_layout(layout, painter, [selection], QRectF(0, 0, 2200, 100))
                    else:
                        layout.draw(painter, QPointF(), [selection], QRectF(0, 0, 2200, 100))
                    self.assertEqual(
                        (painter.worldTransform(), painter.pen(), painter.opacity(),
                         painter.clipPath(), painter.renderHints(), painter.compositionMode()), state)
                    painter.end()
                    outputs.append(image)
                self.assertEqual(outputs[0], outputs[1])
        self.assertEqual((document.toHtml(), document.revision(), document.availableUndoSteps()), before)

    def test_complex_path_is_partitioned_without_growing_a_full_run_per_strip(self) -> None:
        path = self._rings(Qt.FillRule.WindingFill)
        image = self._image()
        painter = QPainter(image)
        painter.setPen(QPen(Qt.GlobalColor.white, 2.6,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        original = painter.drawPath
        sizes = []

        def paint(part: QPainterPath) -> None:
            sizes.append(part.elementCount())
            original(part)

        try:
            with patch.object(painter, 'drawPath', paint):
                _draw_path_in_strips(painter, path)
        finally:
            painter.end()
        self.assertGreater(len(sizes), 1)
        self.assertLess(max(sizes), path.elementCount() // 3)
        self.assertLess(sum(sizes), path.elementCount() * 2)

    def test_repaint_reuses_contours_but_equal_bounds_do_not_alias_different_ink(self) -> None:
        _CONTOUR_CACHE.clear()
        path = self._rings(Qt.FillRule.WindingFill)
        first = _closed_contours(path)
        with patch.object(QPainterPath, 'elementAt', side_effect=AssertionError('reparsed')):
            self.assertIs(_closed_contours(QPainterPath(path)), first)
        changed = QPainterPath(path)
        control = changed.elementAt(2)
        changed.setElementPositionAt(2, control.x, control.y - 0.125)
        self.assertEqual(changed.controlPointRect(), path.controlPointRect())
        self.assertEqual(changed.elementCount(), path.elementCount())
        second = _closed_contours(changed)
        self.assertIsNot(first, second)
        self.assertNotEqual(first[0], second[0])

    def test_contour_retention_is_bounded_for_pages_and_oversized_paths(self) -> None:
        _CONTOUR_CACHE.clear()
        for shift in range(12):
            path = self._rings(Qt.FillRule.WindingFill)
            path.translate(shift, 0)
            _closed_contours(path)
        self.assertLessEqual(len(_CONTOUR_CACHE), 8)
        keys = tuple(_CONTOUR_CACHE)
        oversized = QPainterPath()
        for _ in range(7):
            oversized.addPath(path)
        self.assertGreater(oversized.elementCount(), 32768)
        _closed_contours(oversized)
        self.assertEqual(tuple(_CONTOUR_CACHE), keys)

    def test_layout_failure_restores_the_caller_painter(self) -> None:
        image = self._image()
        painter = QPainter(image)
        painter.translate(3, 4)
        painter.setOpacity(0.7)
        painter.setPen(QPen(Qt.GlobalColor.green, 3))
        state = (painter.worldTransform(), painter.opacity(), painter.pen())
        layout = QTextLayout('text')
        try:
            with patch.object(QTextLayout, 'draw', side_effect=RuntimeError('paint failure')):
                with self.assertRaisesRegex(RuntimeError, 'paint failure'):
                    draw_native_layout(layout, painter, [], QRectF())
            self.assertTrue(painter.isActive())
            self.assertEqual((painter.worldTransform(), painter.opacity(), painter.pen()), state)
        finally:
            painter.end()

    def test_long_item_stroke_edit_undo_and_export_match_the_native_path(self) -> None:
        def native_draw(layout, painter, selections, clip) -> None:
            layout.draw(painter, QPointF(), selections, clip)

        outputs = []
        for paint in (native_draw, draw_native_layout):
            # Offscreen Qt can substitute a wider font than the desktop backend.
            # Keep a genuinely long shaped line under both font implementations.
            block = TextBlock([0, 0, 8000, 160])
            block._bounding_rect = [0, 0, 8000, 160]
            block.translation = '10' * 128
            block.fontformat.font_family = 'Arial'
            block.fontformat.font_size = 24
            block.fontformat.stroke_width = 0.2
            with patch.object(horizontal_layout, 'draw_native_layout', side_effect=paint) as calls:
                item = TextBlkItem(block)
                renderer = item.effect_renderer
                initial = renderer.background_pixmap.toImage()
                cursor = QTextCursor(item.document())
                cursor.movePosition(QTextCursor.MoveOperation.End)
                cursor.insertText('9')
                edited = renderer.background_pixmap.toImage()
                item.document().undo()
                restored = renderer.background_pixmap.toImage()
                self.assertEqual(restored, initial)
                item.set_export_effect_render(True)
                item.repaint_background(2.0)
                exported = renderer.background_pixmap.toImage()
                item.set_export_effect_render(False)
                self.assertGreater(calls.call_count, 0)
                outputs.append((initial, edited, restored, exported))
                item.geometry_controller.release_render_resources()
        self.assertEqual(outputs[0], outputs[1])


if __name__ == '__main__':
    unittest.main()
