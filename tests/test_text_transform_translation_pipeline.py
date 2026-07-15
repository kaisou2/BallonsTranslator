import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy.QtWidgets import QApplication

from ballontranslator.ui import mainwindow as mainwindow_module
from ballontranslator.ui.mainwindow import (
    MainWindow,
    _apply_global_text_transforms,
)
from ballontranslator.utils.config import pcfg
from ballontranslator.utils.fontformat import FontFormat
from ballontranslator.utils.textblock import TextBlock


def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


_APP = qapp()


class _FakeProject:
    def __init__(self, pages):
        self.pages = pages
        self._names = list(pages)
        self.saved = 0
        self.current_page = None

    @property
    def num_pages(self):
        return len(self._names)

    def get_blklist_byidx(self, index):
        return self.pages[self._names[index]]

    def idx2pagename(self, index):
        return self._names[index]

    def set_current_img_byidx(self, index):
        self.current_page = index

    def save(self):
        self.saved += 1


class _FakePageList:
    def currentIndex(self):
        return SimpleNamespace(row=lambda: 0)

    def setCurrentRow(self, _index):
        raise AssertionError('the focused fake page should not change')


class _PipelineHarness:
    _matching_backup_fontformats = MainWindow._matching_backup_fontformats
    _warn_textstyle_preserve_fallback = MainWindow._warn_textstyle_preserve_fallback

    def __init__(self, blocks, global_format):
        self.imgtrans_proj = _FakeProject({'page.png': blocks})
        self.textPanel = SimpleNamespace(
            formatpanel=SimpleNamespace(global_format=global_format)
        )
        self.st_manager = SimpleNamespace(
            auto_textlayout_flag=False,
            textblk_item_list=[],
            updateSceneTextitems=lambda: None,
        )
        self.canvas = SimpleNamespace(updateCanvas=lambda: None)
        self.pageList = _FakePageList()
        self.backup_blkstyles = []
        self._backup_blkstyle_block_ids = []
        self._textstyle_preserve_warning_pages = set()
        self._run_imgtrans_wo_textstyle_update = False
        self.postprocess_calls = 0
        self.saved_current_pages = 0

    def postprocess_translations(self, _blocks):
        self.postprocess_calls += 1

    def saveCurrentPage(self, *_args):
        self.saved_current_pages += 1


