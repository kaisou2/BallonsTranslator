"""Contour reuse must preserve exact geometry and history-independent pixels."""
from __future__ import annotations

import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy.QtCore import QRectF, Qt
from qtpy.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from qtpy.QtWidgets import QApplication

from ballontranslator.ui.text_engine.rendering.native_paint import (
    _CONTOUR_CACHE, _closed_contours, _draw_path_in_strips,
)


class NativeContourPrecisionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        _CONTOUR_CACHE.clear()

    def tearDown(self) -> None:
        _CONTOUR_CACHE.clear()

    @staticmethod
    def _nearby_paths() -> tuple[QPainterPath, QPainterPath]:
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        path.addRect(QRectF(0, 0, 1800, 80))
        for index in range(160):
            path.addRect(QRectF(10 + index * 11, 10.0078125, 5, 40))
        changed = QPainterPath(path)
        # Keep the outer bounds and element count identical. This minute change
        # crosses a raster rounding boundary, although Qt may compare it equal.
        for index in (5, 6, 9):
            element = changed.elementAt(index)
            changed.setElementPositionAt(index, element.x, element.y - 1e-11)
        return path, changed

    @staticmethod
    def _render(path: QPainterPath, optimized: bool) -> QImage:
        image = QImage(1900, 120, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor('white'))
        painter = QPainter(image)
        try:
            painter.translate(20, 20)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(Qt.PenStyle.NoPen))
            painter.setBrush(QColor('black'))
            if optimized:
                _draw_path_in_strips(painter, path)
            else:
                painter.drawPath(path)
        finally:
            painter.end()
        return image

    def test_nearby_vertices_do_not_reuse_previous_contours(self) -> None:
        previous, changed = self._nearby_paths()
        self.assertEqual(previous.elementCount(), changed.elementCount())
        self.assertEqual(previous.controlPointRect(), changed.controlPointRect())
        _closed_contours(previous)
        contours = _closed_contours(changed)
        self.assertEqual(contours[1].elementAt(0).y, changed.elementAt(5).y)
        self.assertIs(_closed_contours(QPainterPath(changed)), contours)

    def test_render_does_not_depend_on_cache_history(self) -> None:
        previous, changed = self._nearby_paths()
        expected = self._render(changed, False)
        cold = self._render(changed, True)
        _CONTOUR_CACHE.clear()
        self._render(previous, True)
        warm = self._render(changed, True)
        self.assertEqual(cold, expected)
        self.assertEqual(warm, expected)


if __name__ == '__main__':
    unittest.main()
