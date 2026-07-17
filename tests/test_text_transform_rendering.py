import math
import os
import unittest

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy import API_NAME, QT_VERSION
from qtpy.QtCore import QRectF, Qt
from qtpy.QtGui import QColor, QFontDatabase, QImage, QPainter
from qtpy.QtWidgets import QApplication, QGraphicsItem, QGraphicsScene

from ballontranslator.utils import shared as C

C.FLAG_QT6 = QT_VERSION.startswith('6')
C.USE_PYSIDE6 = API_NAME == 'PySide6'

from ballontranslator.ui.textitem import TextBlkItem
from ballontranslator.utils.fontformat import FontFormat
from ballontranslator.utils.textblock import TextBlock


_APP = QApplication.instance() or QApplication([])


def _render_font_family():
    windows_arial = r'C:\Windows\Fonts\arial.ttf'
    if os.path.exists(windows_arial):
        font_id = QFontDatabase.addApplicationFont(windows_arial)
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            return families[0]
    families = QFontDatabase.families()
    return families[0] if families else None


_FONT_FAMILY = _render_font_family()


def _make_item(
    *,
    transform=(1.0, 1.0, 0.0),
    angle=0.0,
    stroke_width=0.0,
    shadow_radius=0.0,
    shadow_strength=0.0,
    shadow_offset=(0.0, 0.0),
    gradient=False,
    text='TEST',
):
    font_format = FontFormat(
        font_family=_FONT_FAMILY or 'Sans Serif',
        font_size=40,
        frgb=[0, 220, 0],
        srgb=[230, 0, 0],
        stroke_width=stroke_width,
        shadow_radius=shadow_radius,
        shadow_strength=shadow_strength,
        shadow_offset=list(shadow_offset),
        shadow_color=[0, 0, 160],
        gradient_enabled=gradient,
        gradient_start_color=[255, 180, 0],
        gradient_end_color=[0, 80, 255],
        horizontal_scale=transform[0],
        vertical_scale=transform[1],
        slant_angle=transform[2],
    )
    block = TextBlock(
        xyxy=[80, 70, 300, 220],
        _bounding_rect=[80, 70, 220, 150],
        translation=text,
        angle=angle,
        fontformat=font_format,
    )
    return TextBlkItem(block)


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


def _render_item(item, source_rect, scale=1, background=None):
    scene = QGraphicsScene()
    scene.setSceneRect(source_rect)
    scene.addItem(item)
    width = max(1, math.ceil(source_rect.width() * scale))
    height = max(1, math.ceil(source_rect.height() * scale))
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent if background is None else background)
    painter = QPainter(image)
    try:
        scene.render(
            painter,
            QRectF(0, 0, width, height),
            source_rect,
        )
    finally:
        painter.end()
        scene.removeItem(item)
    return image


