"""Isolated orchestration tests. No live Steam commands or writes."""
import importlib
import sys
import unittest
from unittest.mock import Mock, patch


class ConsentTests(unittest.TestCase):
    def test_explicit_true_consent_is_required_before_platform_or_backend_access(self):
        import importlib.util
        spec = importlib.util.find_spec('steam_debug_session')
        self.assertIsNotNone(spec, 'debug-session orchestration is missing')
        session = importlib.import_module('steam_debug_session')
        from installer_core import SafetyError
        for value in (False, None, 1, 'yes'):
            with self.subTest(consent=value), patch.object(session, 'WindowsPlatform', side_effect=AssertionError('platform touched')):
                with self.assertRaises(SafetyError):
                    with session.debug_session(consent=value):
                        self.fail('must not yield')


class WindowsAdapterTests(unittest.TestCase):
    def test_snapshot_rejects_unusable_or_incomplete_introspection(self):
        import json
        import subprocess
        import steam_debug_session as session
        from installer_core import SafetyError
        cases = (b'invalid json', b'{}', b'{"processes":[],"listeners":[]}',
                 json.dumps({'processes': [{'pid': '10'}], 'listeners': []}).encode(),
                 json.dumps({'processes': [{'pid': 1, 'name': 'System', 'exe': None, 'command_line': None, 'parent_pid': 0}],
                             'listeners': [{'pid': 1, 'port': '8080', 'address': '127.0.0.1'}]}).encode())
        for payload in cases:
            with self.subTest(payload=payload), patch.object(session.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, payload, b'')):
                with self.assertRaises(SafetyError): session.WindowsPlatform().snapshot()
        with patch.object(session.subprocess, 'run', side_effect=subprocess.TimeoutExpired('powershell', 25)):
            with self.assertRaises(SafetyError): session.WindowsPlatform().snapshot()
        with patch.object(session.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, b'', b'denied')):
            with self.assertRaises(SafetyError): session.WindowsPlatform().snapshot()

    def test_snapshot_uses_embedded_powershell_json_without_external_packages(self):
        import base64
        import json
        import subprocess
        import steam_debug_session as session
        from installer_core import WindowsPlatform as CorePlatform
        self.assertTrue(issubclass(session.WindowsPlatform, CorePlatform))
        self.assertTrue(hasattr(session.WindowsPlatform, 'snapshot'), 'native snapshot adapter missing')
        data = {'processes': [{'pid': 10, 'name': 'steam.exe', 'exe': 'C:\\Steam\\steam.exe',
                              'command_line': '"C:\\Steam\\steam.exe"', 'parent_pid': 1}],
                'listeners': [{'pid': 11, 'address': '127.0.0.1', 'port': 8080}]}
        from pathlib import Path
        with patch.dict(session.os.environ, {'SystemRoot': 'C:/Trusted Windows'}), \
                patch.object(session.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(data).encode(), b'')) as run:
            self.assertEqual(session.WindowsPlatform().snapshot(), data)
        args = run.call_args.args[0]
        self.assertEqual(args[0], str(Path('C:/Trusted Windows') / 'System32/WindowsPowerShell/v1.0/powershell.exe'))
        self.assertTrue(Path(args[0]).is_absolute(), 'never resolve PowerShell via PATH or cwd')
        self.assertIn('-EncodedCommand', args)
        script = base64.b64decode(args[-1]).decode('utf-16-le')
        self.assertIn('Win32_Process', script)
        self.assertIn('Get-NetTCPConnection', script)
        self.assertNotIn('Stop-Process', script)
        self.assertGreater(run.call_args.kwargs['timeout'], 0)

    def test_snapshot_fills_in_missing_cim_paths_with_native_query(self):
        import json
        import subprocess
        import steam_debug_session as session
        data = {'processes': [{'pid': 88, 'name': 'csrss.exe', 'exe': None, 'command_line': None, 'parent_pid': 4}], 'listeners': []}
        with patch.object(session.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(data).encode(), b'')):
            with patch.object(session, '_process_image', create=True, return_value='C:/Windows/System32/csrss.exe') as query:
                result = session.WindowsPlatform().snapshot()
        query.assert_called_once_with(88)
        self.assertEqual(result['processes'][0]['exe'], 'C:/Windows/System32/csrss.exe')

    def test_native_launch_is_an_argument_array_not_shell_and_clock_is_available(self):
        import steam_debug_session as session
        self.assertTrue(hasattr(session.WindowsPlatform, 'start'), 'native launch adapter missing')
        platform = session.WindowsPlatform()
        with patch.object(session.subprocess, 'Popen') as spawn:
            platform.start(['C:/Steam/steam.exe', '-shutdown'])
        self.assertEqual(spawn.call_args.args[0], ['C:/Steam/steam.exe', '-shutdown'])
        self.assertIs(spawn.call_args.kwargs.get('shell', False), False)
        self.assertIsInstance(platform.monotonic(), float)
        with patch.object(session.time, 'sleep') as sleep:
            platform.sleep(0.5)
        sleep.assert_called_once_with(0.5)


