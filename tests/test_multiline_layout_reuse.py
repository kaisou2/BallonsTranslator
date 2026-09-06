"""External regressions for reusing unchanged native paragraph layouts.

Run from the checkout under test, with this directory as unittest's discovery
root. Select Qt binding/backend outside the test and run GUI suites separately.
"""
from __future__ import annotations

from copy import deepcopy
from html import escape
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy.QtCore import QCoreApplication, QEvent, QRectF, Qt
from qtpy.QtGui import (
    QColor, QImage, QPainter, QTextCharFormat, QTextCursor, QTextDocument, QTextLayout,
)
from qtpy.QtWidgets import QApplication, QGraphicsScene

from ballontranslator.ui.text_engine.annotations import (
    LETTER_SPACING_ATTRIBUTE, apply_letter_spacing, apply_line_spacing,
    letter_spacing_value, line_spacing_values,
)
from ballontranslator.ui.text_engine.item import TextBlkItem
from ballontranslator.utils.fontformat import LineSpacingType
from ballontranslator.utils.textblock import TEXT_LAYOUT_VERSION, TextBlock


def _short_paragraphs(count: int) -> tuple[str, str]:
    phrases = ('여기는', '短文です！？', 'A😀e\u0301', 'office fi 123', '한글 + ABC')
    paragraphs, lines = [], []
    for index in range(count):
        prefix = f'p{index:03d} '
        phrase = phrases[index % len(phrases)]
        lines.append(prefix + phrase)
        paragraphs.append(
            '<p style="margin:0; line-height:1.3;">'
            f'<span {LETTER_SPACING_ATTRIBUTE}="0.95">'
            + prefix + '<b>' + escape(phrase) + '</b></span></p>'
        )
    html = (
        '<html><body style="font-family:Arial; font-size:18pt; color:#223344;">'
        + ''.join(paragraphs) + '</body></html>'
    )
    return html, '\n'.join(lines)


def _live_layouts(item: TextBlkItem) -> tuple[QTextLayout, ...]:
    layouts = []
    block = item.document().firstBlock()
    while block.isValid():
        layouts.append(block.layout())
        block = block.next()
    return tuple(layouts)


def _document_snapshot(document: QTextDocument) -> tuple:
    blocks = []
    block = document.firstBlock()
    while block.isValid():
        formats = []
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            char_format = fragment.charFormat()
            font = char_format.font()
            values = (
                font.family(), font.pointSizeF(), font.weight(), font.italic(),
                font.letterSpacingType(), font.letterSpacing(),
                char_format.foreground().color().rgba(),
                letter_spacing_value(char_format),
            )
            # Fragment boundaries can merge during HTML serialization. Compare
            # the effective formatting at each Qt UTF-16 position instead.
            formats.extend([values] * fragment.length())
            iterator += 1
        blocks.append((
            block.position(), block.text(), tuple(formats),
            line_spacing_values(block.blockFormat()),
        ))
        block = block.next()
    return document.toPlainText(), tuple(blocks)


def _layout_snapshot(item: TextBlkItem) -> tuple:
    blocks = []
    for layout in _live_layouts(item):
        blocks.append(tuple(
            (line.textStart(), line.textLength(),
             round(line.position().x(), 8), round(line.position().y(), 8),
             round(line.naturalTextWidth(), 8), round(line.height(), 8))
            for line in (layout.lineAt(index) for index in range(layout.lineCount()))
        ))
    size = item.documentSize()
    return round(size.width(), 8), round(size.height(), 8), tuple(blocks)


