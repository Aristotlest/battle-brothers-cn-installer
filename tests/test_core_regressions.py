"""Safety regressions; every fixture is outside the live Steam library."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import test_core


class SafetyRegressions(unittest.TestCase):
    setUp = test_core.CoreTests.setUp

    def snapshot(self):
        return {str(p.relative_to(self.game)): p.read_bytes()
                for p in self.game.rglob('*') if p.is_file()}

    def test_recovery_rejects_null_before_with_nonnull_hash_before_any_write(self):
        self.engine.store.mkdir()
        rows = []
        for rel in self.c.TARGETS:
            target = self.game / rel
            target.write_bytes(b'foreign file')
            rows.append({'path': rel, 'before': None,
                         'before_sha256': self.c.sha256(target),
                         'after_sha256': self.c.sha256(target)})
        (self.engine.store / 'journal.json').write_text(json.dumps({'rows': rows, 'oldstate': None}))
        before = self.snapshot()
        with self.assertRaises(self.c.SafetyError):
            self.engine.recover()
        self.assertEqual(before, self.snapshot())

    def test_recovery_requires_complete_typed_schema_and_valid_digests(self):
        self.engine.store.mkdir()
        valid = {'rows': [{'path': rel, 'before': None, 'before_sha256': None,
                           'after_sha256': 'a' * 64} for rel in self.c.TARGETS],
                 'oldstate': None}
        cases = []
        for invalid in ('invalid', '', 7, False, [], {}):
            value = json.loads(json.dumps(valid))
            value['rows'][1]['after_sha256'] = invalid
            cases.append(value)
        value = json.loads(json.dumps(valid))
        value['rows'][1].pop('after_sha256')
        cases.append(value)
        value = json.loads(json.dumps(valid))
        value['rows'][1]['extra'] = 'unexpected'
        cases.append(value)
        cases.extend(([], {'rows': valid['rows']},
                      dict(valid, extra=True), dict(valid, rows={'0': {}, '1': {}})))
        for value in cases:
            with self.subTest(value=value):
                (self.engine.store / 'journal.json').write_text(json.dumps(value))
                before = self.snapshot()
                with self.assertRaises(self.c.SafetyError):
                    self.engine.recover()
                self.assertEqual(before, self.snapshot())

    def test_recovery_requires_rows_match_old_owned_state(self):
        self.engine.install(self.prepared, self.launcher)
        state = self.engine._state()
        rows = []
        for i, rel in enumerate(self.c.TARGETS):
            target = self.game / rel
            target.write_bytes(b'foreign replacement')
            undo = self.engine.store / f'undo-{i}'
            undo.write_bytes(target.read_bytes())
            rows.append({'path': rel, 'before': undo.name,
                         'before_sha256': self.c.sha256(undo),
                         'after_sha256': self.c.sha256(target)})
        (self.engine.store / 'journal.json').write_text(json.dumps({'rows': rows, 'oldstate': state}))
        before = self.snapshot()
        with self.assertRaises(self.c.SafetyError):
            self.engine.recover()
        self.assertEqual(before, self.snapshot())

    def test_uninstall_after_game_update_restores_backups_but_install_stays_gated(self):
        for rel in self.c.TARGETS:
            (self.game / rel).write_bytes(b'previous user file')
        self.engine.install(self.prepared, self.launcher, replace=True)
        self.engine.platform.version = lambda path: '1.5.2.4'
        before = self.snapshot()
        with self.assertRaises(self.c.SafetyError):
            self.engine.install(self.prepared, self.launcher, replace=True)
        self.assertEqual(before, self.snapshot())
        self.engine.uninstall()
        for rel in self.c.TARGETS:
            self.assertEqual((self.game / rel).read_bytes(), b'previous user file')

    def test_updated_game_can_resume_interrupted_rollback_without_losing_undo(self):
        self.engine.install(self.prepared, self.launcher)
        original = [(self.game / rel).read_bytes() for rel in self.c.TARGETS]
        # Make the interrupted update genuinely different from the old install.
        import zipfile
        with zipfile.ZipFile(self.pkg, 'w') as z:
            z.writestr('scripts/updated.nut', 'updated translation')
        self.prepared = self.c.Prepared(self.pkg, self.c.sha256(self.pkg), [])
        import os
        real_replace = os.replace
        def interrupt_second(src, dst):
            if Path(src).name == 'new-1':
                raise KeyboardInterrupt('simulated interruption')
            return real_replace(src, dst)
        with patch.object(os, 'replace', side_effect=interrupt_second):
            with self.assertRaises(KeyboardInterrupt):
                self.engine.install(self.prepared, self.launcher)
        self.engine.platform.version = lambda path: '1.5.2.4'
        def interrupt_restore(src, dst):
            if Path(src).name == 'restore-1':
                raise KeyboardInterrupt('rollback interrupted')
            return real_replace(src, dst)
        with patch.object(os, 'replace', side_effect=interrupt_restore):
            with self.assertRaises(KeyboardInterrupt):
                self.engine.recover()
        self.assertTrue((self.engine.store / 'journal.json').is_file())
        self.assertTrue((self.engine.store / 'undo-0').is_file())
        self.engine.recover()
        self.assertFalse((self.engine.store / 'journal.json').exists())
        self.assertTrue(self.engine.check()['healthy'])
        self.assertEqual(original, [(self.game / rel).read_bytes() for rel in self.c.TARGETS])

    def test_diagnose_cli_returns_failure_for_installed_corrupt_files(self):
        import contextlib
        import io
        import bb_cn_installer as app
        self.engine.install(self.prepared, self.launcher)
        (self.game / self.c.TARGETS[0]).write_bytes(b'corrupt')
        before = self.snapshot()
        output = io.StringIO()
        with patch.object(app, 'Engine', return_value=self.engine), contextlib.redirect_stdout(output):
            code = app.main(['--diagnose', '--game-dir', str(self.game)])
        self.assertFalse(json.loads(output.getvalue())['healthy'])
        self.assertEqual(code, 2)
        self.assertEqual(before, self.snapshot())

    def test_real_pinned_text_archive_extracts_on_ordinary_long_windows_path(self):
        archive = Path.home() / 'Downloads/BB-CN-build-deps/translation-source.zip'
        self.assertTrue(archive.is_file(), 'real pinned archive required for this regression')
        self.assertEqual(self.c.sha256(archive), self.c.PINS['texts']['sha256'])
        destination = self.root / ('ordinary-cache-' + 'x' * 30) / 'texts'
        import zipfile
        with zipfile.ZipFile(archive) as z:
            longest = max(z.infolist(), key=lambda i: len(i.filename))
            self.assertGreater(len(str(destination / longest.filename)), 260)
            expected = z.read(longest)
        import shutil
        self.addCleanup(lambda: shutil.rmtree(self.c.filesystem_path(destination), ignore_errors=True))
        self.c.safe_extract(archive, destination)
        # Use an explicit prefix only in the assertion: the caller passed a normal path.
        self.assertEqual(Path('\\\\?\\' + str(destination / longest.filename)).read_bytes(), expected)

    def test_real_native_prepare_handles_long_staging_with_pinned_dependencies(self):
        import os
        import shutil
        fixture = os.environ.get('BBCN_REAL_GAME_FIXTURE')
        if not fixture:
            self.skipTest('set BBCN_REAL_GAME_FIXTURE to an isolated, non-live .dat fixture')
        fixture = Path(fixture).absolute()
        fixture.relative_to(test_core.SCRATCH.absolute())
        deps = Path.home() / 'Downloads/BB-CN-build-deps'
        launcher = Path.home() / 'Downloads/BattleBrothers-CN-install-prep/launcher/bb-launcher-steam.exe'
        self.pin.stop()
        for source in (fixture / 'data').glob('*.dat'):
            shutil.copyfile(source, self.game / 'data' / source.name)
        cache = self.root / ('ordinary-cache-' + 'x' * 30)
        cache.mkdir()
        self.addCleanup(lambda: shutil.rmtree(self.c.filesystem_path(cache), ignore_errors=True))
        for name in ('translation-source.zip', 'bb-translator-windows.zip'):
            shutil.copyfile(deps / name, cache / name)
        before = {p.name: self.c.sha256(p) for p in (self.game / 'data').glob('*.dat')}
        prepared = self.c.prepare(self.engine, launcher, cache)
        self.assertEqual(self.c.sha256(prepared.path), prepared.digest)
        self.assertGreater(prepared.path.stat().st_size, 1_000_000)
        self.assertEqual(before, {p.name: self.c.sha256(p) for p in (self.game / 'data').glob('*.dat')})
        self.assertFalse((self.game / self.c.TARGETS[0]).exists())
        import zipfile
        with zipfile.ZipFile(prepared.path) as z:
            self.assertGreater(len(z.infolist()), 2000)
            self.assertIsNone(z.testzip())
        self.assertIn('onDeath', '\n'.join(prepared.warnings))

    def test_state_requires_complete_schema_and_backup_hash_correspondence(self):
        self.engine.install(self.prepared, self.launcher)
        valid = self.engine._state()
        cases = []
        value = json.loads(json.dumps(valid))
        value['entries'][1]['backup_sha256'] = 'a' * 64
        cases.append(value)
        value = json.loads(json.dumps(valid))
        value['entries'][1].pop('backup_sha256')
        cases.append(value)
        value = json.loads(json.dumps(valid))
        value['entries'][1]['extra'] = True
        cases.append(value)
        cases.append(dict(valid, extra=True))
        for value in cases:
            with self.subTest(value=value):
                (self.game / self.c.TARGETS[0]).write_bytes(self.pkg.read_bytes())
                (self.game / self.c.TARGETS[1]).write_bytes(self.launcher.read_bytes())
                (self.engine.store / 'state.json').write_text(json.dumps(value))
                before = self.snapshot()
                with self.assertRaises(self.c.SafetyError):
                    self.engine.uninstall()
                self.assertEqual(before, self.snapshot())

    def test_updated_game_cleanup_still_refuses_modified_files_backups_process_and_manifest(self):
        target = self.game / self.c.TARGETS[0]
        target.write_bytes(b'original user localization')
        self.engine.install(self.prepared, self.launcher, replace=True)
        self.engine.platform.version = lambda path: '1.5.2.4'
        installed = target.read_bytes()
        backup = self.engine.store / 'backup-0'
        manifest = self.game.parent.parent / 'appmanifest_365360.acf'
        good_manifest = manifest.read_bytes()
        for defect in ('owned-file', 'backup', 'running', 'manifest'):
            with self.subTest(defect=defect):
                target.write_bytes(installed)
                backup.write_bytes(b'original user localization')
                self.engine.platform.running = lambda: False
                manifest.write_bytes(good_manifest)
                if defect == 'owned-file': target.write_bytes(b'foreign edit')
                elif defect == 'backup': backup.write_bytes(b'changed backup')
                elif defect == 'running': self.engine.platform.running = lambda: True
                else: manifest.write_text('"AppState" { "appid" "999" }')
                before = self.snapshot()
                with self.assertRaises(self.c.SafetyError):
                    self.engine.uninstall()
                self.assertEqual(before, self.snapshot())

    def test_diagnose_cli_status_matrix_is_read_only(self):
        import contextlib
        import io
        import bb_cn_installer as app
        def diagnose(expected):
            before = self.snapshot()
            output = io.StringIO()
            with patch.object(app, 'Engine', return_value=self.engine), contextlib.redirect_stdout(output):
                self.assertEqual(app.main(['--diagnose', '--game-dir', str(self.game)]), expected)
            self.assertEqual(before, self.snapshot())
            return json.loads(output.getvalue())
        self.assertFalse(diagnose(0)['installed'])
        self.engine.install(self.prepared, self.launcher)
        self.assertTrue(diagnose(0)['healthy'])
        state = self.engine.store / 'state.json'
        good_state = state.read_bytes()
        for invalid in ('not json', '[]', '{"entries": []}'):
            state.write_text(invalid)
            self.assertIn('error', diagnose(2))
        state.write_bytes(good_state)
        pending = self.engine.store / 'journal.json'
        pending.write_text('{}')
        self.assertTrue(diagnose(2)['pending_recovery'])
        pending.unlink()
        self.engine.platform.version = lambda path: '1.5.2.4'
        self.assertFalse(diagnose(2)['supported'])


if __name__ == '__main__':
    unittest.main()
