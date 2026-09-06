"""Bound native Qt outline rasterization without changing its glyph paths."""

from __future__ import annotations

import math
from collections import OrderedDict
from typing import Iterable, Optional, Sequence, Union

from qtpy.QtCore import QLine, QLineF, QPoint, QPointF, QRect, QRectF, Qt
from qtpy.QtGui import (
    QImage,
    QPaintDevice,
    QPaintEngine,
    QPaintEngineState,
    QPainter,
    QPainterPath,
    QPixmap,
    QRegion,
    QTextItem,
    QTextLayout,
    QTransform,
)


_PATH_ELEMENT_THRESHOLD = 512
_STRIP_WIDTH = 256
_CONTOUR_CACHE_MAX_ENTRIES = 8
_CONTOUR_CACHE_MAX_ELEMENTS = 32768
_CONTOUR_CACHE: OrderedDict[tuple, tuple[QPainterPath, list[QPainterPath]]] = OrderedDict()


def _closed_contours(path: QPainterPath) -> Optional[list[QPainterPath]]:
    """Retain native curve coordinates and holes; leave open paths untouched.

    >>> _closed_contours(QPainterPath())
    []
    """
    rect = path.controlPointRect()
    count = path.elementCount()
    key = (count, path.fillRule(), rect.x(), rect.y(), rect.width(), rect.height())
    cached = _CONTOUR_CACHE.get(key)
    if cached is not None and cached[0] == path:
        _CONTOUR_CACHE.move_to_end(key)
        return cached[1]
    contours = []
    contour = None
    start = QPointF()
    index = 0
    while index < count:
        element = path.elementAt(index)
        if element.isMoveTo():
            if contour is not None and contour.elementCount() > 1:
                if contour.currentPosition() != start:
                    return None
                contour.closeSubpath()
                contours.append(contour)
            start = QPointF(element.x, element.y)
            contour = QPainterPath(start)
            contour.setFillRule(path.fillRule())
        elif element.isLineTo():
            contour.lineTo(element.x, element.y)
        elif element.isCurveTo():
            control = path.elementAt(index + 1)
            end = path.elementAt(index + 2)
            contour.cubicTo(
                element.x, element.y, control.x, control.y, end.x, end.y,
            )
            index += 2
        index += 1
    if contour is not None and contour.elementCount() > 1:
        if contour.currentPosition() != start:
            return None
        contour.closeSubpath()
        contours.append(contour)
    if _PATH_ELEMENT_THRESHOLD <= count <= _CONTOUR_CACHE_MAX_ELEMENTS:
        # Fill and Stroke share these native coordinates across repaints and
        # items. Bounds only select a candidate; ask Qt to compare the path
        # geometry before reusing contours from text with the same metrics.
        _CONTOUR_CACHE[key] = (QPainterPath(path), contours)
        _CONTOUR_CACHE.move_to_end(key)
        while len(_CONTOUR_CACHE) > _CONTOUR_CACHE_MAX_ENTRIES:
            _CONTOUR_CACHE.popitem(last=False)
    return contours


