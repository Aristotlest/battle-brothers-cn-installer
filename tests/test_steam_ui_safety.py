"""Safety regressions; no live Steam, clipboard or keyboard access."""
import json
import subprocess
import unittest
from unittest.mock import patch
from installer_core import SafetyError
from steam_ui import AutoSteamUI, Win32SteamUI, WindowsSteamUI, SteamUIUnavailable


class SteamUISafetyTests(unittest.TestCase):
    def test_coordinate_backend_read_refuses_before_touching_desktop(self):
        ui = object.__new__(Win32SteamUI)
        ui._target = lambda *args: self.fail('unsafe desktop lookup')
        with self.assertRaisesRegex(SafetyError, '坐标'):
            ui.read()

    def test_coordinate_backend_exchange_refuses_even_matching_wrong_field(self):
        ui = object.__new__(Win32SteamUI)
        ui._target = lambda *args: self.fail('unsafe desktop lookup')
        with self.assertRaisesRegex(SafetyError, '坐标'):
            ui.exchange('expected', 'desired')

    def test_actual_helper_reopen_error_after_exchange_never_retries(self):
        calls = []
        def runner(*args, **kwargs):
            payload = json.loads(kwargs['input'])
            calls.append(payload['action'])
            if payload['action'] == 'read':
                return subprocess.CompletedProcess([], 0, b'{"value":"expected"}', b'')
            return subprocess.CompletedProcess([], 2, b'{"error":"Unable to locate verified AppID 365360 properties"}', b'')
        class Fallback:
            def exchange(self, *args):
                raise AssertionError('must not retry after possible write')
        with patch('steam_ui.os.name', 'nt'):
            with self.assertRaises(SafetyError):
                AutoSteamUI(primary=WindowsSteamUI(runner), fallback=Fallback()).exchange('expected', 'desired')
        self.assertEqual(calls, ['read', 'exchange'])

    def test_exchange_error_is_not_classified_as_proven_no_write(self):
        def runner(*args, **kwargs):
            return subprocess.CompletedProcess([], 2, b'{"error":"Unable to locate verified AppID 365360 properties"}', b'')
        with patch('steam_ui.os.name', 'nt'):
            try:
                WindowsSteamUI(runner).exchange('expected', 'desired')
            except SteamUIUnavailable:
                self.fail('exchange failure must not claim no write')
            except SafetyError:
                pass
            else:
                self.fail('helper failure accepted')


if __name__ == '__main__':
    unittest.main()
