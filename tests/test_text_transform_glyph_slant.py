import math
import os
import unittest
from unittest import mock

import cv2
import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy import API_NAME, QT_VERSION
from qtpy.QtCore import QPointF, QRectF, Qt
from qtpy.QtGui import (
    QAbstractTextDocumentLayout,
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QImage,
    QInputMethodEvent,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
    QTextLayout,
    QTransform,
)
from qtpy.QtWidgets import QApplication, QGraphicsScene
try:
    from qtpy.QtWidgets import QUndoStack
except ImportError:
    from qtpy.QtGui import QUndoStack

from ballontranslator.utils import shared as C

C.FLAG_QT6 = QT_VERSION.startswith('6')
C.USE_PYSIDE6 = API_NAME == 'PySide6'

from ballontranslator.ui import (
    scene_textlayout,
    text_glyph_renderer,
    textitem as textitem_module,
)
from ballontranslator.ui.text_glyph_renderer import (
    glyph_geometry,
    glyph_slant_transform,
    glyph_slant_x,
    resolve_paint_spans,
)
from ballontranslator.ui.textitem import TextBlkItem
from ballontranslator.ui.textedit_commands import SetTextTransformCommand
from ballontranslator.utils.fontformat import FontFormat
from ballontranslator.utils.textblock import TextBlock


_APP = QApplication.instance() or QApplication([])


def _font_family():
    arial = r'C:\Windows\Fonts\arial.ttf'
    if os.path.exists(arial):
        font_id = QFontDatabase.addApplicationFont(arial)
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            return families[0]
    try:
        families = QFontDatabase.families()
    except TypeError:  # PyQt5 exposes this as an instance method.
        families = QFontDatabase().families()
    return families[0] if families else None


_FONT_FAMILY = _font_family()


def _make_item(
    *,
    text='TEST',
    vertical=False,
    glyph_slant=0.0,
    box_slant=0.0,
    italic=False,
    angle=0.0,
    stroke_width=0.0,
    shadow_radius=0.0,
    shadow_strength=0.0,
    gradient=False,
    font_family=None,
):
    font_format = FontFormat(
        font_family=font_family or _FONT_FAMILY or 'Sans Serif',
        font_size=56,
        frgb=[20, 180, 60],
        vertical=vertical,
        italic=italic,
        slant_angle=box_slant,
        glyph_slant_angle=glyph_slant,
        srgb=[220, 0, 0],
        stroke_width=stroke_width,
        shadow_radius=shadow_radius,
        shadow_strength=shadow_strength,
        shadow_offset=[0.2, -0.15],
        shadow_color=[0, 0, 180],
        gradient_enabled=gradient,
        gradient_start_color=[255, 180, 0],
        gradient_end_color=[0, 80, 255],
    )
    block = TextBlock(
        xyxy=[40, 40, 300, 220],
        _bounding_rect=[40, 40, 260, 180],
        translation=text,
        angle=angle,
        fontformat=font_format,
    )
    return TextBlkItem(block)


def _geometry_snapshot(item):
    return (
        item.fontformat.text_transform,
        item.layout.glyph_slant_angle,
        item.padding(),
        QRectF(item.boundingRect()),
        QRectF(item.shape().boundingRect()),
        QRectF(item.logical_unpadded_rect()),
        QRectF(item.absBoundingRect(qrect=True)),
        QPointF(item.pos()),
        QPointF(item.transformOriginPoint()),
        QTransform(item.transform()),
    )


def _image_array(image):
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    size = converted.width() * converted.height() * 4
    bits = converted.bits()
    if hasattr(bits, 'asstring'):
        raw = bits.asstring(size)
    else:
        raw = bits.tobytes()
    return np.frombuffer(raw, dtype=np.uint8).reshape(
        converted.height(), converted.width(), 4
    ).copy()


def _effect_pixel_counts(pixels):
    alpha = pixels[..., 3] > 0
    red = (
        (pixels[..., 0] > 150)
        & (pixels[..., 1] < 80)
        & (pixels[..., 2] < 80)
        & alpha
    )
    blue = (
        (pixels[..., 0] < 80)
        & (pixels[..., 1] < 80)
        & (pixels[..., 2] > 100)
        & alpha
    )
    return int(red.sum()), int(blue.sum())


def _layout_property_range_count(item, property_id):
    count = 0
    block = item.document().firstBlock()
    while block.isValid():
        count += sum(
            bool(format_range.format.property(property_id))
            for format_range in block.layout().formats()
        )
        block = block.next()
    return count


def _paint_live_layout(item, context=None):
    rect = item.boundingRect()
    image = QImage(
        max(1, math.ceil(rect.width())),
        max(1, math.ceil(rect.height())),
        QImage.Format.Format_ARGB32,
    )
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        painter.translate(-rect.topLeft())
        item._paint_live_layout(
            painter,
            item._effect_paint_context() if context is None else context,
        )
    finally:
        painter.end()
    return _image_array(image)


def _render_glyph_geometry(geometry, char_format, margin=32.0):
    rect = geometry.bounds.adjusted(-margin, -margin, margin, margin)
    image = QImage(
        max(1, math.ceil(rect.width())),
        max(1, math.ceil(rect.height())),
        QImage.Format.Format_ARGB32,
    )
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        painter.translate(-rect.topLeft())
        text_glyph_renderer.draw_glyph_geometry(
            painter, geometry, char_format
        )
    finally:
        painter.end()
    return _image_array(image)


def _render_scene(item, source=QRectF(0, 0, 380, 280)):
    image = QImage(
        max(1, math.ceil(source.width())),
        max(1, math.ceil(source.height())),
        QImage.Format.Format_ARGB32,
    )
    image.fill(Qt.GlobalColor.transparent)
    scene = QGraphicsScene()
    scene.setSceneRect(source)
    scene.addItem(item)
    painter = QPainter(image)
    try:
        scene.render(painter, QRectF(image.rect()), source)
    finally:
        painter.end()
        scene.removeItem(item)
    return _image_array(image)


def _line_snapshot(item):
    records = []
    logical_top_left = item.logical_unpadded_rect().topLeft()
    block = item.document().firstBlock()
    while block.isValid():
        layout = block.layout()
        for index in range(layout.lineCount()):
            line = layout.lineAt(index)
            position = line.position()
            records.append(
                (
                    line.textStart(),
                    line.textLength(),
                    position.x() - logical_top_left.x(),
                    position.y() - logical_top_left.y(),
                    line.horizontalAdvance(),
                    line.height(),
                )
            )
        block = block.next()
    return tuple(records)


def _polygon_snapshot(polygon):
    return tuple((point.x(), point.y()) for point in polygon)


