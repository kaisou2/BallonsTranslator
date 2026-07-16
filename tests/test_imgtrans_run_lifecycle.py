import os
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy.QtCore import QThread, QTimer, Signal
from qtpy.QtWidgets import QApplication

from ballontranslator.ui import module_manager as module_manager_module
from ballontranslator.ui.module_manager import (
    ImgtransThread,
    ModuleManager,
    TranslateThread,
)
from ballontranslator.utils.config import RunStatus, pcfg
from ballontranslator.utils.proj_imgtrans import ProjImgTrans


def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


_APP = qapp()


def process_events_until(predicate, attempts=50):
    for _ in range(attempts):
        _APP.processEvents()
        if predicate():
            return True
    return predicate()


class _ProgressBar:
    def __init__(self):
        self.visible = False

    def setVisible(self, visible):
        self.visible = visible

    def show(self):
        self.visible = True


class _ProgressBox:
    def __init__(self):
        self.hide_calls = 0
        self.show_calls = 0
        self.stage_progress = {}
        self.detect_bar = _ProgressBar()
        self.ocr_bar = _ProgressBar()
        self.translate_bar = _ProgressBar()
        self.inpaint_bar = _ProgressBar()

    def hide(self):
        self.hide_calls += 1

    def zero_progress(self):
        self.stage_progress.clear()

    def show_fitted(self):
        self.show_calls += 1

    def updateDetectProgress(self, value):
        self.stage_progress['detect'] = value

    def updateOCRProgress(self, value):
        self.stage_progress['ocr'] = value

    def updateTranslateProgress(self, value):
        self.stage_progress['translate'] = value

    def updateInpaintProgress(self, value):
        self.stage_progress['inpaint'] = value


class _IdleTranslateThread(QThread):
    module_thread_stopped = Signal()
    progress_changed = Signal(int)

    def __init__(self):
        super().__init__()
        self.finished_counter = 0


class _BlockingTranslateThread(QThread):
    module_thread_stopped = Signal()
    progress_changed = Signal(int)

    def __init__(self):
        super().__init__()
        self.started_event = threading.Event()
        self.release_event = threading.Event()
        self.finished_counter = 0

    def run(self):
        self.started_event.set()
        self.release_event.wait(5)
        self.finished_counter = 1
        self.progress_changed.emit(1)


class _EarlyPrepareThread(QThread):
    prepare_result = Signal()

    def __init__(self):
        super().__init__()
        self.result_emitted = threading.Event()
        self.release_event = threading.Event()
        self.module_key = 'translator'
        self.last_set_module_name = 'synthetic-translator'
        self.last_set_success = True
        self.last_missing_requirements = []
        self.last_error = None

    def run(self):
        self.prepare_result.emit()
        self.result_emitted.set()
        self.release_event.wait(5)


class _InpaintProject(ProjImgTrans):
    def __init__(self, save_error=None):
        super().__init__()
        self.pages = {'page.png': []}
        self._image_info = {'page.png': {'finish_code': 0}}
        self.save_error = save_error

    def read_img(self, _imgname):
        return np.zeros((2, 2, 3), dtype=np.uint8)

    def load_mask_by_imgname(self, _imgname):
        return np.ones((2, 2), dtype=np.uint8)

    def save_inpainted(self, _imgname, _image):
        if self.save_error is not None:
            raise self.save_error

class _StubInpainter:
    def __init__(self, inpaint_error=None):
        self.inpaint_error = inpaint_error
        self.stop_event = None

    def set_stop_event(self, stop_event):
        self.stop_event = stop_event

    def inpaint(self, image, _mask, _textblocks):
        if self.inpaint_error is not None:
            raise self.inpaint_error
        return image.copy()


class _DelayFailureTranslator:
    def delay(self):
        raise RuntimeError('synthetic delay failure')


