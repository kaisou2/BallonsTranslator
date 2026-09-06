"""Protect emphasis output across shaping and transient format boundaries."""
from __future__ import annotations

import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy.QtCore import QPointF
from qtpy.QtGui import (
    QAbstractTextDocumentLayout,
    QTextCharFormat,
    QTextCursor,
    QTextLayout,
    QTransform,
)
from qtpy.QtWidgets import QApplication

from ballontranslator.ui.text_engine.annotations import (
    AnnotationProperty,
    EMPHASIS_GLYPHS,
    apply_emphasis,
    apply_text_combine_upright,
)
from ballontranslator.ui.text_engine.item import TextBlkItem
from ballontranslator.ui.text_engine.rendering.emphasis import (
    _iter_emphasis_marks,
)
from ballontranslator.ui.text_engine.rendering.glyph import (
    GLYPH_STROKE_FORMAT_PROPERTY,
    glyph_geometry,
)
from ballontranslator.utils.textblock import TextBlock


class EmphasisRangeIterationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def make_item(self, text: str, vertical: bool = False) -> TextBlkItem:
        bounds = [0, 0, 42, 240] if not vertical else [0, 0, 240, 70]
        model = TextBlock(bounds)
        model._bounding_rect = list(bounds)
        model.translation = text
        model.vertical = vertical
        model.fontformat.font_size = 24
        model.fontformat.stroke_width = 0
        item = TextBlkItem(model, 0)
        self.addCleanup(item.deleteLater)
        return item

    @staticmethod
    def cursor(item: TextBlkItem, start: int, end: int) -> QTextCursor:
        cursor = QTextCursor(item.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        return cursor

    @staticmethod
    def marks(item: TextBlkItem, context=None) -> tuple:
        block = item.document().firstBlock()
        result = []
        for index in range(block.layout().lineCount()):
            if item.fontformat.vertical:
                placement = item.layout.vertical_line_placement(block, index)
                if placement is None:
                    continue
                line, offset, orientation = placement
            else:
                line = block.layout().lineAt(index)
                offset, orientation = QPointF(), QTransform()
            result.extend(_iter_emphasis_marks(
                block, line, vertical=item.fontformat.vertical,
                context=context, offset=offset, orientation=orientation,
            ))
        return tuple(result)

    def test_plain_text_and_wrapped_unicode_marks(self) -> None:
        for vertical in (False, True):
            with self.subTest(vertical=vertical):
                # The astral character and combining sequence each own one mark.
                item = self.make_item('A😀e\u0301B', vertical)
                self.assertEqual(self.marks(item), ())
                apply_emphasis(self.cursor(item, 0, 6), 'filled circle', 'over right')
                marks = self.marks(item)
                self.assertEqual(len(marks), 4)
                self.assertTrue(all(not mark.ink_bounds.isEmpty() for mark in marks))
                self.assertTrue(all(
                    mark.source.document.toPlainText() == EMPHASIS_GLYPHS['filled circle']
                    for mark in marks
                ))

    def test_transient_layout_formats_can_enable_and_suppress_marks(self) -> None:
        item = self.make_item('AB')
        layout = item.document().firstBlock().layout()
        marked = QTextLayout.FormatRange()
        marked.start, marked.length = 0, 2
        marked_format = QTextCharFormat()
        marked_format.setProperty(AnnotationProperty.EMPHASIS_STYLE, 'filled circle')
        marked.format = marked_format
        layout.setFormats([marked])
        item.layout.reLayout()
        self.assertEqual(len(self.marks(item)), 2)

        suppressed = QTextLayout.FormatRange()
        suppressed.start, suppressed.length = 0, 1
        suppressed_format = QTextCharFormat()
        suppressed_format.setProperty(AnnotationProperty.EMPHASIS_STYLE, 'none')
        suppressed.format = suppressed_format
        layout.setFormats([marked, suppressed])
        item.layout.reLayout()
        self.assertEqual(len(self.marks(item)), 1)

    def test_effect_selection_supplies_emphasis_to_plain_document(self) -> None:
        item = self.make_item('AB')
        context = QAbstractTextDocumentLayout.PaintContext()
        selection = QAbstractTextDocumentLayout.Selection()
        selection.cursor = self.cursor(item, 1, 2)
        selection_format = QTextCharFormat()
        selection_format.setProperty(GLYPH_STROKE_FORMAT_PROPERTY, True)
        selection_format.setProperty(AnnotationProperty.EMPHASIS_STYLE, 'open circle')
        selection.format = selection_format
        context.selections = [selection]
        marks = self.marks(item, context)
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0].source.document.toPlainText(), EMPHASIS_GLYPHS['open circle'])
        self.assertFalse(marks[0].ink_bounds.isEmpty())
        self.assertEqual(self.marks(item), ())

    def test_partly_emphasized_tate_chu_yoko_keeps_one_centered_mark(self) -> None:
        item = self.make_item('12', True)
        apply_text_combine_upright(self.cursor(item, 0, 2), True)
        apply_emphasis(self.cursor(item, 1, 2), 'filled circle', 'over right')
        block = item.document().firstBlock()
        line, offset, orientation = item.layout.vertical_line_placement(block, 0)
        self.assertEqual(line.textLength(), 2)
        marks = self.marks(item)
        self.assertEqual(len(marks), 1)
        cell = glyph_geometry(line, 0, 2, offset, orientation, 0.0).bounds
        self.assertFalse(cell.isEmpty())
        self.assertAlmostEqual(marks[0].ink_bounds.center().y(), cell.center().y())
        self.assertGreater(marks[0].ink_bounds.left(), cell.right())


if __name__ == '__main__':
    unittest.main()
