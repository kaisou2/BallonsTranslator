"""Compare the visible text paint, including QWidget backing-store offsets."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy.QtCore import QCoreApplication, QEvent, QPointF, QRectF
from qtpy.QtGui import (
    QColor, QFont, QImage, QPaintEvent, QPainter, QPen, QPixmap,
    QTextLayout, QTransform,
)
from qtpy.QtWidgets import (
    QApplication, QGraphicsItem, QGraphicsScene, QGraphicsView,
    QStyleOptionGraphicsItem, QWidget,
)

from ballontranslator.ui.text_engine.rendering import native_paint


def _long_layout() -> QTextLayout:
    layout = QTextLayout('0' * 240, QFont('Arial', 24))
    format_range = QTextLayout.FormatRange()
    format_range.start, format_range.length = 0, 240
    format_range.format.setForeground(QColor('black'))
    format_range.format.setTextOutline(QPen(QColor('black'), 2.0))
    layout.setFormats([format_range])
    layout.beginLayout()
    line = layout.createLine()
    line.setLineWidth(7000)
    layout.endLayout()
    return layout


def _paint_layout(layout: QTextLayout, painter: QPainter, batched: bool) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if batched:
        native_paint.draw_native_layout(layout, painter, [], QRectF())
    else:
        layout.draw(painter, QPointF(), [], QRectF())


class _LayoutWidget(QWidget):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.text_layout = _long_layout()
        self.batched = False

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        try:
            painter.fillRect(self.rect(), QColor('cyan'))
            painter.translate(10, 15)
            painter.scale(0.9, 1.1)
            _paint_layout(self.text_layout, painter, self.batched)
        finally:
            painter.end()


class _LayoutItem(QGraphicsItem):
    def __init__(self) -> None:
        super().__init__()
        self.text_layout = _long_layout()
        self.batched = False

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, 7000, 80)

    def paint(
        self, painter: QPainter, option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        _paint_layout(self.text_layout, painter, self.batched)


class NativeWidgetPaintTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _assert_same_pixels(
        self, expected: QImage, actual: QImage, *, allow_outer_edge_rounding: bool = False,
    ) -> None:
        arrays = []
        for image in (expected, actual):
            image = image.convertToFormat(QImage.Format.Format_RGBA8888)
            arrays.append(np.frombuffer(
                image.constBits().asstring(image.sizeInBytes()), dtype=np.uint8,
            ).reshape(image.height(), image.width(), 4).copy())
        self.assertTrue(np.any(np.all(arrays[0][:, :, :3] == 0, axis=2)),
                        'The reference must contain visible black glyph pixels.')
        delta = np.abs(arrays[0].astype(np.int16) - arrays[1])
        if allow_outer_edge_rounding:
            # As in test_native_path_paint, Qt can round the implicit outer
            # device edge by one level. Interior pixels must remain identical.
            self.assertFalse(np.any(delta[:, 1:-1]))
            self.assertLessEqual(int(delta.max()), 1)
            return
        self.assertFalse(np.any(delta),
                         f'{np.count_nonzero(np.any(delta, axis=2))} differing pixels; '
                         f'maximum channel difference {delta.max()}')

    def test_child_widget_backing_store_offset_keeps_visible_glyphs_aligned(self) -> None:
        parent = QWidget()
        parent.resize(900, 240)
        child = _LayoutWidget(parent)
        child.setGeometry(67, 51, 750, 140)
        parent.show()
        self.app.processEvents()
        try:
            # Grabbing only the child removes the backing-store translation
            # and misses the screen regression. Render the whole parent.
            expected = parent.grab().toImage()
            child.batched = True
            child.update()
            actual = parent.grab().toImage()
            self._assert_same_pixels(expected, actual)
        finally:
            parent.close()
            parent.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()

    def test_graphics_view_zoom_and_item_caches_preserve_visible_text(self) -> None:
        parent = QWidget()
        parent.resize(900, 600)
        view = QGraphicsView(parent)
        view.setGeometry(87, 227, 720, 290)
        scene = QGraphicsScene(view)
        scene.setSceneRect(0, 0, 4000, 600)
        item = _LayoutItem()
        item.setPos(112, 67)
        scene.addItem(item)
        view.setScene(scene)
        parent.show()
        self.app.processEvents()
        try:
            for cache in (QGraphicsItem.CacheMode.NoCache,
                          QGraphicsItem.CacheMode.DeviceCoordinateCache,
                          QGraphicsItem.CacheMode.ItemCoordinateCache):
                for zoom in (0.45, 0.7, 1.25):
                    with self.subTest(cache=cache, zoom=zoom,
                                      dpr=parent.devicePixelRatioF()):
                        view.setTransform(QTransform.fromScale(zoom, zoom))
                        view.centerOn(400, 110)
                        # Compare fresh caches: Qt's partial cache repaint can
                        # round differently even for two native-only draws.
                        images = []
                        for batched in (False, True):
                            item.batched = batched
                            item.setCacheMode(QGraphicsItem.CacheMode.NoCache)
                            item.setCacheMode(cache)
                            item.update()
                            images.append(parent.grab().toImage())
                        self._assert_same_pixels(*images)
        finally:
            parent.close()
            parent.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()

    def test_image_and_pixmap_surfaces_keep_accelerated_identical_output(self) -> None:
        layout = _long_layout()
        for surface_type in (QImage, QPixmap):
            for dpr in (0.5, 1.0, 1.25, 2.0):
                with self.subTest(surface=surface_type.__name__, dpr=dpr):
                    images = []
                    for batched in (False, True):
                        if surface_type is QImage:
                            surface = QImage(2200, 180,
                                             QImage.Format.Format_ARGB32_Premultiplied)
                        else:
                            surface = QPixmap(2200, 180)
                        surface.setDevicePixelRatio(dpr)
                        surface.fill(QColor('cyan'))
                        painter = QPainter(surface)
                        painter.translate(0.25, 4.5)
                        with patch.object(native_paint, '_closed_contours',
                                          wraps=native_paint._closed_contours) as contours:
                            try:
                                _paint_layout(layout, painter, batched)
                            finally:
                                painter.end()
                            if batched and dpr >= 1.0:
                                self.assertGreater(contours.call_count, 0,
                                                   'Raster surfaces must still partition long paths.')
                        images.append(surface if surface_type is QImage else surface.toImage())
                    self._assert_same_pixels(*images, allow_outer_edge_rounding=True)


if __name__ == '__main__':
    unittest.main()