class FakePlatform:
    def __init__(self, root):
        self.root = root
        (root / 'steamapps').mkdir()
        self.exe = root / 'steam.exe'
        self.exe.write_bytes(b'fixture, never executed')
        self.helper = root / 'bin/cef/steamwebhelper.exe'
        self.helper.parent.mkdir(parents=True)
        self.helper.write_bytes(b'fixture')
        self.state = 'normal'
        self.port = 8080
        self.calls = []
        self.clock = 0

    def debug_port(self):
        return self.port

    def steam(self):
        return self.root

    def snapshot(self):
        if self.state == 'down':
            return {'processes': [], 'listeners': []}
        command = '"' + str(self.exe) + '"'
        if self.state == 'debug':
            command += ' -dev -cef-enable-remote-debugging'
        return {'processes': [
            {'pid': 10, 'name': 'steam.exe', 'exe': str(self.exe), 'command_line': command, 'parent_pid': 1},
            {'pid': 11, 'name': 'steamwebhelper.exe', 'exe': str(self.helper), 'command_line': 'helper', 'parent_pid': 10}],
            'listeners': [{'pid': 11, 'address': '127.0.0.1', 'port': self.port}] if self.state == 'debug' else []}

    def start(self, args):
        self.calls.append(list(args))
        self.state = 'down' if '-shutdown' in args else ('debug' if '-cef-enable-debugging' in args else 'normal')

    def monotonic(self):
        return self.clock

    def sleep(self, seconds):
        self.clock += seconds


class SessionTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        import types
        from pathlib import Path
        import os
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR', 'C:/Users/Administrator/AppData/Local/hermes/cache/scratch'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.steam = self.root / 'Steam'
        self.steam.mkdir()
        self.platform = FakePlatform(self.steam)
        self.env = patch.dict('os.environ', {'LOCALAPPDATA': str(self.root / 'Local')})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.backend = types.SimpleNamespace(read=lambda: '')
        self.factory = patch.dict(sys.modules, {'steam_cdp': types.SimpleNamespace(CdpSteamUI=lambda **kw: self.backend)})
        self.factory.start()
        self.addCleanup(self.factory.stop)
        self.session = importlib.import_module('steam_debug_session')
        exited = patch.object(self.session, '_process_exited', return_value=False)
        exited.start()
        self.addCleanup(exited.stop)
        self.journal = self.root / 'Local/BB-CN-Installer/steam-debug-session.json'

    def test_preflight_blocks_unsafe_states_without_closing_steam(self):
        import json
        from installer_core import SafetyError
        original_snapshot = self.platform.snapshot
        cases = ('stale', 'extra_args', 'relative_command', 'missing_command', 'wrong_exe', 'missing_exe',
                 'marker', 'config', 'game', 'other_library_game', 'named_game', 'unknown_process',
                 'existing_listener', 'already_debug', 'missing_steam', 'multiple_steam')
        for case in cases:
            with self.subTest(case=case):
                snapshot = original_snapshot()
                proc = snapshot['processes'][0]
                undo = []
                if case == 'stale':
                    self.journal.parent.mkdir(parents=True, exist_ok=True)
                    self.journal.write_text('untrusted old session')
                    undo.append(self.journal)
                if case == 'extra_args': proc['command_line'] += ' -silent'
                if case == 'relative_command': proc['command_line'] = 'steam.exe'
                if case == 'missing_command': proc['command_line'] = None
                if case == 'wrong_exe': proc['exe'] = str(self.root / 'alien/steam.exe')
                if case == 'missing_exe':
                    self.platform.exe.rename(self.steam / 'hidden.exe')
                if case in ('marker', 'config'):
                    file = self.steam / ('.cef-enable-remote-debugging' if case == 'marker' else 'steam.cfg')
                    file.write_text('BootStrapperInhibitAll=enable')
                    undo.append(file)
                if case in ('game', 'other_library_game', 'named_game', 'unknown_process'):
                    game_exe = self.steam / 'steamapps/common/Another Game/play.exe'
                    if case == 'other_library_game':
                        lib = self.root / 'Other Library'
                        lib.mkdir(exist_ok=True)
                        folders = self.steam / 'steamapps/libraryfolders.vdf'
                        folders.write_text(json.dumps('libraryfolders') + ' { "1" { "path" ' + json.dumps(str(lib)) + ' } }')
                        undo.append(folders)
                        game_exe = lib / 'steamapps/common/G/play.exe'
                    snapshot['processes'].append({'pid': 20, 'name': 'BattleBrothers.exe' if case == 'named_game' else 'play.exe',
                                                  'exe': None if case == 'unknown_process' else str(game_exe),
                                                  'command_line': '', 'parent_pid': 1})
                if case == 'existing_listener': snapshot['listeners'] = [{'pid': 11, 'address': '127.0.0.1', 'port': 8080}]
                if case == 'already_debug': snapshot['processes'][1]['command_line'] += ' --remote-debugging-port=8080'
                if case == 'missing_steam': snapshot['processes'] = []
                if case == 'multiple_steam': snapshot['processes'].append(dict(proc, pid=30))
                try:
                    with patch.object(self.platform, 'snapshot', return_value=snapshot):
                        with self.assertRaises(SafetyError):
                            with self.session.debug_session(consent=True, platform=self.platform):
                                self.fail('unsafe preflight yielded')
                    self.assertEqual(self.platform.calls, [], 'preflight must never close Steam')
                    if case == 'stale': self.assertEqual(self.journal.read_text(), 'untrusted old session')
                    else: self.assertFalse(self.journal.exists())
                finally:
                    for file in undo: file.unlink(missing_ok=True)
                    if case == 'missing_exe': (self.steam / 'hidden.exe').rename(self.platform.exe)

    def test_preflight_accepts_native_equivalent_path_separators_and_session_zero(self):
        snap = self.platform.snapshot()
        snap['processes'][0]['command_line'] = '"' + self.platform.exe.as_posix() + '"'
        snap['processes'].append({'pid': 60, 'parent_pid': 4, 'name': 'csrss.exe',
                                  'exe': None, 'command_line': None, 'session_id': 0})
        with patch.object(self.platform, 'snapshot', return_value=snap):
            result = self.session._preflight(self.platform, self.steam, self.platform.exe)
        self.assertEqual(result['pid'], 10)
        snap['processes'][-1]['session_id'] = 1
        from installer_core import SafetyError
        with patch.object(self.platform, 'snapshot', return_value=snap), self.assertRaises(SafetyError):
            self.session._preflight(self.platform, self.steam, self.platform.exe)

    def test_debug_ready_waits_for_process_snapshot_to_catch_new_loopback_listener(self):
        self.platform.state = 'debug'
        snap = self.platform.snapshot()
        snap['listeners'][0]['pid'] = 99
        with patch.object(self.platform, 'snapshot', return_value=snap):
            self.assertFalse(self.session._debug_ready(self.platform, self.steam, self.backend))

    def test_owned_ignores_snapshot_process_confirmed_exited(self):
        snap = self.platform.snapshot()
        snap['processes'][1]['exe'] = None
        with patch.object(self.session, '_process_exited', create=True, return_value=True):
            result = self.session._owned(snap, self.steam)
        self.assertEqual([p['pid'] for p in result], [10])
        with patch.object(self.session, '_process_exited', create=True, return_value=False):
            from installer_core import SafetyError
            with self.assertRaises(SafetyError):
                self.session._owned(snap, self.steam)

    def test_shutdown_timeout_never_force_kills_or_starts_over_running_client(self):
        from installer_core import SafetyError
        def stuck(args):
            self.platform.calls.append(list(args))
        with patch.object(self.platform, 'start', side_effect=stuck):
            with self.assertRaisesRegex(SafetyError, 'restor|cleanup'):
                with self.session.debug_session(consent=True, platform=self.platform):
                    self.fail('shutdown did not complete')
        self.assertTrue(self.journal.exists())
        self.assertGreater(self.platform.clock, 0)
        self.assertTrue(all(args == [str(self.platform.exe), '-shutdown'] for args in self.platform.calls))

    def test_debug_read_retries_until_verified_and_rejects_non_string(self):
        from installer_core import SafetyError
        reads = []
        def delayed():
            reads.append(1)
            if len(reads) < 3: raise SafetyError('not ready')
            return ''
        self.backend.read = delayed
        with self.session.debug_session(consent=True, platform=self.platform):
            self.assertEqual(len(reads), 3)
        self.assertGreater(self.platform.clock, 0)
        self.backend.read = lambda: None
        with self.assertRaisesRegex(SafetyError, 'read|ready'):
            with self.session.debug_session(consent=True, platform=self.platform):
                self.fail('invalid read yielded')
        self.assertEqual(self.platform.state, 'normal')
        self.assertFalse(self.journal.exists())

    def test_new_debug_listeners_require_loopback_and_owned_helper(self):
        from installer_core import SafetyError
        original_snapshot = self.platform.snapshot
        for case in ('wildcard', 'foreign', 'no_listener', 'main_steam'):
            self.platform.calls.clear()
            with self.subTest(case=case):
                def snapshot():
                    data = original_snapshot()
                    if self.platform.state == 'debug':
                        if case == 'wildcard': data['listeners'][0]['address'] = '0.0.0.0'
                        if case == 'foreign': data['processes'][1]['exe'] = str(self.root / 'foreign/steamwebhelper.exe')
                        if case == 'no_listener': data['listeners'] = []
                        if case == 'main_steam': data['listeners'][0]['pid'] = 10
                    return data
                with patch.object(self.platform, 'snapshot', side_effect=snapshot):
                    with self.assertRaises(SafetyError):
                        with self.session.debug_session(consent=True, platform=self.platform):
                            self.fail('unverified listener yielded')
                self.assertEqual(self.platform.state, 'normal')
                self.assertFalse(self.journal.exists())

    def test_cleanup_retains_evidence_if_normal_restart_is_not_verified(self):
        from installer_core import SafetyError
        original_start = self.platform.start
        original_snapshot = self.platform.snapshot
        for case in ('restart_down', 'listener_remains', 'restart_debug', 'restart_raise'):
            self.platform.state = 'normal'
            self.platform.calls.clear()
            with self.subTest(case=case):
                def start(args):
                    original_start(args)
                    if len(args) == 1:
                        if case == 'restart_down': self.platform.state = 'down'
                        if case == 'restart_debug': self.platform.state = 'debug'
                        if case == 'restart_raise': raise OSError('restart failed')
                def snapshot():
                    data = original_snapshot()
                    if case == 'listener_remains' and self.platform.state == 'normal' and self.platform.calls:
                        data['listeners'] = [{'pid': 11, 'address': '127.0.0.1', 'port': 8080}]
                    return data
                with patch.object(self.platform, 'start', side_effect=start), patch.object(self.platform, 'snapshot', side_effect=snapshot):
                    with self.assertRaisesRegex(SafetyError, 'restor|cleanup'):
                        with self.session.debug_session(consent=True, platform=self.platform):
                            pass
                self.assertTrue(self.journal.exists())
                self.journal.unlink()

    def test_body_and_debug_start_failures_still_restore_without_hiding_error(self):
        original_start = self.platform.start
        with self.assertRaisesRegex(ValueError, 'body failure'):
            with self.session.debug_session(consent=True, platform=self.platform):
                raise ValueError('body failure')
        self.assertEqual(self.platform.state, 'normal')
        def start(args):
            original_start(args)
            if '-cef-enable-debugging' in args: raise OSError('debug startup failed after spawn')
        with patch.object(self.platform, 'start', side_effect=start):
            with self.assertRaisesRegex(OSError, 'debug startup'):
                with self.session.debug_session(consent=True, platform=self.platform):
                    self.fail('startup raised')
        self.assertEqual(self.platform.state, 'normal')
        self.assertFalse(self.journal.exists())

    def test_journal_reservation_does_not_overwrite_a_racing_session(self):
        from installer_core import SafetyError
        original_preflight = self.session._preflight
        def race(*args):
            result = original_preflight(*args)
            self.journal.parent.mkdir(parents=True, exist_ok=True)
            self.journal.write_text('racing evidence')
            return result
        with patch.object(self.session, '_preflight', side_effect=race):
            with self.assertRaisesRegex(SafetyError, 'session|journal'):
                with self.session.debug_session(consent=True, platform=self.platform):
                    self.fail('must not replace existing journal')
        self.assertEqual(self.journal.read_text(), 'racing evidence')
        self.assertEqual(self.platform.calls, [])

    def test_journal_write_failure_never_closes_steam(self):
        with patch.object(self.session, 'atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                with self.session.debug_session(consent=True, platform=self.platform):
                    self.fail('journal not durable')
        self.assertEqual(self.platform.calls, [])
        self.assertTrue(self.journal.exists(), 'retain reservation evidence on failed persistence')

    def test_debug_cleanup_refuses_to_ignore_unidentified_helper_or_lingering_endpoint(self):
        from installer_core import SafetyError
        original_snapshot = self.platform.snapshot
        original_start = self.platform.start
        for case in ('unidentified_helper', 'foreign_listener'):
            self.platform.calls.clear()
            self.platform.state = 'normal'
            with self.subTest(case=case):
                def snapshot():
                    data = original_snapshot()
                    if len(self.platform.calls) >= 3:
                        if case == 'unidentified_helper':
                            data['processes'].append({'pid': 99, 'name': 'steamwebhelper.exe', 'exe': None,
                                                      'command_line': None, 'parent_pid': 10})
                        if case == 'foreign_listener':
                            data['listeners'].append({'pid': 99, 'address': '127.0.0.1', 'port': 8080})
                    return data
                with patch.object(self.platform, 'snapshot', side_effect=snapshot):
                    with self.assertRaisesRegex(SafetyError, 'restor|cleanup'):
                        with self.session.debug_session(consent=True, platform=self.platform):
                            pass
                self.assertTrue(self.journal.exists())
                if case == 'unidentified_helper':
                    self.assertFalse(any(len(args) == 1 for args in self.platform.calls))
                self.journal.unlink()

    def test_preexisting_foreign_debug_port_blocks_before_shutdown(self):
        from installer_core import SafetyError
        snapshot = self.platform.snapshot()
        snapshot['listeners'] = [{'pid': 999, 'address': '127.0.0.1', 'port': 8080}]
        with patch.object(self.platform, 'snapshot', return_value=snapshot):
            with self.assertRaises(SafetyError):
                with self.session.debug_session(consent=True, platform=self.platform):
                    self.fail('do not close Steam when port is occupied')
        self.assertEqual(self.platform.calls, [])
        self.assertFalse(self.journal.exists())

    def test_graceful_shutdown_waits_for_helpers_before_debug_spawn(self):
        original_snapshot = self.platform.snapshot
        original_start = self.platform.start
        waiting = [2]
        def snapshot():
            if self.platform.state == 'down' and len(self.platform.calls) == 1 and waiting[0]:
                waiting[0] -= 1
                return {'processes': [{'pid': 11, 'name': 'steamwebhelper.exe', 'exe': str(self.platform.helper),
                                        'command_line': 'helper', 'parent_pid': 10}], 'listeners': []}
            return original_snapshot()
        def start(args):
            if '-cef-enable-debugging' in args:
                self.assertEqual(waiting[0], 0, 'must wait for lingering helper')
                self.assertGreater(self.platform.clock, 0)
            original_start(args)
        with patch.object(self.platform, 'snapshot', side_effect=snapshot), patch.object(self.platform, 'start', side_effect=start):
            with self.session.debug_session(consent=True, platform=self.platform):
                pass
        self.assertFalse(self.journal.exists())

    def test_preexisting_native_debug_switch_blocks_before_shutdown(self):
        from installer_core import SafetyError
        snapshot = self.platform.snapshot()
        snapshot['processes'][1]['command_line'] += ' -cef-enable-debugging'
        with patch.object(self.platform, 'snapshot', return_value=snapshot):
            with self.assertRaises(SafetyError):
                with self.session.debug_session(consent=True, platform=self.platform):
                    self.fail('pre-existing debugging must not be restarted')
        self.assertEqual(self.platform.calls, [])

    def test_verified_session_uses_probed_port_for_launch_backend_and_journal(self):
        import json
        self.platform.debug_port = lambda: 12691
        self.platform.port = 12691
        with patch.dict(sys.modules, {'steam_cdp': type('Module', (), {'CdpSteamUI': Mock(return_value=self.backend)})}) as modules:
            with self.session.debug_session(consent=True, platform=self.platform):
                record = json.loads(self.journal.read_text(encoding='utf-8'))
                self.assertEqual(record['debug_port'], 12691)
                self.assertEqual(record['debug_argv'], [str(self.platform.exe), '-cef-enable-debugging', '-devtools-port', '12691'])
            modules['steam_cdp'].CdpSteamUI.assert_called_once_with(enabled=True, port=12691)
        self.assertEqual(self.platform.state, 'normal')
        self.assertFalse(self.journal.exists())

    def test_verified_session_yields_backend_and_restores_normal_steam(self):
        import json
        with self.session.debug_session(consent=True, platform=self.platform) as backend:
            self.assertIs(backend, self.backend)
            record = json.loads(self.journal.read_text(encoding='utf-8'))
            self.assertEqual(record['original_command_line'], '"' + str(self.platform.exe) + '"')
            self.assertEqual(self.platform.state, 'debug')
        self.assertFalse(self.journal.exists())
        self.assertEqual(self.platform.calls, [
            [str(self.platform.exe), '-shutdown'],
            [str(self.platform.exe), '-cef-enable-debugging', '-devtools-port', '8080'],
            [str(self.platform.exe), '-shutdown'], [str(self.platform.exe)]])
        self.assertEqual(self.platform.state, 'normal')


if __name__ == '__main__':
    unittest.main()
