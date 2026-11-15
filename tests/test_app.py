import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import tkinter as tk
from test_core import SCRATCH

ROOT = Path(__file__).resolve().parents[1]

class AppTests(unittest.TestCase):
    def setUp(self):
        app = importlib.import_module('bb_cn_installer')
        detector = patch.object(app, 'detect_paths', return_value={
            'games': [], 'game': None, 'launchers': [], 'launcher': None, 'note': '测试：未发现路径'})
        detector.start()
        self.addCleanup(detector.stop)

    def test_cli_help_and_diagnose_never_create_game_directory(self):
        app = ROOT / 'bb_cn_installer.py'
        self.assertTrue(app.is_file(), 'CLI/GUI entry point missing')
        help_run = subprocess.run([sys.executable, str(app), '--help'], capture_output=True)
        self.assertEqual(help_run.returncode, 0, help_run.stderr)
        self.assertIn(b'--diagnose', help_run.stdout)
        missing = SCRATCH / 'bbcn-deliberately-nonexistent-game'
        self.assertFalse(missing.exists())
        result = subprocess.run([sys.executable, str(app), '--diagnose', '--game-dir', str(missing)], capture_output=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        data = json.loads(result.stdout.decode('utf-8'))
        self.assertFalse(data['supported'])
        self.assertFalse(missing.exists())

    def test_chinese_gui_builds_with_all_actions_and_no_startup_writes(self):
        try: app = importlib.import_module('bb_cn_installer')
        except ModuleNotFoundError: self.fail('Chinese GUI missing')
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        ui = app.Application(root)
        self.addCleanup(lambda: root.after_cancel(ui.poll_id))
        root.after_cancel(ui.detect_id)
        root.update()
        self.assertIn('汉化', root.title())
        self.assertEqual(set(ui.actions), {'install_all', 'discover', 'prepare', 'install', 'check', 'uninstall', 'recover', 'steam', 'steam_restore'})
        self.assertIsNone(ui.prepared)
        self.assertFalse(ui.busy)
        self.assertIn('手动粘贴', ui.instructions.cget('text'))
        self.assertIn('自动设置', ui.actions['steam'].cget('text'))
        self.assertTrue(hasattr(ui, 'restart_steam'), 'Missing explicit temporary Steam restart consent')
        self.assertFalse(ui.restart_steam.get())
        self.assertIn('恢复', ui.actions['steam_restore'].cget('text'))

    def test_gui_steam_confirmation_cancel_does_not_open_or_write_steam(self):
        app = importlib.import_module('bb_cn_installer')
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        ui = app.Application(root)
        root.after_cancel(ui.detect_id)
        self.addCleanup(lambda: root.after_cancel(ui.poll_id))
        from unittest.mock import Mock
        engine = Mock()
        engine.check.return_value = {'healthy': True}
        engine.game = Path('C:/fake/Battle Brothers')
        engine.store = engine.game / '.bb-cn-installer'
        with patch.object(ui, 'engine', return_value=engine), patch.object(app.messagebox, 'askyesno', return_value=False), patch.object(app.webbrowser, 'open') as opened:
            ui.steam()
            ui.steam_restore()
        engine.set_steam_options.assert_not_called()
        engine.restore_steam_options.assert_not_called()
        opened.assert_not_called()
        self.assertFalse(ui.busy)

    def test_cli_steam_actions_require_explicit_yes_without_writing(self):
        app = importlib.import_module('bb_cn_installer')
        import io
        from unittest.mock import Mock
        engine = Mock()
        for action, method in [('steam-set', 'set_steam_options'), ('steam-restore', 'restore_steam_options')]:
            with self.subTest(action=action), patch.object(app, 'Engine', return_value=engine), patch.object(sys, 'stdout', new=io.StringIO()):
                self.assertEqual(app.main([action, '--game-dir', 'C:/fake/Battle Brothers']), 2)
                getattr(engine, method).assert_not_called()
                getattr(engine, method).return_value = {'status': 'applied'}
                self.assertEqual(app.main([action, '--game-dir', 'C:/fake/Battle Brothers', '--yes']), 0)
                getattr(engine, method).assert_called_once_with()

    def test_gui_restart_checkbox_is_passed_to_selected_operation(self):
        app = importlib.import_module('bb_cn_installer')
        from unittest.mock import Mock
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        ui = app.Application(root)
        root.after_cancel(ui.detect_id)
        self.addCleanup(lambda: root.after_cancel(ui.poll_id))
        ui.restart_steam.set(True)
        engine = Mock()
        engine.game = Path('C:/fake/Battle Brothers')
        engine.store = engine.game / '.bb-cn-installer'
        engine.check.return_value = {'healthy': True}
        engine.set_steam_options.return_value = {'status': 'applied', 'note': 'legacy'}
        engine.restore_steam_options.return_value = {'status': 'restored', 'note': 'legacy'}
        engine.uninstall.return_value = {'note': 'legacy'}
        def immediate(label, task, done=None):
            return task()
        with patch.object(ui, 'engine', return_value=engine), patch.object(ui, 'start', side_effect=immediate), patch.object(app.messagebox, 'askyesno', return_value=True), patch.object(app, 'steam_operation', return_value={'status':'applied','note':'verified'}) as operation:
            ui.steam()
            ui.steam_restore()
            ui.uninstall()
        self.assertEqual(operation.call_args_list, [
            unittest.mock.call(engine, 'set', restart=True),
            unittest.mock.call(engine, 'restore', restart=True),
            unittest.mock.call(engine, 'uninstall', restart=True)])

    def test_gui_background_worker_returns_to_main_thread_without_blocking(self):
        app = importlib.import_module('bb_cn_installer')
        import threading
        import time
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        ui = app.Application(root)
        root.after_cancel(ui.detect_id)
        self.addCleanup(lambda: root.after_cancel(ui.poll_id))
        release = threading.Event()
        done = []
        def task():
            release.wait(2)
            return 'worker completed'
        ui.start('background', task, done.append)
        self.assertTrue(ui.busy)
        root.update()
        self.assertEqual(done, [])
        release.set()
        deadline = time.monotonic() + 4
        while ui.busy and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        self.assertFalse(ui.busy)
        self.assertEqual(done, ['worker completed'])

if __name__ == '__main__': unittest.main()
