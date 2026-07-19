import os
import unittest


os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy import API_NAME, QT_VERSION
from qtpy.QtWidgets import QApplication, QGraphicsScene

from ballontranslator.ui.scene_textlayout import (
    HorizontalTextDocumentLayout,
    VerticalTextDocumentLayout,
)
from ballontranslator.ui.textitem import TextBlkItem
from ballontranslator.utils import shared
from ballontranslator.utils.fontformat import FontFormat
from ballontranslator.utils.textblock import TextBlock


shared.FLAG_QT6 = QT_VERSION.startswith('6')
shared.USE_PYSIDE6 = API_NAME == 'PySide6'

_APP = QApplication.instance() or QApplication([])


def make_item(glyph_slant_angle):
    font_format = FontFormat(
        font_size=26,
        glyph_slant_angle=glyph_slant_angle,
        stroke_width=0.1,
    )
    block = TextBlock(
        xyxy=[0, 0, 400, 300],
        _bounding_rect=[0, 0, 400, 300],
        translation='TEST',
        fontformat=font_format,
    )
    item = TextBlkItem(block)
    scene = QGraphicsScene()
    scene.addItem(item)
    return item, block, scene


class TextWritingModeTransitionTests(unittest.TestCase):
    def test_stroked_glyph_slant_writing_mode_transitions_are_coherent(self):
        for angle in (-40.0, 40.0):
            with self.subTest(angle=angle):
                item, block, _scene = make_item(angle)
                transition_states = []
                repaint_background = item.repaint_background

                def record_transition_state():
                    transition_states.append(
                        (
                            isinstance(
                                item.document().documentLayout(),
                                VerticalTextDocumentLayout,
                            ),
                            isinstance(item.layout, VerticalTextDocumentLayout),
                            block.fontformat.vertical,
                        )
                    )
                    return repaint_background()

                item.repaint_background = record_transition_state

                self.assertIsInstance(item.layout, HorizontalTextDocumentLayout)
                for _ in range(3):
                    item.setVertical(True)

                    self.assertIsInstance(
                        item.layout, VerticalTextDocumentLayout
                    )
                    self.assertIs(item.document().documentLayout(), item.layout)
                    self.assertTrue(block.fontformat.vertical)
                    self.assertEqual(item.layout.glyph_slant_angle, angle)
                    self.assertIsNotNone(item.background_pixmap)

                    item.setVertical(False)

                    self.assertIsInstance(
                        item.layout, HorizontalTextDocumentLayout
                    )
                    self.assertIs(item.document().documentLayout(), item.layout)
                    self.assertFalse(block.fontformat.vertical)
                    self.assertEqual(item.layout.glyph_slant_angle, angle)
                    self.assertIsNotNone(item.background_pixmap)

                self.assertTrue(transition_states)
                for (
                    document_vertical,
                    item_vertical,
                    format_vertical,
                ) in transition_states:
                    self.assertEqual(document_vertical, item_vertical)
                    self.assertEqual(item_vertical, format_vertical)


if __name__ == '__main__':
    unittest.main()
