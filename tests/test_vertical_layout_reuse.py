"""Observable regressions for reuse of unchanged vertical paragraphs."""
from __future__ import annotations

from copy import deepcopy
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy.QtCore import QCoreApplication, QEvent, QRectF, Qt
from qtpy.QtGui import QColor, QImage, QPainter, QTextCharFormat, QTextCursor, QTextLayout
from qtpy.QtWidgets import QApplication, QGraphicsScene

from ballontranslator.ui.text_engine.annotations import (
    apply_line_spacing, letter_spacing_value, line_spacing_values,
)
from ballontranslator.ui.text_engine.effects.renderer import STROKE_ALIGNMENT_LAYOUT_FORMAT_PROPERTY
from ballontranslator.ui.text_engine.item import TextBlkItem
from ballontranslator.utils.fontformat import LineSpacingType
from ballontranslator.utils.textblock import TEXT_LAYOUT_VERSION, TextBlock


def _layouts(item: TextBlkItem) -> tuple[QTextLayout, ...]:
    result = []
    block = item.document().firstBlock()
    while block.isValid():
        result.append(block.layout())
        block = block.next()
    return tuple(result)


def _rect(rectangle: QRectF) -> tuple[float, ...]:
    return tuple(round(value, 8) for value in rectangle.getRect())


def _document(item: TextBlkItem) -> tuple:
    result = []
    block = item.document().firstBlock()
    while block.isValid():
        formats = []
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            fmt = fragment.charFormat()
            font = fmt.font()
            value = (font.family(), font.pointSizeF(), font.weight(), font.italic(),
                     fmt.foreground().color().rgba(),
                     letter_spacing_value(fmt, item.fontformat.letter_spacing))
            formats.extend([value] * fragment.length())
            iterator += 1
        result.append((block.position(), block.text(), tuple(formats),
                       line_spacing_values(block.blockFormat(),
                                           item.fontformat.line_spacing,
                                           item.fontformat.line_spacing_type)))
        block = block.next()
    return item.toPlainText(), tuple(result)


def _placement(item: TextBlkItem) -> tuple:
    lines = tuple(tuple(
        (line.textStart(), line.textLength(), _rect(line.rect()),
         round(line.naturalTextWidth(), 8), round(line.ascent(), 8))
        for line in (native.lineAt(index) for index in range(native.lineCount()))
    ) for native in _layouts(item))
    carets = []
    for position in range(item.document().characterCount()):
        caret = item.layout.source_cursor_rect(position)
        hit = None if caret.isEmpty() else item.layout.hitTest(
            caret.center(), Qt.HitTestAccuracy.FuzzyHit,
        )
        carets.append((_rect(caret), hit))
    size = item.documentSize()
    return (round(size.width(), 8), round(size.height(), 8), lines, tuple(carets),
            _rect(item.logical_unpadded_rect()), _rect(item.boundingRect()),
            round(item.pos().x(), 8), round(item.pos().y(), 8))


