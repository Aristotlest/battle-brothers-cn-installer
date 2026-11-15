"""Shared write preflight regressions; all game/input files are scratch fixtures."""
import io
import json
from pathlib import Path
import time
import tkinter as tk
import unittest
from unittest.mock import patch

import bb_cn_installer as app
import test_core


class LaunchValidationTests(unittest.TestCase):
    setUp = test_core.CoreTests.setUp

    def unsafe_game(self):
        game = self.game.with_name('100% Battle Brothers')
        self.game.rename(game)
        self.game = game
        (game.parent.parent / 'appmanifest_365360.acf').write_text(
            '\"AppState\" { \"appid\" \"365360\" \"installdir\" \"100% Battle Brothers\" }')
        self.engine = self.c.Engine(game, platform=self.engine.platform)

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() if p.is_file() else None
                for p in self.root.rglob('*')}

    def gui(self):
        root = tk.Tk()
        root.withdraw()
        ui = app.Application(root)
        root.after_cancel(ui.detect_id)
        self.addCleanup(root.destroy)
        self.addCleanup(lambda: root.after_cancel(ui.poll_id))
        ui.game.set(str(self.game))
        ui.launcher.set(str(self.launcher))
        ui.cache.set(str(self.root / 'new-cache'))
        return root, ui

    def drain(self, root, ui):
        deadline = time.monotonic() + 5
        while ui.busy and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        self.assertFalse(ui.busy, 'Worker/callback chain timed out')

    def test_core_prepare_rejects_percent_path_before_cache_or_dependency_writes(self):
        self.unsafe_game()
        before = self.snapshot()
        with patch.object(self.c, 'fetch_pinned', side_effect=self.c.SafetyError('dependency fetch reached')) as fetch:
            with self.assertRaises(self.c.SafetyError) as refused:
                self.c.prepare(self.engine, self.launcher, self.root / 'new-cache')
        self.assertEqual(before, self.snapshot(), 'rejection must precede even cache mkdir')
        self.assertIn('不安全启动选项字符', str(refused.exception))
        fetch.assert_not_called()

    def test_advanced_gui_prepare_rejects_percent_path_without_writes(self):
        self.unsafe_game()
        root, ui = self.gui()
        before = self.snapshot()
        with patch.object(ui, 'engine', return_value=self.engine), \
                patch.object(self.c, 'fetch_pinned', side_effect=self.c.SafetyError('dependency fetch reached')) as fetch, \
                patch.object(app.messagebox, 'showerror') as error:
            ui.actions['prepare'].invoke()
            self.drain(root, ui)
        self.assertEqual(before, self.snapshot(), 'advanced prepare must use shared write preflight')
        fetch.assert_not_called()
        self.assertIn('不安全启动选项字符', str(error.call_args))
        self.assertIsNone(ui.prepared)

    def test_cli_prepare_rejects_percent_path_without_writes(self):
        self.unsafe_game()
        before = self.snapshot()
        with patch.object(app, 'Engine', return_value=self.engine), \
                patch.object(self.c, 'fetch_pinned', side_effect=self.c.SafetyError('dependency fetch reached')) as fetch, \
                patch.object(app.sys, 'stdout', new=io.StringIO()) as output:
            code = app.main(['prepare', '--game-dir', str(self.game),
                             '--launcher', str(self.launcher), '--cache-dir', str(self.root / 'new-cache')])
        self.assertEqual(before, self.snapshot(), 'CLI prepare must use shared write preflight')
        self.assertEqual(code, 2)
        self.assertIn('不安全启动选项字符', output.getvalue())
        fetch.assert_not_called()

    def test_redetect_explicit_installed_launcher_separates_hash_evidence_from_source_usability(self):
        from installer_paths import detect_paths
        installed = self.game / self.c.TARGETS[1]
        installed.write_bytes(self.launcher.read_bytes())
        root, ui = self.gui()
        ui.launcher.set(str(installed))
        self.engine.platform.steam = lambda: self.root
        before = self.snapshot()
        captured = []
        detector = lambda **kw: detect_paths(platform=self.engine.platform, downloads_roots=[], **kw)
        with patch.object(app, 'detect_paths', side_effect=detector), \
                patch.object(ui, 'start', side_effect=lambda label, task, done: captured.append((task(), done))):
            ui.discover()
        result, done = captured.pop()
        self.assertTrue(result['explicit_launcher_valid'], 'installed hash is valid component evidence')
        self.assertFalse(result.get('explicit_launcher_usable', True), 'inside-game source is not usable')
        done(result)
        self.assertEqual(ui.launcher.get(), str(installed), 'do not silently replace explicit selection')
        self.assertIn('SHA256', ui.launcher_state.get())
        self.assertIn('不可作安装源', ui.launcher_state.get())
        self.assertIn('游戏目录之外', ui.status.get())
        self.assertEqual(before, self.snapshot())

    def test_redetect_explicit_external_launcher_is_usable_for_selected_game(self):
        from installer_paths import detect_paths
        root, ui = self.gui()
        self.engine.platform.steam = lambda: self.root
        before = self.snapshot()
        captured = []
        detector = lambda **kw: detect_paths(platform=self.engine.platform, downloads_roots=[], **kw)
        with patch.object(app, 'detect_paths', side_effect=detector), \
                patch.object(ui, 'start', side_effect=lambda label, task, done: captured.append((task(), done))):
            ui.discover()
        result, done = captured.pop()
        self.assertTrue(result['explicit_launcher_valid'])
        self.assertTrue(result.get('explicit_launcher_usable', False))
        done(result)
        self.assertIn('已校验', ui.launcher_state.get())
        self.assertNotIn('不可作安装源', ui.launcher_state.get())
        self.assertEqual(before, self.snapshot())

    def test_redetect_explicit_source_usability_is_invalidated_by_game_edit(self):
        from installer_paths import detect_paths
        root, ui = self.gui()
        self.engine.platform.steam = lambda: self.root
        captured = []
        detector = lambda **kw: detect_paths(platform=self.engine.platform, downloads_roots=[], **kw)
        with patch.object(app, 'detect_paths', side_effect=detector), \
                patch.object(ui, 'start', side_effect=lambda label, task, done: captured.append((task(), done))):
            ui.discover()
        result, done = captured.pop()
        ui.game.set(str(self.root))  # The formerly external source is now inside the selected target.
        done(result)
        self.assertNotIn('已校验', ui.launcher_state.get(), 'stale target must not authorize source usability')

    def test_core_install_rejects_percent_path_before_lock_or_state_writes(self):
        self.unsafe_game()
        before = self.snapshot()
        refused = None
        with patch.object(self.c, 'game_lock', wraps=self.c.game_lock) as lock:
            try:
                self.engine.install(self.prepared, self.launcher)
            except self.c.SafetyError as exc:
                refused = exc
        self.assertEqual(before, self.snapshot(), 'rejection must precede state and target writes')
        self.assertIsNotNone(refused, 'install must reject the unrepresentable launch command')
        self.assertIn('不安全启动选项字符', str(refused))
        lock.assert_not_called()

    def test_core_install_rejects_percent_path_even_with_replacement_and_warning_consent(self):
        self.unsafe_game()
        for rel in self.c.TARGETS:
            (self.game / rel).write_bytes(b'unknown existing file')
        prepared = self.c.Prepared(self.pkg, self.prepared.digest, ['actual warning'])
        before = self.snapshot()
        refused = None
        try:
            self.engine.install(prepared, self.launcher, replace=True, accept_warnings=True)
        except self.c.SafetyError as exc:
            refused = exc
        self.assertEqual(before, self.snapshot(), 'consent must not bypass command safety')
        self.assertIn('不安全启动选项字符', str(refused))

    def test_advanced_gui_install_rejects_percent_path_without_writes(self):
        self.unsafe_game()
        root, ui = self.gui()
        ui.prepared = self.prepared
        ui.prepared_game = self.game
        before = self.snapshot()
        with patch.object(ui, 'engine', return_value=self.engine), \
                patch.object(app.messagebox, 'askyesno', return_value=True), \
                patch.object(app.messagebox, 'showerror') as error, \
                patch.object(app, 'steam_operation') as steam:
            ui.actions['install'].invoke()
            self.drain(root, ui)
        self.assertEqual(before, self.snapshot(), 'advanced install must use shared write preflight')
        self.assertIn('不安全启动选项字符', str(error.call_args))
        self.assertIn('disabled', ui.copy_button.state())
        self.assertNotIn('已安装并校验', ui.file_result.get())
        steam.assert_not_called()

    def test_cli_install_rejects_percent_path_without_writes(self):
        self.unsafe_game()
        manifest = self.pkg.parent / 'prepared.json'
        manifest.write_text(json.dumps({'sha256': self.prepared.digest, 'warnings': []}))
        before = self.snapshot()
        with patch.object(app, 'Engine', return_value=self.engine), \
                patch.object(app.sys, 'stdout', new=io.StringIO()) as output:
            code = app.main(['install', '--game-dir', str(self.game), '--launcher', str(self.launcher),
                             '--prepared', str(manifest), '--yes'])
        self.assertEqual(before, self.snapshot(), 'CLI install must use shared write preflight')
        self.assertEqual(code, 2)
        self.assertIn('不安全启动选项字符', output.getvalue())

    def test_uninstall_remains_available_after_move_to_percent_path_and_game_update(self):
        self.engine.install(self.prepared, self.launcher)
        self.unsafe_game()
        self.engine.platform.version = lambda path: '1.5.2.4'
        self.engine.uninstall()
        self.assertFalse((self.engine.store / 'state.json').exists())
        for rel in self.c.TARGETS:
            self.assertFalse((self.game / rel).exists())
        self.assertEqual((self.game / 'win32/BattleBrothers.exe').read_bytes(), b'original-game')

    def test_recovery_remains_available_after_move_to_percent_path_and_game_update(self):
        replace = self.c.os.replace
        def interrupt(source, target):
            if Path(target) == self.game / self.c.TARGETS[1] and Path(source).name == 'new-1':
                raise KeyboardInterrupt('interrupted fixture transaction')
            return replace(source, target)
        with patch.object(self.c.os, 'replace', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.engine.install(self.prepared, self.launcher)
        self.unsafe_game()
        self.engine.platform.version = lambda path: '1.5.2.4'
        self.engine.recover()
        self.assertFalse((self.engine.store / 'journal.json').exists())
        for rel in self.c.TARGETS:
            self.assertFalse((self.game / rel).exists())


if __name__ == '__main__':
    unittest.main()