def _scene_image(item: TextBlkItem) -> QImage:
    scene = QGraphicsScene()
    scene.addItem(item)
    image = QImage(720, 480, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        scene.render(painter, QRectF(0, 0, 720, 480), scene.itemsBoundingRect())
    finally:
        painter.end()
        scene.removeItem(item)
        scene.deleteLater()
    return image


def _geometry(item: TextBlkItem) -> tuple:
    return (
        tuple(round(value, 8) for value in item.logical_unpadded_rect().getRect()),
        tuple(round(value, 8) for value in item.boundingRect().getRect()),
        round(item.pos().x(), 8), round(item.pos().y(), 8),
        round(item.transformOriginPoint().x(), 8),
        round(item.transformOriginPoint().y(), 8),
    )


class MultilineLayoutReuseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _dispose(item: TextBlkItem) -> None:
        item.geometry_controller.release_render_resources()
        item.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def _item(self, count: int = 8, *, stroke_width: float = 0.0) -> TextBlkItem:
        fixture = _short_paragraphs(count)
        # Short mixed-format paragraphs fit one line without relying on a font's
        # exact advance. Height also avoids automatic growth during these edits.
        bounds = [0, 0, 900, max(1200, count * 40)]
        block = TextBlock(bounds, text_layout_version=TEXT_LAYOUT_VERSION)
        block._bounding_rect = list(bounds)
        block.rich_text, block.translation = fixture
        block.fontformat.font_family = 'Arial'
        block.fontformat.font_size = 24
        block.fontformat.stroke_width = stroke_width
        block.fontformat.letter_spacing = 0.95
        item = TextBlkItem(block)
        self.addCleanup(self._dispose, item)
        self.assertEqual(item.toPlainText(), fixture[1])
        self.assertTrue(all(layout.lineCount() == 1 for layout in _live_layouts(item)))
        return item

    def _assert_matches_fresh_import(self, item: TextBlkItem) -> None:
        saved = deepcopy(item.blk)
        saved.translation = item.toPlainText()
        saved.rich_text = item.toHtml()
        saved.fontformat = deepcopy(item.fontformat)
        expected = TextBlkItem(saved)
        try:
            self.assertEqual(_document_snapshot(item.document()), _document_snapshot(expected.document()))
            self.assertEqual(_layout_snapshot(item), _layout_snapshot(expected))
            self.assertEqual(_geometry(item), _geometry(expected))
            self.assertEqual(_scene_image(item), _scene_image(expected))
        finally:
            self._dispose(expected)

    def test_unchanged_relayout_keeps_lines_without_reshaping_them(self) -> None:
        item = self._item(20)
        self.assertIsNone(item.layout.render_delegate)
        self.assertTrue(all(not layout.formats() for layout in _live_layouts(item)))
        before = _layout_snapshot(item), _geometry(item), _scene_image(item)
        live = _live_layouts(item)
        visited = []
        original = QTextLayout.beginLayout

        def record(layout: QTextLayout) -> None:
            if any(layout is candidate for candidate in live):
                visited.append(layout)
            original(layout)

        with patch.object(QTextLayout, 'beginLayout', record):
            item.layout.reLayout()
            item.layout.reLayout()
        self.assertEqual(visited, [])
        self.assertEqual((_layout_snapshot(item), _geometry(item), _scene_image(item)), before)

    def test_format_change_reshapes_the_affected_paragraph_only(self) -> None:
        item = self._item(20)
        self.assertIsNone(item.layout.render_delegate)
        self.assertTrue(all(not layout.formats() for layout in _live_layouts(item)))
        document = item.document()
        number = 10
        block = document.findBlockByNumber(number)
        cursor = QTextCursor(document)
        # Stay inside the block: changing a paragraph separator may legitimately
        # invalidate its neighbor's insertion format too.
        cursor.setPosition(block.position() + 1)
        cursor.setPosition(block.position() + 4, QTextCursor.MoveMode.KeepAnchor)
        live = _live_layouts(item)
        visited = []
        original = QTextLayout.beginLayout

        def record(layout: QTextLayout) -> None:
            for index, candidate in enumerate(live):
                if layout is candidate:
                    visited.append(index)
                    break
            original(layout)

        with patch.object(QTextLayout, 'beginLayout', record):
            apply_letter_spacing(cursor, 1.25, vertical=False)
        self.assertTrue(visited, 'the changed native line must be shaped')
        self.assertEqual(set(visited), {number})
        self._assert_matches_fresh_import(item)

    def test_saved_import_native_shaping_grows_linearly_with_paragraphs(self) -> None:
        original = QTextLayout.beginLayout
        counts = []
        for paragraphs in (10, 100):
            with self.subTest(paragraphs=paragraphs):
                visited = []

                def record(layout: QTextLayout) -> None:
                    visited.append(layout)
                    original(layout)

                with patch.object(QTextLayout, 'beginLayout', record):
                    # Saved import can reuse plain lines before the final stroke
                    # render installs additional native alignment formats.
                    item = self._item(paragraphs, stroke_width=0.1)
                live = _live_layouts(item)
                counts.append(sum(
                    any(layout is candidate for candidate in live) for layout in visited
                ))
                self.assertGreater(counts[-1], 0)
                self.assertEqual(item.document().blockCount(), paragraphs)
        # Ten times the input may need about ten times the shaping, never the
        # roughly hundredfold work caused by rebuilding every block per format.
        self.assertLessEqual(counts[1], 12 * counts[0], counts)

    def test_text_format_width_and_writing_mode_changes_invalidate_reuse(self) -> None:
        item = self._item()
        document = item.document()
        document.clearUndoRedoStacks()
        original_text = item.toPlainText()
        cursor = QTextCursor(document)
        cursor.setPosition(document.findBlockByNumber(3).position() + 2)
        cursor.insertText('Z😀')
        self._assert_matches_fresh_import(item)
        document.undo()
        self.assertEqual(item.toPlainText(), original_text)
        self._assert_matches_fresh_import(item)
        document.redo()
        self.assertIn('Z😀', item.toPlainText())
        self._assert_matches_fresh_import(item)

        cursor = QTextCursor(document)
        cursor.setPosition(document.findBlockByNumber(2).position())
        cursor.setPosition(cursor.position() + 4, QTextCursor.MoveMode.KeepAnchor)
        modifier = QTextCharFormat()
        modifier.setFontPointSize(24)
        modifier.setFontItalic(True)
        modifier.setForeground(QColor('#b02060'))
        cursor.mergeCharFormat(modifier)
        self._assert_matches_fresh_import(item)
        apply_line_spacing(cursor, 0.75, LineSpacingType.Distance)
        self._assert_matches_fresh_import(item)

        # Resize the logical box. Passing padding=False would set a fractional
        # source box that project serialization deliberately rounds to pixels.
        item.setRect([0, 0, 90, 1200])
        self.assertTrue(any(layout.lineCount() > 1 for layout in _live_layouts(item)))
        self._assert_matches_fresh_import(item)
        item.setRect([0, 0, 900, 1200])
        self._assert_matches_fresh_import(item)
        item.setVertical(True)
        self._assert_matches_fresh_import(item)
        item.setVertical(False)
        self._assert_matches_fresh_import(item)


if __name__ == '__main__':
    unittest.main()
