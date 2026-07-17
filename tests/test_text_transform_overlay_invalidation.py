import os
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy.QtCore import QEvent, QPointF, QRectF, Qt
from qtpy.QtGui import (
    QImage,
    QKeyEvent,
    QPainter,
    QPixmap,
    QPolygonF,
    QRegion,
    QTextCursor,
)
from qtpy.QtWidgets import (
    QApplication,
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QWidget,
)

from ballontranslator.ui.canvas import Canvas
from ballontranslator.ui.scenetext_manager import SceneTextManager
from ballontranslator.ui.textedit_commands import RotateItemCommand
from ballontranslator.ui.texteditshapecontrol import (
    PROXY_HANDLE_VIEWPORT_INSET,
    UI_OVERLAY_ITEM_DATA_KEY,
    TextBlkShapeControl,
    TextOverlayManager,
)
from ballontranslator.ui.textitem import (
    EffectRasterAllocationError,
    TextBlkItem,
)
from ballontranslator.utils.fontformat import FontFormat
from ballontranslator.utils.textblock import TextBlock


_APP = QApplication.instance() or QApplication([])


def _make_item(*, rect=(160, 120, 260, 190), text='overlay', item_class=TextBlkItem):
    left, top, right, bottom = rect
    block = TextBlock(
        xyxy=[left, top, right, bottom],
        _bounding_rect=[left, top, right - left, bottom - top],
        translation=text,
        fontformat=FontFormat(),
    )
    return item_class(block)


def _image_bytes(image):
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    size = image.width() * image.height() * 4
    bits = image.bits()
    if hasattr(bits, 'asstring'):
        return bits.asstring(size)
    return bits.tobytes()


class TrackingViewport(QWidget):
    def __init__(self):
        super().__init__()
        self.updated_regions = []

    def update(self, *args):
        if len(args) == 1 and isinstance(args[0], QRegion):
            self.updated_regions.append(QRegion(args[0]))
        return super().update(*args)


class ExportTrackingTextItem(TextBlkItem):
    def __init__(self, block):
        self.export_effect_modes = []
        super().__init__(block)

    def set_export_effect_render(self, enabled: bool):
        self.export_effect_modes.append(bool(enabled))
        return super().set_export_effect_render(enabled)


