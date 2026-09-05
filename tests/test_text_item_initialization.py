import os
import unittest
from unittest.mock import patch


os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy.QtGui import QTextCursor
from qtpy.QtWidgets import QApplication

from ballontranslator.ui.text_engine.effects.renderer import TextEffectRenderer
from ballontranslator.ui.text_engine.item import TextBlkItem
from ballontranslator.utils.fontformat import (
    ProjectiveTextTransform,
    TextTransformStack,
)
from ballontranslator.utils.textblock import TextBlock


class TextItemInitializationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _block(vertical: bool, rich: bool) -> TextBlock:
        bounds = [0, 0, 500, 350]
        block = TextBlock(bounds)
        block._bounding_rect = list(bounds)
        block.fontformat.font_family = 'Arial'
        block.fontformat.font_size = 24
        block.fontformat.vertical = vertical
        block.fontformat.stroke_width = 0.1
        block.fontformat.text_transform = TextTransformStack((
            ProjectiveTextTransform(vertical_scale=1.2),
        ))
        block.translation = 'Long text 0123456789 ' * 5
        if rich:
            block.rich_text = (
                '<p><span style="color: #2468ac; letter-spacing: 1px;">'
                + block.translation
                + '</span><span style="font-weight: bold; '
                'text-emphasis-style: filled dot;">end한😀</span></p>'
            )
        return block

    def test_import_rasterizes_only_the_completed_document(self) -> None:
        render = TextEffectRenderer._render_effect_surface
        for vertical in (False, True):
            for rich in (False, True):
                with self.subTest(vertical=vertical, rich=rich):
                    block = self._block(vertical, rich)
                    saved = (block.translation, block.rich_text)
                    with patch.object(
                        TextEffectRenderer,
                        '_render_effect_surface',
                        autospec=True,
                        side_effect=render,
                    ) as raster:
                        item = TextBlkItem(block)
                    self.assertEqual(raster.call_count, 1)
                    self.assertEqual(
                        item.toPlainText(),
                        block.translation + ('end한😀' if rich else ''),
                    )
                    self.assertEqual((block.translation, block.rich_text), saved)
                    self.assertFalse(item.repainting)
                    self.assertEqual(item.transform().m22(), 1.2)
                    initial = item.effect_renderer.background_pixmap.toImage()
                    item.repaint_background()
                    self.assertEqual(
                        item.effect_renderer.background_pixmap.toImage(), initial
                    )
                    item.geometry_controller.release_render_resources()

    def test_edit_and_undo_refresh_the_imported_effects(self) -> None:
        for vertical in (False, True):
            with self.subTest(vertical=vertical):
                item = TextBlkItem(self._block(vertical, True))
                document = item.document()
                document.clearUndoRedoStacks()
                initial_text = item.toPlainText()
                initial_image = item.effect_renderer.background_pixmap.toImage()
                cursor = QTextCursor(document)
                cursor.movePosition(QTextCursor.MoveOperation.End)
                cursor.insertText(' added')
                edited_image = item.effect_renderer.background_pixmap.toImage()
                self.assertEqual(item.toPlainText(), initial_text + ' added')
                self.assertNotEqual(edited_image, initial_image)
                item.repaint_background()
                self.assertEqual(
                    item.effect_renderer.background_pixmap.toImage(), edited_image
                )
                document.undo()
                self.assertEqual(item.toPlainText(), initial_text)
                self.assertEqual(
                    item.effect_renderer.background_pixmap.toImage(), initial_image
                )
                item.geometry_controller.release_render_resources()


if __name__ == '__main__':
    unittest.main()
