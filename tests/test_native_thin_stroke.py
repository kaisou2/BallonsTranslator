"""Thin native outlines retain Qt's coverage through item edits and effects."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy.QtCore import QPointF, QRectF
from qtpy.QtGui import (
    QImage, QPainter, QTextCursor, QTextLayout,
)
from qtpy.QtWidgets import QApplication

from ballontranslator.ui.text_engine import horizontal_layout
from ballontranslator.ui.text_engine.item import TextBlkItem
from ballontranslator.ui.text_engine.rendering.native_paint import draw_native_layout
from ballontranslator.utils.text_effects import (
    SolidPaint, StrokeEffect, TextEffectStack,
)
from ballontranslator.utils.textblock import TextBlock


def _direct_draw(
    layout: QTextLayout, painter: QPainter,
    selections: list[QTextLayout.FormatRange], clip: QRectF,
) -> None:
    layout.draw(painter, QPointF(), selections, clip)


class NativeThinStrokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _item_images(
        self, family: str, size: float, width: float,
        optimized: bool, mixed: bool = False,
    ) -> list[QImage]:
        block = TextBlock([0, 0, 8000, 200])
        block._bounding_rect = [0, 0, 8000, 200]
        block.translation = 'AVfj0' * 36
        block.fontformat.font_family = family
        block.fontformat.font_size = size
        block.fontformat.italic = True
        block.fontformat.frgb = [40, 70, 200]
        block.fontformat.text_effects = TextEffectStack(effects=(
            StrokeEffect(
                width=width, opacity=0.8,
                paint=SolidPaint(color=(240, 40, 60)),
            ),
        ))
        if mixed:
            block.rich_text = (
                '<p><span style="font-family:Times New Roman;'
                'font-size:27pt;font-style:italic;">' + 'AVfj0' * 18
                + '</span><span style="font-family:Malgun Gothic;'
                'font-size:9pt;">' + 'AVfj0' * 18 + '</span></p>'
            )
        with patch.object(
            horizontal_layout, 'draw_native_layout',
            side_effect=draw_native_layout if optimized else _direct_draw,
        ) as draw_calls:
            item = TextBlkItem(block)
            try:
                renderer = item.effect_renderer
                initial = renderer.background_pixmap.toImage()
                cursor = QTextCursor(item.document())
                cursor.movePosition(QTextCursor.MoveOperation.End)
                cursor.insertText('9')
                edited = renderer.background_pixmap.toImage()
                item.document().undo()
                restored = renderer.background_pixmap.toImage()
                self.assertEqual(initial, restored)
                self.assertGreater(draw_calls.call_count, 0)
                return [initial, edited, restored]
            finally:
                item.geometry_controller.release_render_resources()

    def test_thin_and_ordinary_stroke_effects_match_native_after_edit_and_undo(self) -> None:
        for family in ('Times New Roman', 'Malgun Gothic'):
            for size in (12, 36):
                for width in (0.01, 0.02, 0.1):
                    with self.subTest(family=family, size=size, width=width):
                        self.assertEqual(
                            self._item_images(family, size, width, False),
                            self._item_images(family, size, width, True),
                        )

    def test_saved_mixed_font_sizes_keep_thin_stroke_coverage(self) -> None:
        for width in (0.01, 0.02, 0.1):
            with self.subTest(width=width):
                self.assertEqual(
                    self._item_images('Arial', 36, width, False, mixed=True),
                    self._item_images('Arial', 36, width, True, mixed=True),
                )

if __name__ == '__main__':
    unittest.main()
