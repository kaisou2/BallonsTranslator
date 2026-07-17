import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy.QtCore import QPointF, QRectF
from qtpy.QtGui import QTransform
from qtpy.QtWidgets import (
    QApplication,
    QGraphicsRectItem,
    QGraphicsScene,
)

from ballontranslator.ui.textitem import TextBlkItem
from ballontranslator.utils.fontformat import FontFormat, TextAlignment
from ballontranslator.utils.textblock import TextBlock


_APP = QApplication.instance() or QApplication([])


def _size_tuple(size):
    return (float(size.width()), float(size.height()))


def _display_size(item):
    return _size_tuple(item.boundingRect().size())


def _document_size(item):
    return _size_tuple(item.documentSize())


def _semantic_anchor(item):
    rect = item.logical_unpadded_rect()
    if item.fontformat.vertical or item.fontformat.alignment == TextAlignment.Right:
        return rect.topRight()
    if item.fontformat.alignment == TextAlignment.Left:
        return rect.topLeft()
    return rect.center()


def _make_item(
    *,
    alignment=TextAlignment.Left,
    vertical=False,
    text='x',
    size=(200.0, 100.0),
    transform=(1.0, 1.0, 0.0),
    angle=0.0,
    item_class=TextBlkItem,
):
    width, height = size
    fontformat = FontFormat(
        font_size=30,
        alignment=alignment,
        vertical=vertical,
        horizontal_scale=transform[0],
        vertical_scale=transform[1],
        slant_angle=transform[2],
    )
    block = TextBlock(
        xyxy=[10, 20, 10 + width, 20 + height],
        _bounding_rect=[10, 20, width, height],
        translation=text,
        angle=angle,
        fontformat=fontformat,
    )
    return item_class(block), block


def _observe_size_transaction(item):
    layout_events = []
    item_events = []

    def on_layout_size(announced):
        layout_events.append(
            {
                'announced': _size_tuple(announced),
                'display': _display_size(item),
                'document': _document_size(item),
            }
        )

    def on_item_size(index):
        item_events.append(
            {
                'index': index,
                'display': _display_size(item),
                'document': _document_size(item),
            }
        )

    item.layout.documentSizeChanged.connect(on_layout_size)
    item.doc_size_changed.connect(on_item_size)
    return layout_events, item_events


class GeometryTrackingTextBlkItem(TextBlkItem):
    """Real TextBlkItem with an observation point at the Qt geometry contract."""

    def __init__(self, *args, **kwargs):
        self.geometry_prepare_snapshots = []
        super().__init__(*args, **kwargs)

    def prepareGeometryChange(self):
        if getattr(self, 'layout', None) is not None:
            self.geometry_prepare_snapshots.append(
                (_display_size(self), _document_size(self))
            )
        return super().prepareGeometryChange()


