"""Simplified GUI tests; all engines/Steam work use isolated fakes, never live files."""
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import bb_cn_installer as app


class SimpleAppTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.ui = app.Application(self.root)
        self.addCleanup(self.cleanup)
        errors = patch.object(app.messagebox, 'showerror')
        self.error_dialog = errors.start()
        self.addCleanup(errors.stop)
        if hasattr(self.ui, 'detect_id'):
            self.root.after_cancel(self.ui.detect_id)

    def cleanup(self):
        self.root.after_cancel(self.ui.poll_id)
        self.root.destroy()

    def test_simple_main_and_collapsed_advanced_preserve_legacy_actions(self):
        ui = self.ui
        self.assertIn('install_all', ui.actions)
        self.assertEqual(ui.actions['install_all'].cget('text'), '一键安装')
        self.assertEqual(ui.advanced.winfo_manager(), '')
        self.assertIsInstance(ui.game_summary, app.ttk.Label)
        self.assertIsInstance(ui.launcher_summary, app.ttk.Label)
        self.assertFalse(ui.restart_steam.get())
        self.assertIn('不会', ui.restart_checkbox.cget('text'))
        for key in ('discover', 'prepare', 'install', 'check', 'recover', 'steam', 'steam_restore'):
            self.assertTrue(str(ui.actions[key]).startswith(str(ui.advanced)))
        ui.toggle_advanced()
        self.assertEqual(ui.advanced.winfo_manager(), 'pack')
        ui.toggle_advanced()
        self.assertEqual(ui.advanced.winfo_manager(), '')
    def test_detection_is_delayed_read_only_and_does_not_choose_ambiguous_game(self):
        ui = self.ui
        self.assertTrue(hasattr(ui, 'detect_id'), 'Detection must be scheduled after creating the GUI')
        result = {'games': [Path('one'), Path('two')], 'game': None,
                  'launchers': [], 'launcher': None, 'note': '发现多个游戏，请选择'}
        pending = []
        with patch.object(ui, 'start', side_effect=lambda label, task, done=None: pending.append((task, done))), \
                patch.object(app, 'detect_paths', return_value=result) as detect:
            ui.discover()
            self.assertEqual(ui.game.get(), '')
            task, done = pending.pop()
            done(task())
        detect.assert_called_once_with()
        self.assertEqual(ui.game.get(), '')
        self.assertEqual(ui.launcher.get(), '')
        self.assertIn('多个', ui.status.get())

    def test_detection_preserves_paths_changed_before_its_completion(self):
        ui = self.ui
        result = {'games': [Path('found')], 'game': Path('found'),
                  'launchers': [Path('launcher.exe')], 'launcher': Path('launcher.exe'), 'note': '检测完成'}
        pending = []
        with patch.object(ui, 'start', side_effect=lambda label, task, done=None: pending.append((task, done))), \
                patch.object(app, 'detect_paths', return_value=result):
            ui.discover()
            ui.game.set('manually chosen')
            ui.launcher.set('manual.exe')
            task, done = pending.pop()
            done(task())
        self.assertEqual(ui.game.get(), 'manually chosen')
        self.assertEqual(ui.launcher.get(), 'manual.exe')
    def test_main_guidance_and_validation_states_invalidate_on_edit(self):
        ui = self.ui
        self.assertTrue(hasattr(ui, 'game_state'), 'Missing explicit validation state')
        self.assertIn('缺少', ui.game_state.get())
        self.assertIn('缺少', ui.launcher_state.get())
        self.assertFalse(str(ui.download_button).startswith(str(ui.advanced)))
        self.assertFalse(str(ui.redetect_button).startswith(str(ui.advanced)))
        self.assertIn('下载', ui.component_help.cget('text'))
        with patch.object(app.webbrowser, 'open') as opened:
            ui.download_button.invoke()
        opened.assert_called_once_with(app.PINS['launcher']['url'])
        result = {'game': Path('found'), 'launcher': Path('pinned.exe'),
                  'games': [Path('found')], 'launchers': [Path('pinned.exe')], 'note': '只读检测'}
        pending = []
        with patch.object(ui, 'start', side_effect=lambda label, task, done=None: pending.append((task, done))), \
                patch.object(app, 'detect_paths', return_value=result):
            ui.discover()
            task, done = pending.pop()
            done(task())
        self.assertIn('已校验', ui.game_state.get())
        self.assertIn('已校验', ui.launcher_state.get())
        with patch.object(app, 'sha256') as digest:
            ui.game.set('manual game')
            ui.launcher.set(__file__)
        digest.assert_not_called()
        self.assertIn('未校验', ui.game_state.get())
        self.assertIn('未校验', ui.launcher_state.get())

    def test_redetect_validates_explicit_paths_off_thread_without_replacing_them(self):
        ui = self.ui
        ui.game.set('chosen game')
        ui.launcher.set(__file__)
        threads = []
        def validate_launcher(path):
            threads.append(threading.get_ident())
            return app.PINS['launcher']['sha256']
        result = {'game': Path('chosen game'), 'launcher': Path('other.exe'),
                  'games': [Path('chosen game')], 'launchers': [Path('other.exe')], 'note': 'checked'}
        with patch.object(app, 'detect_paths', return_value=result) as detect, \
                patch.object(app, 'sha256', side_effect=validate_launcher):
            ui.discover()
            self.drain()
        detect.assert_called_once_with(game='chosen game')
        self.assertEqual(ui.launcher.get(), __file__)
        self.assertEqual(ui.game.get(), 'chosen game')
        self.assertIn('已校验', ui.game_state.get())
        self.assertIn('已校验', ui.launcher_state.get())
        self.assertTrue(threads)
        self.assertNotIn(threading.get_ident(), threads)

    def test_redetect_mismatch_or_changed_selection_never_claims_valid(self):
        ui = self.ui
        ui.game.set('chosen game')
        ui.launcher.set(__file__)
        result = {'game': None, 'launcher': Path('other.exe'),
                  'games': [], 'launchers': [Path('other.exe')], 'note': 'unsupported'}
        with patch.object(app, 'detect_paths', return_value=result), \
                patch.object(app, 'sha256', return_value='wrong'):
            ui.discover()
            self.drain()
        self.assertIn('未通过', ui.game_state.get())
        self.assertIn('未通过', ui.launcher_state.get())
        pending = []
        with patch.object(ui, 'start', side_effect=lambda label, task, done=None: pending.append((task, done))), \
                patch.object(app, 'detect_paths', return_value=result), \
                patch.object(app, 'sha256', return_value=app.PINS['launcher']['sha256']):
            ui.discover()
            task, done = pending.pop()
            value = task()
            ui.launcher.set('other selection')
            ui.launcher.set(__file__)
            done(value)
        self.assertIn('未校验', ui.launcher_state.get())

    def fake_engine(self):
        engine = Mock()
        engine.game = Path('C:/fake/Battle Brothers')
        engine.store = engine.game / '.bb-cn-installer'
        engine.check.return_value = {'healthy': True}
        self.ui.game.set(str(engine.game))
        self.ui.launcher.set(__file__)
        return engine

    def drain(self):
        deadline = time.monotonic() + 5
        while self.ui.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertFalse(self.ui.busy, 'Worker/callback chain timed out')

    def test_install_results_are_separate_and_copy_uses_captured_verified_target(self):
        engine = self.fake_engine()
        prepared = app.Prepared(Path('cached/zdata_cn.zip'), 'digest', [])
        ui = self.ui
        self.assertTrue(hasattr(ui, 'file_result'), 'Missing main-screen file result')
        self.assertIn('disabled', ui.copy_button.state())
        with patch.object(ui, 'engine', return_value=engine), \
                patch.object(app, 'prepare', return_value=prepared), \
                patch.object(app.messagebox, 'askyesno', return_value=True):
            ui.install_all()
            self.drain()
        self.assertIn('已安装并校验', ui.file_result.get())
        self.assertIn('未修改', ui.steam_result.get())
        before = ui.steam_result.get()
        with patch.object(self.root, 'clipboard_clear') as clear, \
                patch.object(self.root, 'clipboard_append') as copied:
            ui.copy_button.invoke()
        clear.assert_called_once_with()
        copied.assert_called_once_with(app.launch_option(engine.game))
        self.assertEqual(ui.steam_result.get(), before, 'Clipboard is not Steam success')
        ui.game.set('edited target')
        ui.game.set(str(engine.game))
        self.assertIn('disabled', ui.copy_button.state())
        with patch.object(self.root, 'clipboard_append') as copied:
            ui.copy_launch_command()
        copied.assert_not_called()
        self.assertIn('未校验', ui.file_result.get())

    def test_layout_bounds_advanced_height_to_screen_and_keeps_main_copy_visible(self):
        ui = self.ui
        self.assertTrue(hasattr(ui, 'content_canvas'), 'Missing scrollable content for small desktops')
        with patch.object(self.root, 'winfo_screenheight', return_value=768):
            ui.toggle_advanced()
            self.root.update_idletasks()
            self.assertLessEqual(int(self.root.geometry().split('x')[1].split('+')[0]), 688)
        self.assertTrue(ui.content_canvas.cget('yscrollcommand'))
        self.assertFalse(str(ui.copy_button).startswith(str(ui.advanced)))

    def test_steam_file_check_is_off_thread_and_failure_does_not_claim_installed(self):
        engine = self.fake_engine()
        threads = []
        def check():
            threads.append(threading.get_ident())
            return {'healthy': False}
        engine.check.side_effect = check
        with patch.object(self.ui, 'engine', return_value=engine), \
                patch.object(app.messagebox, 'askyesno', return_value=True), \
                patch.object(app, 'steam_operation') as operation:
            self.ui.steam()
            self.drain()
        operation.assert_not_called()
        self.assertTrue(threads)
        self.assertNotIn(threading.get_ident(), threads)
        self.assertNotIn('已安装', self.ui.file_result.get())
        self.assertIn('disabled', self.ui.copy_button.state())

    def test_changed_target_during_install_never_receives_old_success_or_copy(self):
        engine = self.fake_engine()
        pending = []
        ui = self.ui
        with patch.object(ui, 'engine', return_value=engine), \
                patch.object(app.messagebox, 'askyesno', return_value=True), \
                patch.object(app, 'prepare', return_value=app.Prepared(Path('cache/zdata_cn.zip'), 'digest', [])), \
                patch.object(ui, 'start', side_effect=lambda label, task, done=None, **kw: pending.append((task, done))):
            ui.install_all()
            task, done = pending.pop(0)
            done(task())
            task, done = pending.pop(0)
            task()
            ui.game.set('new target')
            done(None)
        self.assertNotIn('已安装', ui.file_result.get())
        self.assertIn('选择已变化', ui.steam_result.get())
        self.assertIn('disabled', ui.copy_button.state())

    def test_copy_refuses_result_whose_captured_target_does_not_match_selection(self):
        ui = self.ui
        ui.game.set('current target')
        ui.record_installed(Path('other target'), dict(ui.selection_revision))
        with patch.object(self.root, 'clipboard_append') as copied:
            ui.copy_launch_command()
        copied.assert_not_called()
        self.assertIn('disabled', ui.copy_button.state())

    def test_new_failed_steam_file_check_invalidates_old_copy_result(self):
        engine = self.fake_engine()
        ui = self.ui
        ui.record_installed(engine.game, dict(ui.selection_revision))
        engine.check.return_value = {'healthy': False}
        with patch.object(ui, 'engine', return_value=engine), \
                patch.object(app.messagebox, 'askyesno', return_value=True):
            ui.steam()
            self.drain()
        self.assertNotIn('已安装', ui.file_result.get())
        self.assertIn('disabled', ui.copy_button.state())

    def test_stale_steam_restore_and_uninstall_callbacks_do_not_update_new_target(self):
        engine = self.fake_engine()
        ui = self.ui
        for method in (ui.steam, ui.steam_restore, ui.uninstall):
            with self.subTest(method=method.__name__):
                ui.game.set(str(engine.game))
                pending = []
                with patch.object(ui, 'engine', return_value=engine), \
                        patch.object(app.messagebox, 'askyesno', return_value=True), \
                        patch.object(app, 'steam_operation', return_value={'status': 'restored', 'note': 'verified'}), \
                        patch.object(ui, 'start', side_effect=lambda label, task, done=None, **kw: pending.append((task, done))):
                    method()
                    task, done = pending.pop()
                    result = task()
                    ui.game.set('new selection')
                    done(result)
                self.assertIn('选择已变化', ui.file_result.get())
                self.assertIn('选择已变化', ui.steam_result.get())
                self.assertIn('disabled', ui.copy_button.state())

    def test_recovery_invalidates_previously_verified_copy_target(self):
        engine = self.fake_engine()
        ui = self.ui
        ui.record_installed(engine.game, dict(ui.selection_revision))
        with patch.object(ui, 'engine', return_value=engine), \
                patch.object(app.messagebox, 'askyesno', return_value=True):
            ui.recover()
            self.drain()
        self.assertIn('disabled', ui.copy_button.state())
        self.assertNotIn('已安装', ui.file_result.get())

    def test_legacy_install_and_steam_results_are_independent(self):
        engine = self.fake_engine()
        ui = self.ui
        ui.prepared = app.Prepared(Path('cached/zdata_cn.zip'), 'digest', [])
        ui.prepared_game = engine.game
        with patch.object(ui, 'engine', return_value=engine), \
                patch.object(app.messagebox, 'askyesno', return_value=True):
            ui.install()
            self.drain()
        self.assertIn('已安装并校验', ui.file_result.get())
        self.assertIn('未修改', ui.steam_result.get())
        for result in ({'status': 'applied', 'note': 'verified'},
                       {'status': 'manual', 'note': 'not verified'}):
            with self.subTest(result=result), patch.object(ui, 'engine', return_value=engine), \
                    patch.object(app.messagebox, 'askyesno', return_value=True), \
                    patch.object(app, 'steam_operation', return_value=result), \
                    patch.object(self.root, 'clipboard_append') as clipboard:
                ui.steam()
                self.drain()
            self.assertIn('已安装并校验', ui.file_result.get())
            self.assertIn('已设置并读回' if result['status'] == 'applied' else '未完成', ui.steam_result.get())
            clipboard.assert_not_called()  # Explicit copy button, never an incidental fallback.

    def test_one_click_partial_failure_is_visible_in_separate_results(self):
        engine = self.fake_engine()
        ui = self.ui
        ui.restart_steam.set(True)
        with patch.object(ui, 'engine', return_value=engine), \
                patch.object(app, 'prepare', return_value=app.Prepared(Path('cache/zdata_cn.zip'), 'digest', [])), \
                patch.object(app.messagebox, 'askyesno', return_value=True), \
                patch.object(app, 'steam_operation', side_effect=RuntimeError('after possible write')):
            ui.install_all()
            self.drain()
        self.assertIn('已安装并校验', ui.file_result.get())
        self.assertIn('未完成', ui.steam_result.get())
        self.assertNotIn('disabled', ui.copy_button.state())

    def test_failed_file_verification_does_not_offer_copy_or_file_success(self):
        engine = self.fake_engine()
        engine.check.return_value = {'healthy': False}
        ui = self.ui
        with patch.object(ui, 'engine', return_value=engine), \
                patch.object(app, 'prepare', return_value=app.Prepared(Path('cache/zdata_cn.zip'), 'digest', [])), \
                patch.object(app.messagebox, 'askyesno', return_value=True):
            ui.install_all()
            self.drain()
        self.assertIn('未完成', ui.file_result.get())
        self.assertIn('未修改', ui.steam_result.get())
        self.assertIn('disabled', ui.copy_button.state())

    def test_uninstall_success_and_failure_report_file_and_steam_separately(self):
        engine = self.fake_engine()
        ui = self.ui
        for error in (None, app.SafetyError('restore refused')):
            with self.subTest(error=error), patch.object(ui, 'engine', return_value=engine), \
                    patch.object(app.messagebox, 'askyesno', return_value=True), \
                    patch.object(app, 'steam_operation', return_value={'status': 'not-managed', 'note': '手动设置须核对'}, side_effect=error):
                ui.uninstall()
                self.drain()
            self.assertIn('未完成' if error else '已卸载', ui.file_result.get())
            self.assertIn('未确认' if error else '手动', ui.steam_result.get())
            self.assertIn('disabled', ui.copy_button.state())

    def test_restore_reports_steam_only_without_claiming_file_installation(self):
        engine = self.fake_engine()
        with patch.object(self.ui, 'engine', return_value=engine), \
                patch.object(app.messagebox, 'askyesno', return_value=True), \
                patch.object(app, 'steam_operation', return_value={'status': 'restored', 'note': '原值读回'}):
            self.ui.steam_restore()
            self.drain()
        self.assertIn('已恢复并读回', self.ui.steam_result.get())
        self.assertNotIn('已安装', self.ui.file_result.get())

    def test_percent_game_path_is_rejected_before_prepare_or_install_without_restart(self):
        engine = self.fake_engine()
        engine.game = Path('C:/fake/100% Battle Brothers')
        self.ui.game.set(str(engine.game))
        self.assertFalse(self.ui.restart_steam.get())
        with patch.object(self.ui, 'engine', return_value=engine), \
                patch.object(app, 'prepare', return_value=app.Prepared(Path('cache/zdata_cn.zip'), 'digest', [])) as convert, \
                patch.object(app.messagebox, 'askyesno', return_value=True), \
                patch.object(app, 'steam_operation') as steam, \
                patch.object(self.root, 'report_callback_exception'):
            self.ui.install_all()
            self.drain()
        convert.assert_not_called()
        engine.install.assert_not_called()
        steam.assert_not_called()
        self.error_dialog.assert_called_once()
        self.assertIn('不安全启动选项字符', str(self.error_dialog.call_args))
        self.assertNotIn('已安装', self.ui.file_result.get())
        self.assertIn('disabled', self.ui.copy_button.state())

    def test_poll_contains_done_callback_failure_and_processes_later_events(self):
        engine = self.fake_engine()
        ui = self.ui
        revision = dict(ui.selection_revision)
        def broken_done(value):
            ui.record_installed(engine.game, revision)
            ui.steam_result.set('Steam 启动项：已设置并读回')
            raise RuntimeError('completion callback exploded')
        self.root.after_cancel(ui.poll_id)
        original_poll = ui.poll_id
        ui.events.put((True, None, broken_done))
        callback_error = None
        try:
            ui.poll()
        except Exception as exc:
            callback_error = exc
        self.assertIsNone(callback_error, 'done errors must not escape the poll loop')
        self.assertNotEqual(ui.poll_id, original_poll, 'poll must always reschedule')
        self.assertIn('未完成', ui.status.get())
        self.assertIn('未确认', ui.file_result.get())
        self.assertIn('未确认', ui.steam_result.get())
        self.assertIn('disabled', ui.copy_button.state())
        self.assertIsNone(ui.installed_target)
        self.assertIn('completion callback exploded', ui.log.get('1.0', 'end'))
        self.assertIn('Traceback', ui.log.get('1.0', 'end'))
        self.error_dialog.assert_called_once()
        processed = []
        ui.events.put((True, 'later event', processed.append))
        deadline = time.monotonic() + 2
        while not processed and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertEqual(processed, ['later event'])

    def test_one_click_prepares_installs_checks_without_steam_by_default(self):
        engine = self.fake_engine()
        prepared = app.Prepared(Path('cached/zdata_cn.zip'), 'digest', [])
        calls = []
        main_thread = threading.get_ident()
        def prepare(*args):
            self.assertNotEqual(threading.get_ident(), main_thread)
            calls.append('prepare')
            return prepared
        def install(*args, **kwargs):
            self.assertNotEqual(threading.get_ident(), main_thread)
            calls.append('install')
        def confirm(*args, **kwargs):
            self.assertEqual(threading.get_ident(), main_thread)
            calls.append('confirm')
            return True
        engine.install.side_effect = install
        engine.check.side_effect = lambda: calls.append('check') or {'healthy': True}
        with patch.object(self.ui, 'engine', return_value=engine), \
                patch.object(app, 'prepare', side_effect=prepare), \
                patch.object(app.messagebox, 'askyesno', side_effect=confirm), \
                patch.object(app, 'steam_operation') as steam:
            self.ui.actions['install_all'].invoke()
            self.drain()
        self.assertEqual(calls, ['confirm', 'prepare', 'install', 'check'])
        engine.install.assert_called_once_with(prepared, Path(__file__), replace=False, accept_warnings=False)
        steam.assert_not_called()
        self.assertIn('手动粘贴', self.ui.status.get())
        self.assertIs(self.ui.prepared, prepared)
    def test_actual_warnings_need_main_thread_acceptance_before_install(self):
        engine = self.fake_engine()
        warnings = [f'{i:04d} player/onDeath patch mismatch — 实际转换警告 ' + 'x' * 35
                    for i in range(2377)]
        prepared = app.Prepared(Path('cached/zdata_cn.zip'), 'digest', warnings)
        for accept in (False, True):
            with self.subTest(accept=accept):
                engine.reset_mock()
                def confirm_warnings(parent, actual, log_path):
                    self.assertEqual(threading.get_ident(), threading.main_thread().ident)
                    self.assertIs(parent, self.root)
                    self.assertIs(actual, warnings)
                    self.assertEqual(log_path, Path('cached/translator.log'))
                    return accept
                with patch.object(self.ui, 'engine', return_value=engine), \
                        patch.object(app, 'prepare', return_value=prepared), \
                        patch.object(app.messagebox, 'askyesno', return_value=True) as native, \
                        patch.object(app, 'confirm_conversion_warnings', side_effect=confirm_warnings) as modal, \
                        patch.object(app, 'steam_operation') as steam:
                    self.ui.install_all()
                    self.drain()
                modal.assert_called_once()
                self.assertEqual(native.call_count, 1, 'Warnings must not use a native message box')
                self.assertIn('\n'.join(warnings), self.ui.log.get('1.0', 'end'))
                steam.assert_not_called()
                if accept:
                    engine.install.assert_called_once_with(prepared, Path(__file__), replace=False, accept_warnings=True)
                else:
                    engine.install.assert_not_called()
                    engine.check.assert_not_called()
                    self.assertIn('取消', self.ui.status.get())

    def test_legacy_install_uses_complete_warning_modal_before_any_write(self):
        engine = self.fake_engine()
        warnings = [f'{i:04d} 实际转换警告 ' + 'x' * 45 for i in range(2377)]
        prepared = app.Prepared(Path('cached/zdata_cn.zip'), 'digest', warnings)
        self.ui.prepared = prepared
        self.ui.prepared_game = engine.game
        for accept in (False, True):
            with self.subTest(accept=accept):
                engine.reset_mock()
                def confirm_warnings(parent, actual, log_path):
                    self.assertEqual(threading.get_ident(), threading.main_thread().ident)
                    self.assertIs(actual, warnings)
                    self.assertEqual(log_path, Path('cached/translator.log'))
                    return accept
                with patch.object(self.ui, 'engine', return_value=engine), \
                        patch.object(app, 'confirm_conversion_warnings', side_effect=confirm_warnings) as modal, \
                        patch.object(app.messagebox, 'askyesno', return_value=True) as native, \
                        patch.object(app, 'steam_operation') as steam:
                    self.ui.install()
                    self.drain()
                modal.assert_called_once_with(self.root, warnings, Path('cached/translator.log'))
                self.assertEqual(native.call_count, int(accept))
                steam.assert_not_called()
                if accept:
                    engine.install.assert_called_once_with(prepared, Path(__file__), replace=False, accept_warnings=True)
                else:
                    engine.install.assert_not_called()
                    engine.check.assert_not_called()
    def test_restart_permission_confirmed_before_prepare_and_used_only_after_check(self):
        engine = self.fake_engine()
        prepared = app.Prepared(Path('cached/zdata_cn.zip'), 'digest', [])
        self.ui.restart_steam.set(True)
        calls = []
        def confirm(title, text, **kwargs):
            calls.append(('confirm', text))
            return True
        def convert(*args):
            self.assertEqual(len(calls), 2, 'Both write and Steam permissions must precede preparation')
            calls.append(('prepare', None))
            return prepared
        engine.check.side_effect = lambda: calls.append(('check', None)) or {'healthy': True}
        def operation(*args, **kwargs):
            self.assertEqual(calls[-1][0], 'check')
            self.assertNotEqual(threading.get_ident(), threading.main_thread().ident)
            return {'status': 'applied', 'note': '已设置并读回'}
        with patch.object(self.ui, 'engine', return_value=engine), \
                patch.object(app, 'prepare', side_effect=convert), \
                patch.object(app.messagebox, 'askyesno', side_effect=confirm), \
                patch.object(app, 'steam_operation', side_effect=operation) as steam:
            self.ui.install_all()
            self.drain()
        self.assertGreaterEqual(len(calls), 3, 'Missing explicit Steam permission before prepare')
        self.assertIn('重启', calls[1][1])
        self.assertIn('启动项', calls[1][1])
        steam.assert_called_once_with(engine, 'set', restart=True)
        self.assertIn('Steam', self.ui.status.get())
        self.assertIn('完成', self.ui.status.get())
    def test_steam_failure_preserves_installed_patch_with_truthful_partial_status(self):
        engine = self.fake_engine()
        prepared = app.Prepared(Path('cached/zdata_cn.zip'), 'digest', [])
        self.ui.restart_steam.set(True)
        for failure in (app.SafetyError('接口不可用'), RuntimeError('unexpected Steam failure')):
            with self.subTest(error=type(failure).__name__):
                engine.reset_mock()
                with patch.object(self.ui, 'engine', return_value=engine), \
                        patch.object(app, 'prepare', return_value=prepared), \
                        patch.object(app.messagebox, 'askyesno', return_value=True), \
                        patch.object(app, 'steam_operation', side_effect=failure) as steam:
                    self.ui.install_all()
                    self.drain()
                engine.install.assert_called_once()
                engine.check.assert_called_once()
                engine.uninstall.assert_not_called()
                engine.recover.assert_not_called()
                steam.assert_called_once()
                self.assertIn('已安装', self.ui.status.get())
                self.assertIn('Steam', self.ui.status.get())
                self.assertIn('未完成', self.ui.status.get())
                self.assertIn(str(failure), self.ui.log.get('1.0', 'end'))
    def test_instructions_explain_bundled_text_but_external_converter_and_launcher(self):
        text = self.ui.instructions.cget('text')
        self.assertIn('内置汉化文本', text)
        self.assertIn('首次需联网获取转换器', text)
        self.assertIn('从作者页面导入启动器', text)
        self.assertNotIn('完全离线', text)
    def test_permission_cancellation_never_prepares_or_writes(self):
        engine = self.fake_engine()
        for restart, answers in ((False, [False]), (True, [True, False])):
            with self.subTest(restart=restart):
                self.ui.restart_steam.set(restart)
                with patch.object(self.ui, 'engine', return_value=engine), \
                        patch.object(app, 'prepare') as convert, \
                        patch.object(app.messagebox, 'askyesno', side_effect=answers), \
                        patch.object(app, 'steam_operation') as steam:
                    self.ui.install_all()
                convert.assert_not_called()
                engine.install.assert_not_called()
                steam.assert_not_called()
                self.assertFalse(self.ui.busy)

    def test_failed_file_verification_never_sets_steam(self):
        engine = self.fake_engine()
        engine.check.return_value = {'healthy': False}
        self.ui.restart_steam.set(True)
        prepared = app.Prepared(Path('cached/zdata_cn.zip'), 'digest', [])
        with patch.object(self.ui, 'engine', return_value=engine), \
                patch.object(app, 'prepare', return_value=prepared), \
                patch.object(app.messagebox, 'askyesno', return_value=True), \
                patch.object(app, 'steam_operation') as steam:
            self.ui.install_all()
            self.drain()
        engine.install.assert_called_once()
        steam.assert_not_called()
        self.error_dialog.assert_called_once()
        self.assertIn('安装后校验失败', self.ui.log.get('1.0', 'end'))

    def test_startup_detection_runs_off_thread_and_changes_only_summaries(self):
        main = threading.get_ident()
        detected_threads = []
        def detect():
            detected_threads.append(threading.get_ident())
            return {'games': [Path('detected game')], 'game': Path('detected game'),
                    'launchers': [Path('pinned.exe')], 'launcher': Path('pinned.exe'), 'note': '只读检测完成'}
        with patch.object(app, 'detect_paths', side_effect=detect), \
                patch.object(app, 'prepare') as convert, \
                patch.object(app, 'Engine') as engine, \
                patch.object(app, 'steam_operation') as steam:
            self.ui.detect_id = self.root.after(1, self.ui.discover)
            deadline = time.monotonic() + 3
            while self.ui.game.get() != 'detected game' and time.monotonic() < deadline:
                self.root.update()
                time.sleep(0.01)
        self.assertEqual(self.ui.game.get(), 'detected game')
        self.assertEqual(self.ui.launcher.get(), 'pinned.exe')
        self.assertEqual(len(detected_threads), 1)
        self.assertNotEqual(detected_threads[0], main)
        convert.assert_not_called()
        engine.assert_not_called()
        steam.assert_not_called()


if __name__ == '__main__':
    unittest.main()
