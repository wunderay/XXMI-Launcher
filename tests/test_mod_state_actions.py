"""Headless tests of the real launcher actions and early snapshot-worker dispatch.

Platform services, file pickers and Tk are inert fixtures. The Application module
is imported in full; its real action, queue polling and thread gate methods run.
No installed launcher, user config, game process or external helper is touched.
"""
import builtins
import importlib.util
from pathlib import Path
from queue import Queue
import runpy
import sys
import tempfile
from threading import Lock
import types
import unittest
from unittest.mock import MagicMock, patch


REPOSITORY = Path(__file__).resolve().parents[1]
SOURCE = REPOSITORY / 'src' / 'xxmi_launcher'


def module(name, **values):
    result = types.ModuleType(name)
    result.__dict__.update(values)
    return result


class ModStateActionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='xxmi-actions-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'XXMI Launcher'
        self.root.mkdir()
        self.archive = self.root.parent / 'saved setup.zip'
        self.sequence = []
        self.dialogs = module('tkinter.filedialog',
            asksaveasfilename=MagicMock(return_value=str(self.archive)),
            askopenfilename=MagicMock(return_value=str(self.archive)))
        self.config = module('core.config_manager',
            Launcher=types.SimpleNamespace(active_importer='ZZMI'),
            Manager=types.SimpleNamespace(save=MagicMock(
                side_effect=lambda: self.sequence.append('save-config'))))
        self.worker = module('core.mod_state_worker',
            prepare_snapshot_job=MagicMock(), worker_main=MagicMock(return_value=0))
        self.events = module('core.event_manager', Fire=MagicMock(), Call=MagicMock(),
                             Subscribe=MagicMock())
        self.paths = module('core.path_manager', App=types.SimpleNamespace(Root=self.root))
        stubs = {
            'core': module('core', __path__=[str(SOURCE / 'core')]),
            'core.utils': module('core.utils', __path__=[]),
            'core.utils.system_info': module('core.utils.system_info'),
            'core.events': module('core.events', __path__=[]),
            'core.packages': module('core.packages', __path__=[]),
            'core.packages.model_importers': module('core.packages.model_importers', __path__=[]),
            'core.error_manager': module('core.error_manager'),
            'core.locale_manager': module('core.locale_manager', L=lambda key, text: text),
            'core.path_manager': self.paths,
            'core.config_manager': self.config,
            'core.event_manager': self.events,
            'core.mod_state_worker': self.worker,
            'core.package_manager': module('core.package_manager', PackageManager=MagicMock()),
            'core.game_launcher': module('core.game_launcher', Launcher=MagicMock()),
            'gui': module('gui', __path__=[]),
            'gui.windows': module('gui.windows', __path__=[]),
            'gui.windows.main': module('gui.windows.main', __path__=[]),
            'gui.windows.main.main_window': module('gui.windows.main.main_window', MainWindow=MagicMock()),
            'tkinter': module('tkinter', filedialog=self.dialogs),
            'tkinter.filedialog': self.dialogs,
        }
        for relative, name in (
            ('launcher_package', 'LauncherPackage'),
            ('migoto_package', 'MigotoPackage'),
            ('genshin_fps_unlock_package', 'GenshinFpsUnlockerPackage'),
            *[(f'model_importers.{key}_package', f'{key.upper()}Package')
              for key in ('gimi', 'srmi', 'wwmi', 'zzmi', 'himi', 'efmi')],
        ):
            full_name = 'core.packages.' + relative
            stubs[full_name] = module(full_name, **{name: MagicMock()})
        # Give import statements normal package attributes even when a test suite
        # has already imported the real core/gui packages in another fixture.
        for name, value in list(stubs.items()):
            parent, _, child = name.rpartition('.')
            if parent in stubs:
                setattr(stubs[parent], child, value)
        modules = patch.dict(sys.modules, stubs)
        modules.start()
        self.addCleanup(modules.stop)

        event_name = 'core.events.application'
        event_spec = importlib.util.spec_from_file_location(event_name, SOURCE / 'core/events/application.py')
        event_module = importlib.util.module_from_spec(event_spec)
        sys.modules[event_name] = event_module
        event_spec.loader.exec_module(event_module)
        self.event_types = event_module.ApplicationEvents
        self.events.Application = self.event_types
        self.events.Fire.side_effect = lambda event: self.sequence.append(event)

        name = '_mod_state_application_fixture'
        spec = importlib.util.spec_from_file_location(name, SOURCE / 'core/application.py')
        self.loaded = importlib.util.module_from_spec(spec)
        sys.modules[name] = self.loaded
        spec.loader.exec_module(self.loaded)
        self.app = self.loaded.Application.__new__(self.loaded.Application)
        self.app._mod_state_pending = False
        self.app._mod_state_results = Queue()
        self.app.is_locked = False
        self.app.shutting_down = False
        self.app.thread_lock = Lock()
        self.app.threads = []
        self.app.error_queue = Queue()
        self.app.package_manager = types.SimpleNamespace(update_running=False)
        self.app.gui = MagicMock()
        self.app.gui.show_messagebox.return_value = True
        self.app.run_as_thread = MagicMock(side_effect=lambda *args, **kwargs:
            self.sequence.append(('prepare-scheduled', args, kwargs)))

    def fired(self, event_type):
        return [item for item in self.sequence if isinstance(item, event_type)]

    def assert_not_started(self):
        self.config.Manager.save.assert_not_called()
        self.app.run_as_thread.assert_not_called()
        self.assertFalse(self.fired(self.event_types.CloseSettings))
        self.assertFalse(self.fired(self.event_types.Close))

    def job(self, returncode=None):
        return {'process': MagicMock(poll=MagicMock(return_value=returncode)),
                'result_path': self.root.parent / 'result.json',
                'request_path': self.root.parent / 'request.json',
                'ready_path': self.root.parent / 'ready.json',
                'job_dir': self.root.parent}

    def test_file_picker_cancel_never_saves_or_closes(self):
        for operation, picker in (('backup', self.dialogs.asksaveasfilename),
                                  ('restore', self.dialogs.askopenfilename)):
            with self.subTest(operation=operation):
                picker.return_value = ''
                self.app.handle_mod_state(operation)
                self.assert_not_started()
                self.app.gui.show_messagebox.assert_not_called()
                self.assertFalse(self.app._mod_state_pending)
                self.assertFalse(self.app.is_locked)

    def test_confirmation_cancel_never_saves_or_closes(self):
        self.app.gui.show_messagebox.return_value = False
        self.app.handle_mod_state('restore')
        self.assert_not_started()
        dialogue = self.app.gui.show_messagebox.call_args.args[0]
        self.assertIsInstance(dialogue, self.event_types.ShowDialogue)
        self.assertTrue(dialogue.modal)
        self.assertIn(str(self.root), dialogue.message)
        self.assertIn(str(self.archive), dialogue.message)
        self.assertFalse(self.app._mod_state_pending)
        self.assertFalse(self.app.is_locked)

    def test_confirm_commits_settings_before_background_preparation(self):
        self.app.handle_mod_state('backup')
        saved = self.fired(self.event_types.CloseSettings)
        self.assertEqual(len(saved), 1)
        self.assertTrue(saved[0].save)
        scheduled = next(item for item in self.sequence if isinstance(item, tuple))
        self.assertLess(self.sequence.index(saved[0]), self.sequence.index('save-config'))
        self.assertLess(self.sequence.index('save-config'), self.sequence.index(scheduled))
        self.app.run_as_thread.assert_called_once_with(
            self.app._prepare_mod_state, 'backup', self.root, self.archive)
        self.assertTrue(self.app._mod_state_pending)
        self.assertTrue(self.app.is_locked)
        self.assertTrue(self.fired(self.event_types.Busy))
        self.assertFalse(self.fired(self.event_types.Close))

    def test_launcher_only_closes_after_ready_worker_result(self):
        self.app.handle_mod_state('restore')
        self.app._poll_mod_state()
        self.assertFalse(self.fired(self.event_types.Close))
        self.app.gui.after.assert_called_with(50, self.app._poll_mod_state)
        self.worker.prepare_snapshot_job.return_value = self.job()
        self.app._prepare_mod_state('restore', self.root, self.archive)
        self.assertFalse(self.fired(self.event_types.Close))
        self.worker.prepare_snapshot_job.assert_called_once_with('restore', self.root, self.archive)
        self.app._poll_mod_state()
        self.assertEqual(len(self.fired(self.event_types.Close)), 1)
        self.assertTrue(self.app._mod_state_pending)
        self.assertTrue(self.app.is_locked)

    def test_worker_preparation_error_releases_launcher_and_uses_error_queue(self):
        self.app.handle_mod_state('backup')
        error = OSError('cannot copy helper runtime')
        self.worker.prepare_snapshot_job.side_effect = error
        self.app._prepare_mod_state('backup', self.root, self.archive)
        self.app._poll_mod_state()
        queued_error, trace = self.app.error_queue.get_nowait()
        self.assertIs(queued_error, error)
        self.assertIn('cannot copy helper runtime', trace)
        self.assertFalse(self.app._mod_state_pending)
        self.assertFalse(self.app.is_locked)
        self.assertTrue(self.fired(self.event_types.Ready))
        self.assertFalse(self.fired(self.event_types.Close))

    def test_worker_import_error_also_releases_launcher(self):
        self.app._mod_state_pending = self.app.is_locked = True
        original_import = builtins.__import__
        def import_failure(name, *args, **kwargs):
            if name == 'core.mod_state_worker':
                raise ImportError('helper unavailable')
            return original_import(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=import_failure):
            self.app._prepare_mod_state('backup', self.root, self.archive)
        self.app._poll_mod_state()
        error, trace = self.app.error_queue.get_nowait()
        self.assertIsInstance(error, ImportError)
        self.assertIn('helper unavailable', trace)
        self.assertFalse(self.app.is_locked)
        self.assertFalse(self.app._mod_state_pending)
        self.assertFalse(self.fired(self.event_types.Close))

    def test_helper_exit_before_shutdown_is_an_error_not_close(self):
        self.app._mod_state_pending = self.app.is_locked = True
        self.app._mod_state_results.put((self.job(returncode=1), None, ''))
        self.app._poll_mod_state()
        error, trace = self.app.error_queue.get_nowait()
        self.assertIn('stopped', str(error))
        self.assertIn('result.json', trace)
        self.assertFalse(self.app.is_locked)
        self.assertFalse(self.app._mod_state_pending)
        self.assertFalse(self.fired(self.event_types.Close))

    def test_save_error_does_not_start_helper_or_leave_operation_locked(self):
        self.config.Manager.save.side_effect = OSError('cannot save config')
        with self.assertLogs(level='ERROR'):
            self.app.handle_mod_state('restore')
        self.app.run_as_thread.assert_not_called()
        self.assertFalse(self.app._mod_state_pending)
        self.assertFalse(self.app.is_locked)
        self.assertFalse(self.fired(self.event_types.Close))
        self.assertIsInstance(self.app.gui.show_messagebox.call_args.args[0], self.event_types.ShowError)

    def test_busy_launcher_refuses_before_showing_file_picker(self):
        for reason in ('locked', 'updating', 'worker', 'shutdown'):
            with self.subTest(reason=reason):
                self.app.is_locked = reason == 'locked'
                self.app.package_manager.update_running = reason == 'updating'
                self.app.shutting_down = reason == 'shutdown'
                self.app.threads = [MagicMock(is_alive=MagicMock(return_value=True))] if reason == 'worker' else []
                self.app.handle_mod_state('backup')
                self.dialogs.asksaveasfilename.assert_not_called()
                self.assert_not_started()
                self.assertFalse(self.app._mod_state_pending)

    def test_duplicate_operation_and_other_importer_do_not_start_dialog(self):
        self.app._mod_state_pending = True
        self.app.handle_mod_state('backup')
        self.app._mod_state_pending = False
        self.config.Launcher.active_importer = 'GIMI'
        self.app.handle_mod_state('restore')
        self.assert_not_started()
        self.dialogs.asksaveasfilename.assert_not_called()
        self.dialogs.askopenfilename.assert_not_called()

    def test_thread_gate_blocks_normal_work_including_synchronous_requests(self):
        callback = MagicMock()
        self.app._mod_state_pending = True
        with patch.object(self.loaded, 'Thread') as thread, self.assertLogs(level='WARNING'):
            self.loaded.Application.run_as_thread(self.app, callback)
            self.loaded.Application.run_as_thread(self.app, callback, no_thread=True)
            thread.assert_not_called()
        callback.assert_not_called()
        self.app._mod_state_pending = False
        self.loaded.Application.run_as_thread(self.app, callback, no_thread=True)
        callback.assert_called_once_with()

    def test_thread_gate_allows_only_the_backup_preparer_while_pending(self):
        self.app._mod_state_pending = True
        with patch.object(self.loaded, 'Thread') as thread:
            self.loaded.Application.run_as_thread(
                self.app, self.app._prepare_mod_state, 'backup', self.root, self.archive)
            thread.assert_called_once()
            thread.return_value.start.assert_called_once_with()
        self.assertEqual(self.app.threads, [thread.return_value])

    def test_worker_dispatch_bypasses_both_normal_entrypoints(self):
        for entrypoint in (SOURCE / 'app.py', REPOSITORY / 'tools/PreparedLoading/launcher-bootstrap.py'):
            with self.subTest(entrypoint=entrypoint.name):
                self.worker.worker_main.reset_mock()
                with patch.object(sys, 'argv', [str(entrypoint), '--mod-state-worker', 'request with spaces.json']), \
                     patch('multiprocessing.freeze_support'), \
                     patch('logging.basicConfig', side_effect=AssertionError('normal logging reached')), \
                     patch('runpy.run_module', side_effect=AssertionError('normal app reached')):
                    with self.assertRaises(SystemExit) as exited:
                        runpy.run_path(str(entrypoint), run_name='__main__')
                self.assertEqual(exited.exception.code, 0)
                self.worker.worker_main.assert_called_once_with('request with spaces.json')

    def test_worker_dispatch_refuses_ambiguous_extra_arguments(self):
        for entrypoint in (SOURCE / 'app.py', REPOSITORY / 'tools/PreparedLoading/launcher-bootstrap.py'):
            with self.subTest(entrypoint=entrypoint.name):
                with patch.object(sys, 'argv', [str(entrypoint), '--mod-state-worker', 'request.json', '--nogui']), \
                     patch('multiprocessing.freeze_support'), \
                     patch('logging.basicConfig', side_effect=AssertionError('normal logging reached')), \
                     patch('runpy.run_module', side_effect=AssertionError('normal app reached')):
                    with self.assertRaises(SystemExit) as exited:
                        runpy.run_path(str(entrypoint), run_name='__main__')
                self.assertIn('without other arguments', str(exited.exception))
        self.worker.worker_main.assert_not_called()


if __name__ == '__main__':
    unittest.main()