class TextTransformTranslationPipelineTests(unittest.TestCase):
    _PROGRAM_FLAGS = (
        'let_fntsize_flag',
        'let_fntstroke_flag',
        'let_fntcolor_flag',
        'let_fnt_scolor_flag',
        'let_alignment_flag',
        'let_fnteffect_flag',
        'let_writing_mode_flag',
        'let_family_flag',
        'let_autolayout_flag',
    )
    _MODULE_FLAGS = (
        'enable_detect',
        'enable_ocr',
        'enable_translate',
        'enable_inpaint',
    )

    def setUp(self):
        self._program_values = {
            name: getattr(pcfg, name) for name in self._PROGRAM_FLAGS
        }
        self._module_values = {
            name: getattr(pcfg.module, name) for name in self._MODULE_FLAGS
        }
        for name in self._PROGRAM_FLAGS[:-1]:
            setattr(pcfg, name, 0)
        pcfg.let_autolayout_flag = False
        pcfg.module.enable_detect = False
        pcfg.module.enable_ocr = False
        pcfg.module.enable_translate = True
        pcfg.module.enable_inpaint = False

    def tearDown(self):
        for name, value in self._program_values.items():
            setattr(pcfg, name, value)
        for name, value in self._module_values.items():
            setattr(pcfg.module, name, value)

    @staticmethod
    def _run_page(harness):
        MainWindow.on_pagtrans_finished(harness, 0)

    def test_global_helper_copies_normalized_quartet_including_neutral_horizontal(self):
        block = TextBlock(
            fontformat=FontFormat(
                horizontal_scale=0.8,
                vertical_scale=1.35,
                slant_angle=-18,
                glyph_slant_angle=-12,
            )
        )
        global_format = FontFormat(
            horizontal_scale=1.0,
            vertical_scale=1.2,
            slant_angle=20,
            glyph_slant_angle=12,
        )

        self.assertTrue(_apply_global_text_transforms(block, global_format))
        self.assertEqual(block.fontformat.text_transform, (1.0, 1.2, 20.0, 12.0))
        self.assertFalse(_apply_global_text_transforms(block, global_format))

    def test_normal_run_applies_global_quartet_to_every_result_block(self):
        blocks = [
            TextBlock(
                fontformat=FontFormat(
                    horizontal_scale=0.8,
                    vertical_scale=1.35,
                    slant_angle=-18,
                    glyph_slant_angle=-12,
                )
            ),
            TextBlock(fontformat=FontFormat()),
        ]
        global_format = FontFormat(
            horizontal_scale=1.0,
            vertical_scale=1.2,
            slant_angle=20,
            glyph_slant_angle=12,
        )
        harness = _PipelineHarness(blocks, global_format)

        self._run_page(harness)

        for block in blocks:
            self.assertEqual(
                block.fontformat.text_transform,
                (1.0, 1.2, 20.0, 12.0),
            )
        self.assertEqual(harness.postprocess_calls, 1)

    def test_detect_created_block_uses_normal_global_quartet(self):
        pcfg.module.enable_detect = True
        block = TextBlock(src_is_vertical=True, fontformat=FontFormat())
        global_format = FontFormat(
            horizontal_scale=1.0,
            vertical_scale=1.2,
            slant_angle=20,
            glyph_slant_angle=12,
        )
        harness = _PipelineHarness([block], global_format)

        self._run_page(harness)

        self.assertEqual(block.fontformat.text_transform, (1.0, 1.2, 20.0, 12.0))

    def test_preserve_run_restores_full_backup_and_skips_global_quartet(self):
        block = TextBlock(fontformat=FontFormat(font_family='Current'))
        backup = FontFormat(
            font_family='Preserved',
            font_size=31,
            frgb=[12, 34, 56],
            horizontal_scale=0.8,
            vertical_scale=1.35,
            slant_angle=-18,
            glyph_slant_angle=-12,
        )
        global_format = FontFormat(
            font_family='Global',
            horizontal_scale=1.0,
            vertical_scale=1.2,
            slant_angle=20,
            glyph_slant_angle=12,
        )
        harness = _PipelineHarness([block], global_format)
        harness._run_imgtrans_wo_textstyle_update = True
        harness.backup_blkstyles = [[backup]]

        self._run_page(harness)

        self.assertEqual(block.fontformat, backup)
        self.assertEqual(block.fontformat.text_transform, (0.8, 1.35, -18.0, -12.0))

    def test_preserve_fallback_cases_use_normal_semantics_and_warn_once(self):
        global_format = FontFormat(
            horizontal_scale=1.0,
            vertical_scale=1.2,
            slant_angle=20,
            glyph_slant_angle=12,
        )
        backup = FontFormat(
            horizontal_scale=0.8,
            vertical_scale=1.35,
            slant_angle=-18,
            glyph_slant_angle=-12,
        )

        cases = (
            ('missing backup', [], []),
            ('count mismatch', [[backup, backup.deepcopy()]], []),
            ('identity mismatch', [[backup]], [(0,)]),
        )
        for name, backups, identity_pages in cases:
            block = TextBlock(fontformat=backup.deepcopy())
            harness = _PipelineHarness([block], global_format)
            harness._run_imgtrans_wo_textstyle_update = True
            harness.backup_blkstyles = backups
            harness._backup_blkstyle_block_ids = identity_pages
            with self.subTest(name=name), patch.object(
                mainwindow_module.LOGGER, 'warning'
            ) as warning:
                self._run_page(harness)
                self._run_page(harness)
                self.assertEqual(
                    block.fontformat.text_transform,
                    (1.0, 1.2, 20.0, 12.0),
                )
                warning.assert_called_once()

    def test_inpaint_only_skips_postprocess_and_keeps_full_style(self):
        pcfg.module.enable_detect = False
        pcfg.module.enable_ocr = False
        pcfg.module.enable_translate = False
        pcfg.module.enable_inpaint = True
        original = FontFormat(
            font_family='Untouched',
            alignment=2,
            vertical=True,
            horizontal_scale=0.8,
            vertical_scale=1.35,
            slant_angle=-18,
            glyph_slant_angle=-12,
        )
        block = TextBlock(fontformat=original.deepcopy())
        harness = _PipelineHarness(
            [block],
            FontFormat(
                horizontal_scale=1.0,
                vertical_scale=1.2,
                slant_angle=20,
                glyph_slant_angle=12,
            ),
        )
        harness._run_imgtrans_wo_textstyle_update = True

        self._run_page(harness)

        self.assertEqual(block.fontformat, original)
        self.assertEqual(harness.postprocess_calls, 0)


if __name__ == '__main__':
    unittest.main()
