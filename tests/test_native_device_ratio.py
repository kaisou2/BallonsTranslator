"""Raster proxy dispatch must not truncate a device's fractional pixel ratio."""
from __future__ import annotations

import math
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy.QtCore import QPointF, QRectF, Qt
from qtpy.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap, QTextLayout
from qtpy.QtWidgets import QApplication

from ballontranslator.ui.text_engine.rendering import native_paint


class NativeDeviceRatioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _compare(self, dpr: float, pixmap: bool, accelerated: bool) -> None:
        font = QFont('Arial', 16)
        font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        layout = QTextLayout('0' * 160, font)
        span = QTextLayout.FormatRange()
        span.start, span.length = 0, 160
        span.format.setForeground(QColor(30, 70, 190, 220))
        span.format.setTextOutline(QPen(
            QColor(240, 225, 200), 2.4, Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin,
        ))
        layout.setFormats([span])
        layout.beginLayout()
        line = layout.createLine()
        line.setLineWidth(20000)
        layout.endLayout()
        width = math.ceil((line.naturalTextWidth() + 80) * dpr)
        height = math.ceil((line.height() + 80) * dpr)
        images = []
        for optimized in (False, True):
            surface = (
                QPixmap(width, height) if pixmap else
                QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
            )
            surface.setDevicePixelRatio(dpr)
            surface.fill(QColor(20, 70, 110, 160))
            painter = QPainter(surface)
            try:
                painter.translate(40.25, 40.125)
                painter.setOpacity(0.73)
                painter.setRenderHints(
                    QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                )
                before = (painter.worldTransform(), painter.opacity(), painter.pen())
                if optimized:
                    with patch.object(
                        native_paint, '_closed_contours', wraps=native_paint._closed_contours,
                    ) as contours:
                        native_paint.draw_native_layout(layout, painter, [], QRectF())
                        if accelerated:
                            self.assertGreater(contours.call_count, 0)
                else:
                    layout.draw(painter, QPointF(), [], QRectF())
                self.assertEqual((painter.worldTransform(), painter.opacity(), painter.pen()), before)
            finally:
                painter.end()
            image = surface.toImage() if pixmap else surface
            images.append(image.convertToFormat(QImage.Format.Format_RGBA8888))
        self.assertEqual(images[0], images[1])

    def test_fractional_device_ratios_preserve_pixels(self) -> None:
        for dpr in (1.1, 1.2, 1.3, 4 / 3):
            for pixmap in (False, True):
                with self.subTest(dpr=dpr, pixmap=pixmap):
                    self._compare(dpr, pixmap, accelerated=False)

    def test_exact_fixed_point_ratios_keep_acceleration(self) -> None:
        for dpr in (1.0, 1.25, 1.5, 2.0):
            for pixmap in (False, True):
                with self.subTest(dpr=dpr, pixmap=pixmap):
                    self._compare(dpr, pixmap, accelerated=True)


if __name__ == '__main__':
    unittest.main()