class TextItemSetSizeTransactionTests(unittest.TestCase):
    def assertPointAlmostEqual(self, actual, expected, places=6):
        self.assertAlmostEqual(actual.x(), expected.x(), places=places)
        self.assertAlmostEqual(actual.y(), expected.y(), places=places)

    def assertSizeAlmostEqual(self, actual, expected, places=6):
        self.assertAlmostEqual(actual[0], expected[0], places=places)
        self.assertAlmostEqual(actual[1], expected[1], places=places)

    def assertSingleCoherentNotification(
        self, layout_events, item_events, expected_size
    ):
        self.assertEqual(len(layout_events), 1)
        self.assertEqual(len(item_events), 1)

        layout_event = layout_events[0]
        self.assertSizeAlmostEqual(layout_event['announced'], expected_size)
        self.assertSizeAlmostEqual(layout_event['document'], expected_size)
        self.assertSizeAlmostEqual(layout_event['display'], expected_size)

        item_event = item_events[0]
        self.assertSizeAlmostEqual(item_event['document'], expected_size)
        self.assertSizeAlmostEqual(item_event['display'], expected_size)

    def test_no_enlargement_notifies_only_after_display_and_layout_agree(self):
        item, _block = _make_item(text='x', transform=(1.2, 0.9, 12.0))
        requested = (360.0, 220.0)
        layout_events, item_events = _observe_size_transaction(item)

        item.set_size(*requested, set_layout_maxsize=True)

        self.assertSizeAlmostEqual(_document_size(item), requested)
        self.assertSizeAlmostEqual(_display_size(item), requested)
        self.assertSingleCoherentNotification(
            layout_events, item_events, requested
        )

    def test_forced_layout_enlargement_uses_final_document_size(self):
        item, block = _make_item(
            text='Tall text', transform=(1.2, 0.9, 12.0)
        )
        requested = (80.0, 5.0)
        layout_events, item_events = _observe_size_transaction(item)

        item.set_size(*requested, set_layout_maxsize=True)

        final_size = _document_size(item)
        self.assertGreater(final_size[1], requested[1])
        self.assertSizeAlmostEqual(_display_size(item), final_size)
        self.assertSingleCoherentNotification(
            layout_events, item_events, final_size
        )
        self.assertEqual(block._bounding_rect, item.absBoundingRect())

    def test_semantic_anchor_survives_full_affine_size_transaction(self):
        cases = (
            ('left', TextAlignment.Left, False),
            ('center', TextAlignment.Center, False),
            ('right', TextAlignment.Right, False),
            ('vertical', TextAlignment.Left, True),
        )

        for name, alignment, vertical in cases:
            with self.subTest(anchor=name):
                item, _block = _make_item(
                    alignment=alignment,
                    vertical=vertical,
                    transform=(1.65, 0.55, 22.0),
                    angle=37.0,
                )
                item.setPadding(7.5)
                item.setScale(1.3)

                scene = QGraphicsScene()
                parent = QGraphicsRectItem(QRectF(-20, -20, 40, 40))
                parent.setTransform(
                    QTransform(1.2, 0.25, -0.3, 0.85, 70.0, -40.0)
                )
                parent.setRotation(11.0)
                scene.addItem(parent)
                item.setParentItem(parent)

                try:
                    anchor = _semantic_anchor(item)
                    parent_before = item.mapToParent(anchor)
                    scene_before = item.mapToScene(anchor)

                    item.set_size(360.0, 210.0, set_layout_maxsize=True)

                    anchor = _semantic_anchor(item)
                    self.assertPointAlmostEqual(
                        item.mapToParent(anchor), parent_before
                    )
                    self.assertPointAlmostEqual(
                        item.mapToScene(anchor), scene_before
                    )
                finally:
                    item.setParentItem(None)
                    scene.removeItem(item)
                    scene.removeItem(parent)

    def test_geometry_change_precedes_relayout_and_scene_index_stays_current(self):
        item, _block = _make_item(
            alignment=TextAlignment.Left,
            transform=(1.2, 0.9, 12.0),
            item_class=GeometryTrackingTextBlkItem,
        )
        scene = QGraphicsScene(QRectF(-1000, -1000, 3000, 3000))
        scene.setItemIndexMethod(
            QGraphicsScene.ItemIndexMethod.BspTreeIndex
        )
        scene.addItem(item)

        try:
            before = (_display_size(item), _document_size(item))
            item.geometry_prepare_snapshots.clear()
            item.set_size(360.0, 180.0, set_layout_maxsize=True)

            self.assertEqual(item.geometry_prepare_snapshots, [before])
            self.assertEqual(scene.itemsBoundingRect(), item.sceneBoundingRect())
            expanded_point = item.mapToScene(QPointF(330.0, 50.0))
            self.assertIn(item, scene.items(expanded_point))

            before = (_display_size(item), _document_size(item))
            item.geometry_prepare_snapshots.clear()
            item.set_size(120.0, 60.0, set_layout_maxsize=True)

            self.assertEqual(item.geometry_prepare_snapshots, [before])
            self.assertEqual(scene.itemsBoundingRect(), item.sceneBoundingRect())
            self.assertNotIn(item, scene.items(expanded_point))
        finally:
            scene.removeItem(item)

    def test_squeeze_under_ctrl_does_not_duplicate_size_notification(self):
        item, _block = _make_item(
            text='squeeze me',
            size=(600.0, 300.0),
            transform=(1.2, 0.9, 12.0),
        )
        item.under_ctrl = True
        layout_events, item_events = _observe_size_transaction(item)

        item.squeezeBoundingRect(repaint=False)

        final_size = _document_size(item)
        self.assertSizeAlmostEqual(_display_size(item), final_size)
        self.assertSingleCoherentNotification(
            layout_events, item_events, final_size
        )

if __name__ == '__main__':
    unittest.main()
