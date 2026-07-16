import copy
import os
import sys
import unittest
import weakref
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy.QtWidgets import QApplication

from ballontranslator.ui import mainwindow as mainwindow_module
from ballontranslator.ui.mainwindow import (
    MainWindow,
    _apply_global_text_transforms,
)
from ballontranslator.ui.module_manager import ModuleManager
from ballontranslator.ui.scenetext_manager import SceneTextManager
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
        self._progress = {name: 0 for name in pages}
        self.saved = 0
        self.current_page = None
        self.is_all_pages_no_text = True

    @property
    def num_pages(self):
        return len(self._names)

    def get_blklist_byidx(self, index):
        return self.pages[self._names[index]]

    def idx2pagename(self, index):
        return self._names[index]

    def set_current_img_byidx(self, index):
        self.current_page = index

    def set_page_progress(self, page_name, value):
        self._progress[page_name] = value

    def get_page_progress(self, page_name):
        return self._progress[page_name]

    def save(self):
        self.saved += 1


class _FakePageList:
    def currentIndex(self):
        return SimpleNamespace(row=lambda: 0)

    def setCurrentRow(self, _index):
        raise AssertionError('the focused fake page should not change')


class _FakeRunDialog:
    Question = object()
    YesRole = object()
    AcceptRole = object()
    RejectRole = object()
    next_choice = 'Cancel'

    def __init__(self, _parent):
        self._buttons = {}

    def setIcon(self, _icon):
        pass

    def setWindowTitle(self, _title):
        pass

    def setText(self, _text):
        pass

    def addButton(self, text, _role):
        button = object()
        self._buttons[text] = button
        return button

    def setDefaultButton(self, _button):
        pass

    def exec_(self):
        pass

    def clickedButton(self):
        return self._buttons[self.next_choice]


class _FakeSignal:
    def __init__(self):
        self.calls = 0

    def emit(self):
        self.calls += 1


