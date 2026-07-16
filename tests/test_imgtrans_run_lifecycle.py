import os
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qtpy.QtCore import QThread, Signal
from qtpy.QtWidgets import QApplication

from ballontranslator.ui import module_manager as module_manager_module
from ballontranslator.ui.module_manager import (
    ImgtransThread,
    ModuleManager,
    TranslateThread,
)
from ballontranslator.utils.config import pcfg
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


class _ProgressBox:
    def __init__(self):
        self.hide_calls = 0

    def hide(self):
        self.hide_calls += 1


class _IdleTranslateThread(QThread):
    module_thread_stopped = Signal()


class _BlockingTranslateThread(QThread):
    module_thread_stopped = Signal()

    def __init__(self):
        super().__init__()
        self.started_event = threading.Event()
        self.release_event = threading.Event()

    def run(self):
        self.started_event.set()
        self.release_event.wait(5)


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
