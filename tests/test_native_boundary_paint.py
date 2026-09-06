"""Raster boundaries preserve native fill and visible stroke coverage."""
from __future__ import annotations

import os
import unittest
from itertools import product
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy.QtCore import QRectF, Qt
from qtpy.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen
from qtpy.QtWidgets import QApplication

from ballontranslator.ui.text_engine.rendering.native_paint import _draw_path_in_strips


class NativeBoundaryPaintTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _curves(top: float) -> QPainterPath:
        path = QPainterPath()
        for index in range(100):
            path.addEllipse(QRectF(
                index * 22.137 + 0.415, top, 18.362, 25.725,
            ))
        return path

    def test_fill_clipping_matches_native_at_both_horizontal_edges(self) -> None:
        path = self._curves(8.364)
        pens = (
            ('none', QPen(Qt.PenStyle.NoPen)),
            ('transparent', QPen(QColor(0, 0, 0, 0), 0.0, Qt.PenStyle.SolidLine)),
            ('no-brush', QPen(QBrush(Qt.BrushStyle.NoBrush), 3.0)),
        )
        for name, pen in pens:
            for dpr in (1.0, 1.25):
                for offset in (-30.375, 0.125):
                    for reflected in (False, True):
                        with self.subTest(pen=name, dpr=dpr, offset=offset, reflected=reflected):
                            images = []
                            for optimized in (False, True):
                                image = QImage(1400, 100, QImage.Format.Format_ARGB32_Premultiplied)
                                image.setDevicePixelRatio(dpr)
                                image.fill(QColor(20, 60, 100, 170))
                                painter = QPainter(image)
                                try:
                                    origin = image.width() - offset if reflected else offset
                                    painter.translate(origin / dpr, 0)
                                    painter.scale((-1.25 if reflected else 1.25) / dpr, 1.25 / dpr)
                                    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                                    painter.setPen(pen)
                                    painter.setBrush(QColor(30, 80, 130, 190))
                                    painter.setOpacity(0.73)
                                    if optimized:
                                        _draw_path_in_strips(painter, path)
                                    else:
                                        painter.drawPath(path)
                                finally:
                                    painter.end()
                                images.append(image)
                            self.assertEqual(*images)

    def test_vertical_clip_includes_visible_stroke_overhang(self) -> None:
        path = self._curves(0.125)
        configurations = (
            (1.25, 1.25, False, Qt.PenJoinStyle.RoundJoin),
            (2.0, 0.75, False, Qt.PenJoinStyle.RoundJoin),
            (2.0, 0.75, True, Qt.PenJoinStyle.RoundJoin),
            (1.25, 1.25, False, Qt.PenJoinStyle.MiterJoin),
        )
        for dpr, reflected, config in product((1.0, 1.25), (False, True), configurations):
            scale_x, scale_y, cosmetic, join = config
            with self.subTest(dpr=dpr, reflected=reflected, config=config):
                images = []
                for optimized in (False, True):
                    image = QImage(1400, 100, QImage.Format.Format_ARGB32_Premultiplied)
                    image.setDevicePixelRatio(dpr)
                    image.fill(QColor(20, 60, 100, 170))
                    painter = QPainter(image)
                    try:
                        if reflected:
                            painter.translate(0, image.height() / dpr)
                        painter.scale(scale_x / dpr, (-scale_y if reflected else scale_y) / dpr)
                        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                        pen = QPen(
                            QColor(240, 240, 240), 2.5, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, join,
                        )
                        pen.setCosmetic(cosmetic)
                        painter.setPen(pen)
                        painter.setBrush(QColor(0, 0, 0, 1))
                        if optimized:
                            _draw_path_in_strips(painter, path)
                        else:
                            painter.drawPath(path)
                    finally:
                        painter.end()
                    images.append(image)
                self.assertEqual(*images)

    def test_supported_strokes_and_invisible_pen_width_keep_partitioning(self) -> None:
        path = self._curves(12.0)
        cases = (
            ('horizontally clipped stroke', 1400, QPen(QColor('white'), 3.0)),
            ('transparent wide pen', 2600, QPen(QColor(0, 0, 0, 0), 80.0)),
            ('wide pen without brush', 2600, QPen(QBrush(Qt.BrushStyle.NoBrush), 80.0)),
        )
        for name, width, pen in cases:
            with self.subTest(name=name):
                image = QImage(width, 80, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(Qt.GlobalColor.transparent)
                painter = QPainter(image)
                painter.setPen(pen)
                painter.setBrush(QColor('black'))
                original = painter.drawPath
                sizes = []

                def record(part: QPainterPath) -> None:
                    sizes.append(part.elementCount())
                    original(part)

                try:
                    with patch.object(painter, 'drawPath', side_effect=record):
                        _draw_path_in_strips(painter, path)
                finally:
                    painter.end()
                self.assertGreater(len(sizes), 1)
                self.assertLess(max(sizes), path.elementCount())


if __name__ == '__main__':
    unittest.main()