def _draw_path_in_strips(painter: QPainter, path: QPainterPath) -> None:
    """Rasterize overlapping native contours once per device-aligned strip.

    Qt joins a whole text run into one potentially expensive outline path.
    Keep every contour that can affect a strip, including holes and stroke
    overhang. Disjoint integer device clips ensure each pixel is written once.

    >>> _STRIP_WIDTH > 0
    True
    """
    transform = painter.deviceTransform()
    bounds = transform.mapRect(path.controlPointRect())
    pen = painter.pen()
    if (
        path.elementCount() < _PATH_ELEMENT_THRESHOLD
        or bounds.width() <= _STRIP_WIDTH
        or transform.m12() != 0.0
        or transform.m21() != 0.0
        or not transform.isAffine()
        or pen.style() not in (Qt.PenStyle.NoPen, Qt.PenStyle.SolidLine)
    ):
        painter.drawPath(path)
        return
    if bounds.top() < 0.0 or bounds.bottom() > painter.device().height():
        # Partitioning a fill cut by the device's top/bottom changes Qt's
        # coverage. Inside Stroke carries that difference into final page pixels.
        painter.drawPath(path)
        return
    pen_width = pen.widthF()
    smallest_device_width = (
        pen_width if pen.isCosmetic()
        else pen_width * min(abs(transform.m11()), abs(transform.m22()))
    )
    if (
        pen.style() != Qt.PenStyle.NoPen
        and smallest_device_width <= 1.0
        and pen.brush().style() != Qt.BrushStyle.NoBrush
        and (
            pen.brush().style() != Qt.BrushStyle.SolidPattern
            or pen.color().alpha() != 0
        )
    ):
        # Separately clipped draws change Qt's thin-stroke coverage throughout
        # the run. Transparent alignment outlines still accelerate native fill.
        painter.drawPath(path)
        return
    inverse, invertible = transform.inverted()
    if not invertible:
        painter.drawPath(path)
        return
    world_transform = painter.worldTransform()
    device_coordinates = inverse * world_transform
    contours = _closed_contours(path)
    if not contours:
        painter.drawPath(path)
        return

    device = painter.device()
    width, height = device.width(), device.height()
    if width <= 0 or height <= 0:
        painter.drawPath(path)
        return
    if pen.isCosmetic():
        pen_width = max(1.0, pen_width)
    else:
        pen_width *= abs(transform.m11())
    reach = pen_width / 2.0
    if pen.joinStyle() in (Qt.PenJoinStyle.MiterJoin, Qt.PenJoinStyle.SvgMiterJoin):
        reach = pen_width * max(0.5, pen.miterLimit())
    margin = reach + 2.0
    strips: dict[int, QPainterPath] = {}
    last_strip = math.ceil(width / _STRIP_WIDTH) - 1
    for contour in contours:
        rect = transform.mapRect(contour.controlPointRect())
        first = max(0, math.floor((rect.left() - margin) / _STRIP_WIDTH))
        last = min(last_strip, math.floor((rect.right() + margin) / _STRIP_WIDTH))
        for index in range(first, last + 1):
            if index not in strips:
                combined = QPainterPath()
                combined.setFillRule(path.fillRule())
                strips[index] = combined
            strips[index].addPath(contour)

    for index, combined in strips.items():
        left = index * _STRIP_WIDTH if index else -_STRIP_WIDTH
        right = (index + 1) * _STRIP_WIDTH
        if index == last_strip:
            right = width + _STRIP_WIDTH
        # Preserve the raster engine's implicit outer-device clipping. Adding
        # another explicit clip at that edge can change its antialias rounding.
        device_rect = QRectF(
            left, -_STRIP_WIDTH, right - left, height + 2 * _STRIP_WIDTH,
        )
        painter.save()
        try:
            # Clip in integer device coordinates. Mapping fractional logical
            # rectangles back through Qt 5's high-DPI matrix can leave a seam.
            painter.setWorldTransform(device_coordinates)
            painter.setClipRegion(
                QRegion(device_rect.toRect()), Qt.ClipOperation.IntersectClip,
            )
            painter.setWorldTransform(world_transform)
            painter.drawPath(combined)
        finally:
            painter.restore()