class _PipelineHarness:
    _matching_backup_fontformats = MainWindow._matching_backup_fontformats
    _warn_textstyle_preserve_fallback = MainWindow._warn_textstyle_preserve_fallback
    _clear_imgtrans_run_state = MainWindow._clear_imgtrans_run_state
    run_imgtrans = MainWindow.run_imgtrans
    run_imgtrans_wo_textstyle_update = MainWindow.run_imgtrans_wo_textstyle_update
    on_run_imgtrans = MainWindow.on_run_imgtrans

    def __init__(self, blocks, global_format):
        self.imgtrans_proj = _FakeProject({'page.png': blocks})
        self.textPanel = SimpleNamespace(
            formatpanel=SimpleNamespace(global_format=global_format)
        )
        self.st_manager = SimpleNamespace(
            auto_textlayout_flag=False,
            textblk_item_list=[],
            updateTextBlkList=lambda: None,
            updateSceneTextitems=lambda: None,
        )
        self.canvas = SimpleNamespace(updateCanvas=lambda: None)
        self.pageList = _FakePageList()
        self.backup_blkstyles = []
        self._backup_blkstyle_block_refs = []
        self._textstyle_preserve_warning_pages = set()
        self._run_imgtrans_wo_textstyle_update = False
        self.postprocess_mt_toggle = True
        self.postprocess_calls = 0
        self.saved_current_pages = 0
        self.pipeline_launches = []
        self.bottomBar = SimpleNamespace(
            textblockChecker=SimpleNamespace(
                isChecked=lambda: False,
                click=lambda: None,
            )
        )
        self.module_manager = SimpleNamespace(
            runImgtransPipeline=self._record_pipeline_launch
        )

    def _record_pipeline_launch(self, pages):
        self.pipeline_launches.append(pages)
        return True

    @staticmethod
    def tr(text):
        return text

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
        'keep_exist_textlines',
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
        pcfg.module.keep_exist_textlines = False

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
        harness._backup_blkstyle_block_refs = [(weakref.ref(block),)]

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
            ('identity mismatch', [[backup]], 'mismatch'),
        )
        for name, backups, identity_pages in cases:
            block = TextBlock(fontformat=backup.deepcopy())
            harness = _PipelineHarness([block], global_format)
            harness._run_imgtrans_wo_textstyle_update = True
            harness.backup_blkstyles = backups
            harness._backup_blkstyle_block_refs = (
                [(weakref.ref(TextBlock()),)]
                if identity_pages == 'mismatch'
                else identity_pages
            )
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

    def test_preserve_identity_requires_the_same_live_objects(self):
        block = TextBlock(fontformat=FontFormat(font_family='Original'))
        backup = block.fontformat.deepcopy()
        harness = _PipelineHarness([block], FontFormat())
        harness.backup_blkstyles = [[backup]]
        harness._backup_blkstyle_block_refs = [(weakref.ref(block),)]

        self.assertIs(
            harness._matching_backup_fontformats(0, [block])[0],
            backup,
        )
        replacement = TextBlock(fontformat=block.fontformat.deepcopy())
        self.assertIsNone(
            harness._matching_backup_fontformats(0, [replacement])
        )

    def test_preserve_identity_rejects_missing_and_dead_references(self):
        current = TextBlock(fontformat=FontFormat(font_family='Current'))
        harness = _PipelineHarness([current], FontFormat())
        harness.backup_blkstyles = [[current.fontformat.deepcopy()]]

        self.assertIsNone(harness._matching_backup_fontformats(0, [current]))
        expired = TextBlock(fontformat=FontFormat(font_family='Expired'))
        expired_ref = weakref.ref(expired)
        del expired
        self.assertIsNone(expired_ref())
        harness._backup_blkstyle_block_refs = [(expired_ref,)]
        self.assertIsNone(harness._matching_backup_fontformats(0, [current]))

    def test_detect_replacement_with_same_count_uses_normal_fallback(self):
        pcfg.module.enable_detect = True
        pcfg.module.keep_exist_textlines = False
        pcfg.let_fnteffect_flag = 1
        original = TextBlock(
            fontformat=FontFormat(
                opacity=0.27,
                horizontal_scale=0.8,
                vertical_scale=1.35,
                slant_angle=-18,
                glyph_slant_angle=-12,
            )
        )
        global_format = FontFormat(
            opacity=0.73,
            horizontal_scale=1.4,
            vertical_scale=0.75,
            slant_angle=17,
            glyph_slant_angle=-8,
        )
        harness = _PipelineHarness([original], global_format)
        harness._run_imgtrans_wo_textstyle_update = True

        self.assertTrue(harness.on_run_imgtrans())
        self.assertEqual(harness.imgtrans_proj.pages['page.png'], [])
        replacement = TextBlock(src_is_vertical=True, fontformat=FontFormat())
        harness.imgtrans_proj.pages['page.png'] = [replacement]

        with patch.object(mainwindow_module.LOGGER, 'warning') as warning:
            self._run_page(harness)

        self.assertEqual(
            replacement.fontformat.text_transform,
            (1.4, 0.75, 17.0, -8.0),
        )
        self.assertEqual(replacement.fontformat.opacity, 0.73)
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

    def test_inpaint_only_start_to_finish_preserves_writing_mode_and_style(self):
        pcfg.module.enable_detect = False
        pcfg.module.enable_ocr = False
        pcfg.module.enable_translate = False
        pcfg.module.enable_inpaint = True
        original = FontFormat(
            font_family='Untouched',
            alignment=2,
            vertical=True,
            opacity=0.39,
            shadow_offset=[2.5, -3.75],
            gradient_enabled=True,
            horizontal_scale=0.8,
            vertical_scale=1.35,
            slant_angle=-18,
            glyph_slant_angle=-12,
        )
        block = TextBlock(
            text=['source'],
            translation='translation',
            rich_text='<p>translation</p>',
            src_is_vertical=False,
            fontformat=original.deepcopy(),
        )
        harness = _PipelineHarness([block], FontFormat())

        self.assertTrue(harness.on_run_imgtrans())
        self.assertEqual(block.fontformat, original)
        self.assertEqual(block.rich_text, '<p>translation</p>')
        self._run_page(harness)
        self.assertEqual(block.fontformat, original)
        self.assertEqual(block.rich_text, '<p>translation</p>')
        self.assertEqual(harness.imgtrans_proj.saved, 1)

    def test_inpaint_only_clears_stale_auto_layout_before_scene_refresh(self):
        pcfg.module.enable_detect = False
        pcfg.module.enable_ocr = False
        pcfg.module.enable_translate = False
        pcfg.module.enable_inpaint = True
        block = TextBlock(
            translation='keep translation',
            rich_text='<p>keep rich text</p>',
            fontformat=FontFormat(font_family='Untouched', font_size=27),
        )
        harness = _PipelineHarness([block], FontFormat())
        layout_calls = []
        model_write_calls = []

        scene_manager = SimpleNamespace(
            auto_textlayout_flag=True,
            textblk_item_list=[],
            text_overlay_manager=SimpleNamespace(
                batch_update=lambda: nullcontext()
            ),
            imgtrans_proj=SimpleNamespace(
                current_block_list=lambda: [block]
            ),
            formatpanel=SimpleNamespace(
                familybox=SimpleNamespace(currentText=lambda: 'Fallback')
            ),
            clearSceneTextitems=lambda: None,
            updateTextBlkList=lambda: model_write_calls.append(block),
        )

        def add_text_block(candidate):
            # This is the auto-layout gate in SceneTextManager.addTextBlock();
            # updateSceneTextitems itself is the production implementation.
            if scene_manager.auto_textlayout_flag and not candidate.vertical:
                layout_calls.append(candidate)
                candidate.translation = 'unexpected layout mutation'

        scene_manager.addTextBlock = add_text_block
        scene_manager.updateSceneTextitems = lambda: (
            SceneTextManager.updateSceneTextitems(scene_manager)
        )
        harness.st_manager = scene_manager
        before = copy.deepcopy(vars(block))

        self.assertTrue(harness.run_imgtrans())
        self.assertFalse(harness.st_manager.auto_textlayout_flag)
        # on_run_imgtrans intentionally synchronizes pending user edits before
        # launch; only writes caused by the completion scene refresh matter.
        model_write_calls.clear()
        self._run_page(harness)

        self.assertEqual(layout_calls, [])
        self.assertEqual(model_write_calls, [])
        self.assertEqual(vars(block), before)

    def test_terminal_cleanup_resets_auto_layout_state(self):
        harness = _PipelineHarness([TextBlock()], FontFormat())
        harness.st_manager.auto_textlayout_flag = True

        harness._clear_imgtrans_run_state()

        self.assertFalse(harness.st_manager.auto_textlayout_flag)

    def test_preserve_cancel_does_not_leak_into_next_normal_run(self):
        block = TextBlock(
            fontformat=FontFormat(
                horizontal_scale=0.8,
                vertical_scale=1.35,
                slant_angle=-18,
                glyph_slant_angle=-12,
            )
        )
        global_format = FontFormat(
            horizontal_scale=1.4,
            vertical_scale=0.75,
            slant_angle=17,
            glyph_slant_angle=-8,
        )
        harness = _PipelineHarness([block], global_format)
        harness.imgtrans_proj.is_all_pages_no_text = False
        _FakeRunDialog.next_choice = 'Cancel'

        with patch.object(mainwindow_module, 'QMessageBox', _FakeRunDialog):
            self.assertFalse(harness.run_imgtrans_wo_textstyle_update())

        self.assertFalse(harness._run_imgtrans_wo_textstyle_update)
        self.assertTrue(harness.postprocess_mt_toggle)
        harness.imgtrans_proj.is_all_pages_no_text = True
        self.assertTrue(harness.run_imgtrans())
        self.assertFalse(harness._run_imgtrans_wo_textstyle_update)
        self._run_page(harness)
        self.assertEqual(
            block.fontformat.text_transform,
            (1.4, 0.75, 17.0, -8.0),
        )

    def test_preserve_continue_with_no_pages_cleans_invocation_state(self):
        harness = _PipelineHarness([TextBlock()], FontFormat())
        harness.imgtrans_proj.is_all_pages_no_text = False
        harness.imgtrans_proj.set_page_progress('page.png', 1)
        _FakeRunDialog.next_choice = 'Continue'

        with patch.object(mainwindow_module, 'QMessageBox', _FakeRunDialog):
            self.assertFalse(harness.run_imgtrans_wo_textstyle_update())

        self.assertFalse(harness._run_imgtrans_wo_textstyle_update)
        self.assertTrue(harness.postprocess_mt_toggle)
        self.assertEqual(harness.backup_blkstyles, [])
        self.assertEqual(harness._backup_blkstyle_block_refs, [])
        self.assertEqual(harness.pipeline_launches, [])

    def test_preserve_synchronous_launch_failure_cleans_invocation_state(self):
        harness = _PipelineHarness([TextBlock()], FontFormat())

        def fail_launch(_pages):
            raise RuntimeError('synthetic launch failure')

        harness.module_manager.runImgtransPipeline = fail_launch
        with self.assertRaisesRegex(RuntimeError, 'synthetic launch failure'):
            harness.run_imgtrans_wo_textstyle_update()

        self.assertFalse(harness._run_imgtrans_wo_textstyle_update)
        self.assertTrue(harness.postprocess_mt_toggle)
        self.assertEqual(harness.backup_blkstyles, [])
        self.assertEqual(harness._backup_blkstyle_block_refs, [])

    def test_empty_project_emits_pipeline_finished_and_reports_no_start(self):
        finished = _FakeSignal()
        hidden = []
        manager = SimpleNamespace(
            imgtrans_proj=SimpleNamespace(is_empty=True),
            progress_msgbox=SimpleNamespace(hide=lambda: hidden.append(True)),
            imgtrans_pipeline_finished=finished,
            _imgtrans_terminal_emitted=True,
        )
        manager._finish_imgtrans_pipeline_once = (
            lambda: ModuleManager._finish_imgtrans_pipeline_once(manager)
        )

        self.assertFalse(ModuleManager.runImgtransPipeline(manager))
        self.assertEqual(finished.calls, 1)
        self.assertEqual(hidden, [True])

        # A later invocation gets a fresh terminal guard.
        self.assertFalse(ModuleManager.runImgtransPipeline(manager))
        self.assertEqual(finished.calls, 2)
        self.assertEqual(hidden, [True, True])

    def test_async_module_preparation_failure_emits_pipeline_finished(self):
        finished = _FakeSignal()

        def fail_preparation(_modules, _on_success, on_failure):
            on_failure()

        manager = SimpleNamespace(
            imgtrans_proj=SimpleNamespace(is_empty=False, num_pages=1),
            imgtrans_pipeline_finished=finished,
            progress_msgbox=SimpleNamespace(hide=lambda: None),
            terminateRunningThread=lambda: None,
            _prepare_modules_then=fail_preparation,
            _imgtrans_terminal_emitted=True,
        )
        manager._finish_imgtrans_pipeline_once = (
            lambda: ModuleManager._finish_imgtrans_pipeline_once(manager)
        )

        self.assertTrue(ModuleManager.runImgtransPipeline(manager))
        self.assertEqual(finished.calls, 1)


if __name__ == '__main__':
    unittest.main()