class ImgtransRunLifecycleTests(unittest.TestCase):
    def _worker(self, translate_thread):
        return ImgtransThread(
            SimpleNamespace(),
            SimpleNamespace(),
            translate_thread,
            SimpleNamespace(),
        )

    def _pipeline_manager(self, project, worker, translate_thread):
        manager = ModuleManager(project)
        manager.imgtrans_thread = worker
        manager.translate_thread = translate_thread
        manager.progress_msgbox = _ProgressBox()
        manager.prepare_msgbox = None
        manager.terminateRunningThread = lambda: None

        def prepare_immediately(_modules, on_success, on_failure=None):
            on_success()

        manager._prepare_modules_then = prepare_immediately
        return manager

    def test_late_torch_device_cancel_finishes_pending_prepare_once(self):
        original_auto_install = pcfg.package_manager.auto_install_missing_packages
        try:
            for auto_install in (False, True):
                with self.subTest(auto_install=auto_install):
                    manager = ModuleManager(SimpleNamespace())
                    pcfg.package_manager.auto_install_missing_packages = auto_install
                    setter_calls = []
                    success_calls = []
                    failure_calls = []
                    run_state = SimpleNamespace(
                        postprocess_mt_toggle=False,
                        auto_textlayout_flag=True,
                    )
                    manager._module_ready = lambda *_args: False
                    manager._setter_for_module_key = lambda key: (
                        lambda name: setter_calls.append((key, name))
                    )
                    manager._ask_install_missing_packages = lambda *_args: True
                    manager._confirm_torch_install_device = lambda _requirements: (
                        False,
                        None,
                        None,
                    )
                    def finish_failed_run():
                        failure_calls.append('failure')
                        run_state.postprocess_mt_toggle = True
                        run_state.auto_textlayout_flag = False

                    manager._begin_prepare_queue(
                        [('ocr', 'late-ocr'), ('translator', 'next-translator')],
                        lambda: success_calls.append('success'),
                        finish_failed_run,
                    )
                    self.assertEqual(setter_calls, [('ocr', 'late-ocr')])

                    thread = SimpleNamespace(
                        module_key='ocr',
                        last_set_module_name='late-ocr',
                        last_set_success=False,
                        last_missing_requirements=['torch', 'torch'],
                        last_error=RuntimeError('missing torch'),
                    )
                    manager.on_module_prepare_finished(thread)

                    self.assertEqual(success_calls, [])
                    self.assertEqual(failure_calls, ['failure'])
                    self.assertEqual(manager._pending_prepare_queue, [])
                    self.assertIsNone(manager._pending_prepare_success)
                    self.assertIsNone(manager._pending_prepare_failure)
                    self.assertEqual(manager._package_install_retried, set())
                    self.assertTrue(run_state.postprocess_mt_toggle)
                    self.assertFalse(run_state.auto_textlayout_flag)

                    # A duplicate completion or later standalone preparation
                    # cannot revive the canceled RUN callback.
                    manager.on_module_prepare_finished(thread)
                    thread.last_set_success = True
                    thread.last_missing_requirements = []
                    manager.on_module_prepare_finished(thread)
                    self.assertEqual(success_calls, [])
                    self.assertEqual(failure_calls, ['failure'])
                    manager.deleteLater()
        finally:
            pcfg.package_manager.auto_install_missing_packages = original_auto_install

    def test_worker_runtime_exception_reaches_manager_terminal_once(self):
        translate_thread = _IdleTranslateThread()
        worker = self._worker(translate_thread)
        manager = ModuleManager(SimpleNamespace())
        manager.progress_msgbox = _ProgressBox()
        manager._imgtrans_terminal_emitted = False
        finished_calls = []
        cleanup_state = SimpleNamespace(
            postprocess_mt_toggle=False,
            auto_textlayout_flag=True,
        )

        def on_finished():
            finished_calls.append('finished')
            cleanup_state.postprocess_mt_toggle = True
            cleanup_state.auto_textlayout_flag = False

        manager.imgtrans_pipeline_finished.connect(on_finished)
        worker.pipeline_stopped.connect(manager.on_imgtrans_thread_stopped)
        worker.clearStopRequest()
        worker._running_imgtrans_pipeline = True
        worker.job = lambda: (_ for _ in ()).throw(RuntimeError('synthetic failure'))

        with patch.object(module_manager_module, 'create_error_dialog') as error_dialog:
            worker.start()
            self.assertTrue(worker.wait(5000))
            self.assertTrue(process_events_until(lambda: bool(finished_calls)))

        self.assertTrue(worker.isStopRequested())
        self.assertEqual(finished_calls, ['finished'])
        self.assertEqual(manager.progress_msgbox.hide_calls, 1)
        self.assertTrue(cleanup_state.postprocess_mt_toggle)
        self.assertFalse(cleanup_state.auto_textlayout_flag)
        error_dialog.assert_called_once()

        # A racing progress/stop callback is harmless after terminal delivery.
        manager.on_imgtrans_thread_stopped()
        self.assertEqual(finished_calls, ['finished'])
        self.assertEqual(manager.progress_msgbox.hide_calls, 1)
        worker.deleteLater()
        translate_thread.deleteLater()
        manager.deleteLater()

    def test_missing_project_image_uses_the_same_terminal_path(self):
        module_flags = (
            'enable_detect',
            'enable_ocr',
            'enable_translate',
            'enable_inpaint',
        )
        original_flags = {
            name: getattr(pcfg.module, name) for name in module_flags
        }
        translate_thread = _IdleTranslateThread()
        translate_thread.translator = None
        worker = self._worker(translate_thread)
        stopped_calls = []
        worker.pipeline_stopped.connect(lambda: stopped_calls.append('stopped'))

        try:
            for name in module_flags:
                setattr(pcfg.module, name, False)
            with tempfile.TemporaryDirectory() as directory:
                project = ProjImgTrans()
                project.directory = directory
                project.pages = {'missing.png': []}
                project._image_info = {'missing.png': {'finish_code': 0}}

                with patch.object(module_manager_module, 'create_error_dialog') as error_dialog:
                    worker.runImgtransPipeline(project)
                    self.assertTrue(worker.wait(5000))
                    self.assertTrue(process_events_until(lambda: bool(stopped_calls)))

                self.assertTrue(worker.isStopRequested())
                self.assertEqual(stopped_calls, ['stopped'])
                error_dialog.assert_called_once()
                self.assertIn('shape', str(error_dialog.call_args.args[0]))
        finally:
            for name, value in original_flags.items():
                setattr(pcfg.module, name, value)
            worker.wait(5000)
            worker.deleteLater()
            translate_thread.deleteLater()

    def test_worker_failure_waits_for_parallel_translation_before_terminal(self):
        translate_thread = _BlockingTranslateThread()
        worker = self._worker(translate_thread)
        stopped_calls = []
        worker.pipeline_stopped.connect(lambda: stopped_calls.append('stopped'))
        translate_thread.start()
        self.assertTrue(translate_thread.started_event.wait(2))
        worker.clearStopRequest()
        worker._running_imgtrans_pipeline = True
        worker.job = lambda: (_ for _ in ()).throw(RuntimeError('synthetic failure'))

        try:
            with patch.object(module_manager_module, 'create_error_dialog'):
                worker.start()
                self.assertTrue(worker.wait(5000))
                _APP.processEvents()
                self.assertEqual(stopped_calls, [])

                translate_thread.release_event.set()
                self.assertTrue(translate_thread.wait(5000))
                self.assertTrue(process_events_until(lambda: bool(stopped_calls)))

            self.assertEqual(stopped_calls, ['stopped'])
        finally:
            translate_thread.release_event.set()
            translate_thread.wait(5000)
            worker.wait(5000)
            worker.deleteLater()
            translate_thread.deleteLater()

    def _assert_ocr_off_parallel_translation_waits_for_stage(self, stage):
        module_flags = (
            'enable_detect',
            'enable_ocr',
            'enable_translate',
            'enable_inpaint',
        )
        original_flags = {
            name: getattr(pcfg.module, name) for name in module_flags
        }
        translate_thread = _BlockingTranslateThread()
        worker = self._worker(translate_thread)
        manager = ModuleManager(SimpleNamespace())
        manager.imgtrans_thread = worker
        manager.progress_msgbox = _ProgressBox()
        manager._imgtrans_terminal_emitted = False
        manager.last_finished_index = -1
        worker.imgtrans_proj = SimpleNamespace()
        worker.num_pages = 1
        worker.process_idx_to_page_idx = {0: 0}
        worker.detect_counter = int(stage == 'detect')
        worker.ocr_counter = 0
        worker.translate_counter = 0
        worker.inpaint_counter = int(stage == 'inpaint')
        worker.parallel_trans = True
        terminal_calls = []
        manager.imgtrans_pipeline_finished.connect(
            lambda: terminal_calls.append('finished')
        )
        translate_thread.progress_changed.connect(
            manager.on_update_translate_progress
        )

        try:
            pcfg.module.enable_detect = stage == 'detect'
            pcfg.module.enable_ocr = False
            pcfg.module.enable_translate = True
            pcfg.module.enable_inpaint = stage == 'inpaint'

            translate_thread.start()
            self.assertTrue(translate_thread.started_event.wait(2))
            if stage == 'detect':
                manager.on_update_detect_progress(1)
            else:
                manager.on_update_inpaint_progress(1)
            _APP.processEvents()

            self.assertEqual(terminal_calls, [])
            self.assertEqual(manager.progress_msgbox.hide_calls, 0)

            translate_thread.release_event.set()
            self.assertTrue(translate_thread.wait(5000))
            self.assertTrue(process_events_until(lambda: bool(terminal_calls)))

            self.assertEqual(terminal_calls, ['finished'])
            self.assertEqual(manager.progress_msgbox.hide_calls, 1)
        finally:
            for name, value in original_flags.items():
                setattr(pcfg.module, name, value)
            translate_thread.release_event.set()
            translate_thread.wait(5000)
            worker.deleteLater()
            translate_thread.deleteLater()
            manager.deleteLater()

    def test_ocr_off_parallel_translation_does_not_finish_on_detect_100(self):
        self._assert_ocr_off_parallel_translation_waits_for_stage('detect')

    def test_ocr_off_parallel_translation_does_not_finish_on_inpaint_100(self):
        self._assert_ocr_off_parallel_translation_waits_for_stage('inpaint')

    def _assert_inpaint_failure_does_not_complete_page(
        self,
        *,
        inpaint_error=None,
        save_error=None,
    ):
        module_flags = (
            'enable_detect',
            'enable_ocr',
            'enable_translate',
            'enable_inpaint',
        )
        original_flags = {
            name: getattr(pcfg.module, name) for name in module_flags
        }
        translate_thread = _IdleTranslateThread()
        translate_thread.translator = None
        project = _InpaintProject(save_error=save_error)
        inpainter = _StubInpainter(inpaint_error=inpaint_error)
        worker = ImgtransThread(
            SimpleNamespace(),
            SimpleNamespace(),
            translate_thread,
            SimpleNamespace(inpainter=inpainter),
        )
        manager = ModuleManager(project)
        manager.progress_msgbox = _ProgressBox()
        manager._imgtrans_terminal_emitted = False
        terminal_calls = []
        inpaint_progress = []
        manager.imgtrans_pipeline_finished.connect(
            lambda: terminal_calls.append('finished')
        )
        worker.pipeline_stopped.connect(manager.on_imgtrans_thread_stopped)
        worker.update_inpaint_progress.connect(inpaint_progress.append)

        try:
            pcfg.module.enable_detect = False
            pcfg.module.enable_ocr = False
            pcfg.module.enable_translate = False
            pcfg.module.enable_inpaint = True

            with patch.object(module_manager_module, 'create_error_dialog') as error_dialog:
                worker.runImgtransPipeline(project)
                self.assertTrue(worker.wait(5000))
                self.assertTrue(process_events_until(lambda: bool(terminal_calls)))

            self.assertTrue(worker.isStopRequested())
            self.assertEqual(worker.inpaint_counter, 0)
            self.assertEqual(
                project._image_info['page.png']['finish_code']
                & RunStatus.FIN_INPAINT,
                0,
            )
            self.assertFalse(project.get_page_progress('page.png'))
            self.assertEqual(inpaint_progress, [])
            self.assertEqual(terminal_calls, ['finished'])
            self.assertEqual(manager.progress_msgbox.hide_calls, 1)
            error_dialog.assert_called_once()
        finally:
            for name, value in original_flags.items():
                setattr(pcfg.module, name, value)
            worker.wait(5000)
            worker.deleteLater()
            translate_thread.deleteLater()
            manager.deleteLater()

    def test_inpaint_exception_does_not_mark_page_complete(self):
        self._assert_inpaint_failure_does_not_complete_page(
            inpaint_error=RuntimeError('synthetic inpaint failure'),
        )

    def test_inpaint_save_failure_does_not_mark_page_complete(self):
        self._assert_inpaint_failure_does_not_complete_page(
            save_error=OSError('synthetic save failure'),
        )

    def test_stop_terminal_not_lost_when_translate_finishes_during_worker_exit(self):
        module_flags = (
            'enable_detect',
            'enable_ocr',
            'enable_translate',
            'enable_inpaint',
        )
        original_flags = {
            name: getattr(pcfg.module, name) for name in module_flags
        }
        translate_thread = _BlockingTranslateThread()
        worker = self._worker(translate_thread)
        project = SimpleNamespace(
            is_empty=False,
            num_pages=1,
            pages={'page.png': []},
        )
        manager = self._pipeline_manager(project, worker, translate_thread)
        terminal_calls = []
        manager.imgtrans_pipeline_finished.connect(
            lambda: terminal_calls.append('finished')
        )
        worker.parallel_trans = True
        worker_checked = threading.Event()
        allow_worker_return = threading.Event()
        original_readiness_check = worker._emit_pipeline_stopped_if_ready
        main_thread_id = threading.get_ident()

        def stop_with_live_translation():
            translate_thread.start()
            translate_thread.started_event.wait(2)
            worker.requestStop()

        worker._imgtrans_pipeline = stop_with_live_translation

        def hold_after_worker_readiness_check(imgtrans_running):
            original_readiness_check(imgtrans_running)
            if threading.get_ident() != main_thread_id \
                    and not worker_checked.is_set():
                worker_checked.set()
                allow_worker_return.wait(5)

        worker._emit_pipeline_stopped_if_ready = (
            hold_after_worker_readiness_check
        )

        try:
            pcfg.module.enable_detect = False
            pcfg.module.enable_ocr = False
            pcfg.module.enable_translate = True
            pcfg.module.enable_inpaint = False

            self.assertTrue(manager.runImgtransPipeline())
            self.assertTrue(worker_checked.wait(2))
            self.assertTrue(translate_thread.started_event.is_set())

            translate_thread.release_event.set()
            self.assertTrue(translate_thread.wait(5000))
            _APP.processEvents()
            self.assertEqual(terminal_calls, [])

            allow_worker_return.set()
            self.assertTrue(worker.wait(5000))
            self.assertTrue(process_events_until(lambda: bool(terminal_calls)))

            self.assertEqual(terminal_calls, ['finished'])
            self.assertEqual(manager.progress_msgbox.hide_calls, 1)
        finally:
            for name, value in original_flags.items():
                setattr(pcfg.module, name, value)
            translate_thread.release_event.set()
            allow_worker_return.set()
            translate_thread.wait(5000)
            worker.wait(5000)
            worker.deleteLater()
            translate_thread.deleteLater()
            manager.deleteLater()

    def test_immediate_next_run_waits_for_previous_worker_exit(self):
        module_flags = (
            'enable_detect',
            'enable_ocr',
            'enable_translate',
            'enable_inpaint',
        )
        original_flags = {
            name: getattr(pcfg.module, name) for name in module_flags
        }
        translate_thread = _IdleTranslateThread()
        translate_thread.translator = None
        worker = self._worker(translate_thread)
        project = SimpleNamespace(
            is_empty=False,
            num_pages=1,
            pages={'page.png': []},
        )
        manager = self._pipeline_manager(project, worker, translate_thread)
        first_progress_emitted = threading.Event()
        release_first_run = threading.Event()
        second_run_calls = []
        terminal_calls = []

        def first_job():
            worker.process_idx_to_page_idx = {0: 0}
            worker.parallel_trans = False
            worker.detect_counter = 1
            worker.ocr_counter = 0
            worker.translate_counter = 0
            worker.inpaint_counter = 0
            worker.update_detect_progress.emit(1)
            first_progress_emitted.set()
            release_first_run.wait(5)

        def second_job():
            second_run_calls.append('second')
            worker.requestStop()

        def on_terminal():
            terminal_calls.append('finished')
            if len(terminal_calls) == 1:
                def start_second_run():
                    worker._imgtrans_pipeline = second_job
                    manager.runImgtransPipeline()

                QTimer.singleShot(0, start_second_run)

        manager.imgtrans_pipeline_finished.connect(on_terminal)

        try:
            pcfg.module.enable_detect = True
            pcfg.module.enable_ocr = False
            pcfg.module.enable_translate = False
            pcfg.module.enable_inpaint = False
            worker._imgtrans_pipeline = first_job

            self.assertTrue(manager.runImgtransPipeline())
            self.assertTrue(first_progress_emitted.wait(2))
            _APP.processEvents()

            self.assertEqual(terminal_calls, [])
            self.assertEqual(second_run_calls, [])
            self.assertTrue(worker.isRunning())
            first_run_job = worker.job
            self.assertFalse(manager.runImgtransPipeline())
            self.assertIs(worker.job, first_run_job)

            release_first_run.set()
            self.assertTrue(worker.wait(5000))
            self.assertTrue(process_events_until(lambda: bool(terminal_calls)))
            self.assertTrue(process_events_until(lambda: bool(second_run_calls)))
            self.assertTrue(worker.wait(5000))
            self.assertTrue(
                process_events_until(lambda: len(terminal_calls) == 2)
            )

            self.assertEqual(second_run_calls, ['second'])
            self.assertEqual(terminal_calls, ['finished', 'finished'])
            self.assertEqual(manager.progress_msgbox.hide_calls, 2)
        finally:
            for name, value in original_flags.items():
                setattr(pcfg.module, name, value)
            release_first_run.set()
            worker.requestStop()
            worker.wait(5000)
            worker.deleteLater()
            translate_thread.deleteLater()
            manager.deleteLater()

    def test_late_run_signals_do_not_finalize_or_update_next_run(self):
        module_flags = (
            'enable_detect',
            'enable_ocr',
            'enable_translate',
            'enable_inpaint',
        )
        original_flags = {
            name: getattr(pcfg.module, name) for name in module_flags
        }
        translate_thread = _IdleTranslateThread()
        translate_thread.translator = None
        worker = self._worker(translate_thread)
        project = SimpleNamespace(
            is_empty=False,
            num_pages=1,
            pages={'page.png': []},
        )
        manager = self._pipeline_manager(project, worker, translate_thread)
        terminal_calls = []
        manager.imgtrans_pipeline_finished.connect(
            lambda: terminal_calls.append('finished')
        )
        second_run_started = threading.Event()
        release_second_run = threading.Event()

        def first_job():
            worker.requestStop()

        def second_job():
            second_run_started.set()
            release_second_run.wait(5)
            worker.requestStop()

        try:
            pcfg.module.enable_detect = True
            pcfg.module.enable_ocr = False
            pcfg.module.enable_translate = False
            pcfg.module.enable_inpaint = False
            worker._imgtrans_pipeline = first_job

            self.assertTrue(manager.runImgtransPipeline())
            old_detect_slot = manager._imgtrans_run_signal_connections[1][1]
            old_stopped_slot = manager._imgtrans_run_signal_connections[5][1]
            self.assertTrue(worker.wait(5000))
            self.assertTrue(process_events_until(lambda: bool(terminal_calls)))
            self.assertEqual(terminal_calls, ['finished'])

            worker._imgtrans_pipeline = second_job
            self.assertTrue(manager.runImgtransPipeline())
            self.assertTrue(second_run_started.wait(2))
            manager.progress_msgbox.stage_progress.clear()

            QTimer.singleShot(0, old_stopped_slot)
            QTimer.singleShot(0, lambda: old_detect_slot(1))
            _APP.processEvents()

            self.assertEqual(terminal_calls, ['finished'])
            self.assertFalse(manager._imgtrans_terminal_emitted)
            self.assertTrue(worker.isRunning())
            self.assertEqual(manager.progress_msgbox.stage_progress, {})
            self.assertEqual(manager.progress_msgbox.hide_calls, 1)

            release_second_run.set()
            self.assertTrue(worker.wait(5000))
            self.assertTrue(
                process_events_until(lambda: len(terminal_calls) == 2)
            )
            self.assertEqual(terminal_calls, ['finished', 'finished'])
            self.assertEqual(manager.progress_msgbox.hide_calls, 2)
        finally:
            for name, value in original_flags.items():
                setattr(pcfg.module, name, value)
            release_second_run.set()
            worker.requestStop()
            worker.wait(5000)
            worker.deleteLater()
            translate_thread.deleteLater()
            manager.deleteLater()

    def test_prepare_success_waits_for_module_qthread_exit(self):
        manager = ModuleManager(SimpleNamespace())
        prepare_thread = _EarlyPrepareThread()
        success_calls = []
        manager._pending_prepare_queue = []
        manager._pending_prepare_success = lambda: success_calls.append(
            'success'
        )
        manager._pending_prepare_failure = lambda: None
        prepare_thread.prepare_result.connect(
            lambda: manager.on_module_prepare_finished(prepare_thread)
        )

        try:
            prepare_thread.start()
            self.assertTrue(prepare_thread.result_emitted.wait(2))
            _APP.processEvents()
            _APP.processEvents()

            self.assertTrue(prepare_thread.isRunning())
            self.assertEqual(success_calls, [])

            prepare_thread.release_event.set()
            self.assertTrue(prepare_thread.wait(5000))
            self.assertTrue(process_events_until(lambda: bool(success_calls)))
            self.assertEqual(success_calls, ['success'])
        finally:
            prepare_thread.release_event.set()
            prepare_thread.wait(5000)
            prepare_thread.deleteLater()
            manager.deleteLater()

    def test_parallel_translate_outer_exception_stops_full_pipeline(self):
        translate_thread = TranslateThread()
        translate_thread.translator = _DelayFailureTranslator()
        worker = self._worker(translate_thread)
        stopped_calls = []
        worker.pipeline_stopped.connect(lambda: stopped_calls.append('stopped'))

        with patch.object(module_manager_module, 'create_error_dialog') as error_dialog:
            translate_thread.runTranslatePipeline(
                SimpleNamespace(),
                worker.stop_event,
            )
            self.assertTrue(translate_thread.wait(5000))
            self.assertTrue(process_events_until(lambda: bool(stopped_calls)))

        self.assertTrue(worker.isStopRequested())
        self.assertEqual(stopped_calls, ['stopped'])
        error_dialog.assert_called_once()
        self.assertIn('delay failure', str(error_dialog.call_args.args[0]))
        worker.deleteLater()
        translate_thread.deleteLater()

    def test_manager_normal_stop_and_failure_callbacks_share_once_guard(self):
        manager = ModuleManager(SimpleNamespace())
        manager.progress_msgbox = _ProgressBox()
        manager._imgtrans_terminal_emitted = False
        manager.proj_finished = lambda: True
        finished_calls = []
        manager.imgtrans_pipeline_finished.connect(
            lambda: finished_calls.append('finished')
        )

        manager.finishImgtransPipeline()
        manager.finishImgtransPipeline()
        manager.on_imgtrans_thread_stopped()

        self.assertEqual(finished_calls, ['finished'])
        self.assertEqual(manager.progress_msgbox.hide_calls, 1)
        manager.deleteLater()


if __name__ == '__main__':
    unittest.main()