class GlyphSlantMathTests(unittest.TestCase):

    def test_direction_formula_and_transform_match_at_limits(self):
        for angle in (-45.0, -12.0, 0.0, 12.0, 45.0):
            with self.subTest(angle=angle):
                point = QPointF(7.0, -3.0)
                baseline = 11.0
                mapped = glyph_slant_transform(angle, baseline).map(point)
                self.assertAlmostEqual(
                    mapped.x(),
                    glyph_slant_x(
                        point.x(), point.y(), baseline, angle
                    ),
                )
                self.assertEqual(mapped.y(), point.y())
                self.assertTrue(math.isfinite(mapped.x()))

    @unittest.skipUnless(_FONT_FAMILY, 'No usable font is available')
    def test_live_glyph_geometry_maps_positive_slant_to_local_plus_x(self):
        item = _make_item(text='H')
        line = item.document().firstBlock().layout().lineAt(0)

        negative = glyph_geometry(
            line, line.textStart(), line.textLength(), QPointF(), QTransform(), -30
        )
        positive = glyph_geometry(
            line, line.textStart(), line.textLength(), QPointF(), QTransform(), 30
        )
        self.assertFalse(negative.bounds.isEmpty())
        self.assertGreater(positive.bounds.center().x(), negative.bounds.center().x())
        self.assertAlmostEqual(
            positive.bounds.center().y(), negative.bounds.center().y()
        )

        rotated = QTransform().rotate(90)
        negative_rotated = glyph_geometry(
            line,
            line.textStart(),
            line.textLength(),
            QPointF(),
            rotated,
            -30,
        )
        positive_rotated = glyph_geometry(
            line,
            line.textStart(),
            line.textLength(),
            QPointF(),
            rotated,
            30,
        )
        self.assertGreater(
            positive_rotated.bounds.center().y(),
            negative_rotated.bounds.center().y(),
        )
        self.assertAlmostEqual(
            positive_rotated.bounds.center().x(),
            negative_rotated.bounds.center().x(),
        )

    def test_pathless_glyph_uses_fallback_and_raw_bounds(self):
        class FakeRawFont:
            @staticmethod
            def pathForGlyph(_index):
                return QPainterPath()

            @staticmethod
            def boundingRect(_index):
                return QRectF(0.0, -10.0, 5.0, 10.0)

        class FakeRun:
            @staticmethod
            def rawFont():
                return FakeRawFont()

            @staticmethod
            def glyphIndexes():
                return [7]

            @staticmethod
            def positions():
                return [QPointF(2.0, 10.0)]

        class FakeLine:
            @staticmethod
            def y():
                return 0.0

            @staticmethod
            def ascent():
                return 10.0

            @staticmethod
            def glyphRuns(_start, _length):
                return [FakeRun()]

        fallback = object()
        with mock.patch.object(
            text_glyph_renderer, '_fallback_run', return_value=fallback
        ):
            geometry = glyph_geometry(
                FakeLine(), 0, 1, QPointF(), QTransform(), 45.0
            )

        self.assertTrue(geometry.path.isEmpty())
        self.assertEqual(len(geometry.fallbacks), 1)
        self.assertIs(geometry.fallbacks[0][0], fallback)
        self.assertEqual(geometry.fallbacks[0][2], geometry.bounds)
        self.assertAlmostEqual(geometry.bounds.left(), 2.0)
        self.assertAlmostEqual(geometry.bounds.right(), 17.0)
        self.assertEqual(geometry.bounds.top(), 0.0)
        self.assertEqual(geometry.bounds.bottom(), 10.0)

    def test_color_table_routes_nonempty_glyph_path_to_native_fallback(self):
        class FakeColorRawFont:
            @staticmethod
            def isValid():
                return True

            @staticmethod
            def fontTable(table_name):
                return b'color' if table_name == 'COLR' else b''

            @staticmethod
            def pathForGlyph(_index):
                path = QPainterPath()
                path.addRect(QRectF(0.0, -10.0, 8.0, 10.0))
                return path

            @staticmethod
            def boundingRect(_index):
                return QRectF(0.0, -10.0, 8.0, 10.0)

        raw_font = FakeColorRawFont()

        class FakeRun:
            rawFont = staticmethod(lambda: raw_font)
            glyphIndexes = staticmethod(lambda: [7])
            positions = staticmethod(lambda: [QPointF(2.0, 10.0)])

        class FakeLine:
            y = staticmethod(lambda: 0.0)
            ascent = staticmethod(lambda: 10.0)
            glyphRuns = staticmethod(lambda _start, _length: [FakeRun()])

        key = (FakeColorRawFont, raw_font)
        text_glyph_renderer._COLOR_FONT_CACHE.pop(key, None)
        fallback = object()
        with mock.patch.object(
            text_glyph_renderer, '_fallback_run', return_value=fallback
        ):
            geometry = glyph_geometry(
                FakeLine(), 0, 1, QPointF(), QTransform(), 20.0
            )

        self.assertFalse(geometry.paths)
        self.assertEqual(len(geometry.fallbacks), 1)
        self.assertIs(geometry.fallbacks[0][0], fallback)
        self.assertTrue(text_glyph_renderer._COLOR_FONT_CACHE[key])

    @unittest.skipUnless(_FONT_FAMILY, 'No usable font is available')
    def test_thick_fallback_outline_is_connected_and_native_fill_is_on_top(self):
        item = _make_item(text='I')
        line = item.document().firstBlock().layout().lineAt(0)
        with mock.patch.object(
            text_glyph_renderer,
            '_raw_font_has_color_glyphs',
            return_value=True,
        ):
            geometry = glyph_geometry(
                line,
                line.textStart(),
                line.textLength(),
                QPointF(),
                QTransform(),
                25.0,
            )
        self.assertFalse(geometry.paths)
        self.assertTrue(geometry.fallbacks)

        fill_format = QTextCharFormat()
        fill_format.setForeground(QColor(0, 200, 0))
        outline_format = QTextCharFormat(fill_format)
        outline = QPen(QColor(0, 0, 240))
        outline.setWidthF(32.0)
        outline_format.setTextOutline(outline)
        fill_pixels = _render_glyph_geometry(
            geometry, fill_format, margin=24.0
        )
        dilated_results = []
        original_dilate = text_glyph_renderer._dilate_fallback_alpha

        def tracking_dilate(alpha, radius):
            result = original_dilate(alpha, radius)
            dilated_results.append((alpha.copy(), radius, result.copy()))
            return result

        with mock.patch.object(
            text_glyph_renderer,
            '_dilate_fallback_alpha',
            side_effect=tracking_dilate,
        ):
            outlined_pixels = _render_glyph_geometry(
                geometry, outline_format, margin=24.0
            )

        self.assertEqual(len(dilated_results), 1)
        source_alpha, radius, dilated_alpha = dilated_results[0]
        self.assertGreaterEqual(radius, 16)
        self.assertGreater(
            int((dilated_alpha > 0).sum()),
            int((source_alpha > 0).sum()),
        )
        component_count, _labels = cv2.connectedComponents(
            (dilated_alpha > 0).astype(np.uint8)
        )
        self.assertEqual(component_count - 1, 1)

        fill_alpha = fill_pixels[..., 3]
        blue_outline = (
            (outlined_pixels[..., 2] > 160)
            & (outlined_pixels[..., 0] < 80)
            & (outlined_pixels[..., 1] < 100)
            & (outlined_pixels[..., 3] > 0)
        )
        green_fill = (
            (outlined_pixels[..., 1] > 120)
            & (outlined_pixels[..., 0] < 100)
            & (outlined_pixels[..., 2] < 100)
            & (outlined_pixels[..., 3] > 0)
        )
        self.assertGreater(int((blue_outline & (fill_alpha == 0)).sum()), 100)
        self.assertGreater(int((green_fill & (fill_alpha > 128)).sum()), 20)

    def test_compensated_gradient_matches_item_local_mapped_path_field(self):
        gradient = QLinearGradient(0.0, 0.0, 160.0, 0.0)
        gradient.setColorAt(0.0, QColor('red'))
        gradient.setColorAt(1.0, QColor('blue'))
        brush = QBrush(gradient)
        brush_transform = QTransform()
        brush_transform.translate(7.0, 3.0)
        brush_transform.scale(0.8, 1.2)
        brush.setTransform(brush_transform)
        glyph_transform = glyph_slant_transform(45.0, 80.0)
        compensated = text_glyph_renderer._item_space_brush(
            brush, glyph_transform
        )

        effective = text_glyph_renderer._composed_transform(
            compensated.transform(), glyph_transform
        )
        for point in (
            QPointF(),
            QPointF(3.0, 4.0),
            QPointF(-5.0, 2.0),
        ):
            expected = brush.transform().map(point)
            actual = effective.map(point)
            self.assertAlmostEqual(actual.x(), expected.x(), places=9)
            self.assertAlmostEqual(actual.y(), expected.y(), places=9)

        raw_path = QPainterPath()
        raw_path.addRect(QRectF(30.0, 30.0, 60.0, 50.0))
        images = []
        for mapped_vector_path in (True, False):
            image = QImage(180, 120, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            try:
                painter.setRenderHint(
                    QPainter.RenderHint.Antialiasing, False
                )
                painter.setPen(Qt.PenStyle.NoPen)
                if mapped_vector_path:
                    painter.setBrush(brush)
                    painter.drawPath(glyph_transform.map(raw_path))
                else:
                    painter.setTransform(glyph_transform, True)
                    painter.setBrush(compensated)
                    painter.drawPath(raw_path)
            finally:
                painter.end()
            images.append(_image_array(image))

        self.assertGreater(int((images[0][..., 3] > 0).sum()), 100)
        np.testing.assert_array_equal(images[0], images[1])

    def test_decorations_keep_logical_baseline_orientation_and_style(self):
        class RecordingPainter:
            def __init__(self):
                self.transform_calls = []
                self.current_pen = QPen()
                self.lines = []
                self.paths = []

            def save(self):
                pass

            def restore(self):
                pass

            def pen(self):
                return QPen(self.current_pen)

            def setTransform(self, transform, combine):
                self.transform_calls.append((QTransform(transform), combine))

            def setPen(self, pen):
                self.current_pen = QPen(pen)

            def drawLine(self, start, end):
                self.lines.append(
                    (QPen(self.current_pen), QPointF(start), QPointF(end))
                )

            def drawPath(self, path):
                self.paths.append((QPen(self.current_pen), QPainterPath(path)))

        font = QFont(_FONT_FAMILY or 'Sans Serif')
        font.setPointSizeF(24.0)
        font.setUnderline(False)
        font.setOverline(True)
        font.setStrikeOut(True)
        char_format = QTextCharFormat()
        char_format.setFont(font)
        char_format.setForeground(QColor('red'))
        char_format.setUnderlineColor(QColor('blue'))
        char_format.setUnderlineStyle(
            QTextCharFormat.UnderlineStyle.DashUnderline
        )
        rect = QRectF(10.0, 20.0, 100.0, 50.0)
        baseline = 60.0
        orientation = QTransform().rotate(90.0)
        painter = RecordingPainter()

        text_glyph_renderer._draw_decorations(
            painter, rect, char_format, orientation, baseline
        )

        metrics = QFontMetricsF(char_format.font())
        self.assertEqual(painter.transform_calls, [(orientation, True)])
        self.assertEqual(len(painter.lines), 3)
        underline, overline, strikeout = painter.lines
        self.assertEqual(underline[0].style(), Qt.PenStyle.DashLine)
        self.assertEqual(underline[0].color(), QColor('blue'))
        self.assertAlmostEqual(
            underline[1].y(), baseline + metrics.underlinePos()
        )
        self.assertAlmostEqual(underline[2].y(), underline[1].y())
        self.assertEqual(overline[0].color(), QColor('red'))
        self.assertAlmostEqual(
            overline[1].y(), baseline - metrics.ascent()
        )
        self.assertAlmostEqual(
            strikeout[1].y(), baseline - metrics.strikeOutPos()
        )
        for _pen, start, end in painter.lines:
            self.assertEqual((start.x(), end.x()), (rect.left(), rect.right()))

        # IME/spell-check formats can carry a wave style without setting the
        # legacy QFont underline boolean. The style itself must be sufficient.
        wave_format = QTextCharFormat()
        wave_format.setFont(font)
        wave_format.setFontOverline(False)
        wave_format.setFontStrikeOut(False)
        wave_format.setUnderlineStyle(
            QTextCharFormat.UnderlineStyle.WaveUnderline
        )
        self.assertFalse(wave_format.font().underline())
        wave_painter = RecordingPainter()
        text_glyph_renderer._draw_decorations(
            wave_painter,
            rect,
            wave_format,
            QTransform(),
            baseline,
        )
        self.assertFalse(wave_painter.lines)
        self.assertEqual(len(wave_painter.paths), 1)
        wave_bounds = wave_painter.paths[0][1].boundingRect()
        expected_y = baseline + QFontMetricsF(
            wave_format.font()
        ).underlinePos()
        self.assertLessEqual(wave_bounds.top(), expected_y)
        self.assertGreaterEqual(wave_bounds.bottom(), expected_y)

    def test_overlapping_odd_even_glyphs_keep_fill_and_inner_counter(self):
        class FakeRawFont:
            @staticmethod
            def pathForGlyph(_index):
                path = QPainterPath()
                path.setFillRule(Qt.FillRule.OddEvenFill)
                path.addRect(QRectF(0.0, -12.0, 12.0, 12.0))
                path.addRect(QRectF(3.0, -9.0, 6.0, 6.0))
                return path

            @staticmethod
            def boundingRect(_index):
                return QRectF(0.0, -12.0, 12.0, 12.0)

        class FakeRun:
            @staticmethod
            def rawFont():
                return FakeRawFont()

            @staticmethod
            def glyphIndexes():
                return [3, 4]

            @staticmethod
            def positions():
                return [QPointF(2.0, 12.0), QPointF(2.0, 12.0)]

        class FakeLine:
            y = staticmethod(lambda: 0.0)
            ascent = staticmethod(lambda: 12.0)
            glyphRuns = staticmethod(lambda _start, _length: [FakeRun()])

        geometry = glyph_geometry(
            FakeLine(), 0, 2, QPointF(), QTransform(), 0.0
        )

        self.assertEqual(len(geometry.paths), 2)
        self.assertTrue(
            all(
                path.fillRule() == Qt.FillRule.OddEvenFill
                for path in geometry.paths
            )
        )
        self.assertFalse(geometry.path.isEmpty())
        self.assertTrue(geometry.path.contains(QPointF(3.0, 6.0)))
        self.assertFalse(geometry.path.contains(QPointF(8.0, 6.0)))


@unittest.skipUnless(_FONT_FAMILY, 'No usable font is available for raster tests')
class GlyphSlantRenderingTests(unittest.TestCase):

    def test_interactive_paint_warns_and_draws_direct_on_fallback_raster_failure(self):
        item = _make_item(text='FAIL', glyph_slant=20.0)
        direct = text_glyph_renderer._draw_direct_fallbacks

        with mock.patch.object(
            text_glyph_renderer,
            '_raw_font_has_color_glyphs',
            return_value=True,
        ), mock.patch.object(
            text_glyph_renderer,
            'QImage',
            return_value=QImage(),
        ), mock.patch.object(
            text_glyph_renderer,
            '_draw_direct_fallbacks',
            wraps=direct,
        ) as draw_direct, mock.patch.object(
            textitem_module.LOGGER,
            'warning',
        ) as warning:
            pixels = _render_scene(item)

        self.assertGreater(draw_direct.call_count, 0)
        self.assertGreater(int((pixels[..., 3] > 0).sum()), 20)
        warning.assert_called_once()
        self.assertIn(
            'unable to allocate pathless glyph image',
            str(warning.call_args.args[-1]),
        )
        self.assertEqual(
            item._effect_allocation_warning_generation,
            item._effect_cache_generation,
        )
        self.assertIsNone(item.export_effect_error)

    def test_segoe_emoji_native_fill_keeps_visible_item_local_outline(self):
        emoji_path = r'C:\Windows\Fonts\seguiemj.ttf'
        if not os.path.exists(emoji_path):
            self.skipTest('Segoe UI Emoji is unavailable')
        font_id = QFontDatabase.addApplicationFont(emoji_path)
        families = QFontDatabase.applicationFontFamilies(font_id)
        if not families:
            self.skipTest('Qt could not load Segoe UI Emoji')

        item = _make_item(
            text='😀', glyph_slant=20.0, font_family=families[0]
        )
        line = item.document().firstBlock().layout().lineAt(0)
        geometry = glyph_geometry(
            line,
            line.textStart(),
            line.textLength(),
            QPointF(),
            QTransform(),
            20.0,
        )
        if not geometry.fallbacks:
            self.skipTest('Qt binding exposes no native emoji fallback run')
        self.assertFalse(geometry.paths)

        fill_format = QTextCharFormat()
        fill_format.setForeground(QColor(0, 200, 0))
        outline_format = QTextCharFormat(fill_format)
        outline = QPen(QColor(0, 0, 240))
        outline.setWidthF(20.0)
        outline_format.setTextOutline(outline)
        fill_pixels = _render_glyph_geometry(
            geometry, fill_format, margin=24.0
        )
        outlined_pixels = _render_glyph_geometry(
            geometry, outline_format, margin=24.0
        )

        fill_alpha = fill_pixels[..., 3]
        outline_alpha = outlined_pixels[..., 3]
        blue_outline = (
            (outlined_pixels[..., 2] > 160)
            & (outlined_pixels[..., 0] < 100)
            & (outlined_pixels[..., 1] < 120)
            & (outline_alpha > 0)
        )
        self.assertGreater(int((fill_alpha > 0).sum()), 50)
        self.assertGreater(
            int((outline_alpha > 0).sum()), int((fill_alpha > 0).sum())
        )
        self.assertGreater(
            int((blue_outline & (fill_alpha == 0)).sum()), 100
        )

    def test_explicit_zero_is_pixel_identical_to_default(self):
        for vertical in (False, True):
            with self.subTest(vertical=vertical):
                default = _make_item(text='漢A fi', vertical=vertical)
                explicit = _make_item(
                    text='漢A fi', vertical=vertical, glyph_slant=0.0
                )
                np.testing.assert_array_equal(
                    _render_scene(default), _render_scene(explicit)
                )

    def test_zero_degree_bypasses_custom_line_renderer(self):
        for vertical in (False, True):
            with self.subTest(vertical=vertical):
                item = _make_item(text='AB', vertical=vertical)
                with mock.patch.object(
                    scene_textlayout,
                    'draw_slanted_line',
                    side_effect=AssertionError('custom renderer used at 0 degrees'),
                ):
                    pixels = _paint_live_layout(item)
                self.assertGreater(int((pixels[..., 3] > 0).sum()), 20)

    def test_nonzero_degree_uses_custom_line_renderer(self):
        original = scene_textlayout.draw_slanted_line
        for vertical in (False, True):
            with self.subTest(vertical=vertical):
                item = _make_item(
                    text='漢A', vertical=vertical, glyph_slant=12.0
                )
                with mock.patch.object(
                    scene_textlayout,
                    'draw_slanted_line',
                    wraps=original,
                ) as draw:
                    pixels = _paint_live_layout(item)
                self.assertGreater(draw.call_count, 0)
                self.assertGreater(int((pixels[..., 3] > 0).sum()), 20)

    def test_vertical_empty_block_ime_preedit_uses_slanted_live_line(self):
        item = _make_item(text='', vertical=True, glyph_slant=20.0)
        item.startEdit()
        try:
            item.inputMethodEvent(QInputMethodEvent('A', []))
            _APP.processEvents()
            block = item.document().firstBlock()
            layout = block.layout()
            self.assertTrue(item.document().isEmpty())
            self.assertEqual(item.toPlainText(), '')
            self.assertEqual(layout.preeditAreaText(), 'A')
            self.assertGreater(layout.lineCount(), 0)
            self.assertGreater(layout.lineAt(0).textLength(), 0)

            original = scene_textlayout.draw_slanted_line
            with mock.patch.object(
                scene_textlayout,
                'draw_slanted_line',
                wraps=original,
            ) as draw:
                pixels = _paint_live_layout(item)
            self.assertGreater(draw.call_count, 0)
            self.assertGreater(int((pixels[..., 3] > 0).sum()), 20)
            self.assertEqual(item.toPlainText(), '')
            self.assertEqual(layout.preeditAreaText(), 'A')
        finally:
            item.inputMethodEvent(QInputMethodEvent('', []))
            item.endEdit(keep_focus=False)

    def test_vertical_orientation_keeps_upright_and_rotated_categories(self):
        item = _make_item(
            text='漢A。（', vertical=True, glyph_slant=20.0
        )
        placements = list(item.layout._iter_glyph_line_placements())
        self.assertEqual(len(placements), 4)
        self.assertEqual(
            [orientation.isIdentity() for _, _, orientation in placements],
            [True, False, True, False],
        )

        for index, (line, offset, orientation) in enumerate(placements):
            negative = glyph_geometry(
                line,
                line.textStart(),
                line.textLength(),
                offset,
                orientation,
                -30.0,
            )
            positive = glyph_geometry(
                line,
                line.textStart(),
                line.textLength(),
                offset,
                orientation,
                30.0,
            )
            with self.subTest(index=index):
                self.assertFalse(negative.bounds.isEmpty())
                self.assertFalse(positive.bounds.isEmpty())
                if orientation.isIdentity():
                    self.assertGreater(
                        positive.bounds.center().x(),
                        negative.bounds.center().x(),
                    )
                else:
                    self.assertGreater(
                        positive.bounds.center().y(),
                        negative.bounds.center().y(),
                    )

    def test_zero_vertical_effect_mask_bypasses_custom_renderer(self):
        zero = _make_item(
            text='AB', vertical=True, stroke_width=0.18
        )
        pixmap = zero._new_effect_pixmap(1.0)
        painter = QPainter(pixmap)
        try:
            painter.translate(-zero.boundingRect().topLeft())
            with mock.patch.object(
                scene_textlayout,
                'draw_slanted_glyph_mask',
                side_effect=AssertionError('custom mask used at 0 degrees'),
            ):
                zero.paint_stroke(painter)
        finally:
            painter.end()
        self.assertGreater(int((_image_array(pixmap.toImage())[..., 3] > 0).sum()), 20)

        slanted = _make_item(
            text='AB', vertical=True, glyph_slant=12.0, stroke_width=0.18
        )
        pixmap = slanted._new_effect_pixmap(1.0)
        painter = QPainter(pixmap)
        original = scene_textlayout.draw_slanted_glyph_mask
        try:
            painter.translate(-slanted.boundingRect().topLeft())
            with mock.patch.object(
                scene_textlayout,
                'draw_slanted_glyph_mask',
                wraps=original,
            ) as draw:
                slanted.paint_stroke(painter)
        finally:
            painter.end()
        self.assertGreater(draw.call_count, 0)
        self.assertGreater(int((_image_array(pixmap.toImage())[..., 3] > 0).sum()), 20)

    def test_selection_background_is_logical_not_glyph_slanted(self):
        images = []
        for angle in (-30.0, 30.0):
            item = _make_item(text='AB', glyph_slant=angle)
            context = QAbstractTextDocumentLayout.PaintContext()
            selection = QAbstractTextDocumentLayout.Selection()
            selection.cursor = QTextCursor(item.document())
            selection.cursor.setPosition(0)
            selection.cursor.setPosition(
                1, QTextCursor.MoveMode.KeepAnchor
            )
            selection.format.setBackground(QColor(240, 10, 220))
            context.selections = [selection]
            with mock.patch.object(
                text_glyph_renderer, 'draw_glyph_geometry'
            ):
                pixels = _paint_live_layout(item, context)
            bounds = item.boundingRect()
            logical = item.logical_unpadded_rect()
            left = round(logical.left() - bounds.left())
            top = round(logical.top() - bounds.top())
            images.append(
                pixels[
                    top:top + round(logical.height()),
                    left:left + round(logical.width()),
                ]
            )

        np.testing.assert_array_equal(images[0], images[1])
        self.assertGreater(int((images[0][..., 3] > 0).sum()), 20)

    def test_nonzero_full_width_selection_paints_background_and_foreground(self):
        item = _make_item(text='AB', glyph_slant=25.0)
        context = QAbstractTextDocumentLayout.PaintContext()
        selection = QAbstractTextDocumentLayout.Selection()
        selection.cursor = QTextCursor(item.document())
        selection.cursor.setPosition(1)
        selection.format.setProperty(QTextFormat.FullWidthSelection, True)
        selection.format.setBackground(QColor(240, 10, 220))
        selection.format.setForeground(QColor(250, 220, 10))
        context.selections = [selection]

        pixels = _paint_live_layout(item, context)
        magenta = (
            (pixels[..., 0] == 240)
            & (pixels[..., 1] == 10)
            & (pixels[..., 2] == 220)
            & (pixels[..., 3] == 255)
        )
        selected_ink = (
            (pixels[..., 0] > 180)
            & (pixels[..., 1] > 150)
            & (pixels[..., 2] < 80)
            & (pixels[..., 3] > 0)
        )
        self.assertGreater(int(magenta.sum()), 100)
        self.assertGreater(int(selected_ink.sum()), 20)

    def test_opaque_gradient_changes_color_field_not_glyph_alpha(self):
        plain = _make_item(text='Gradient', glyph_slant=30.0)
        gradient = _make_item(
            text='Gradient', glyph_slant=30.0, gradient=True
        )
        np.testing.assert_array_equal(
            _paint_live_layout(plain)[..., 3],
            _paint_live_layout(gradient)[..., 3],
        )

    def test_preview_and_cancel_preserve_document_and_logical_geometry(self):
        item = _make_item(text='fi ffi e\u0301 漢字')
        cursor = item.textCursor()
        cursor.setPosition(8)
        cursor.setPosition(2, QTextCursor.MoveMode.KeepAnchor)
        item.setTextCursor(cursor)
        document = item.document()
        model_transform = item.fontformat.text_transform
        box_transform = QTransform(item.transform())
        logical_rect = item.absBoundingRect(qrect=True)
        geometry_snapshot = _geometry_snapshot(item)
        logical_points = (QPointF(5.0, 10.0), QPointF(80.0, 30.0))
        hits = tuple(
            item.layout.hitTest(
                item.logical_unpadded_rect().topLeft() + point,
                Qt.HitTestAccuracy.FuzzyHit,
            )
            for point in logical_points
        )
        snapshot = (
            document.toHtml(),
            document.toPlainText(),
            document.revision(),
            document.availableUndoSteps(),
            document.isModified(),
            cursor.position(),
            cursor.anchor(),
            _line_snapshot(item),
        )

        self.assertTrue(
            item.set_text_transform(glyph_slant_angle=27.0, preview=True)
        )
        self.assertEqual(item.fontformat.text_transform, model_transform)
        self.assertEqual(item.layout.glyph_slant_angle, 27.0)
        self.assertGreater(item.padding(), geometry_snapshot[2])
        self.assertEqual(item.transform(), box_transform)
        self.assertEqual(item.absBoundingRect(qrect=True), logical_rect)
        self.assertEqual(item.shape().boundingRect(), item.logical_unpadded_rect())
        self.assertEqual(
            tuple(
                item.layout.hitTest(
                    item.logical_unpadded_rect().topLeft() + point,
                    Qt.HitTestAccuracy.FuzzyHit,
                )
                for point in logical_points
            ),
            hits,
        )
        current_cursor = item.textCursor()
        self.assertEqual(
            (
                document.toHtml(),
                document.toPlainText(),
                document.revision(),
                document.availableUndoSteps(),
                document.isModified(),
                current_cursor.position(),
                current_cursor.anchor(),
                _line_snapshot(item),
            ),
            snapshot,
        )

        self.assertTrue(item.clear_text_transform_preview())
        self.assertEqual(item.layout.glyph_slant_angle, 0.0)
        self.assertEqual(item.fontformat.text_transform, model_transform)
        self.assertEqual(item.transform(), box_transform)
        self.assertEqual(item.absBoundingRect(qrect=True), logical_rect)
        self.assertEqual(_geometry_snapshot(item), geometry_snapshot)

    def test_commit_zero_and_undo_restore_neutral_padding_and_hit_shape(self):
        for transition in ('commit-zero', 'undo'):
            with self.subTest(transition=transition):
                item = _make_item(text='fi ffi e\u0301 漢字')
                before = _geometry_snapshot(item)

                if transition == 'commit-zero':
                    self.assertTrue(
                        item.set_text_transform(glyph_slant_angle=45.0)
                    )
                    active_padding = item.padding()
                    self.assertTrue(
                        item.set_text_transform(glyph_slant_angle=0.0)
                    )
                else:
                    stack = QUndoStack()
                    stack.push(
                        SetTextTransformCommand(
                            [item],
                            [(1.0, 1.0, 0.0, 0.0)],
                            [(1.0, 1.0, 0.0, 45.0)],
                        )
                    )
                    active_padding = item.padding()
                    stack.undo()

                self.assertGreater(active_padding, before[2])
                self.assertEqual(
                    item.fontformat.text_transform,
                    (1.0, 1.0, 0.0, 0.0),
                )
                self.assertEqual(item.layout.glyph_slant_angle, 0.0)
                self.assertEqual(_geometry_snapshot(item), before)

    def test_neutral_restore_preserves_base_effect_and_existing_padding(self):
        effect_cases = (
            {'stroke_width': 0.2},
            {'shadow_radius': 0.25, 'shadow_strength': 0.9},
            {
                'stroke_width': 0.2,
                'shadow_radius': 0.25,
                'shadow_strength': 0.9,
            },
        )
        for effect_kwargs in effect_cases:
            for transition in ('preview-clear', 'commit-zero', 'undo'):
                with self.subTest(
                    effect_kwargs=effect_kwargs,
                    transition=transition,
                ):
                    item = _make_item(text='TEST', **effect_kwargs)
                    before = _geometry_snapshot(item)
                    before_pixels = _render_scene(item)
                    before_red, before_blue = _effect_pixel_counts(before_pixels)
                    self.assertIsNotNone(item.background_pixmap)

                    if transition == 'preview-clear':
                        self.assertTrue(
                            item.set_text_transform(
                                glyph_slant_angle=45.0,
                                preview=True,
                            )
                        )
                    elif transition == 'commit-zero':
                        self.assertTrue(
                            item.set_text_transform(glyph_slant_angle=45.0)
                        )
                    else:
                        stack = QUndoStack()
                        stack.push(
                            SetTextTransformCommand(
                                [item],
                                [(1.0, 1.0, 0.0, 0.0)],
                                [(1.0, 1.0, 0.0, 45.0)],
                            )
                        )

                    _render_scene(item)
                    self.assertIsNotNone(item.background_pixmap)
                    active_cache_key = item.background_pixmap.cacheKey()
                    if transition == 'preview-clear':
                        self.assertTrue(item.clear_text_transform_preview())
                    elif transition == 'commit-zero':
                        self.assertTrue(
                            item.set_text_transform(glyph_slant_angle=0.0)
                        )
                    else:
                        stack.undo()

                    self.assertEqual(_geometry_snapshot(item), before)
                    self.assertIsNotNone(item.background_pixmap)
                    self.assertNotEqual(
                        item.background_pixmap.cacheKey(),
                        active_cache_key,
                    )
                    after_red, after_blue = _effect_pixel_counts(
                        _render_scene(item)
                    )
                    if effect_kwargs.get('stroke_width', 0.0) > 0.0:
                        self.assertGreater(before_red, 50)
                        self.assertLessEqual(
                            abs(after_red - before_red),
                            max(4, math.ceil(before_red * 0.01)),
                        )
                    if (
                        effect_kwargs.get('shadow_radius', 0.0) > 0.0
                        and effect_kwargs.get('shadow_strength', 0.0) > 0.0
                    ):
                        self.assertGreater(before_blue, 50)
                        self.assertLessEqual(
                            abs(after_blue - before_blue),
                            max(4, math.ceil(before_blue * 0.01)),
                        )

        item = _make_item(text='TEST')
        self.assertTrue(item.setPadding(80.0))
        before = _geometry_snapshot(item)
        self.assertTrue(
            item.set_text_transform(glyph_slant_angle=45.0, preview=True)
        )
        self.assertNotEqual(item.padding(), before[2])
        self.assertTrue(item.clear_text_transform_preview())
        self.assertEqual(_geometry_snapshot(item), before)

        neutral = _make_item(text='TEST')
        neutral.setStrokeWidth(0.5)
        neutral.setStrokeWidth(0.0)
        expected = _geometry_snapshot(neutral)

        item = _make_item(text='TEST')
        self.assertTrue(item.set_text_transform(glyph_slant_angle=45.0))
        item.setStrokeWidth(0.5)
        item.setStrokeWidth(0.0)
        self.assertTrue(item.set_text_transform(glyph_slant_angle=0.0))
        self.assertEqual(_geometry_snapshot(item), expected)

    def test_gradient_layout_state_is_removed_on_neutral_restore(self):
        gradient_property = textitem_module.GRADIENT_LAYOUT_FORMAT_PROPERTY
        unrelated_property = gradient_property + 1
        for transition in ('preview-clear', 'commit-zero', 'undo'):
            with self.subTest(transition=transition):
                item = _make_item(text='GRADIENT TEST', gradient=True)
                block_layout = item.document().firstBlock().layout()
                unrelated_format = QTextCharFormat()
                unrelated_format.setProperty(unrelated_property, True)
                unrelated_range = QTextLayout.FormatRange()
                unrelated_range.start = 0
                unrelated_range.length = 1
                unrelated_range.format = unrelated_format
                block_layout.setFormats(
                    list(block_layout.formats()) + [unrelated_range]
                )
                item.layout.reLayout()

                before = _geometry_snapshot(item)
                before_html = item.toHtml()
                before_pixels = _render_scene(item)
                item.set_export_effect_render(True)
                try:
                    before_export_pixels = _render_scene(item)
                finally:
                    item.set_export_effect_render(False)
                self.assertEqual(
                    _layout_property_range_count(item, gradient_property),
                    0,
                )
                self.assertEqual(
                    _layout_property_range_count(item, unrelated_property),
                    1,
                )

                if transition == 'preview-clear':
                    self.assertTrue(
                        item.set_text_transform(
                            glyph_slant_angle=45.0,
                            preview=True,
                        )
                    )
                elif transition == 'commit-zero':
                    self.assertTrue(
                        item.set_text_transform(glyph_slant_angle=45.0)
                    )
                else:
                    stack = QUndoStack()
                    stack.push(
                        SetTextTransformCommand(
                            [item],
                            [(1.0, 1.0, 0.0, 0.0)],
                            [(1.0, 1.0, 0.0, 45.0)],
                        )
                    )
                _render_scene(item)
                self.assertGreater(
                    _layout_property_range_count(item, gradient_property),
                    0,
                )

                if transition == 'preview-clear':
                    self.assertTrue(item.clear_text_transform_preview())
                elif transition == 'commit-zero':
                    self.assertTrue(
                        item.set_text_transform(glyph_slant_angle=0.0)
                    )
                else:
                    stack.undo()

                self.assertEqual(_geometry_snapshot(item), before)
                self.assertEqual(item.toHtml(), before_html)
                self.assertEqual(
                    _layout_property_range_count(item, gradient_property),
                    0,
                )
                self.assertEqual(
                    _layout_property_range_count(item, unrelated_property),
                    1,
                )
                np.testing.assert_array_equal(
                    _render_scene(item),
                    before_pixels,
                )
                item.set_export_effect_render(True)
                try:
                    after_export_pixels = _render_scene(item)
                finally:
                    item.set_export_effect_render(False)
                np.testing.assert_array_equal(
                    after_export_pixels,
                    before_export_pixels,
                )

    def test_box_only_neutral_restore_rebuilds_effects_and_gradient_state(self):
        gradient_property = textitem_module.GRADIENT_LAYOUT_FORMAT_PROPERTY
        unrelated_property = gradient_property + 1
        for transition in ('preview-clear', 'commit-zero', 'undo'):
            with self.subTest(transition=transition):
                item = _make_item(
                    text='BOX EFFECT',
                    gradient=True,
                    shadow_radius=0.01,
                    shadow_strength=1.0,
                )
                block_layout = item.document().firstBlock().layout()
                unrelated_format = QTextCharFormat()
                unrelated_format.setProperty(unrelated_property, True)
                unrelated_range = QTextLayout.FormatRange()
                unrelated_range.start = 0
                unrelated_range.length = 1
                unrelated_range.format = unrelated_format
                block_layout.setFormats(
                    list(block_layout.formats()) + [unrelated_range]
                )
                item.layout.reLayout()

                before = _geometry_snapshot(item)
                before_html = item.toHtml()
                before_pixels = _render_scene(item)
                item.set_export_effect_render(True)
                try:
                    before_export_pixels = _render_scene(item)
                finally:
                    item.set_export_effect_render(False)

                if transition == 'preview-clear':
                    self.assertTrue(
                        item.set_text_transform(
                            horizontal_scale=1.5,
                            preview=True,
                        )
                    )
                elif transition == 'commit-zero':
                    self.assertTrue(
                        item.set_text_transform(horizontal_scale=1.5)
                    )
                else:
                    stack = QUndoStack()
                    stack.push(
                        SetTextTransformCommand(
                            [item],
                            [(1.0, 1.0, 0.0, 0.0)],
                            [(1.5, 1.0, 0.0, 0.0)],
                        )
                    )

                _render_scene(item)
                self.assertIsNotNone(item.background_pixmap)
                active_cache_key = item.background_pixmap.cacheKey()
                self.assertNotEqual(item.padding(), before[2])
                self.assertGreater(
                    _layout_property_range_count(item, gradient_property),
                    0,
                )

                if transition == 'preview-clear':
                    self.assertTrue(item.clear_text_transform_preview())
                elif transition == 'commit-zero':
                    self.assertTrue(
                        item.set_text_transform(horizontal_scale=1.0)
                    )
                else:
                    stack.undo()

                self.assertEqual(_geometry_snapshot(item), before)
                self.assertIsNone(item._text_transform_entry_padding)
                self.assertEqual(item.toHtml(), before_html)
                self.assertEqual(
                    _layout_property_range_count(item, gradient_property),
                    0,
                )
                self.assertEqual(
                    _layout_property_range_count(item, unrelated_property),
                    1,
                )
                self.assertIsNotNone(item.background_pixmap)
                self.assertNotEqual(
                    item.background_pixmap.cacheKey(),
                    active_cache_key,
                )
                np.testing.assert_array_equal(
                    _render_scene(item),
                    before_pixels,
                )
                item.set_export_effect_render(True)
                try:
                    after_export_pixels = _render_scene(item)
                finally:
                    item.set_export_effect_render(False)
                np.testing.assert_array_equal(
                    after_export_pixels,
                    before_export_pixels,
                )

    def test_staged_box_glyph_neutral_restore_removes_gradient_marker(self):
        gradient_property = textitem_module.GRADIENT_LAYOUT_FORMAT_PROPERTY
        unrelated_property = gradient_property + 1
        item = _make_item(text='STAGED GRADIENT', gradient=True)
        block_layout = item.document().firstBlock().layout()
        unrelated_format = QTextCharFormat()
        unrelated_format.setProperty(unrelated_property, True)
        unrelated_range = QTextLayout.FormatRange()
        unrelated_range.start = 0
        unrelated_range.length = 1
        unrelated_range.format = unrelated_format
        block_layout.setFormats(
            list(block_layout.formats()) + [unrelated_range]
        )
        item.layout.reLayout()
        before = _geometry_snapshot(item)
        before_html = item.toHtml()
        before_pixels = _render_scene(item)

        self.assertTrue(
            item.set_text_transform(
                horizontal_scale=1.5,
                slant_angle=10.0,
                glyph_slant_angle=45.0,
            )
        )
        _render_scene(item)
        self.assertTrue(item.set_text_transform(glyph_slant_angle=0.0))
        self.assertGreater(
            _layout_property_range_count(item, gradient_property),
            0,
        )
        self.assertTrue(
            item.set_text_transform(
                horizontal_scale=1.0,
                slant_angle=0.0,
            )
        )

        self.assertEqual(_geometry_snapshot(item), before)
        self.assertEqual(item.toHtml(), before_html)
        self.assertEqual(
            _layout_property_range_count(item, gradient_property),
            0,
        )
        self.assertEqual(
            _layout_property_range_count(item, unrelated_property),
            1,
        )
        np.testing.assert_array_equal(_render_scene(item), before_pixels)

    def test_loaded_box_only_transform_uses_neutral_effect_fallback(self):
        effect_kwargs = {
            'gradient': True,
            'shadow_radius': 0.01,
            'shadow_strength': 1.0,
        }
        neutral = _make_item(text='LOADED BOX', **effect_kwargs)
        expected = _geometry_snapshot(neutral)
        expected_pixels = _render_scene(neutral)
        neutral.set_export_effect_render(True)
        try:
            expected_export_pixels = _render_scene(neutral)
        finally:
            neutral.set_export_effect_render(False)
        item = _make_item(
            text='LOADED BOX',
            box_slant=10.0,
            **effect_kwargs,
        )
        _render_scene(item)
        active_cache_key = item.background_pixmap.cacheKey()

        self.assertIsNotNone(item._text_transform_entry_padding)
        self.assertTrue(item.set_text_transform(slant_angle=0.0))
        self.assertEqual(_geometry_snapshot(item), expected)
        self.assertIsNotNone(item.background_pixmap)
        self.assertNotEqual(
            item.background_pixmap.cacheKey(),
            active_cache_key,
        )
        self.assertEqual(
            _layout_property_range_count(
                item,
                textitem_module.GRADIENT_LAYOUT_FORMAT_PROPERTY,
            ),
            0,
        )
        np.testing.assert_array_equal(_render_scene(item), expected_pixels)
        item.set_export_effect_render(True)
        try:
            actual_export_pixels = _render_scene(item)
        finally:
            item.set_export_effect_render(False)
        np.testing.assert_array_equal(
            actual_export_pixels,
            expected_export_pixels,
        )

    def test_loaded_and_staggered_transforms_restore_neutral_padding(self):
        for effect_kwargs in (
            {},
            {
                'stroke_width': 0.2,
                'shadow_radius': 0.25,
                'shadow_strength': 0.9,
            },
        ):
            with self.subTest(loaded_effects=effect_kwargs):
                neutral = _make_item(text='TEST', **effect_kwargs)
                item = _make_item(
                    text='TEST',
                    glyph_slant=45.0,
                    **effect_kwargs,
                )
                self.assertTrue(
                    item.set_text_transform(glyph_slant_angle=0.0)
                )
                self.assertEqual(
                    _geometry_snapshot(item),
                    _geometry_snapshot(neutral),
                )

        for effect_kwargs, clear_effect in (
            (
                {'stroke_width': 0.5},
                lambda target: target.setStrokeWidth(0.0),
            ),
            (
                {'shadow_radius': 0.25, 'shadow_strength': 0.0},
                lambda target: target.setBGAttribute(
                    'shadow_radius', 0.0, repaint=False
                ),
            ),
        ):
            with self.subTest(loaded_then_clear_effect=effect_kwargs):
                neutral = _make_item(text='TEST', **effect_kwargs)
                clear_effect(neutral)
                item = _make_item(
                    text='TEST',
                    glyph_slant=45.0,
                    **effect_kwargs,
                )
                clear_effect(item)
                self.assertTrue(
                    item.set_text_transform(glyph_slant_angle=0.0)
                )
                self.assertEqual(
                    _geometry_snapshot(item),
                    _geometry_snapshot(neutral),
                )

        item = _make_item(text='fi ffi e\u0301 漢字')
        before = _geometry_snapshot(item)
        self.assertTrue(
            item.set_text_transform(
                horizontal_scale=1.5,
                glyph_slant_angle=45.0,
            )
        )
        self.assertTrue(item.set_text_transform(glyph_slant_angle=0.0))
        self.assertEqual(
            item.fontformat.text_transform,
            (1.5, 1.0, 0.0, 0.0),
        )
        self.assertTrue(item.set_text_transform(horizontal_scale=1.0))
        self.assertEqual(_geometry_snapshot(item), before)

    def test_ligature_combining_cjk_and_native_italic_keep_layout(self):
        item = _make_item(
            text='fi ffi e\u0301 漢字', italic=True, box_slant=85.0, angle=27.0
        )
        before = _line_snapshot(item)
        logical_rect = item.absBoundingRect(qrect=True)
        visual_polygon = _polygon_snapshot(item.visual_polygon_in_scene())
        matrix_before = QTransform(item.transform())
        self.assertTrue(item.set_text_transform(glyph_slant_angle=45.0))

        self.assertEqual(_line_snapshot(item), before)
        self.assertEqual(item.absBoundingRect(qrect=True), logical_rect)
        for actual, expected in zip(
            _polygon_snapshot(item.visual_polygon_in_scene()), visual_polygon
        ):
            self.assertAlmostEqual(actual[0], expected[0], places=9)
            self.assertAlmostEqual(actual[1], expected[1], places=9)
        self.assertEqual(item.transform().m11(), matrix_before.m11())
        self.assertEqual(item.transform().m12(), matrix_before.m12())
        self.assertEqual(item.transform().m21(), matrix_before.m21())
        self.assertEqual(item.transform().m22(), matrix_before.m22())
        self.assertFalse(item.layout.glyphInkBounds().isEmpty())
        inverse, invertible = item.transform().inverted()
        self.assertTrue(invertible)
        for value in (
            item.transform().m11(),
            item.transform().m12(),
            item.transform().m21(),
            item.transform().m22(),
            inverse.m11(),
            inverse.m22(),
        ):
            self.assertTrue(math.isfinite(value))

        # Rendering through the live layout is enough to exercise the custom
        # glyph path without allocating an image as wide as the box shear.
        self.assertGreater(int((_paint_live_layout(item)[..., 3] > 0).sum()), 20)

    def test_empty_and_whitespace_documents_keep_logical_advance(self):
        empty = _make_item(text='')
        self.assertTrue(empty.layout.glyphInkBounds().isEmpty())

        spaces = _make_item(text='   ', glyph_slant=20.0)
        lines = _line_snapshot(spaces)
        self.assertTrue(lines)
        self.assertGreater(lines[0][4], 0.0)
        self.assertTrue(spaces.layout.glyphInkBounds().isEmpty())

    def test_bounds_cache_invalidates_only_when_angle_or_layout_changes(self):
        item = _make_item(text='Bounds', glyph_slant=12.0)
        first = item.layout.glyphInkBounds()
        self.assertFalse(first.isEmpty())
        self.assertEqual(len(item.layout._glyph_bounds_cache), 1)
        self.assertEqual(item.layout.glyphInkBounds(), first)
        self.assertEqual(len(item.layout._glyph_bounds_cache), 1)

        item.set_text_transform(glyph_slant_angle=-12.0)
        self.assertEqual(len(item.layout._glyph_bounds_cache), 0)
        second = item.layout.glyphInkBounds()
        self.assertNotEqual(second, first)
        self.assertEqual(len(item.layout._glyph_bounds_cache), 1)

        generation = item.layout.layout_generation
        item.setPlainText('Bounds changed')
        self.assertGreater(item.layout.layout_generation, generation)
        item.layout.glyphInkBounds()
        self.assertEqual(len(item.layout._glyph_bounds_cache), 1)

    def test_paint_span_merging_uses_additional_order_then_selection(self):
        item = _make_item(text='AB')
        block = item.document().firstBlock()
        line = block.layout().lineAt(0)

        blue = QTextLayout.FormatRange()
        blue.start = 0
        blue.length = 2
        blue.format.setForeground(QColor('blue'))
        yellow = QTextLayout.FormatRange()
        yellow.start = 1
        yellow.length = 1
        yellow.format.setForeground(QColor('yellow'))

        normal = resolve_paint_spans(block, line, (blue, yellow))
        self.assertEqual(
            [span.char_format.foreground().color().name() for span in normal],
            [QColor('blue').name(), QColor('yellow').name()],
        )

        selection = QAbstractTextDocumentLayout.Selection()
        selection.cursor = QTextCursor(item.document())
        selection.cursor.setPosition(block.position() + 1)
        selection.cursor.setPosition(
            block.position() + 2, QTextCursor.MoveMode.KeepAnchor
        )
        selection.format.setForeground(QColor('green'))
        selected = resolve_paint_spans(
            block, line, (blue, yellow), selection
        )
        self.assertEqual(
            [span.char_format.foreground().color().name() for span in selected],
            [QColor('blue').name(), QColor('green').name()],
        )


if __name__ == '__main__':
    unittest.main()
