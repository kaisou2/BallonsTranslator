"""Behavior and complexity regressions for long-text layout indexing."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy.QtGui import QTextBlock, QTextCharFormat, QTextCursor
from qtpy.QtWidgets import QApplication

from ballontranslator.ui.text_engine.item import TextBlkItem
from ballontranslator.utils.textblock import TEXT_LAYOUT_VERSION, TextBlock


def make_item(text: str, vertical: bool = False) -> TextBlkItem:
    bounds = [0, 0, 320, 240]
    block = TextBlock(bounds, text_layout_version=TEXT_LAYOUT_VERSION)
    block._bounding_rect = list(bounds)
    block.translation = text
    block.vertical = vertical
    block.fontformat.font_family = 'DejaVu Sans'
    block.fontformat.font_size = 24
    block.fontformat.stroke_width = 0
    block.fontformat.letter_spacing = 1.0
    return TextBlkItem(block, 0)


def document_runs(block: QTextBlock) -> list[tuple[int, int, QTextCharFormat]]:
    result = []
    iterator = block.begin()
    while not iterator.atEnd():
        fragment = iterator.fragment()
        start = fragment.position() - block.position()
        if fragment.length() > 0:
            result.append((start, start + fragment.length(), fragment.charFormat()))
        iterator += 1
    return result


class TextLayoutPerformanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def assert_matches_document(self, item: TextBlkItem) -> None:
        block = item.document().firstBlock()
        while block.isValid():
            runs = document_runs(block)
            number = block.blockNumber()
            length = block.length() - 1
            for position in range(-2, length + 4):
                expected = None
                if runs:
                    target = position if 0 <= position < length else length - 1
                    expected = next(fmt for a, b, fmt in runs if a <= target < b)
                actual = item.layout.get_char_fontfmt(number, position)
                if expected is None:
                    self.assertIsNone(actual)
                else:
                    self.assertEqual(actual.font, expected.font())
            for start in (-3, 0, 1, length // 2, length, length + 3):
                for end in (0, 2, length // 2, length, length + 5):
                    expected = tuple(
                        (max(a, start, 0), min(b, end, length), fmt)
                        for a, b, fmt in runs
                        if min(b, end, length) > max(a, start, 0)
                    )
                    self.assertEqual(
                        item.layout.fragment_format_ranges(number, start, end),
                        expected,
                    )
                    best, best_size = None, -1
                    for position in range(start, end):
                        if not runs:
                            break
                        target = position if 0 <= position < length else length - 1
                        fmt = next(fmt for a, b, fmt in runs if a <= target < b)
                        size = fmt.font().pointSizeF()
                        if size > best_size:
                            best, best_size = fmt, size
                    actual = item.layout.largest_font_format(number, start, end)
                    if best is None:
                        self.assertIsNone(actual)
                    else:
                        self.assertEqual(actual.font, best.font())
            block = block.next()

    def test_utf16_fragments_and_ime_fallback_in_both_writing_modes(self) -> None:
        for vertical in (False, True):
            with self.subTest(vertical=vertical):
                item = make_item('A😀한글e\u0301XYZ\n👩\u200d🚀abc', vertical)
                cursor = QTextCursor(item.document())
                cursor.setPosition(1)
                cursor.setPosition(5, QTextCursor.MoveMode.KeepAnchor)
                fmt = QTextCharFormat()
                fmt.setFontPointSize(42)
                cursor.mergeCharFormat(fmt)
                self.assert_matches_document(item)

    def test_empty_blocks(self) -> None:
        for text in ('', '\n\n'):
            self.assert_matches_document(make_item(text))

    def test_format_change_undo_and_redo_rebuild_the_index(self) -> None:
        item = make_item('abc😀def')
        doc = item.document()
        before = item.layout.fragment_format_ranges(0, 0, doc.characterCount() - 1)
        cursor = QTextCursor(doc)
        cursor.setPosition(1)
        cursor.setPosition(6, QTextCursor.MoveMode.KeepAnchor)
        fmt = QTextCharFormat()
        fmt.setFontPointSize(48)
        cursor.mergeCharFormat(fmt)
        self.assert_matches_document(item)
        self.assertEqual(item.layout.largest_font_format(0, 0, 8).size, 48)
        doc.undo()
        self.assert_matches_document(item)
        self.assertEqual(
            item.layout.fragment_format_ranges(0, 0, doc.characterCount() - 1),
            before,
        )
        doc.redo()
        self.assert_matches_document(item)
        self.assertEqual(item.layout.largest_font_format(0, 0, 8).size, 48)

    def test_insert_delete_and_document_replacement(self) -> None:
        item = make_item('abc\n한글')
        cursor = QTextCursor(item.document())
        cursor.setPosition(1)
        cursor.insertText('😀')
        self.assert_matches_document(item)
        cursor.setPosition(1)
        cursor.setPosition(3, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        self.assert_matches_document(item)
        item.setPlainText('replacement\n')
        self.assert_matches_document(item)

    def test_equal_size_keeps_the_first_font(self) -> None:
        item = make_item('AAAABBBB')
        cursor = QTextCursor(item.document())
        cursor.setPosition(4)
        cursor.setPosition(8, QTextCursor.MoveMode.KeepAnchor)
        fmt = QTextCharFormat()
        fmt.setFontItalic(True)
        cursor.mergeCharFormat(fmt)
        self.assertFalse(item.layout.largest_font_format(0, 0, 8).font.italic())
        self.assertTrue(item.layout.largest_font_format(0, 4, 8).font.italic())

    def test_unset_point_size_preserves_the_line_metric_fallback(self) -> None:
        item = make_item('text')
        cursor = QTextCursor(item.document())
        cursor.select(QTextCursor.SelectionType.Document)
        font = cursor.charFormat().font()
        font.setPixelSize(36)
        char_format = QTextCharFormat()
        char_format.setFont(font)
        cursor.mergeCharFormat(char_format)
        self.assertEqual(item.layout.get_char_fontfmt(0, 0).size, -1)
        self.assertIsNone(item.layout.largest_font_format(0, 0, 4))
        self.assert_matches_document(item)

    def test_horizontal_font_lookup_scales_with_lines_not_characters(self) -> None:
        item = make_item('A' * 4000)
        layout = item.layout
        with patch.object(
            layout, 'get_char_fontfmt', wraps=layout.get_char_fontfmt,
        ) as lookup:
            layout.reLayout()
        line_count = item.document().firstBlock().layout().lineCount()
        self.assertLessEqual(lookup.call_count, line_count + 2)
        self.assertEqual(item.toPlainText(), 'A' * 4000)

    def test_horizontal_layout_does_not_copy_the_paragraph_per_line(self) -> None:
        original = QTextBlock.text
        for count in (1000, 4000):
            with self.subTest(count=count):
                text = '한글😀' * count
                item = make_item(text)
                text_copies = 0

                def copy_text(block: QTextBlock) -> str:
                    nonlocal text_copies
                    text_copies += 1
                    return original(block)

                with patch.object(QTextBlock, 'text', copy_text):
                    item.layout.reLayout()
                self.assertLessEqual(text_copies, 4)
                self.assertEqual(item.toPlainText(), text)


if __name__ == '__main__':
    unittest.main()