class _NativePathEngine(QPaintEngine):
    """Forward Qt's native document paint, bounding only complex paths.

    >>> issubclass(_NativePathEngine, QPaintEngine)
    True
    """

    def __init__(self, painter: QPainter, probing: bool = False) -> None:
        super().__init__(QPaintEngine.PaintEngineFeature.AllFeatures)
        self.probing = probing
        self.has_text_items = False
        self.painter = painter
        self.proxy: Optional[QPainter] = None
        world_inverse, _ = painter.worldTransform().inverted()
        device_transform = world_inverse * painter.deviceTransform()
        self._device_to_world, _ = device_transform.inverted()

    def begin(self, device: QPaintDevice) -> bool:
        return True

    def end(self) -> bool:
        return True

    def type(self) -> QPaintEngine.Type:
        return QPaintEngine.Type.User

    def updateState(self, state: QPaintEngineState) -> None:
        if self.probing:
            return
        flags = state.state()
        dirty = QPaintEngine.DirtyFlag
        clip_changed = flags & (
            dirty.DirtyClipPath | dirty.DirtyClipRegion | dirty.DirtyClipEnabled
        )
        if clip_changed:
            # Keep the caller's existing rasterized clip intact. Re-encoding
            # it as a new path can shift a fractional high-DPI edge by a pixel.
            self.painter.restore()
            self.painter.save()
            flags = dirty.AllDirty
        for flag, setter, getter in (
            (dirty.DirtyPen, self.painter.setPen, state.pen),
            (dirty.DirtyBrush, self.painter.setBrush, state.brush),
            (dirty.DirtyBrushOrigin, self.painter.setBrushOrigin, state.brushOrigin),
            (dirty.DirtyFont, self.painter.setFont, state.font),
            (dirty.DirtyBackground, self.painter.setBackground, state.backgroundBrush),
            (dirty.DirtyBackgroundMode, self.painter.setBackgroundMode, state.backgroundMode),
            (dirty.DirtyCompositionMode, self.painter.setCompositionMode, state.compositionMode),
            (dirty.DirtyOpacity, self.painter.setOpacity, state.opacity),
        ):
            if flags & flag:
                setter(getter())
        if flags & dirty.DirtyTransform:
            # Engine state already includes the device pixel ratio. Applying
            # it as another world scale would double-scale high-DPI surfaces.
            self.painter.setWorldTransform(state.transform() * self._device_to_world)
        if flags & dirty.DirtyHints:
            self.painter.setRenderHints(self.painter.renderHints(), False)
            self.painter.setRenderHints(state.renderHints())
        if clip_changed and state.isClipEnabled() and self.proxy is not None:
            self.painter.setClipPath(
                self.proxy.clipPath(), Qt.ClipOperation.IntersectClip,
            )

    def drawPath(self, path: QPainterPath) -> None:
        if self.probing:
            return
        _draw_path_in_strips(self.painter, path)

    def drawPixmap(self, rect: QRectF, pixmap: QPixmap, source: QRectF) -> None:
        if self.probing:
            return
        self.painter.drawPixmap(rect, pixmap, source)

    def drawImage(
        self, rect: QRectF, image: QImage, source: QRectF,
        flags: Qt.ImageConversionFlag = Qt.ImageConversionFlag.AutoColor,
    ) -> None:
        if self.probing:
            return
        self.painter.drawImage(rect, image, source, flags)

    def drawPolygon(
        self, points: Iterable[Union[QPoint, QPointF]],
        mode: QPaintEngine.PolygonDrawMode,
    ) -> None:
        if self.probing:
            return
        if mode == QPaintEngine.PolygonDrawMode.PolylineMode:
            self.painter.drawPolyline(points)
        else:
            rule = (
                Qt.FillRule.WindingFill
                if mode == QPaintEngine.PolygonDrawMode.WindingMode
                else Qt.FillRule.OddEvenFill
            )
            self.painter.drawPolygon(points, rule)

    def drawRects(self, rectangles: Iterable[Union[QRect, QRectF]]) -> None:
        if self.probing:
            return
        self.painter.drawRects(rectangles)

    def drawLines(self, lines: Iterable[Union[QLine, QLineF]]) -> None:
        if self.probing:
            return
        self.painter.drawLines(lines)

    def drawTextItem(self, point: QPointF, item: QTextItem) -> None:
        # The dry run detects native glyph items before any target paint.
        # PyQt cannot forward them through QPainter without changing rendering.
        self.has_text_items = True


