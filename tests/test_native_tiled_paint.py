"""Compare final page pixels when native text effects use real visible tiles."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy.QtCore import QCoreApplication, QEvent, QPointF, QRectF
from qtpy.QtGui import (
    QColor, QFont, QFontDatabase, QImage, QPainter,
    QRawFont, QTextLayout,
)
from qtpy.QtWidgets import QApplication, QGraphicsScene

from ballontranslator.ui.text_engine import horizontal_layout
from ballontranslator.ui.text_engine.item import TextBlkItem
from ballontranslator.ui.text_engine.rendering.native_paint import draw_native_layout
from ballontranslator.utils.text_effects import SolidPaint, StrokeEffect, TextEffectStack
from ballontranslator.utils.textblock import TextBlock


def _direct_draw(
    layout: QTextLayout, painter: QPainter,
    selections: list[QTextLayout.FormatRange], clip: QRectF,
) -> None:
    layout.draw(painter, QPointF(), selections, clip)


class NativeTiledPaintTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        try:
            families = QFontDatabase.families()
        except TypeError:
            families = QFontDatabase().families()
        if not {'malgun gothic', '맑은 고딕'}.intersection(
            family.casefold() for family in families
        ):
            raise unittest.SkipTest('Malgun Gothic is unavailable on this Qt platform.')
        raw_font = QRawFont.fromFont(QFont('Malgun Gothic', 18))
        if (
            not raw_font.isValid()
            or raw_font.familyName().casefold() not in ('malgun gothic', '맑은 고딕')
            or not all(raw_font.glyphIndexesForString('한글영어'))
        ):
            raise unittest.SkipTest('This raster regression requires real Malgun Gothic glyphs.')

    def _render_page(
        self, optimized: bool, scale: float,
        width: int = 10000, position: str = 'inside',
    ) -> QImage:
        block = TextBlock([30, 40, width + 30, 220])
        block._bounding_rect = [30, 40, width, 180]
        block.translation = '한글영어' * 40
        block.fontformat.font_family = 'Malgun Gothic'
        block.fontformat.font_size = 18
        block.fontformat.frgb = [20, 80, 130]
        block.fontformat.text_effects = TextEffectStack(effects=(
            StrokeEffect(
                width=0.36, position=position, opacity=0.5,
                paint=SolidPaint((240, 40, 70)),
            ),
        ))
        with patch.object(
            horizontal_layout, 'draw_native_layout',
            side_effect=draw_native_layout if optimized else _direct_draw,
        ) as draw_calls:
            item = TextBlkItem(block)
            scene = QGraphicsScene()
            scene.addItem(item)
            try:
                renderer = item.effect_renderer
                item.set_export_effect_render(True)
                image = QImage(
                    int(3700 * scale), int(270 * scale),
                    QImage.Format.Format_ARGB32_Premultiplied,
                )
                background = QColor(30, 90, 210)
                image.fill(background)
                painter = QPainter(image)
                try:
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                    scene.render(
                        painter,
                        QRectF(0, 0, image.width(), image.height()),
                        QRectF(0, 0, 3700, 270),
                    )
                finally:
                    painter.end()
                if width > 8192:
                    self.assertIsNone(renderer.background_pixmap)
                    self.assertGreater(len(renderer.tile_cache), 0)
                else:
                    self.assertIsNotNone(renderer.background_pixmap)
                    self.assertEqual(len(renderer.tile_cache), 0)
                self.assertGreater(draw_calls.call_count, 0)
                pixels = np.frombuffer(
                    image.constBits().asstring(image.sizeInBytes()), np.uint8,
                ).reshape(image.height(), image.width(), 4)
                # Missing fonts or empty layout must not yield a false match.
                backdrop = np.array([background.blue(), background.green(), background.red(), 255])
                self.assertGreater(np.count_nonzero(np.any(pixels != backdrop, axis=2)), 1000)
                return image
            finally:
                item.set_export_effect_render(False)
                item.geometry_controller.release_render_resources()
                scene.removeItem(item)
                item.deleteLater()
                scene.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_inside_stroke_tiles_preserve_final_page_pixels(self) -> None:
        for scale in (1.0, 1.25, 2.0):
            with self.subTest(scale=scale):
                self.assertEqual(
                    self._render_page(False, scale),
                    self._render_page(True, scale),
                )

    def test_full_surface_and_outside_stroke_stay_identical(self) -> None:
        for width, position in ((3800, 'inside'), (10000, 'outside')):
            with self.subTest(width=width, position=position):
                self.assertEqual(
                    self._render_page(False, 1.25, width, position),
                    self._render_page(True, 1.25, width, position),
                )


if __name__ == '__main__':
    unittest.main()