def _image(item: TextBlkItem) -> QImage:
    scene = QGraphicsScene()
    scene.addItem(item)
    image = QImage(2000, 600, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scene.render(painter, QRectF(0, 0, 2000, 600), QRectF(-100, -100, 2000, 600))
    finally:
        painter.end()
        scene.removeItem(item)
        scene.deleteLater()
    return image


class VerticalLayoutReuseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _dispose(item: TextBlkItem) -> None:
        item.geometry_controller.release_render_resources()
        item.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def _item(self, text: str = 'ABC\nDEF\nA𠮷é\nGHI\n「」\nJKL',
              *, width: float = 900, height: float = 400, stroke: float = 0.0) -> TextBlkItem:
        bounds = [0, 0, width, height]
        block = TextBlock(bounds, _bounding_rect=list(bounds), translation=text,
                          text_layout_version=TEXT_LAYOUT_VERSION)
        block.fontformat.vertical = True
        block.fontformat.font_family = 'Noto Sans CJK KR'
        block.fontformat.font_size = 20
        block.fontformat.letter_spacing = 0.95
        block.fontformat.line_spacing = 1.3
        block.fontformat.alignment = 1
        block.fontformat.stroke_width = stroke
        item = TextBlkItem(block)
        self.addCleanup(self._dispose, item)
        return item

    @staticmethod
    def _range(item: TextBlkItem, start: int, end: int) -> QTextCursor:
        cursor = QTextCursor(item.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        return cursor

    def _assert_matches_fresh(self, item: TextBlkItem,
                              additional: tuple[int, QTextLayout.FormatRange] | None = None) -> None:
        saved = deepcopy(item.blk)
        saved.translation = item.toPlainText()
        saved.rich_text = item.toHtml()
        saved.fontformat = deepcopy(item.fontformat)
        expected = TextBlkItem(saved)
        try:
            if additional is not None:
                number, entry = additional
                native = expected.document().findBlockByNumber(number).layout()
                copied = QTextLayout.FormatRange()
                copied.start, copied.length = entry.start, entry.length
                copied.format = QTextCharFormat(entry.format)
                native.setFormats(list(native.formats()) + [copied])
                expected.layout.reLayoutEverything()
            self.assertEqual(_document(item), _document(expected))
            self.assertEqual(_placement(item), _placement(expected))
            self.assertEqual(_image(item), _image(expected))
        finally:
            self._dispose(expected)

    def test_text_and_format_edits_match_fresh_single_and_multiple_columns(self) -> None:
        for text, width, height in (('ABC\nDEF\nA𠮷é\nGHI', 900, 400), ('ABC' * 55, 1800, 85)):
            with self.subTest(automatic_columns=height == 85):
                # Leave room for every automatic column: serialized logical
                # boxes round growth to whole pixels, independently of reuse.
                item = self._item(text, width=width, height=height)
                item.layout.reLayoutEverything()
                before_length = item.document().characterCount()
                cursor = self._range(item, 0, 3)
                cursor.insertText('XYZ')
                self.assertEqual(item.document().characterCount(), before_length)
                self._assert_matches_fresh(item)
                cursor = self._range(item, 1, 1)
                cursor.insertText('Z𠮷')
                self.assertEqual(item.document().characterCount(), before_length + 3)
                self._assert_matches_fresh(item)
                modifier = QTextCharFormat()
                modifier.setFontPointSize(21)
                modifier.setFontItalic(True)
                modifier.setForeground(QColor('#b02060'))
                self._range(item, 2, 6).mergeCharFormat(modifier)
                self._assert_matches_fresh(item)

    def test_relocated_separator_with_same_block_count_undo_redo(self) -> None:
        item = self._item('ABC\nDEF\nGHI\nJKL')
        document = item.document()
        document.clearUndoRedoStacks()
        before = item.toPlainText()
        count = document.blockCount()
        cursor = self._range(item, 2, 6)
        cursor.beginEditBlock()
        cursor.insertText('CDE\n')
        cursor.endEditBlock()
        after = 'ABCDE\nF\nGHI\nJKL'
        self.assertEqual(document.blockCount(), count)
        self.assertEqual(item.toPlainText(), after)
        self._assert_matches_fresh(item)
        document.undo()
        self.assertEqual(item.toPlainText(), before)
        self._assert_matches_fresh(item)
        document.redo()
        self.assertEqual(item.toPlainText(), after)
        self._assert_matches_fresh(item)

    def test_paragraph_spacing_after_width_only_resize_and_full_relayout(self) -> None:
        item = self._item()
        apply_line_spacing(self._range(item, 0, 2), 2.1, LineSpacingType.Proportional)
        later = item.document().findBlockByNumber(3)
        apply_line_spacing(self._range(item, later.position(), later.position() + 2),
                           0.65, LineSpacingType.Distance)
        for alignment in (0, 1, 2):
            with self.subTest(alignment=alignment):
                item.setAlignment(alignment)
                item.setRect([0, 0, 980, 400])
                before = _placement(item), _image(item)
                item.layout.reLayoutEverything()
                item.layout.reLayoutEverything()
                self.assertEqual((_placement(item), _image(item)), before)
                self._assert_matches_fresh(item)
                item.setRect([0, 0, 900, 400])
                self._assert_matches_fresh(item)

    def test_native_outline_reuses_other_paragraphs_but_extra_style_reflows(self) -> None:
        item = self._item(stroke=0.1)
        # A repeated layout must preserve observable output before an edit.
        initial = _placement(item), _image(item)
        item.layout.reLayoutEverything()
        self.assertEqual((_placement(item), _image(item)), initial)
        live = _layouts(item)
        self.assertTrue(all(len(native.formats()) == 1 for native in live))
        self.assertTrue(all(native.formats()[0].format.property(
            STROKE_ALIGNMENT_LAYOUT_FORMAT_PROPERTY,
        ) for native in live))
        visited: list[int] = []
        original = QTextLayout.beginLayout

        def record(native: QTextLayout) -> None:
            for number, candidate in enumerate(live):
                if native is candidate:
                    visited.append(number)
                    break
            original(native)

        target = item.document().findBlockByNumber(1)
        with patch.object(QTextLayout, 'beginLayout', record):
            self._range(item, target.position() + 1, target.position() + 2).insertText('X')
        self.assertIn(1, visited)
        self.assertNotIn(len(live) - 1, visited, 'an untouched outlined paragraph was reshaped')
        self._assert_matches_fresh(item)

        number = 3
        styled = item.document().findBlockByNumber(number).layout()
        unstyled_image = _image(item)
        extra = QTextLayout.FormatRange()
        extra.start, extra.length = 0, 1
        modifier = QTextCharFormat()
        modifier.setFontItalic(True)
        modifier.setForeground(QColor('#d02060'))
        extra.format = modifier
        styled.setFormats(list(styled.formats()) + [extra])
        item.layout.reLayoutEverything()
        before = _placement(item), _image(item)
        self.assertNotEqual(before[1], unstyled_image)
        visited.clear()
        with patch.object(QTextLayout, 'beginLayout', record):
            item.layout.reLayout()
        self.assertIn(number, visited, 'arbitrary transient style must use normal Qt shaping')
        self.assertNotIn(len(live) - 1, visited)
        self.assertEqual((_placement(item), _image(item)), before)
        self._assert_matches_fresh(item, (number, extra))


if __name__ == '__main__':
    unittest.main()
