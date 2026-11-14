"""All launch-option writes are fake; never open or modify live Steam."""
import hashlib
import json
import subprocess
import unittest
from unittest.mock import patch
import test_core


class FakeSteam:
    def __init__(self, value=''):
        self.value = value
        self.writes = []

    def read(self):
        return self.value

    def exchange(self, expected, value):
        if self.value != expected:
            raise RuntimeError('concurrent Steam edit')
        self.writes.append((expected, value))
        self.value = value
        return self.value


class SteamOptionTests(unittest.TestCase):
    setUp = test_core.CoreTests.setUp
    def test_existing_options_are_backed_up_before_exact_ui_set_and_restored_on_uninstall(self):
        self.engine.install(self.prepared, self.launcher)
        backend = FakeSteam('-windowed 原选项')
        self.assertTrue(hasattr(self.engine, 'set_steam_options'), 'automatic UI set is missing')
        result = self.engine.set_steam_options(backend)
        self.assertEqual(result['status'], 'applied')
        expected = self.c.launch_option(self.game)
        self.assertEqual(backend.value, expected)
        state = json.loads((self.engine.store / 'launch-options.json').read_text(encoding='utf-8'))
        self.assertEqual(state['original'], '-windowed 原选项')
        self.assertEqual(state['original_sha256'], hashlib.sha256(state['original'].encode()).hexdigest())
        self.assertEqual(state['applied'], expected)
        self.assertEqual(state['phase'], 'applied')
        self.assertTrue(self.engine.check()['healthy'])
        self.engine.uninstall(steam_backend=backend)
        self.assertEqual(backend.value, '-windowed 原选项')
        self.assertFalse((self.game / self.c.TARGETS[1]).exists())
        self.assertEqual(json.loads((self.engine.store / 'launch-options.json').read_text())['phase'], 'restored')


    def test_empty_original_is_cleared_and_repeat_set_keeps_first_backup(self):
        self.engine.install(self.prepared, self.launcher)
        backend = FakeSteam('')
        self.engine.set_steam_options(backend)
        self.engine.set_steam_options(backend)
        self.assertEqual(len(backend.writes), 1)
        self.engine.restore_steam_options(backend)
        self.assertEqual(backend.value, '')

    def test_user_edit_refuses_restore_and_uninstall_without_writes(self):
        self.engine.install(self.prepared, self.launcher)
        backend = FakeSteam('-windowed')
        self.engine.set_steam_options(backend)
        backend.value = '-user-changed'
        before = (self.engine.store / 'launch-options.json').read_bytes()
        with self.assertRaises(self.c.SafetyError):
            self.engine.uninstall(steam_backend=backend)
        self.assertEqual(backend.value, '-user-changed')
        self.assertEqual((self.engine.store / 'launch-options.json').read_bytes(), before)
        self.assertTrue(self.engine.check()['healthy'])

    def test_ui_unavailable_has_manual_fallback_and_does_not_create_ownership(self):
        self.engine.install(self.prepared, self.launcher)
        backend = FakeSteam()
        def unavailable():
            raise RuntimeError('UI inaccessible')
        backend.read = unavailable
        with self.assertRaisesRegex(self.c.SafetyError, '未能自动定位，未写入'):
            self.engine.set_steam_options(backend)
        self.assertFalse((self.engine.store / 'launch-options.json').exists())
        self.assertEqual(backend.writes, [])

    def test_verification_failure_does_not_report_success_and_preserves_original(self):
        self.engine.install(self.prepared, self.launcher)
        backend = FakeSteam('-original')
        def ignored(expected, value):
            return value  # Simulates a misleading UI setter acknowledgement, not saved state.
        backend.exchange = ignored
        with self.assertRaisesRegex(self.c.SafetyError, '自动设置失败'):
            self.engine.set_steam_options(backend)
        self.assertEqual(backend.value, '-original')
        self.assertEqual(json.loads((self.engine.store / 'launch-options.json').read_text())['phase'], 'pending')

    def test_failed_write_rolls_back_with_persisted_original(self):
        self.engine.install(self.prepared, self.launcher)
        backend = FakeSteam('-original')
        original_exchange = backend.exchange
        def fail_after_set(expected, value):
            saved = json.loads((self.engine.store / 'launch-options.json').read_text())
            self.assertEqual(saved['original'], '-original')
            self.assertEqual(saved['phase'], 'pending')
            result = original_exchange(expected, value)
            if value == self.c.launch_option(self.game):
                raise RuntimeError('Steam save failed after UI changed')
            return result
        backend.exchange = fail_after_set
        with self.assertRaisesRegex(self.c.SafetyError, '已读回确认原值'):
            self.engine.set_steam_options(backend)
        self.assertEqual(backend.value, '-original')
        self.assertEqual(json.loads((self.engine.store / 'launch-options.json').read_text())['phase'], 'pending')

    def test_native_backend_uses_fixed_script_and_json_stdin_not_shell_interpolation(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('steam_ui'), 'native UIA backend missing')
        from steam_ui import WindowsSteamUI
        calls = []
        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, json.dumps({'value': '" & evil; 中文 %command%'}).encode(), b'')
        ui = WindowsSteamUI(runner=runner)
        self.assertEqual(ui.exchange('', '" & evil; 中文 %command%'), '" & evil; 中文 %command%')
        command, kwargs = calls[0]
        self.assertNotIn('" & evil; 中文 %command%', ' '.join(command))
        self.assertFalse(kwargs.get('shell', False))
        self.assertEqual(json.loads(kwargs['input']), {'action': 'exchange', 'expected': '', 'value': '" & evil; 中文 %command%'})
        self.assertEqual(kwargs['timeout'], 60)

    def test_native_helper_selftest_loads_dotnet_without_opening_steam(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('steam_ui'), 'native UIA backend missing')
        from steam_ui import WindowsSteamUI
        self.assertEqual(WindowsSteamUI()._request({'action': 'selftest'}), 'ready')

    def test_native_selector_semantics_use_appid_and_ids_not_chinese_or_window_depth(self):
        import base64
        from steam_ui import SCRIPT
        # Exercise actual PowerShell selector functions on synthetic UIA Current
        # property objects. No root tree lookup, URI opening or Steam UI access.
        prefix = SCRIPT.split('    $field = Open-Field\n')[0]
        harness = r'''
    function Node($id, $name) {
        return [pscustomobject]@{Current=[pscustomobject]@{AutomationId=$id;Name=$name;HelpText=''}}
    }
    $checks = @(
        (Semantic (Node 'gameproperties/365360' 'arbitrary localized title') 'page'),
        (-not (Semantic (Node 'gameproperties/999' 'Battle Brothers') 'page')),
        (-not (Semantic (Node 'gameproperties/3653600' 'Battle Brothers') 'page')),
        (-not (Semantic (Node '' 'Battle Brothers') 'page')),
        (Semantic (Node 'properties-general-tab' 'untranslated') 'general'),
        (Semantic (Node 'launch-options' 'untranslated') 'launch'),
        (-not (Semantic (Node 'password' 'Login') 'launch'))
    )
    if ($checks -contains $false) { throw 'Semantic identity check failed' }
    @{value='selectors-ready'} | ConvertTo-Json -Compress
} catch { @{error=$_.Exception.Message} | ConvertTo-Json -Compress; exit 2 }
'''
        import os
        from pathlib import Path
        ps = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        command = [str(ps), '-NoProfile', '-NonInteractive', '-STA', '-EncodedCommand',
                   base64.b64encode((prefix + harness).encode('utf-16-le')).decode()]
        result = subprocess.run(command, input=b'{"action":"read"}', capture_output=True, timeout=30,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout.decode('utf-8-sig')), {'value': 'selectors-ready'})

    def test_corrupt_launch_schema_is_rejected_by_check_before_ui_or_game_writes(self):
        self.engine.install(self.prepared, self.launcher)
        backend = FakeSteam('-original')
        self.engine.set_steam_options(backend)
        path = self.engine.store / 'launch-options.json'
        good = json.loads(path.read_text())
        for key, value in [('schema', True), ('appid', '999'), ('original_sha256', '0'*64),
                           ('original', None), ('phase', 'unknown'), ('extra', 1),
                           ('game', str(self.root)), ('applied', 'evil')]:
            with self.subTest(key=key):
                bad = dict(good, **{key: value})
                path.write_text(json.dumps(bad), encoding='utf-8')
                with self.assertRaises(self.c.SafetyError):
                    self.engine.check()
                with self.assertRaises(self.c.SafetyError):
                    self.engine.uninstall(steam_backend=backend)
                self.assertEqual(backend.value, good['applied'])
        path.write_text(json.dumps(good)[:-1] + ', "schema":1}', encoding='utf-8')
        with self.assertRaises(self.c.SafetyError):
            self.engine.restore_steam_options(backend)

    def test_rollback_never_overwrites_a_third_value_and_retry_can_restore_pending(self):
        self.engine.install(self.prepared, self.launcher)
        backend = FakeSteam('-original')
        def edited(expected, value):
            backend.value = '-user-concurrent'
            raise RuntimeError('concurrent edit')
        backend.exchange = edited
        with self.assertRaisesRegex(self.c.SafetyError, '回滚未确认'):
            self.engine.set_steam_options(backend)
        self.assertEqual(backend.value, '-user-concurrent')
        backend.exchange = FakeSteam.exchange.__get__(backend)
        backend.value = self.c.launch_option(self.game)
        self.assertEqual(self.engine.restore_steam_options(backend)['status'], 'restored')
        self.assertEqual(backend.value, '-original')


