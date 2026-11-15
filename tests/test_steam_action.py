import contextlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

import bb_cn_installer as app


class SteamActionTests(unittest.TestCase):
    def test_explicit_restart_routes_backend_and_closes_session(self):
        engine = Mock()
        backend = object()
        events = []
        @contextlib.contextmanager
        def session(*, consent=False):
            self.assertTrue(consent)
            events.append('enter')
            try:
                yield backend
            finally:
                events.append('exit')
        fake = types.SimpleNamespace(debug_session=session)
        with patch.dict(sys.modules, {'steam_debug_session': fake}):
            self.assertTrue(hasattr(app, 'steam_operation'), 'Steam session integration missing')
            app.steam_operation(engine, 'set', restart=True)
        engine.set_steam_options.assert_called_once_with(backend=backend)
        self.assertEqual(events, ['enter', 'exit'])

    def test_failure_still_closes_session_without_second_backend_retry(self):
        engine = Mock()
        engine.restore_steam_options.side_effect = RuntimeError('write/readback failure')
        events = []
        @contextlib.contextmanager
        def session(*, consent=False):
            try:
                yield 'backend'
            finally:
                events.append('closed')
        with patch.dict(sys.modules, {'steam_debug_session': types.SimpleNamespace(debug_session=session)}):
            self.assertTrue(hasattr(app, 'steam_operation'), 'Steam session integration missing')
            with self.assertRaisesRegex(RuntimeError, 'write/readback'):
                app.steam_operation(engine, 'restore', restart=True)
        engine.restore_steam_options.assert_called_once_with(backend='backend')
        self.assertEqual(events, ['closed'])

    def test_cli_requires_yes_and_passes_explicit_restart(self):
        import io
        engine = Mock()
        engine.game = Path('C:/fake/Battle Brothers')
        with patch.object(app, 'Engine', return_value=engine), patch.object(app, 'steam_operation', return_value={'status': 'applied'}) as action, patch.object(sys, 'stdout', new=io.StringIO()):
            try:
                result = app.main(['steam-set', '--game-dir', str(engine.game), '--restart-steam'])
            except SystemExit:
                self.fail('Explicit restart CLI flag missing')
            self.assertEqual(result, 2)
            action.assert_not_called()
            self.assertEqual(app.main(['steam-set', '--game-dir', str(engine.game), '--restart-steam', '--yes']), 0)
            action.assert_called_once_with(engine, 'set', restart=True)

    def test_cdp_operation_does_not_claim_properties_page_was_reopened(self):
        engine = Mock()
        engine.set_steam_options.return_value = {'status': 'applied', 'note': '已通过 Steam 界面设置并重新打开读回确认；原值已备份。'}
        @contextlib.contextmanager
        def session(*, consent=False):
            yield 'backend'
        with patch.dict(sys.modules, {'steam_debug_session': types.SimpleNamespace(debug_session=session)}):
            result = app.steam_operation(engine, 'set', restart=True)
        self.assertNotIn('重新打开', result['note'])
        self.assertIn('原生', result['note'])

    def test_default_route_never_enters_restart_session(self):
        engine = Mock()
        session = Mock(side_effect=AssertionError('Must not restart without consent'))
        with patch.dict(sys.modules, {'steam_debug_session': types.SimpleNamespace(debug_session=session)}):
            self.assertTrue(hasattr(app, 'steam_operation'), 'Steam session integration missing')
            app.steam_operation(engine, 'uninstall')
        engine.uninstall.assert_called_once_with()
        session.assert_not_called()


if __name__ == '__main__':
    unittest.main()