class TextTransformOverlayInvalidationTests(unittest.TestCase):
    def _make_view(self, scene, size=(360, 280)):
        view = QGraphicsView(scene)
        viewport = TrackingViewport()
        view.setViewport(viewport)
        view.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate
        )
        view.resize(*size)
        view.show()
        _APP.processEvents()
        return view, viewport

    def test_manager_invalidates_old_and_new_device_regions_in_every_view(self):
        scene = QGraphicsScene()
        scene.setSceneRect(QRectF(0, 0, 600, 400))
        base = QGraphicsRectItem(scene.sceneRect())
        scene.addItem(base)
        view1, viewport1 = self._make_view(scene)
        view2, viewport2 = self._make_view(scene)
        view2.scale(1.25, 1.25)
        view1.centerOn(scene.sceneRect().center())
        view2.centerOn(scene.sceneRect().center())
        _APP.processEvents()

        control = TextBlkShapeControl(view1)
        control.setParentItem(base)
        manager = TextOverlayManager(scene, base, control)
        item = _make_item()
        item.setParentItem(base)
        manager.set_textblock_mode(True)
        manager.register_item(item)

        overlay = manager.overlay_for_item(item)
        self.assertIsNotNone(overlay)
        self.assertTrue(overlay.isVisible())
        self.assertFalse(overlay._selected)
        self.assertEqual(overlay.data(UI_OVERLAY_ITEM_DATA_KEY), True)
        self.assertEqual(overlay.cacheMode(), QGraphicsItem.CacheMode.NoCache)

        item.setSelected(True)
        manager.sync_overlays()
        self.assertTrue(overlay._selected)
        old_rects = {
            view: overlay.deviceTransform(view.viewportTransform())
            .mapRect(overlay.boundingRect())
            .toAlignedRect()
            for view in (view1, view2)
        }
        viewport1.updated_regions.clear()
        viewport2.updated_regions.clear()

        item.set_logical_position(item.logical_position() + QPointF(45, 24))
        manager.sync_overlays()
        new_rects = {
            view: overlay.deviceTransform(view.viewportTransform())
            .mapRect(overlay.boundingRect())
            .toAlignedRect()
            for view in (view1, view2)
        }

        for view, viewport in (
            (view1, viewport1),
            (view2, viewport2),
        ):
            with self.subTest(view=view):
                self.assertTrue(viewport.updated_regions)
                dirty = viewport.updated_regions[-1]
                self.assertTrue(dirty.contains(old_rects[view].center()))
                self.assertTrue(dirty.contains(new_rects[view].center()))
                self.assertNotEqual(dirty, QRegion(view.viewport().rect()))

        control.setBlkItem(item)
        manager.sync_overlays()
        self.assertFalse(overlay.isVisible())
        handle = control.ctrlblock_group[0]
        old_handle_rects = {
            view: handle.deviceTransform(view.viewportTransform())
            .mapRect(handle.boundingRect())
            .toAlignedRect()
            for view in (view1, view2)
        }
        viewport1.updated_regions.clear()
        viewport2.updated_regions.clear()
        item.set_logical_position(item.logical_position() + QPointF(-18, 11))
        manager.sync_overlays()
        new_handle_rects = {
            view: handle.deviceTransform(view.viewportTransform())
            .mapRect(handle.boundingRect())
            .toAlignedRect()
            for view in (view1, view2)
        }
        for view, viewport in (
            (view1, viewport1),
            (view2, viewport2),
        ):
            with self.subTest(handle_view=view):
                dirty = viewport.updated_regions[-1]
                self.assertTrue(dirty.contains(old_handle_rects[view].center()))
                self.assertTrue(dirty.contains(new_handle_rects[view].center()))

    def test_manager_batch_update_coalesces_lifecycle_invalidation(self):
        scene = QGraphicsScene()
        scene.setSceneRect(QRectF(0, 0, 600, 400))
        base = QGraphicsRectItem(scene.sceneRect())
        scene.addItem(base)
        view, viewport = self._make_view(scene)
        control = TextBlkShapeControl(view)
        control.setParentItem(base)
        manager = TextOverlayManager(scene, base, control)
        control.overlay_sync_callback = manager.sync_overlays
        first = _make_item()
        second = _make_item(rect=(310, 210, 410, 280))
        first.setParentItem(base)
        second.setParentItem(base)
        manager.set_textblock_mode(True)
        manager.register_item(first)
        old_overlay = manager.overlay_for_item(first)
        old_center = old_overlay.deviceTransform(
            view.viewportTransform()
        ).mapRect(old_overlay.boundingRect()).toAlignedRect().center()

        invalidations = []
        original_sync = manager.invalidator.sync

        def track_sync(update_geometry):
            invalidations.append(True)
            return original_sync(update_geometry)

        manager.invalidator.sync = track_sync
        viewport.updated_regions.clear()
        with manager.batch_update():
            manager.register_item(second)
            manager.unregister_item(first)
            control.setBlkItem(None)
            manager.set_textblock_mode(False)

        self.assertEqual(invalidations, [True])
        self.assertIsNone(manager.overlay_for_item(first))
        self.assertTrue(
            any(region.contains(old_center) for region in viewport.updated_regions)
        )

    def test_unregister_captures_a_view_added_after_the_previous_sync(self):
        scene = QGraphicsScene()
        scene.setSceneRect(QRectF(0, 0, 600, 400))
        base = QGraphicsRectItem(scene.sceneRect())
        scene.addItem(base)
        view1, _viewport1 = self._make_view(scene)
        control = TextBlkShapeControl(view1)
        control.setParentItem(base)
        manager = TextOverlayManager(scene, base, control)
        item = _make_item()
        item.setParentItem(base)
        manager.set_textblock_mode(True)
        manager.register_item(item)

        view2, viewport2 = self._make_view(scene)
        view2.scale(1.2, 1.2)
        view2.centerOn(scene.sceneRect().center())
        _APP.processEvents()
        self.assertNotIn(view2, manager.invalidator._regions)
        overlay = manager.overlay_for_item(item)
        old_center = overlay.deviceTransform(
            view2.viewportTransform()
        ).mapRect(overlay.boundingRect()).toAlignedRect().center()
        viewport2.updated_regions.clear()

        manager.unregister_item(item)

        self.assertTrue(viewport2.updated_regions)
        self.assertTrue(
            any(region.contains(old_center) for region in viewport2.updated_regions)
        )

    def test_unregister_refreshes_an_existing_views_panned_footprint(self):
        scene = QGraphicsScene()
        scene.setSceneRect(QRectF(0, 0, 600, 400))
        base = QGraphicsRectItem(scene.sceneRect())
        scene.addItem(base)
        view1, _viewport1 = self._make_view(scene)
        view2, viewport2 = self._make_view(scene)
        control = TextBlkShapeControl(view1)
        control.setParentItem(base)
        manager = TextOverlayManager(scene, base, control)
        item = _make_item()
        item.setParentItem(base)
        manager.set_textblock_mode(True)
        manager.register_item(item)
        self.assertIn(view2, manager.invalidator._regions)

        overlay = manager.overlay_for_item(item)
        center_before_pan = overlay.deviceTransform(
            view2.viewportTransform()
        ).mapRect(overlay.boundingRect()).toAlignedRect().center()
        view2.centerOn(QPointF(250, 175))
        _APP.processEvents()
        old_center = overlay.deviceTransform(
            view2.viewportTransform()
        ).mapRect(overlay.boundingRect()).toAlignedRect().center()
        self.assertNotEqual(old_center, center_before_pan)
        viewport2.updated_regions.clear()

        manager.unregister_item(item)

        self.assertTrue(viewport2.updated_regions)
        self.assertTrue(
            any(region.contains(old_center) for region in viewport2.updated_regions)
        )

    def test_rotation_preview_and_commit_each_use_one_manager_sync(self):
        scene = QGraphicsScene()
        scene.setSceneRect(QRectF(0, 0, 600, 400))
        base = QGraphicsRectItem(scene.sceneRect())
        scene.addItem(base)
        view, _viewport = self._make_view(scene)
        item = _make_item()
        item.setParentItem(base)
        control = TextBlkShapeControl(view)
        control.setParentItem(base)
        manager = TextOverlayManager(scene, base, control)
        syncs = []

        def sync_once():
            syncs.append('sync')
            manager.sync_overlays()

        control.overlay_sync_callback = sync_once
        control.setBlkItem(item)
        syncs.clear()
        original_angle = item.rotation()
        center = control.visualCenterInScene()

        preview_angle = control.rotateFromScene(
            center + QPointF(80, 50), 20.0
        )
        self.assertEqual(syncs, ['sync'])
        syncs.clear()
        committed_angle = control.finishRotationPreview(original_angle)
        self.assertEqual(committed_angle, preview_angle)
        self.assertEqual(syncs, [])

        command = RotateItemCommand(
            item, committed_angle, control, sync_once
        )
        command.redo()
        self.assertEqual(syncs, ['sync'])

    def test_standalone_rotation_cancel_restores_control_geometry(self):
        scene = QGraphicsScene()
        base = QGraphicsRectItem(QRectF(0, 0, 600, 400))
        scene.addItem(base)
        view, _viewport = self._make_view(scene)
        item = _make_item()
        item.setParentItem(base)
        control = TextBlkShapeControl(view)
        control.setParentItem(base)
        control.setBlkItem(item)
        original_polygon = QPolygonF(control.visualPolygonInScene())
        original_angle = item.rotation()
        center = control.visualCenterInScene()

        control.rotateFromScene(center + QPointF(70, 60), 25.0)
        self.assertNotEqual(control.visualPolygonInScene(), original_polygon)
        control.finishRotationPreview(original_angle)

        self.assertEqual(item.rotation(), original_angle)
        restored_polygon = control.visualPolygonInScene()
        self.assertEqual(len(restored_polygon), len(original_polygon))
        for index, (restored, original) in enumerate(
            zip(restored_polygon, original_polygon)
        ):
            with self.subTest(point=index):
                self.assertAlmostEqual(restored.x(), original.x())
                self.assertAlmostEqual(restored.y(), original.y())

    def test_canvas_selection_and_zoom_coalesce_to_one_manager_sync(self):
        canvas = Canvas()
        canvas.baseLayer.setRect(QRectF(0, 0, 900, 700))
        canvas.setSceneRect(canvas.baseLayer.sceneBoundingRect())
        canvas.imgtrans_proj = SimpleNamespace(img_valid=True)
        canvas.gv.resize(320, 240)
        canvas.gv.show()
        _APP.processEvents()
        syncs = []
        canvas.text_overlay_manager.sync_overlays = (
            lambda: syncs.append('sync')
        )
        selection_events = []
        canvas.incanvas_selection_changed.connect(
            lambda: selection_events.append('selection')
        )
        canvas.setFocus()

        canvas.on_selection_changed()
        self.assertEqual(syncs, ['sync'])
        self.assertEqual(selection_events, ['selection'])

        syncs.clear()
        selection_events.clear()
        canvas.block_selection_signal = True
        canvas.on_selection_changed()
        canvas.block_selection_signal = False
        self.assertEqual(syncs, [])
        self.assertEqual(selection_events, [])

        syncs.clear()
        canvas.scaleImage(1.25)
        self.assertEqual(syncs, ['sync'])

    def test_document_resize_defers_sync_during_shape_transaction(self):
        item = object()
        syncs = []
        control = SimpleNamespace(reshaping=True, blk_item=item)
        state = SimpleNamespace(
            txtblkShapeControl=control,
            textblk_item_list=[item],
            text_overlay_manager=SimpleNamespace(
                sync_overlays=lambda: syncs.append('sync')
            ),
        )

        SceneTextManager.onTextBlkItemSizeChanged(state, 0)
        self.assertEqual(syncs, [])
        control.reshaping = False
        SceneTextManager.onTextBlkItemSizeChanged(state, 0)
        self.assertEqual(syncs, ['sync'])

    def test_active_outline_repaint_is_idempotent_on_patterned_background(self):
        scene = QGraphicsScene()
        scene.setSceneRect(QRectF(0, 0, 128, 96))
        base = QGraphicsRectItem()
        scene.addItem(base)
        view = QGraphicsView(scene)
        control = TextBlkShapeControl(view)
        control.setParentItem(base)
        control.setRect(QRectF(18, 16, 88, 58))
        control.hideControls()
        control.show()

        self.assertTrue(control.pen().isCosmetic())
        self.assertEqual(control.data(UI_OVERLAY_ITEM_DATA_KEY), True)
        def render_once():
            image = QImage(128, 96, QImage.Format.Format_RGB32)
            painter = QPainter(image)
            for y in range(0, image.height(), 8):
                painter.fillRect(
                    0,
                    y,
                    image.width(),
                    8,
                    Qt.GlobalColor.lightGray
                    if (y // 8) % 2
                    else Qt.GlobalColor.darkGray,
                )
            painter.end()
            painter = QPainter(image)
            target = QRectF(0, 0, image.width(), image.height())
            scene.render(painter, target, scene.sceneRect())
            painter.end()
            return _image_bytes(image)

        self.assertEqual(render_once(), render_once())

    def test_offscreen_proxy_handle_preserves_true_geometry_and_maps_drag_delta(self):
        scene = QGraphicsScene()
        scene.setSceneRect(QRectF(0, 0, 2200, 1800))
        base = QGraphicsRectItem(scene.sceneRect())
        scene.addItem(base)
        view, _viewport = self._make_view(scene, (240, 180))
        view.centerOn(QPointF(100, 100))
        _APP.processEvents()

        item = _make_item(rect=(1500, 1300, 1650, 1410))
        item.setParentItem(base)
        polygon_before = QPolygonF(item.visual_polygon_in_scene())
        control = TextBlkShapeControl(view)
        control.setParentItem(base)
        control.setBlkItem(item)

        true_scene = control.handleScenePoint(0)
        display_scene = control.handleDisplayScenePoint(0)
        transform = view.viewportTransform()
        true_device = transform.map(true_scene)
        display_device = transform.map(display_scene)
        inset = PROXY_HANDLE_VIEWPORT_INSET
        self.assertTrue(
            true_device.x() < inset
            or true_device.x() > view.viewport().width() - inset
            or true_device.y() < inset
            or true_device.y() > view.viewport().height() - inset
        )
        self.assertGreaterEqual(display_device.x(), inset)
        self.assertLessEqual(display_device.x(), view.viewport().width() - inset)
        self.assertGreaterEqual(display_device.y(), inset)
        self.assertLessEqual(display_device.y(), view.viewport().height() - inset)
        polygon_after = item.visual_polygon_in_scene()
        self.assertEqual(len(polygon_after), len(polygon_before))
        for actual, expected in zip(polygon_after, polygon_before):
            self.assertAlmostEqual(actual.x(), expected.x(), places=6)
            self.assertAlmostEqual(actual.y(), expected.y(), places=6)

        control._beginProxyDrag(0, display_scene)
        inverse, invertible = transform.inverted()
        self.assertTrue(invertible)
        near_edge_inside = inverse.map(QPointF(5, 40))
        unchanged = control._clamped_handle_scene_point(near_edge_inside)
        self.assertAlmostEqual(unchanged.x(), near_edge_inside.x(), places=6)
        self.assertAlmostEqual(unchanged.y(), near_edge_inside.y(), places=6)
        pointer_device = display_device + QPointF(21, -13)
        pointer_scene = inverse.map(pointer_device)
        target_scene = control._proxySceneTarget(0, pointer_scene)
        expected_delta = inverse.map(pointer_device) - inverse.map(display_device)
        expected = true_scene + expected_delta
        self.assertAlmostEqual(target_scene.x(), expected.x(), places=6)
        self.assertAlmostEqual(target_scene.y(), expected.y(), places=6)

    def _make_export_canvas(self):
        width, height = 180, 120
        canvas = Canvas()
        canvas.imgtrans_proj = SimpleNamespace(
            img_valid=True,
            inpainted_valid=True,
            inpainted_array=np.zeros((height, width, 4), dtype=np.uint8),
        )
        transparent = QPixmap(width, height)
        transparent.fill(Qt.GlobalColor.transparent)
        canvas.inpaintLayer.setPixmap(transparent)
        canvas.textLayer.setPixmap(transparent)
        canvas.baseLayer.setRect(QRectF(0, 0, width, height))
        canvas.setSceneRect(QRectF(0, 0, width, height))
        item = _make_item(
            rect=(30, 25, 130, 85),
            text='',
            item_class=ExportTrackingTextItem,
        )
        item.setParentItem(canvas.textLayer)
        canvas.text_overlay_manager.register_item(item)
        canvas.txtblkShapeControl.setBlkItem(item)
        canvas._set_scene_scale(0.75)
        canvas.inpaintLayer.show()
        canvas.textLayer.setOpacity(0.35)
        canvas.textLayer.hide()
        return canvas, item

    def test_canvas_export_excludes_overlays_and_restores_state(self):
        canvas, item = self._make_export_canvas()
        overlay_visibility = {
            overlay: overlay.isVisible()
            for overlay in canvas.items()
            if bool(overlay.data(UI_OVERLAY_ITEM_DATA_KEY))
        }

        result = canvas.render_result_img()

        self.assertFalse(any(_image_bytes(result)[3::4]))
        self.assertEqual(item.export_effect_modes, [True, False])
        self.assertAlmostEqual(canvas.scale_factor, 0.75)
        self.assertTrue(canvas.inpaintLayer.isVisible())
        self.assertFalse(canvas.textLayer.isVisible())
        self.assertAlmostEqual(canvas.textLayer.opacity(), 0.35)
        for overlay, was_visible in overlay_visibility.items():
            self.assertEqual(overlay.isVisible(), was_visible)

    def test_canvas_export_exception_restores_overlays_layers_and_effect_mode(self):
        canvas, item = self._make_export_canvas()
        overlay_visibility = {
            overlay: overlay.isVisible()
            for overlay in canvas.items()
            if bool(overlay.data(UI_OVERLAY_ITEM_DATA_KEY))
        }

        def fail_render(*_args, **_kwargs):
            raise RuntimeError('render failed')

        canvas.render = fail_render
        with self.assertRaisesRegex(RuntimeError, 'render failed'):
            canvas.render_result_img()

        self.assertEqual(item.export_effect_modes, [True, False])
        self.assertFalse(item._export_effect_render)
        self.assertAlmostEqual(canvas.scale_factor, 0.75)
        self.assertTrue(canvas.inpaintLayer.isVisible())
        self.assertFalse(canvas.textLayer.isVisible())
        self.assertAlmostEqual(canvas.textLayer.opacity(), 0.35)
        for overlay, was_visible in overlay_visibility.items():
            self.assertEqual(overlay.isVisible(), was_visible)

    def test_canvas_export_exception_restores_selection_edit_cursor_and_focus(self):
        canvas, item = self._make_export_canvas()
        canvas.editor_index = 1
        canvas.textLayer.show()
        item.setPlainText('restore cursor state')
        other = _make_item(
            rect=(135, 30, 175, 80),
            text='other',
            item_class=ExportTrackingTextItem,
        )
        other.setParentItem(canvas.textLayer)
        canvas.text_overlay_manager.register_item(other)
        item.setSelected(True)
        other.setSelected(True)

        canvas.setFocus()
        item.startEdit()
        canvas.editing_textblkitem = item
        cursor = item.textCursor()
        cursor.setPosition(15)
        cursor.setPosition(3, QTextCursor.MoveMode.KeepAnchor)
        item.setTextCursor(cursor)
        selected_before = set(canvas.selectedItems())
        cursor_before = (
            item.textCursor().position(),
            item.textCursor().anchor(),
        )
        focus_item_before = canvas.focusItem()
        item_focus_before = item.hasFocus()

        def fail_render(*_args, **_kwargs):
            raise RuntimeError('editing render failed')

        canvas.render = fail_render
        with self.assertRaisesRegex(RuntimeError, 'editing render failed'):
            canvas.render_result_img()

        self.assertEqual(set(canvas.selectedItems()), selected_before)
        self.assertTrue(item.is_editting())
        self.assertIs(canvas.editing_textblkitem, item)
        self.assertEqual(
            (item.textCursor().position(), item.textCursor().anchor()),
            cursor_before,
        )
        self.assertIs(canvas.focusItem(), focus_item_before)
        self.assertEqual(item.hasFocus(), item_focus_before)
        self.assertEqual(item.export_effect_modes, [True, False])
        self.assertEqual(other.export_effect_modes, [True, False])

    def test_canvas_export_defers_qt_paint_allocation_error_then_fails(self):
        canvas, item = self._make_export_canvas()
        item.setPlainText('allocation failure')
        item.fontformat.stroke_width = 0.2
        item.fontformat.shadow_radius = 0.2
        item.fontformat.shadow_strength = 0.8
        item._mark_effect_cache_dirty()
        overlay_visibility = {
            overlay: overlay.isVisible()
            for overlay in canvas.items()
            if bool(overlay.data(UI_OVERLAY_ITEM_DATA_KEY))
        }
        failure = EffectRasterAllocationError('paint allocation failed')

        with mock.patch.object(
            item, '_new_effect_pixmap', side_effect=failure
        ):
            with self.assertRaisesRegex(
                EffectRasterAllocationError, 'paint allocation failed'
            ):
                canvas.render_result_img()

        self.assertEqual(item.export_effect_modes, [True, False])
        self.assertFalse(item._export_effect_render)
        self.assertAlmostEqual(canvas.scale_factor, 0.75)
        self.assertTrue(canvas.inpaintLayer.isVisible())
        self.assertFalse(canvas.textLayer.isVisible())
        self.assertAlmostEqual(canvas.textLayer.opacity(), 0.35)
        for overlay, was_visible in overlay_visibility.items():
            self.assertEqual(overlay.isVisible(), was_visible)

    def test_escape_emits_move_cancel_and_clears_drag_state(self):
        canvas = Canvas()
        item = _make_item(rect=(20, 20, 100, 70), text='move')
        item.setParentItem(canvas.textLayer)
        before = QPointF(item.logical_position())
        item.set_logical_position(before + QPointF(32, -14))
        syncs = []
        manager_state = SimpleNamespace(
            canvas=canvas,
            text_overlay_manager=SimpleNamespace(
                sync_overlays=lambda: syncs.append('sync')
            ),
            _text_move_items=[item],
            _text_move_snapshot={item: before},
        )
        emitted = []
        canvas.cancel_text_move_requested.connect(lambda: emitted.append(True))
        canvas.cancel_text_move_requested.connect(
            lambda: SceneTextManager.onCancelTextMove(manager_state)
        )
        canvas.begin_text_move_drag()
        event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Escape,
            Qt.KeyboardModifier.NoModifier,
        )

        canvas.keyPressEvent(event)

        self.assertEqual(emitted, [True])
        self.assertFalse(canvas.text_move_drag_active)
        self.assertTrue(event.isAccepted())
        self.assertEqual(item.logical_position(), before)
        self.assertEqual(item.oldPos, item.pos())
        self.assertEqual(syncs, ['sync'])
        self.assertEqual(canvas.text_undo_stack.count(), 0)


if __name__ == '__main__':
    unittest.main()
