"""Native outline dispatch preserves inherited paint and native glyph runs."""
from __future__ import annotations

from contextlib import ExitStack
import math
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy.QtCore import QPointF, QRectF, Qt
from qtpy.QtGui import (
    QBrush, QColor, QFont, QImage, QPainter, QPen, QTextLayout,
)
from qtpy.QtWidgets import QApplication

from ballontranslator.ui.text_engine.rendering import native_paint


class NativePainterStateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _layout(
        text: str,
        *,
        partial: bool = False,
        no_pen_override: bool = False,
        foreground: bool = False,
        preedit: str = '',
    ) -> QTextLayout:
        font = QFont('Arial', 16)
        font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        layout = QTextLayout(text, font)
        entry = QTextLayout.FormatRange()
        entry.start = 0
        entry.length = 80 if partial else len(text.encode('utf-16-le')) // 2
        entry.format.setTextOutline(QPen(
            QColor(245, 235, 220), 2.4, Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin,
        ))
        if foreground:
            entry.format.setForeground(QColor(205, 30, 70, 220))
        formats = [entry]
        if no_pen_override:
            override = QTextLayout.FormatRange()
            override.start = 80
            override.length = entry.length - override.start
            override.format.setTextOutline(QPen(Qt.PenStyle.NoPen))
            formats.append(override)
        layout.setFormats(formats)
        if preedit:
            layout.setPreeditArea(20, preedit)
        layout.beginLayout()
        line = layout.createLine()
        line.setLineWidth(20000)
        layout.endLayout()
        return layout

    @staticmethod
    def _painter_state(painter: QPainter) -> tuple:
        return (
            painter.pen(), painter.brush(), painter.font(),
            painter.brushOrigin(), painter.background(), painter.backgroundMode(),
            painter.renderHints(), painter.opacity(), painter.compositionMode(),
            painter.worldTransform(), painter.viewTransformEnabled(),
            painter.hasClipping(), painter.clipPath(),
        )

    def _compare(
        self,
        layout: QTextLayout,
        *,
        dpr: float = 1.0,
        opaque_background: bool = False,
        multiply: bool = False,
        selections: tuple[QTextLayout.FormatRange, ...] = (),
        partitioned: bool,
    ) -> None:
        """Check exact pixels and painter ownership away from device edges.

        >>> callable(NativePainterStateTest._compare)
        True
        """
        bounds = layout.boundingRect()
        margin = 40
        width = math.ceil((bounds.width() + 2 * margin) * dpr)
        height = math.ceil((bounds.height() + 2 * margin) * dpr)
        outputs = []
        primitive_calls = {True: [], False: []}
        engine = native_paint._NativePathEngine

        def observe(name: str):
            original = getattr(engine, name)

            def record(instance, *args):
                primitive_calls[instance.probing].append((
                    name, args[0].elementCount() if name == 'drawPath' else None,
                ))
                return original(instance, *args)

            return record

        for accelerated in (False, True):
            image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
            image.setDevicePixelRatio(dpr)
            image.fill(QColor(18, 77, 125, 180))
            painter = QPainter(image)
            painter.translate(margin + 0.375, margin + 2.125)
            painter.setPen(QPen(QColor(10, 40, 235, 210), 1.7))
            painter.setBrush(QBrush(QColor(40, 190, 75), Qt.BrushStyle.Dense3Pattern))
            painter.setFont(QFont('Courier New', 11))
            painter.setBrushOrigin(QPointF(3.25, 7.5))
            painter.setBackground(QBrush(QColor(240, 220, 25, 190)))
            painter.setBackgroundMode(
                Qt.BGMode.OpaqueMode if opaque_background else Qt.BGMode.TransparentMode
            )
            painter.setRenderHints(
                QPainter.RenderHint.Antialiasing
                | QPainter.RenderHint.TextAntialiasing
                | QPainter.RenderHint.SmoothPixmapTransform
            )
            painter.setOpacity(0.73)
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_Multiply
                if multiply else QPainter.CompositionMode.CompositionMode_SourceOver
            )
            painter.setClipRect(bounds.adjusted(-12, -12, 12, 12))
            before = self._painter_state(painter)
            try:
                if accelerated:
                    with ExitStack() as patches:
                        for primitive in (
                            'drawPath', 'drawPixmap', 'drawImage', 'drawPolygon',
                            'drawRects', 'drawLines', 'drawTextItem',
                        ):
                            patches.enter_context(patch.object(
                                engine, primitive, observe(primitive),
                            ))
                        strips = patches.enter_context(patch.object(
                            native_paint, '_draw_path_in_strips',
                            wraps=native_paint._draw_path_in_strips,
                        ))
                        contours = patches.enter_context(patch.object(
                            native_paint, '_closed_contours',
                            wraps=native_paint._closed_contours,
                        ))
                        native_paint.draw_native_layout(layout, painter, selections, QRectF())
                        if partitioned:
                            self.assertGreater(strips.call_count, 0)
                            self.assertGreater(contours.call_count, 0)
                        else:
                            self.assertEqual(strips.call_count, 0)
                else:
                    layout.draw(painter, QPointF(), selections, QRectF())
                self.assertEqual(self._painter_state(painter), before)
            finally:
                painter.end()
            outputs.append(image)
        self.assertEqual(outputs[0], outputs[1])
        if partitioned:
            self.assertTrue(primitive_calls[True])
            self.assertEqual(primitive_calls[True], primitive_calls[False])
            self.assertNotIn('drawTextItem', [name for name, _ in primitive_calls[True]])
        elif not selections and not layout.preeditAreaText():
            self.assertIn('drawTextItem', [name for name, _ in primitive_calls[True]])
            self.assertFalse(primitive_calls[False])
        else:
            self.assertFalse(primitive_calls[True])
            self.assertFalse(primitive_calls[False])

    def test_fully_outlined_runs_inherit_painter_state_and_keep_partitioning(self) -> None:
        for foreground in (False, True):
            layout = self._layout('0' * 160, foreground=foreground)
            for dpr in (1.0, 1.25):
                for opaque_background in (False, True):
                    for multiply in (False, True):
                        with self.subTest(
                            foreground=foreground, dpr=dpr,
                            opaque_background=opaque_background, multiply=multiply,
                        ):
                            self._compare(
                                layout, dpr=dpr, opaque_background=opaque_background,
                                multiply=multiply, partitioned=True,
                            )

    def test_mixed_native_glyphs_fall_back_before_any_outline_paint(self) -> None:
        text = '0' * 80 + '한글 العربية 😀 AV ' * 8
        for no_pen_override in (False, True):
            layout = self._layout(
                text, partial=not no_pen_override, no_pen_override=no_pen_override,
            )
            for dpr in (1.0, 1.25):
                with self.subTest(no_pen_override=no_pen_override, dpr=dpr):
                    self._compare(
                        layout, dpr=dpr, opaque_background=True,
                        multiply=True, partitioned=False,
                    )

    def test_selection_overrides_keep_native_shaping_and_painter_state(self) -> None:
        layout = self._layout('0' * 80 + '한글 العربية 😀 AV ' * 8)
        for remove_outline in (False, True):
            selection = QTextLayout.FormatRange()
            selection.start, selection.length = 20, 130
            selection.format.setForeground(QColor(235, 50, 120))
            selection.format.setBackground(QColor(40, 195, 130, 110))
            if remove_outline:
                selection.format.setTextOutline(QPen(Qt.PenStyle.NoPen))
            for dpr in (1.0, 1.25):
                with self.subTest(remove_outline=remove_outline, dpr=dpr):
                    self._compare(
                        layout, dpr=dpr, opaque_background=True, multiply=True,
                        selections=(selection,), partitioned=False,
                    )

    def test_preedit_keeps_native_shaping_and_painter_state(self) -> None:
        for preedit in ('가😀', 'العربية'):
            layout = self._layout('0' * 160, preedit=preedit)
            for dpr in (1.0, 1.25):
                with self.subTest(preedit=preedit, dpr=dpr):
                    self._compare(layout, dpr=dpr, partitioned=False)


if __name__ == '__main__':
    unittest.main()
