import copy
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


_EFFECT_FIELDS = (
    'opacity',
    'shadow_radius',
    'shadow_strength',
    'shadow_color',
    'shadow_offset',
    'gradient_enabled',
    'gradient_start_color',
    'gradient_end_color',
    'gradient_angle',
    'gradient_size',
)
_MUTABLE_EFFECT_FIELDS = (
    'shadow_color',
    'shadow_offset',
    'gradient_start_color',
    'gradient_end_color',
)


def _effect_snapshot(fontformat):
    return {
        name: copy.deepcopy(getattr(fontformat, name))
        for name in _EFFECT_FIELDS
    }


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

    def assert_effects_equal(self, actual, expected):
        self.assertEqual(_effect_snapshot(actual), _effect_snapshot(expected))

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

    def test_effect_override_on_copies_all_global_effect_fields(self):
        pcfg.module.enable_detect = True
        pcfg.let_fnteffect_flag = 1

        for gradient_enabled in (True, False):
            with self.subTest(gradient_enabled=gradient_enabled):
                block = TextBlock(
                    src_is_vertical=True,
                    fontformat=FontFormat(
                        opacity=0.21,
                        shadow_radius=0.11,
                        shadow_strength=0.22,
                        shadow_color=[10, 20, 30],
                        shadow_offset=[1.5, -2.5],
                        gradient_enabled=not gradient_enabled,
                        gradient_start_color=[9, 8, 7],
                        gradient_end_color=[6, 5, 4],
                        gradient_angle=11,
                        gradient_size=0.6,
                    ),
                )
                global_format = FontFormat(
                    opacity=0.73,
                    shadow_radius=0.42,
                    shadow_strength=0.86,
                    shadow_color=[12, 34, 56],
                    shadow_offset=[-3.5, 4.25],
                    gradient_enabled=gradient_enabled,
                    gradient_start_color=[210, 40, 10],
                    gradient_end_color=[5, 80, 230],
                    gradient_angle=137,
                    gradient_size=1.45,
                )

                self._run_page(_PipelineHarness([block], global_format))

                self.assert_effects_equal(block.fontformat, global_format)

    def test_effect_override_off_preserves_detected_block_effect_fields(self):
        pcfg.module.enable_detect = True
        pcfg.let_fnteffect_flag = 0
        original = FontFormat(
            opacity=0.31,
            shadow_radius=0.12,
            shadow_strength=0.44,
            shadow_color=[11, 22, 33],
            shadow_offset=[2.5, -1.5],
            gradient_enabled=True,
            gradient_start_color=[7, 8, 9],
            gradient_end_color=[90, 80, 70],
            gradient_angle=23,
            gradient_size=0.75,
        )
        block = TextBlock(src_is_vertical=True, fontformat=original.deepcopy())
        global_format = FontFormat(
            opacity=0.91,
            shadow_radius=0.52,
            shadow_strength=0.84,
            shadow_color=[101, 102, 103],
            shadow_offset=[-4.5, 6.5],
            gradient_enabled=False,
            gradient_start_color=[170, 180, 190],
            gradient_end_color=[10, 20, 30],
            gradient_angle=211,
            gradient_size=1.65,
        )

        self._run_page(_PipelineHarness([block], global_format))

        self.assert_effects_equal(block.fontformat, original)

    def test_effect_override_does_not_share_mutable_values(self):
        pcfg.module.enable_detect = True
        pcfg.let_fnteffect_flag = 1
        blocks = [
            TextBlock(src_is_vertical=True, fontformat=FontFormat()),
            TextBlock(src_is_vertical=True, fontformat=FontFormat()),
        ]
        global_format = FontFormat(
            shadow_color=[12, 34, 56],
            shadow_offset=[-3.5, 4.25],
            gradient_enabled=True,
            gradient_start_color=[210, 40, 10],
            gradient_end_color=[5, 80, 230],
        )

        self._run_page(_PipelineHarness(blocks, global_format))

        for name in _MUTABLE_EFFECT_FIELDS:
            with self.subTest(field=name):
                global_value = getattr(global_format, name)
                first_value = getattr(blocks[0].fontformat, name)
                second_value = getattr(blocks[1].fontformat, name)
                expected = list(global_value)

                self.assertEqual(first_value, expected)
                self.assertEqual(second_value, expected)
                self.assertIsNot(first_value, global_value)
                self.assertIsNot(second_value, global_value)
                self.assertIsNot(first_value, second_value)

                global_value[0] += 1000
                global_after_mutation = list(global_value)
                self.assertEqual(first_value, expected)
                self.assertEqual(second_value, expected)

                first_value[-1] -= 1000
                self.assertEqual(global_value, global_after_mutation)
                self.assertEqual(second_value, expected)

    def test_preserve_run_restores_full_backup_and_skips_global_quartet(self):
        pcfg.let_fnteffect_flag = 1
        block = TextBlock(fontformat=FontFormat(font_family='Current'))
        backup = FontFormat(
            font_family='Preserved',
            font_size=31,
            frgb=[12, 34, 56],
            opacity=0.37,
            shadow_radius=0.13,
            shadow_strength=0.47,
            shadow_color=[13, 24, 35],
            shadow_offset=[2.25, -3.5],
            gradient_enabled=True,
            gradient_start_color=[21, 43, 65],
            gradient_end_color=[210, 180, 90],
            gradient_angle=29,
            gradient_size=0.85,
            horizontal_scale=0.8,
            vertical_scale=1.35,
            slant_angle=-18,
            glyph_slant_angle=-12,
        )
        global_format = FontFormat(
            font_family='Global',
            opacity=0.91,
            shadow_radius=0.51,
            shadow_strength=0.88,
            shadow_color=[100, 110, 120],
            shadow_offset=[-7.0, 8.0],
            gradient_enabled=False,
            gradient_start_color=[190, 180, 170],
            gradient_end_color=[10, 20, 30],
            gradient_angle=211,
            gradient_size=1.7,
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
        pcfg.let_fnteffect_flag = 1
        global_format = FontFormat(
            opacity=0.73,
            shadow_radius=0.42,
            shadow_strength=0.86,
            shadow_color=[12, 34, 56],
            shadow_offset=[-3.5, 4.25],
            gradient_enabled=True,
            gradient_start_color=[210, 40, 10],
            gradient_end_color=[5, 80, 230],
            gradient_angle=137,
            gradient_size=1.45,
            horizontal_scale=1.0,
            vertical_scale=1.2,
            slant_angle=20,
            glyph_slant_angle=12,
        )
        backup = FontFormat(
            opacity=0.27,
            shadow_radius=0.12,
            shadow_strength=0.38,
            shadow_color=[3, 6, 9],
            shadow_offset=[1.0, -2.0],
            gradient_enabled=False,
            gradient_start_color=[1, 2, 3],
            gradient_end_color=[4, 5, 6],
            gradient_angle=19,
            gradient_size=0.65,
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
                self.assert_effects_equal(block.fontformat, global_format)
                warning.assert_called_once()

    def test_inpaint_only_skips_postprocess_and_keeps_full_style(self):
        pcfg.module.enable_detect = False
        pcfg.module.enable_ocr = False
        pcfg.module.enable_translate = False
        pcfg.module.enable_inpaint = True
        pcfg.let_fnteffect_flag = 1
        original = FontFormat(
            font_family='Untouched',
            alignment=2,
            vertical=True,
            opacity=0.39,
            shadow_radius=0.17,
            shadow_strength=0.52,
            shadow_color=[14, 28, 42],
            shadow_offset=[2.5, -3.75],
            gradient_enabled=True,
            gradient_start_color=[20, 60, 100],
            gradient_end_color=[200, 160, 120],
            gradient_angle=31,
            gradient_size=0.9,
            horizontal_scale=0.8,
            vertical_scale=1.35,
            slant_angle=-18,
            glyph_slant_angle=-12,
        )
        block = TextBlock(fontformat=original.deepcopy())
        harness = _PipelineHarness(
            [block],
            FontFormat(
                opacity=0.95,
                shadow_radius=0.61,
                shadow_strength=0.9,
                shadow_color=[90, 100, 110],
                shadow_offset=[-8.0, 9.0],
                gradient_enabled=False,
                gradient_start_color=[220, 210, 200],
                gradient_end_color=[10, 20, 30],
                gradient_angle=207,
                gradient_size=1.8,
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