@unittest.skipUnless(_FONT_FAMILY, 'No usable font is available for raster tests')
class TextTransformRenderingTests(unittest.TestCase):

    def test_effect_removal_shrinks_from_zero_and_keeps_outer_transform_separate(self):
        item = _make_item(
            transform=(4.0, 0.1, 45.0),
            angle=27,
            stroke_width=0.2,
            shadow_radius=0.25,
            shadow_strength=0.9,
            shadow_offset=(-0.3, 0.2),
            gradient=True,
        )
        neutral = _make_item(
            stroke_width=0.2,
            shadow_radius=0.25,
            shadow_strength=0.9,
            shadow_offset=(-0.3, 0.2),
        )
        self.assertAlmostEqual(item.padding(), neutral.padding(), places=5)
        self.assertEqual(item.cacheMode(), QGraphicsItem.CacheMode.NoCache)
        logical_rect = item.absBoundingRect(qrect=True)
        scene_pivot = item.mapToScene(item.transformOriginPoint())
        canonical_transform = item.fontformat.text_transform
        visual_polygon = item.visual_polygon_in_scene()
        self.assertGreater(item.padding(), 0)

        item.setStrokeWidth(0)
        after_stroke = item.padding()
        item.setBGAttribute('shadow_strength', 0)
        self.assertLess(after_stroke, neutral.padding())
        self.assertEqual(item.padding(), 0)
        self.assertEqual(item.absBoundingRect(qrect=True), logical_rect)
        self.assertEqual(item.mapToScene(item.transformOriginPoint()), scene_pivot)
        self.assertEqual(item.fontformat.text_transform, canonical_transform)
        for actual, expected in zip(
            item.visual_polygon_in_scene(), visual_polygon
        ):
            self.assertAlmostEqual(actual.x(), expected.x())
            self.assertAlmostEqual(actual.y(), expected.y())
        self.assertIsNone(item.background_pixmap)

        gradient = item.get_text_gradient()
        midpoint = (gradient.start() + gradient.finalStop()) / 2
        self.assertAlmostEqual(
            midpoint.x(), item.logical_unpadded_rect().center().x()
        )
        self.assertAlmostEqual(
            midpoint.y(), item.logical_unpadded_rect().center().y()
        )

    def test_extreme_transform_rotation_has_alpha_and_resolution_parity(self):
        cases = (
            (0.1, 4.0, 45.0, 23),
            (4.0, 0.1, -45.0, -31),
        )
        for horizontal, vertical, slant, angle in cases:
            with self.subTest(case=(horizontal, vertical, slant, angle)):
                item = _make_item(
                    transform=(horizontal, vertical, slant),
                    angle=angle,
                    stroke_width=0.16,
                    shadow_radius=0.18,
                    shadow_strength=0.75,
                    shadow_offset=(0.2, -0.2),
                    text='EDGE',
                )
                bounds = item.mapToScene(item.boundingRect()).boundingRect()
                source = bounds.adjusted(-8, -8, 8, 8)
                low = _image_array(_render_item(item, source, 1))
                high = _image_array(_render_item(item, source, 2))
                low_alpha = low[..., 3] > 0
                high_alpha = high[..., 3] > 0
                self.assertGreater(int(low_alpha.sum()), 50)
                self.assertGreater(int(high_alpha.sum()), 200)
                self.assertFalse(low_alpha[0].any())
                self.assertFalse(low_alpha[-1].any())
                self.assertFalse(low_alpha[:, 0].any())
                self.assertFalse(low_alpha[:, -1].any())
                normalized_high_area = high_alpha.sum() / 4
                self.assertLess(
                    abs(
                        normalized_high_area - low_alpha.sum()
                    ) / low_alpha.sum(),
                    0.3,
                )
                self.assertGreaterEqual(item._background_pixmap_scale, 2.0)

    def test_transformed_effects_survive_opaque_nonediting_paint(self):
        item = _make_item(
            transform=(1.8, 0.65, -22.0),
            angle=17,
            stroke_width=0.2,
            shadow_radius=0.2,
            shadow_strength=0.9,
            shadow_offset=(0.25, -0.2),
            text='OPAQUE',
        )
        source = QRectF(0, 0, 520, 360)
        nonediting = _image_array(
            _render_item(item, source, 2, QColor(Qt.GlobalColor.white))
        )
        item.startEdit()
        item.clearFocus()
        editing = _image_array(
            _render_item(item, source, 2, QColor(Qt.GlobalColor.white))
        )

        counts = []
        for pixels in (nonediting, editing):
            red = (
                (pixels[..., 0] > 150)
                & (pixels[..., 1] < 100)
                & (pixels[..., 2] < 100)
            ).sum()
            blue = (
                (pixels[..., 2] > 120)
                & (pixels[..., 0] < 100)
                & (pixels[..., 1] < 120)
            ).sum()
            green = (
                (pixels[..., 1] > 140)
                & (pixels[..., 0] < 100)
                & (pixels[..., 2] < 100)
            ).sum()
            self.assertGreater(red, 100)
            self.assertGreater(blue, 100)
            self.assertGreater(green, 100)
            counts.append((red, blue, green))
        for nonediting_count, editing_count in zip(*counts):
            ratio = nonediting_count / editing_count
            self.assertGreater(ratio, 0.5)
            self.assertLess(ratio, 2.0)
        item.endEdit(keep_focus=False)


if __name__ == '__main__':
    unittest.main()
