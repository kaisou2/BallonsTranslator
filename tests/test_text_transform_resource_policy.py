import math
import os
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np
import cv2

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy import API_NAME, QT_VERSION
from qtpy.QtCore import QPointF, QRectF, Qt
from qtpy.QtGui import QColor, QFontDatabase, QImage, QPainter, QPixmap
from qtpy.QtWidgets import QApplication, QGraphicsItem

from ballontranslator.utils import shared as C

C.FLAG_QT6 = QT_VERSION.startswith('6')
C.USE_PYSIDE6 = API_NAME == 'PySide6'

from ballontranslator.ui import (
    text_glyph_renderer,
    textitem as textitem_module,
)
from ballontranslator.ui.canvas import Canvas
from ballontranslator.ui.textitem import (
    EFFECT_CACHE_MAX_BYTES,
    EFFECT_CACHE_MAX_DIMENSION,
    EFFECT_CACHE_MAX_PIXELS,
    EFFECT_CACHE_MAX_SCALE,
    EFFECT_RASTER_GUARD,
    EFFECT_TILE_MAX_EDGE,
    EffectRasterAllocationError,
    TextBlkItem,
    plan_effect_raster,
)
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
    transform=(1.0, 1.0, 0.0, 0.0),
    angle=0.0,
    stroke_width=0.0,
    shadow_radius=0.0,
    shadow_strength=0.0,
    shadow_offset=(0.0, 0.0),
    gradient=False,
):
    font_format = FontFormat(
        font_family=_FONT_FAMILY or 'Sans Serif',
        font_size=38,
        frgb=[0, 180, 0],
        srgb=[220, 0, 0],
        horizontal_scale=transform[0],
        vertical_scale=transform[1],
        slant_angle=transform[2],
        glyph_slant_angle=transform[3],
        stroke_width=stroke_width,
        shadow_radius=shadow_radius,
        shadow_strength=shadow_strength,
        shadow_offset=list(shadow_offset),
        shadow_color=[0, 0, 180],
        gradient_enabled=gradient,
        gradient_start_color=[255, 180, 0],
        gradient_end_color=[0, 80, 255],
    )
    block = TextBlock(
        xyxy=[30, 30, 290, 190],
        _bounding_rect=[30, 30, 260, 160],
        translation='RESOURCE',
        angle=angle,
        fontformat=font_format,
    )
    return TextBlkItem(block)


def _transparent_image(width=512, height=384):
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    return image


def _alpha(image):
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    size = converted.width() * converted.height() * 4
    bits = converted.bits()
    if hasattr(bits, 'asstring'):
        raw = bits.asstring(size)
    else:
        raw = bits.tobytes()
    return np.frombuffer(raw, dtype=np.uint8).reshape(
        converted.height(), converted.width(), 4
    )[..., 3]


class EffectRasterPlannerTests(unittest.TestCase):

    def assertPlanWithinCaps(self, plan):
        if plan.mode == 'full':
            self.assertLessEqual(plan.tier, EFFECT_CACHE_MAX_SCALE)
            self.assertLessEqual(plan.pixel_width, EFFECT_CACHE_MAX_DIMENSION)
            self.assertLessEqual(plan.pixel_height, EFFECT_CACHE_MAX_DIMENSION)
            self.assertLessEqual(
                plan.pixel_width * plan.pixel_height,
                EFFECT_CACHE_MAX_PIXELS,
            )
            self.assertLessEqual(
                plan.pixel_width * plan.pixel_height * 4,
                EFFECT_CACHE_MAX_BYTES,
            )
        else:
            self.assertEqual(plan.mode, 'tiles')
            self.assertLessEqual(plan.tile_edge, EFFECT_TILE_MAX_EDGE)
            self.assertLessEqual(plan.tile_edge, EFFECT_CACHE_MAX_DIMENSION)
            self.assertLessEqual(
                plan.tile_edge * plan.tile_edge,
                EFFECT_CACHE_MAX_PIXELS,
            )

    def test_uses_largest_bounded_power_of_two_not_above_request(self):
        cases = (
            ((100.0, 80.0, 99.0), ('full', 8.0)),
            ((100.0, 80.0, 6.0), ('full', 4.0)),
            ((100.0, 80.0, 3.0), ('full', 2.0)),
            ((100.0, 80.0, 1.5), ('full', 1.0)),
            ((1000.0, 1000.0, 8.0), ('full', 2.0)),
        )
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                plan = plan_effect_raster(*arguments)
                self.assertEqual((plan.mode, plan.tier), expected)
                self.assertPlanWithinCaps(plan)

    def test_full_surface_boundary_and_tile_fallback_are_bounded(self):
        full = plan_effect_raster(2048, 2048, 1.0)
        self.assertEqual(full.mode, 'full')
        self.assertEqual(
            full.pixel_width * full.pixel_height, EFFECT_CACHE_MAX_PIXELS
        )
        self.assertPlanWithinCaps(full)

        for dimensions in ((4096, 2048), (8193, 1), (10000, 10000)):
            with self.subTest(dimensions=dimensions):
                tiled = plan_effect_raster(*dimensions, 8.0)
                self.assertEqual(tiled.mode, 'tiles')
                self.assertPlanWithinCaps(tiled)

    def test_zero_negative_and_fractional_inputs_never_make_zero_pixmap(self):
        for dimensions in ((0, 0), (-5, 20), (0.01, 0.01)):
            with self.subTest(dimensions=dimensions):
                plan = plan_effect_raster(*dimensions, 0.1)
                self.assertEqual(plan.mode, 'full')
                self.assertEqual(plan.tier, 1.0)
                self.assertGreaterEqual(plan.pixel_width, 1)
                self.assertGreaterEqual(plan.pixel_height, 1)
                self.assertPlanWithinCaps(plan)