class AdditionalSteamTests(unittest.TestCase):
    setUp = test_core.CoreTests.setUp
    def test_backup_persistence_failure_prevents_first_ui_write(self):
        self.engine.install(self.prepared, self.launcher)
        backend = FakeSteam('-original')
        with patch('steam_options.atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.engine.set_steam_options(backend)
        self.assertEqual(backend.writes, [])
        self.assertEqual(backend.value, '-original')

    def test_helper_malformed_response_fails_closed_with_manual_fallback(self):
        from steam_ui import WindowsSteamUI
        for output in (b'[]', b'null', b'{"value": 123}', b'{"value":"x","extra":1}', b'not json'):
            with self.subTest(output=output):
                def runner(command, **kwargs):
                    return subprocess.CompletedProcess(command, 0, output, b'')
                with self.assertRaisesRegex(self.c.SafetyError, '手动'):
                    WindowsSteamUI(runner=runner).read()

    def test_restore_verification_failure_stops_uninstall_and_retains_backup(self):
        self.engine.install(self.prepared, self.launcher)
        backend = FakeSteam('-original')
        self.engine.set_steam_options(backend)
        applied = backend.value
        backend.exchange = lambda expected, value: value
        with self.assertRaisesRegex(self.c.SafetyError, '恢复未确认'):
            self.engine.uninstall(steam_backend=backend)
        self.assertEqual(backend.value, applied)
        self.assertTrue(self.engine.check()['healthy'])

    def test_pending_applied_retry_must_verify_save_not_just_current_edit_value(self):
        self.engine.install(self.prepared, self.launcher)
        backend = FakeSteam('-original')
        self.engine.set_steam_options(backend)
        path = self.engine.store / 'launch-options.json'
        state = json.loads(path.read_text())
        state['phase'] = 'pending'
        path.write_text(json.dumps(state), encoding='utf-8')
        backend.writes.clear()
        self.engine.set_steam_options(backend)
        self.assertEqual(backend.writes, [(state['applied'], state['applied'])])

    def test_native_fallback_targets_verified_game_popup_and_uses_relative_field_geometry(self):
        from steam_ui import Win32SteamUI, field_point, is_target_window
        self.assertTrue(is_target_window('战场兄弟 Battle Brothers', 'SDL_app', 1263, 903))
        self.assertFalse(is_target_window('Steam', 'SDL_app', 1263, 903))
        self.assertFalse(is_target_window('战场兄弟 Battle Brothers', 'Other', 1263, 903))
        x, y = field_point(1263, 903)
        self.assertGreater(x, 700)
        self.assertLess(x, 850)
        self.assertGreater(y, 650)
        self.assertLess(y, 720)

    def test_default_native_backend_falls_back_only_when_uia_proves_no_write(self):
        from steam_ui import AutoSteamUI, SteamUIUnavailable
        class NoWrite:
            def read(self):
                raise SteamUIUnavailable('未打开属性窗口、未写入')
        class Fallback:
            def read(self): return 'fallback'
        ui = AutoSteamUI(primary=NoWrite(), fallback=Fallback())
        self.assertEqual(ui.read(), 'fallback')

    def test_default_native_backend_does_not_fallback_after_write_verification_failure(self):
        from steam_ui import AutoSteamUI
        from installer_core import SafetyError
        class NoFallback:
            def read(self):
                return 'original'
            def exchange(self, expected, value):
                raise SafetyError('Steam 设置后读回不匹配')
        class Fallback:
            def exchange(self, expected, value):
                raise AssertionError('unsafe fallback')
        ui = AutoSteamUI(primary=NoFallback(), fallback=Fallback())
        with self.assertRaisesRegex(SafetyError, '读回不匹配'):
            ui.exchange('', 'value')

    def test_exchange_falls_back_only_after_primary_read_fails_before_any_write(self):
        from steam_ui import AutoSteamUI, SteamUIUnavailable
        class Primary:
            def read(self):
                raise SteamUIUnavailable('网页控件无障碍树不可用')
            def exchange(self, expected, value):
                raise AssertionError('primary exchange must not run')
        class Fallback:
            def exchange(self, expected, value):
                return value
        self.assertEqual(AutoSteamUI(primary=Primary(), fallback=Fallback()).exchange('', 'value'), 'value')

    def test_primary_exchange_failure_never_falls_back_after_primary_preflight_read(self):
        from steam_ui import AutoSteamUI
        from installer_core import SafetyError
        class Primary:
            def read(self):
                return 'original'
            def exchange(self, expected, value):
                raise SafetyError('自动粘贴后读回不匹配')
        class Fallback:
            def exchange(self, expected, value):
                raise AssertionError('unsafe fallback after primary exchange')
        with self.assertRaisesRegex(SafetyError, '读回不匹配'):
            AutoSteamUI(primary=Primary(), fallback=Fallback()).exchange('original', 'value')


if __name__ == '__main__':
    unittest.main()