class _NativePathDevice(QPaintDevice):
    def __init__(self, painter: QPainter, probing: bool = False) -> None:
        super().__init__()
        self.target = painter.device()
        self.engine = _NativePathEngine(painter, probing)

    def paintEngine(self) -> QPaintEngine:
        return self.engine

    def metric(self, metric: QPaintDevice.PaintDeviceMetric) -> int:
        metrics = QPaintDevice.PaintDeviceMetric
        values = {
            metrics.PdmWidth: self.target.width(),
            metrics.PdmHeight: self.target.height(),
            metrics.PdmWidthMM: self.target.widthMM(),
            metrics.PdmHeightMM: self.target.heightMM(),
            metrics.PdmNumColors: self.target.colorCount(),
            metrics.PdmDepth: self.target.depth(),
            metrics.PdmDpiX: self.target.logicalDpiX(),
            metrics.PdmDpiY: self.target.logicalDpiY(),
            metrics.PdmPhysicalDpiX: self.target.physicalDpiX(),
            metrics.PdmPhysicalDpiY: self.target.physicalDpiY(),
            metrics.PdmDevicePixelRatio: int(self.target.devicePixelRatioF()),
            metrics.PdmDevicePixelRatioScaled: int(
                self.target.devicePixelRatioF() * QPaintDevice.devicePixelRatioFScale()
            ),
        }
        if metric in values:
            return values[metric]
        return super().metric(metric)


def draw_native_layout(
    layout: QTextLayout,
    painter: QPainter,
    selections: Sequence[QTextLayout.FormatRange],
    clip: QRectF,
) -> None:
    """Bound long horizontal paths on independent raster surfaces.

    Stroke-aligned fill and cloned outlines share this transient raster device.
    Widget painting keeps Qt's native device geometry. The document and raster
    cache keys retain their existing ownership.

    >>> callable(draw_native_layout)
    True
    """
    device = painter.device()
    transform = painter.worldTransform()
    # Widget painters carry backing-store offsets and logical device sizes that
    # this raster proxy does not share. Qt must own their complete screen paint,
    # including redirected captures and high-DPI clipping.
    if (
        not painter.isActive()
        or not isinstance(device, (QImage, QPixmap))
        or not transform.isInvertible()
        or painter.viewTransformEnabled()
        or bool(selections)
        or bool(layout.preeditAreaText())
    ):
        layout.draw(painter, QPointF(), selections, clip)
        return
    ratio = device.devicePixelRatioF()
    expected_device_transform = transform * QTransform.fromScale(ratio, ratio)
    if painter.deviceTransform() != expected_device_transform:
        layout.draw(painter, QPointF(), selections, clip)
        return
    hints = painter.renderHints()
    opacity = painter.opacity()
    composition = painter.compositionMode()
    pen = painter.pen()
    brush = painter.brush()
    font = painter.font()
    brush_origin = painter.brushOrigin()
    background = painter.background()
    background_mode = painter.backgroundMode()
    # Additional formats and selections can remove an outline on just part of
    # a shaped run. Let Qt resolve those formats in a dry paint: if it emits any
    # native glyph items, draw the whole layout directly, preserving hinting and
    # color glyphs without partially painting the destination first.
    # These synchronous passes share unchanged layout and painter state, so Qt
    # emits the same primitives; only a path-only probe reaches the paint pass.
    for probing in (True, False):
        painter.save()
        device = _NativePathDevice(painter, probing)
        proxy = QPainter(device)
        device.engine.proxy = proxy
        try:
            proxy.setWorldTransform(transform)
            proxy.setPen(pen)
            proxy.setBrush(brush)
            proxy.setFont(font)
            proxy.setBrushOrigin(brush_origin)
            proxy.setBackground(background)
            proxy.setBackgroundMode(background_mode)
            proxy.setRenderHints(proxy.renderHints(), False)
            proxy.setRenderHints(hints)
            proxy.setOpacity(opacity)
            proxy.setCompositionMode(composition)
            layout.draw(proxy, QPointF(), selections, clip)
        finally:
            proxy.end()
            device.engine.proxy = None
            painter.restore()
        if probing and device.engine.has_text_items:
            layout.draw(painter, QPointF(), selections, clip)
            return