@unittest.skipUnless(_FONT_FAMILY, 'No usable font is available for raster tests')
class EffectRasterItemTests(unittest.TestCase):

    def _paint_effects(self, item, exposed=None):
        image = _transparent_image()
        painter = QPainter(image)
        try:
            painter.translate(-item.boundingRect().topLeft())
            item._draw_effects(
                painter,
                item.boundingRect() if exposed is None else exposed,
            )
        finally:
            painter.end()
        return image

    def test_device_scale_is_singular_value_capped_at_eight(self):
        image = _transparent_image(16, 16)
        painter = QPainter(image)
        try:
            painter.scale(1000.0, 0.01)
            self.assertEqual(item_scale := TextBlkItem._paint_device_scale(painter), 8.0)
            self.assertLessEqual(item_scale, EFFECT_CACHE_MAX_SCALE)
        finally:
            painter.end()

    def test_cache_policy_has_one_owner_and_glyph_only_keeps_device_cache(self):
        item = _make_item(transform=(1.0, 1.0, 0.0, 20.0))
        self.assertEqual(
            item.cacheMode(), QGraphicsItem.CacheMode.DeviceCoordinateCache
        )

        item.set_text_transform(slant_angle=85.0)
        self.assertEqual(item.cacheMode(), QGraphicsItem.CacheMode.NoCache)
        item.set_text_transform(slant_angle=0.0)
        self.assertEqual(
            item.cacheMode(), QGraphicsItem.CacheMode.DeviceCoordinateCache
        )

        item.setRotation(27.0)
        self.assertEqual(
            item.cacheMode(), QGraphicsItem.CacheMode.DeviceCoordinateCache
        )
        item.setRotation(0.0)
        self.assertEqual(
            item.cacheMode(), QGraphicsItem.CacheMode.DeviceCoordinateCache
        )
        item.startEdit()
        self.assertEqual(item.cacheMode(), QGraphicsItem.CacheMode.NoCache)
        item.endEdit(keep_focus=False)
        self.assertEqual(
            item.cacheMode(), QGraphicsItem.CacheMode.DeviceCoordinateCache
        )

    def test_box_preview_reuses_active_local_effect_cache(self):
        item = _make_item(stroke_width=0.18)
        cached = item.background_pixmap
        cached_scale = item._background_pixmap_scale
        generation = item._effect_cache_generation
        rendered_generation = item._effect_cache_rendered_generation

        with mock.patch.object(
            item,
            '_render_effect_surface',
            wraps=item._render_effect_surface,
        ) as render:
            self.assertTrue(
                item.set_text_transform(slant_angle=85.0, preview=True)
            )
            self.assertEqual(item._effect_cache_generation, generation)
            self.assertEqual(
                item._effect_cache_rendered_generation, rendered_generation
            )
            self.assertIs(item.background_pixmap, cached)
            self.assertEqual(item._background_pixmap_scale, cached_scale)
            self._paint_effects(item)
            # Neutral items deliberately use the BASE background path. Entering
            # the active renderer seeds its own cache once.
            self.assertEqual(render.call_count, 1)

            self.assertTrue(
                item.set_text_transform(slant_angle=70.0, preview=True)
            )
            self._paint_effects(item)
            self.assertEqual(render.call_count, 1)

            self.assertTrue(item.clear_text_transform_preview())
            self.assertEqual(item._effect_cache_generation, generation)
            self._paint_effects(item)
            self.assertEqual(render.call_count, 1)

    def test_multiple_glyph_previews_coalesce_until_one_actual_paint(self):
        item = _make_item(stroke_width=0.18)
        initial_generation = item._effect_cache_generation

        with mock.patch.object(
            item,
            '_render_effect_surface',
            wraps=item._render_effect_surface,
        ) as render:
            for angle in (5.0, 12.0, 30.0):
                self.assertTrue(
                    item.set_text_transform(
                        glyph_slant_angle=angle, preview=True
                    )
                )
            self.assertEqual(render.call_count, 0)
            self.assertEqual(
                item._effect_cache_generation, initial_generation + 3
            )
            self.assertTrue(item._effect_cache_dirty)
            self.assertIsNone(item.background_pixmap)

            first_paint = self._paint_effects(item)
            self.assertEqual(render.call_count, 1)
            self.assertGreater(int(_alpha(first_paint).max()), 0)
            self.assertFalse(item._effect_cache_dirty)
            self.assertEqual(
                item._effect_cache_rendered_generation,
                item._effect_cache_generation,
            )

            self._paint_effects(item)
            self.assertEqual(render.call_count, 1)

    def test_stale_generation_is_never_drawn_with_new_fill(self):
        item = _make_item(
            transform=(1.2, 0.9, 12.0, 20.0), stroke_width=0.18
        )
        br = item.boundingRect()
        stale = QPixmap(
            max(1, math.ceil(br.width())),
            max(1, math.ceil(br.height())),
        )
        stale.fill(QColor(255, 0, 255, 255))
        item.background_pixmap = stale
        item._background_pixmap_scale = 1.0
        item._effect_cache_generation = 9
        item._effect_cache_rendered_generation = 8
        item._effect_cache_dirty = False
        item._effect_direct_stroke = False
        item.pre_editing = True  # Suppress rebuild, as during live IME.

        image = self._paint_effects(item)
        self.assertEqual(int(_alpha(image).max()), 0)
        self.assertIs(item.background_pixmap, stale)

    def test_interactive_allocation_failure_degrades_but_export_is_fatal(self):
        item = _make_item(
            transform=(1.2, 0.9, 12.0, 20.0),
            stroke_width=0.18,
            shadow_radius=0.2,
            shadow_strength=0.8,
        )
        model = item.fontformat.text_transform
        item._mark_effect_cache_dirty()
        failure = EffectRasterAllocationError('synthetic allocation failure')

        with mock.patch.object(item, '_new_effect_pixmap', side_effect=failure):
            item.repaint_background(4.0)
        self.assertIsNone(item.background_pixmap)
        self.assertTrue(item._effect_direct_stroke)
        self.assertFalse(item.repainting)
        self.assertEqual(item.fontformat.text_transform, model)
        self.assertEqual(
            item._effect_allocation_warning_generation,
            item._effect_cache_generation,
        )

        item._mark_effect_cache_dirty()
        item.set_export_effect_render(True)
        try:
            with mock.patch.object(item, '_new_effect_pixmap', side_effect=failure):
                item.repaint_background(4.0)
                self.assertTrue(item._force_effect_tiles)
                with self.assertRaisesRegex(
                    EffectRasterAllocationError, 'synthetic allocation failure'
                ):
                    self._paint_effects(item)
        finally:
            item.set_export_effect_render(False)
        self.assertFalse(item.repainting)
        self.assertIsNone(item.background_pixmap)
        self.assertEqual(item.fontformat.text_transform, model)

    def test_export_full_failures_retry_bounded_tiles_successfully(self):
        item = _make_item(
            transform=(1.2, 0.9, 12.0, 20.0),
            stroke_width=0.18,
            shadow_radius=0.2,
            shadow_strength=0.8,
        )
        # Keep tier 1 policy-valid while making each bounded tile surface
        # observably smaller than the attempted full local surface.
        item.setRect(
            QRectF(30.0, 30.0, 3000.0, 200.0), repaint=False
        )
        item._mark_effect_cache_dirty()
        original = item._new_effect_pixmap
        full_calls = 0
        tile_calls = 0
        full_rect = QRectF(item.boundingRect())

        def fail_full_allocations(scale=1.0, surface_rect=None):
            nonlocal full_calls, tile_calls
            rect = full_rect if surface_rect is None else QRectF(surface_rect)
            if rect == full_rect:
                full_calls += 1
                raise EffectRasterAllocationError('full surface failed')
            tile_calls += 1
            return original(scale, surface_rect)

        item.set_export_effect_render(True)
        try:
            with mock.patch.object(
                item,
                '_new_effect_pixmap',
                side_effect=fail_full_allocations,
            ):
                # Scale 4 exercises the requested tier and tier-1 full retry.
                item.repaint_background(4.0)
                self.assertTrue(item._force_effect_tiles)
                image = self._paint_effects(item)
        finally:
            item.set_export_effect_render(False)

        self.assertEqual(full_calls, 2)
        self.assertGreater(tile_calls, 0)
        self.assertGreater(int(_alpha(image).max()), 0)
        self.assertFalse(item._force_effect_tiles)
        self.assertIsNone(item.export_effect_error)

    def test_cv2_error_uses_interactive_fallback_and_fails_export_after_tiles(self):
        item = _make_item(
            transform=(1.2, 0.9, 12.0, 20.0),
            stroke_width=0.18,
            shadow_radius=0.2,
            shadow_strength=0.8,
        )
        failure = cv2.error('synthetic OpenCV allocation failure')

        item._mark_effect_cache_dirty()
        with mock.patch.object(
            textitem_module, 'apply_shadow_effect', side_effect=failure
        ):
            item.repaint_background(1.0)
        self.assertIsNone(item.background_pixmap)
        self.assertTrue(item._effect_direct_stroke)
        self.assertEqual(
            item._effect_allocation_warning_generation,
            item._effect_cache_generation,
        )

        item._mark_effect_cache_dirty()
        item.set_export_effect_render(True)
        try:
            with mock.patch.object(
                textitem_module, 'apply_shadow_effect', side_effect=failure
            ):
                item.repaint_background(1.0)
                self.assertTrue(item._force_effect_tiles)
                with self.assertRaisesRegex(
                    EffectRasterAllocationError,
                    'synthetic OpenCV allocation failure',
                ):
                    self._paint_effects(item)
        finally:
            item.set_export_effect_render(False)
        self.assertFalse(item.repainting)
        self.assertIsNone(item.background_pixmap)

    def test_export_glyph_capture_failure_retries_without_stale_error(self):
        item = _make_item(
            transform=(1.0, 1.0, 0.0, 20.0),
            stroke_width=0.2,
        )
        item._mark_effect_cache_dirty()
        original_pixels = text_glyph_renderer.pixmap2ndarray
        conversion_calls = 0

        def fail_first_conversion(*args, **kwargs):
            nonlocal conversion_calls
            conversion_calls += 1
            if conversion_calls == 1:
                return None
            return original_pixels(*args, **kwargs)

        item.set_export_effect_render(True)
        try:
            with mock.patch.object(
                text_glyph_renderer,
                '_raw_font_has_color_glyphs',
                return_value=True,
            ), mock.patch.object(
                text_glyph_renderer,
                '_native_color_glyph_image',
                return_value=(None, QRectF(), False),
            ), mock.patch.object(
                text_glyph_renderer,
                'pixmap2ndarray',
                side_effect=fail_first_conversion,
            ), mock.patch.object(
                textitem_module.LOGGER,
                'warning',
            ) as warning:
                item.repaint_background(4.0)

            self.assertGreaterEqual(conversion_calls, 3)
            warning.assert_called_once()
            self.assertIsNotNone(item.background_pixmap)
            self.assertEqual(item._background_pixmap_scale, 1.0)
            self.assertFalse(item._force_effect_tiles)
            self.assertFalse(item._effect_cache_dirty)
            self.assertIsNone(item.export_effect_error)
        finally:
            item.set_export_effect_render(False)

        self.assertFalse(item.repainting)
        self.assertFalse(item._capturing_effect_surface)
        self.assertIsNone(item._effect_surface_raster_error)
        self.assertIsNone(item.export_effect_error)

    def test_export_glyph_conversion_failure_raises_after_bounded_retries(self):
        item = _make_item(
            transform=(1.0, 1.0, 0.0, 20.0),
            stroke_width=0.2,
        )
        model = item.fontformat.text_transform
        item._mark_effect_cache_dirty()

        item.set_export_effect_render(True)
        try:
            with mock.patch.object(
                text_glyph_renderer,
                '_raw_font_has_color_glyphs',
                return_value=True,
            ), mock.patch.object(
                text_glyph_renderer,
                '_native_color_glyph_image',
                return_value=(None, QRectF(), False),
            ), mock.patch.object(
                text_glyph_renderer,
                'pixmap2ndarray',
                return_value=None,
            ), mock.patch.object(
                textitem_module.LOGGER,
                'warning',
            ) as warning:
                item.repaint_background(4.0)
                self.assertTrue(item._force_effect_tiles)
                with self.assertRaisesRegex(
                    EffectRasterAllocationError,
                    'unable to access pathless glyph pixels',
                ):
                    self._paint_effects(item)

            warning.assert_called_once()
        finally:
            item.set_export_effect_render(False)

        self.assertFalse(item.repainting)
        self.assertFalse(item._capturing_effect_surface)
        self.assertIsNone(item._effect_surface_raster_error)
        self.assertIsNone(item.background_pixmap)
        self.assertEqual(item.fontformat.text_transform, model)

    def test_canvas_export_records_glyph_failure_and_returns_no_partial(self):
        width, height = 380, 280
        canvas = Canvas()
        canvas.imgtrans_proj = SimpleNamespace(
            img_valid=True,
            inpainted_valid=True,
            inpainted_array=np.zeros(
                (height, width, 4), dtype=np.uint8
            ),
        )
        transparent = QPixmap(width, height)
        transparent.fill(Qt.GlobalColor.transparent)
        canvas.inpaintLayer.setPixmap(transparent)
        canvas.textLayer.setPixmap(transparent)
        canvas.baseLayer.setRect(QRectF(0.0, 0.0, width, height))
        canvas.setSceneRect(QRectF(0.0, 0.0, width, height))
        item = _make_item(transform=(1.0, 1.0, 0.0, 20.0))
        item.setParentItem(canvas.textLayer)
        canvas._set_scene_scale(0.75)
        canvas.inpaintLayer.show()
        canvas.textLayer.setOpacity(0.35)
        canvas.textLayer.hide()
        returned = []

        with mock.patch.object(
            text_glyph_renderer,
            '_raw_font_has_color_glyphs',
            return_value=True,
        ), mock.patch.object(
            text_glyph_renderer,
            'QImage',
            return_value=QImage(),
        ), mock.patch.object(
            textitem_module.LOGGER,
            'warning',
        ) as warning:
            with self.assertRaisesRegex(
                EffectRasterAllocationError,
                'unable to allocate pathless glyph image',
            ):
                returned.append(canvas.render_result_img())

        self.assertEqual(returned, [])
        warning.assert_called_once()
        self.assertIsInstance(
            item.export_effect_error, EffectRasterAllocationError
        )
        self.assertIn(
            'unable to allocate pathless glyph image',
            str(item.export_effect_error),
        )
        self.assertFalse(item._export_effect_render)
        self.assertFalse(item._in_graphics_paint)
        self.assertAlmostEqual(canvas.scale_factor, 0.75)
        self.assertTrue(canvas.inpaintLayer.isVisible())
        self.assertFalse(canvas.textLayer.isVisible())
        self.assertAlmostEqual(canvas.textLayer.opacity(), 0.35)

    def test_huge_stroke_uses_direct_vector_path_without_tile_allocation(self):
        item = _make_item(
            transform=(1.2, 0.9, 12.0, 20.0), stroke_width=120.0
        )
        overlap = item._stroke_outset() + EFFECT_RASTER_GUARD
        self.assertGreaterEqual(
            2 * math.ceil(overlap), EFFECT_TILE_MAX_EDGE
        )
        item._mark_effect_cache_dirty()

        with mock.patch.object(
            item,
            '_render_effect_surface',
            side_effect=AssertionError('huge vector stroke allocated a tile'),
        ) as render, mock.patch.object(
            item,
            '_paint_live_layout',
            wraps=item._paint_live_layout,
        ) as live:
            self._paint_effects(item)

        self.assertEqual(render.call_count, 0)
        self.assertEqual(live.call_count, 1)
        self.assertTrue(item._effect_direct_stroke)
        context = live.call_args.args[1]
        self.assertTrue(context.selections)
        self.assertTrue(
            all(
                bool(
                    selection.format.property(
                        textitem_module.GLYPH_STROKE_FORMAT_PROPERTY
                    )
                )
                for selection in context.selections
            )
        )

    def test_export_many_visible_tiles_stream_with_two_entry_cache(self):
        item = _make_item(
            transform=(1.2, 0.9, 12.0, 20.0), stroke_width=0.2
        )
        item.setRect(
            QRectF(0.0, 0.0, 10000.0, 10000.0), repaint=False
        )
        plan = plan_effect_raster(
            item.boundingRect().width(), item.boundingRect().height(), 8.0
        )
        self.assertEqual(plan.mode, 'tiles')
        overlap = item._stroke_outset() + EFFECT_RASTER_GUARD
        core_edge = plan.tile_edge - 2 * math.ceil(overlap)
        visible = QRectF(
            item.boundingRect().left(),
            item.boundingRect().top(),
            core_edge * 4 - 1.0,
            min(20.0, core_edge / 2),
        )
        events = []

        class RecordingPainter:
            def hasClipping(self):
                return False

            def setRenderHint(self, *_args):
                pass

            def save(self):
                pass

            def restore(self):
                pass

            def setClipRect(self, *_args):
                pass

            def drawPixmap(self, _position, pixmap):
                events.append(('draw', pixmap))

        def fake_surface(*_args, **_kwargs):
            token = object()
            events.append(('render', token))
            return token

        item.set_export_effect_render(True)
        try:
            with mock.patch.object(
                item, '_render_effect_surface', side_effect=fake_surface
            ):
                item._draw_tiled_effects(
                    RecordingPainter(), plan, visible
                )
        finally:
            item.set_export_effect_render(False)

        self.assertGreaterEqual(len(events), 8)
        for index in range(0, len(events), 2):
            self.assertEqual(events[index][0], 'render')
            self.assertEqual(events[index + 1][0], 'draw')
            self.assertIs(events[index][1], events[index + 1][1])
        self.assertLessEqual(len(item._effect_tile_cache), 2)

    def test_interactive_late_tile_failure_never_composites_partial_stage(self):
        item = _make_item(
            transform=(1.2, 0.9, 12.0, 20.0), stroke_width=0.2
        )
        item.setRect(
            QRectF(0.0, 0.0, 10000.0, 10000.0), repaint=False
        )
        item._effect_tile_cache.clear()
        plan = plan_effect_raster(
            item.boundingRect().width(), item.boundingRect().height(), 8.0
        )
        overlap = item._stroke_outset() + EFFECT_RASTER_GUARD
        core_edge = plan.tile_edge - 2 * math.ceil(overlap)
        boundary = item.boundingRect().left() + core_edge
        visible = QRectF(
            boundary - 1.0,
            item.boundingRect().top() + 20.0,
            2.0,
            20.0,
        )
        first_tile = QPixmap(1, 1)
        first_tile.fill(QColor(255, 0, 255, 255))
        calls = 0

        def fail_second_tile(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise EffectRasterAllocationError('late second tile failure')
            return first_tile

        image = _transparent_image(8, 24)
        painter = QPainter(image)
        try:
            painter.translate(-visible.topLeft())
            with mock.patch.object(
                item,
                '_render_effect_surface',
                side_effect=fail_second_tile,
            ):
                item._draw_tiled_effects(painter, plan, visible)
        finally:
            painter.end()

        self.assertEqual(calls, 2)
        self.assertEqual(int(_alpha(image).max()), 0)
        self.assertTrue(item._effect_direct_stroke)
        self.assertLessEqual(len(item._effect_tile_cache), 2)
        self.assertEqual(len(item._effect_tile_cache), 0)
        self.assertEqual(
            item._effect_allocation_warning_generation,
            item._effect_cache_generation,
        )

    def test_tile_mode_generates_only_exposed_tiles_with_overlap(self):
        item = _make_item(
            transform=(1.2, 0.9, 12.0, 20.0),
            stroke_width=0.2,
            shadow_radius=0.2,
            shadow_strength=0.8,
            shadow_offset=(0.2, -0.2),
        )
        item.setRect(
            QRectF(0.0, 0.0, 10000.0, 10000.0),
            repaint=False,
        )
        br = item.boundingRect()
        plan = plan_effect_raster(br.width(), br.height(), 8.0)
        self.assertEqual(plan.mode, 'tiles')
        stroke_overlap = item._stroke_outset() + EFFECT_RASTER_GUARD
        core_edge = (
            plan.tile_edge - 2 * math.ceil(stroke_overlap * plan.tier)
        ) / plan.tier
        boundary_x = br.left() + core_edge
        visible = QRectF(boundary_x - 1.0, br.top() + 50.0, 2.0, 2.0)
        records = []

        def fake_surface(surface, scale, **kwargs):
            records.append((QRectF(surface), scale, kwargs))
            pixmap = QPixmap(1, 1)
            pixmap.fill(Qt.GlobalColor.transparent)
            return pixmap

        image = _transparent_image(64, 64)
        painter = QPainter(image)
        try:
            painter.translate(-visible.topLeft())
            with mock.patch.object(
                item, '_render_effect_surface', side_effect=fake_surface
            ):
                item._draw_tiled_effects(painter, plan, visible)
        finally:
            painter.end()

        self.assertEqual(len(records), 2)
        self.assertLessEqual(len(item._effect_tile_cache), 2)
        surfaces = sorted((record[0] for record in records), key=lambda rect: rect.left())
        overlap = surfaces[0].intersected(surfaces[1]).width()
        self.assertGreaterEqual(overlap, 2 * stroke_overlap - 2.0)
        for surface, scale, kwargs in records:
            self.assertEqual(scale, 1.0)
            self.assertLessEqual(math.ceil(surface.width() * scale), plan.tile_edge)
            self.assertLessEqual(math.ceil(surface.height() * scale), plan.tile_edge)
            self.assertTrue(surface.intersects(visible))
            self.assertIsNotNone(kwargs['shadow_rect'])
            self.assertGreater(kwargs['shadow_scale'], 0.0)
            self.assertLessEqual(kwargs['shadow_scale'], plan.tier)

        remote = QRectF(
            br.right() - 20.0,
            br.bottom() - 20.0,
            10.0,
            10.0,
        )
        records.clear()
        image = _transparent_image(64, 64)
        painter = QPainter(image)
        try:
            painter.translate(-remote.topLeft())
            with mock.patch.object(
                item, '_render_effect_surface', side_effect=fake_surface
            ):
                item._draw_tiled_effects(painter, plan, remote)
        finally:
            painter.end()
        self.assertEqual(len(records), 1)
        self.assertEqual(len(item._effect_tile_cache), 1)

    def test_extreme_transform_combination_allocates_only_local_bounded_surfaces(self):
        item = _make_item(
            transform=(4.0, 4.0, 85.0, 45.0),
            angle=27.0,
            stroke_width=0.18,
            shadow_radius=0.2,
            shadow_strength=0.8,
            shadow_offset=(0.25, -0.2),
            gradient=True,
        )
        allocations = []
        original = item._new_effect_pixmap

        def tracking_pixmap(scale=1.0, surface_rect=None):
            rect = item.boundingRect() if surface_rect is None else surface_rect
            allocations.append((float(scale), QRectF(rect)))
            return original(scale, surface_rect)

        with mock.patch.object(
            item, '_new_effect_pixmap', side_effect=tracking_pixmap
        ):
            item.repaint_background(8.0)

        self.assertTrue(allocations)
        for scale, rect in allocations:
            width = max(1, math.ceil(rect.width() * scale))
            height = max(1, math.ceil(rect.height() * scale))
            self.assertLessEqual(width, EFFECT_CACHE_MAX_DIMENSION)
            self.assertLessEqual(height, EFFECT_CACHE_MAX_DIMENSION)
            self.assertLessEqual(width * height, EFFECT_CACHE_MAX_PIXELS)
            self.assertLessEqual(width * height * 4, EFFECT_CACHE_MAX_BYTES)

        local_pixels = max(
            math.ceil(rect.width() * scale)
            for scale, rect in allocations
        )
        transformed_width = item.visual_bounds_in_scene().width()
        self.assertLess(local_pixels, math.ceil(transformed_width * 8.0))
        self.assertIn(
            item._background_pixmap_scale,
            (1.0, 2.0, 4.0, 8.0),
        )
        self.assertIsNotNone(item.background_pixmap)
        self.assertGreater(
            int((_alpha(item.background_pixmap.toImage()) > 0).sum()), 20
        )


if __name__ == '__main__':
    unittest.main()
