import hashlib
import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SCRATCH = Path(os.environ.get('BBCN_TEST_SCRATCH', str(Path.home() / 'AppData/Local/hermes/cache/scratch')))
SCRATCH.mkdir(parents=True, exist_ok=True)
os.environ['TMPDIR'] = str(SCRATCH)
tempfile.tempdir = str(SCRATCH)

class FakePlatform:
    def version(self, path): return '1.5.2.3'
    def running(self): return False

class CoreTests(unittest.TestCase):
    def setUp(self):
        try:
            self.c = importlib.import_module('installer_core')
        except ModuleNotFoundError:
            self.fail('installer_core install/check/uninstall behavior is not implemented')
        self.tmp = tempfile.TemporaryDirectory(prefix='bbcn-test-', dir=SCRATCH)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.game = self.root / 'steamapps/common/Battle Brothers'
        (self.game / 'win32').mkdir(parents=True)
        (self.game / 'data').mkdir()
        (self.game / 'win32/BattleBrothers.exe').write_bytes(b'original-game')
        (self.game / 'win32/steam_api.dll').write_bytes(b'original-api')
        (self.game / 'data/data_001.dat').write_bytes(b'original-data')
        (self.game.parent.parent / 'appmanifest_365360.acf').write_text('"AppState" { "appid" "365360" "installdir" "Battle Brothers" }')
        self.launcher = self.root / 'bb-launcher-steam.exe'
        self.launcher.write_bytes(b'test-only-launcher')
        self.pkg = self.root / 'zdata_cn.zip'
        with zipfile.ZipFile(self.pkg, 'w') as z: z.writestr('scripts/test.nut', 'translated fixture')
        self.pin = patch.dict(self.c.PINS['launcher'], {'sha256': hashlib.sha256(self.launcher.read_bytes()).hexdigest()})
        self.pin.start()
        self.addCleanup(self.pin.stop)
        self.engine = self.c.Engine(self.game, platform=FakePlatform())
        self.prepared = self.c.Prepared(self.pkg, self.c.sha256(self.pkg), [])

    def test_install_check_uninstall_preserves_game_and_saves(self):
        save = self.game / 'savegames/sample.sav'
        save.parent.mkdir()
        save.write_bytes(b'never change saves')
        self.engine.install(self.prepared, self.launcher)
        self.assertTrue(self.engine.check()['healthy'])
        self.assertEqual((self.game / 'data/zdata_cn.zip').read_bytes(), self.pkg.read_bytes())
        self.engine.uninstall()
        self.assertFalse((self.game / 'data/zdata_cn.zip').exists())
        self.assertFalse((self.game / 'win32/bb-launcher-steam.exe').exists())
        self.assertEqual(save.read_bytes(), b'never change saves')
        self.assertEqual((self.game / 'win32/BattleBrothers.exe').read_bytes(), b'original-game')
        self.assertEqual((self.game / 'win32/steam_api.dll').read_bytes(), b'original-api')

    def test_preflight_refuses_unverified_inputs_before_writes(self):
        def refused():
            with self.assertRaises(self.c.SafetyError): self.engine.install(self.prepared, self.launcher)
            self.assertFalse((self.game / 'data/zdata_cn.zip').exists())
        self.launcher.write_bytes(b'wrong launcher')
        refused()
        self.launcher.write_bytes(b'test-only-launcher')
        self.pkg.write_bytes(b'not a ZIP')
        self.prepared = self.c.Prepared(self.pkg, self.c.sha256(self.pkg), [])
        refused()
        with zipfile.ZipFile(self.pkg, 'w') as z: z.writestr('scripts/test.nut', 'fixture')
        self.prepared = self.c.Prepared(self.pkg, self.c.sha256(self.pkg), ['modify failed: player onDeath'])
        refused()
        self.prepared = self.c.Prepared(self.pkg, '0' * 64, [])
        refused()
        self.prepared = self.c.Prepared(self.pkg, self.c.sha256(self.pkg), [])
        (self.game / 'win32/bb-launcher-steam.exe').write_bytes(b'unknown existing')
        refused()
        (self.game / 'win32/bb-launcher-steam.exe').unlink()
        self.engine.platform.version = lambda path: '1.5.2.4'
        refused()
        self.engine.platform.version = lambda path: '1.5.2.3'
        self.engine.platform.running = lambda: True
        refused()
        self.engine.platform.running = lambda: False
        (self.game.parent.parent / 'appmanifest_365360.acf').write_text('"AppState" { "appid" "999" "installdir" "Battle Brothers" }')
        refused()

    def test_backup_restore_refuses_modified_owned_files(self):
        old = self.game / 'data/zdata_cn.zip'
        old.write_bytes(b'old user localization')
        self.engine.install(self.prepared, self.launcher, replace=True)
        installed = old.read_bytes()
        old.write_bytes(b'user edited file')
        self.assertFalse(self.engine.check()['healthy'])
        with self.assertRaises(self.c.SafetyError): self.engine.uninstall()
        with self.assertRaises(self.c.SafetyError): self.engine.install(self.prepared, self.launcher, replace=True)
        self.assertEqual(old.read_bytes(), b'user edited file')
        old.write_bytes(installed)
        self.engine.install(self.prepared, self.launcher, replace=True)
        self.engine.uninstall()
        self.assertEqual(old.read_bytes(), b'old user localization')
        self.assertFalse((self.game / 'win32/bb-launcher-steam.exe').exists())

    def test_failed_second_write_rolls_back_and_restart_recovers_journal(self):
        original = self.game / 'data/zdata_cn.zip'
        original.write_bytes(b'prior data')
        real_replace = os.replace
        def fail_second(src, dst):
            if Path(dst) == self.game / self.c.TARGETS[1] and Path(src).name.startswith('new-'):
                raise OSError('simulated disk failure')
            return real_replace(src, dst)
        with patch.object(os, 'replace', side_effect=fail_second):
            with self.assertRaises(OSError): self.engine.install(self.prepared, self.launcher, replace=True)
        self.assertEqual(original.read_bytes(), b'prior data')
        self.assertFalse((self.game / self.c.TARGETS[1]).exists())
        self.assertFalse((self.engine.store / 'state.json').exists())
        def interrupt(src, dst):
            if Path(dst) == self.game / self.c.TARGETS[1] and Path(src).name.startswith('new-'):
                raise KeyboardInterrupt('power loss simulation')
            return real_replace(src, dst)
        with patch.object(os, 'replace', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt): self.engine.install(self.prepared, self.launcher, replace=True)
        self.assertTrue((self.engine.store / 'journal.json').exists())
        self.engine.recover()
        self.assertEqual(original.read_bytes(), b'prior data')
        self.assertFalse((self.game / self.c.TARGETS[1]).exists())
        self.assertFalse((self.engine.store / 'journal.json').exists())

    def test_untrusted_state_cannot_delete_foreign_files_or_restore_tampered_backup(self):
        owned = self.game / self.c.TARGETS[0]
        owned.write_bytes(b'prior')
        self.engine.install(self.prepared, self.launcher, replace=True)
        statefile = self.engine.store / 'state.json'
        good = statefile.read_text()
        victim = self.game / 'savegames.sav'
        victim.write_bytes(b'foreign')
        state = json.loads(good)
        state['entries'][0]['path'] = '../savegames.sav'
        statefile.write_text(json.dumps(state))
        with self.assertRaises(self.c.SafetyError): self.engine.uninstall()
        state = json.loads(good)
        state['entries'][0]['backup'] = '../savegames.sav'
        statefile.write_text(json.dumps(state))
        with self.assertRaises(self.c.SafetyError): self.engine.uninstall()
        statefile.write_text(good)
        (self.engine.store / 'backup-0').write_bytes(b'tampered backup')
        with self.assertRaises(self.c.SafetyError): self.engine.uninstall()
        self.assertEqual(victim.read_bytes(), b'foreign')
        self.assertEqual(owned.read_bytes(), self.pkg.read_bytes())

    def test_reparse_points_and_malicious_journal_refused(self):
        foreign = self.root / 'foreign'
        foreign.mkdir()
        # Windows junctions do not require developer mode / symlink privilege.
        (self.game / 'data').rename(self.game / 'real-data')
        import subprocess
        made = subprocess.run(['cmd', '/c', 'mklink', '/J', str(self.game / 'data'), str(foreign)], capture_output=True)
        self.assertEqual(made.returncode, 0, made.stdout)
        try:
            with self.assertRaises(self.c.SafetyError): self.engine.install(self.prepared, self.launcher)
            self.assertEqual(list(foreign.iterdir()), [])
        finally:
            os.rmdir(self.game / 'data')
            (self.game / 'real-data').rename(self.game / 'data')
        self.engine.store.mkdir(exist_ok=True)
        victim = self.game / 'save.sav'
        victim.write_bytes(b'foreign')
        (self.engine.store / 'journal.json').write_text(json.dumps({'rows': [{'path': 'save.sav', 'before': None}], 'oldstate': None}))
        with self.assertRaises(self.c.SafetyError): self.engine.recover()
        self.assertEqual(victim.read_bytes(), b'foreign')

    def test_safe_extract_rejects_traversal_links_collisions_and_bombs(self):
        extract = getattr(self.c, 'safe_extract', None)
        self.assertIsNotNone(extract, 'safe archive extraction missing')
        archive = self.root / 'archive.zip'
        out = self.root / 'extract'
        for name in ('../escape', '/absolute', 'C:/escape', 'a\\\\..\\\\escape', 'file:ads', 'CON', 'a/../../escape'):
            with self.subTest(name=name):
                with zipfile.ZipFile(archive, 'w') as z: z.writestr(name, 'bad')
                with self.assertRaises(self.c.SafetyError): extract(archive, out)
                self.assertFalse(out.exists())
        with zipfile.ZipFile(archive, 'w') as z:
            link = zipfile.ZipInfo('link')
            link.create_system = 3
            link.external_attr = 0o120777 << 16
            z.writestr(link, '../outside')
        with self.assertRaises(self.c.SafetyError): extract(archive, out)
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('A/file', 'x')
            z.writestr('a/FILE', 'y')
        with self.assertRaises(self.c.SafetyError): extract(archive, out)
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('folder', 'file where directory expected')
            z.writestr('folder/file', 'collision')
        with self.assertRaises(self.c.SafetyError): extract(archive, out)
        self.assertFalse(out.exists())
        with zipfile.ZipFile(archive, 'w') as z: z.writestr('folder/file', 'content')
        with self.assertRaises(self.c.SafetyError): extract(archive, out, max_bytes=1)
        extract(archive, out)
        self.assertEqual((out / 'folder/file').read_text(), 'content')

    def test_vdf_discovery_supports_escaped_multi_library_and_old_format(self):
        parse = getattr(self.c, 'parse_vdf', None)
        self.assertIsNotNone(parse, 'VDF parser missing')
        lib = self.root / 'Library with space'
        target = lib / 'steamapps/common/Battle Brothers'
        target.mkdir(parents=True)
        (lib / 'steamapps/appmanifest_365360.acf').write_text('"AppState" { "appid" "365360" "installdir" "Battle Brothers" }')
        steam = self.root / 'Steam'
        (steam / 'steamapps').mkdir(parents=True)
        escaped = str(lib).replace('\\', '\\\\')
        vdf = '"libraryfolders" { "0" { "path" "C:\\\\Steam" "apps" {} } "1" { "path" "' + escaped + '" } } // comment'
        (steam / 'steamapps/libraryfolders.vdf').write_text(vdf)
        self.assertEqual(parse(vdf)['libraryfolders']['1']['path'], str(lib))
        self.assertEqual(self.c.discover_games(steam), [target])
        (steam / 'steamapps/libraryfolders.vdf').write_text('"LibraryFolders" { "1" "' + escaped + '" }')
        self.assertEqual(self.c.discover_games(steam), [target])
        with self.assertRaises(self.c.SafetyError): parse('"bad" { "unterminated"')
        self.assertEqual(self.c.launch_option(self.game), '"' + str(self.game / 'win32/bb-launcher-steam.exe') + '" %command%')

    def test_prepare_stages_dat_and_json_captures_zero_exit_warnings(self):
        prepare = getattr(self.c, 'prepare', None)
        self.assertIsNotNone(prepare, 'isolated preparation missing')
        cache = self.root / 'cache'
        cache.mkdir()
        tz = cache / 'bb-translator-windows.zip'
        texts = cache / 'translation-source.zip'
        with zipfile.ZipFile(tz, 'w') as z:
            z.writestr('Windows/Release/bb_translator.exe', 'fixture')
            z.writestr('Windows/Release/python312.dll', 'fixture')
            z.writestr('Windows/assets/fonts/example.ttf', 'font fixture')
        with zipfile.ZipFile(texts, 'w') as z:
            z.writestr('shabbywu-commit/zh_CN.UTF-8/json/scripts/test.nut', '{"a": "b"}')
        (self.game / 'data/other_mod.zip').write_bytes(b'not copied')
        live_before = (self.game / 'data/data_001.dat').read_bytes()
        def translator(cmd, cwd, **kwargs):
            stage = Path(cmd[cmd.index('--game-dir') + 1])
            self.assertNotEqual(stage, self.game)
            self.assertEqual((stage / 'data/data_001.dat').read_bytes(), live_before)
            self.assertFalse((stage / 'data/other_mod.zip').exists())
            self.assertTrue((stage / 'Battle-Brothers-CN/zh_CN.UTF-8/json/scripts/test.nut').is_file())
            with zipfile.ZipFile(stage / 'data/zdata_cn.zip', 'w') as z: z.writestr('scripts/test.nut', 'Chinese fixture')
            import subprocess
            return subprocess.CompletedProcess(cmd, 0, b'', b'modify failed: player onDeath\n')
        with patch.dict(self.c.PINS['translator'], {'sha256': self.c.sha256(tz)}), patch.dict(self.c.PINS['texts'], {'sha256': self.c.sha256(texts)}), patch.object(self.c, 'repair_obituary_package', return_value={'status': 'patched', 'function': 'getObituaryInfo', 'changed_instructions': 1}) as repair:
            result = prepare(self.engine, self.launcher, cache, runner=translator)
        repair.assert_called_once()
        self.assertEqual(json.loads((result.path.parent / 'obituary-repair.json').read_text(encoding='utf-8'))['function'], 'getObituaryInfo')
        self.assertIn('阵亡记录修复已校验', '\n'.join(result.warnings))
        self.assertTrue(result.path.is_file())
        self.assertEqual(result.digest, self.c.sha256(result.path))
        self.assertIn('player onDeath', '\n'.join(result.warnings))
        self.assertEqual((self.game / 'data/data_001.dat').read_bytes(), live_before)
        self.assertFalse((self.game / 'data/zdata_cn.zip').exists())
        self.assertTrue((result.path.parent / 'translator.log').is_file())
        with self.assertRaises(self.c.SafetyError): self.engine.install(result, self.launcher)
        self.engine.install(result, self.launcher, accept_warnings=True)
        self.assertTrue(self.engine.check()['healthy'])

    def test_native_windows_version_process_and_diagnose_are_read_only(self):
        native = getattr(self.c, 'WindowsPlatform', None)
        self.assertIsNotNone(native, 'native Windows validation missing')
        import sys
        platform = native()
        self.assertRegex(platform.version(Path(sys.executable)), r'^\d+\.\d+\.\d+\.\d+$')
        self.assertIsInstance(platform.running(), bool)
        with self.assertRaises(self.c.SafetyError): platform.version(self.game / 'win32/BattleBrothers.exe')
        before = {str(p.relative_to(self.game)): p.read_bytes() for p in self.game.rglob('*') if p.is_file()}
        diagnostic = self.engine.diagnose()
        self.assertTrue(diagnostic['supported'])
        self.assertFalse(diagnostic['installed'])
        self.assertEqual(before, {str(p.relative_to(self.game)): p.read_bytes() for p in self.game.rglob('*') if p.is_file()})
        self.assertFalse(self.engine.store.exists())

    def test_pending_journal_and_concurrent_lock_refuse_before_backup_writes(self):
        self.engine.store.mkdir()
        backup = self.engine.store / 'backup-0'
        backup.write_bytes(b'precious backup')
        pending = self.engine.store / 'journal.json'
        pending.write_text('{"interrupted": true}')
        (self.game / self.c.TARGETS[0]).write_bytes(b'current')
        with self.assertRaises(self.c.SafetyError): self.engine.install(self.prepared, self.launcher, replace=True)
        self.assertEqual(backup.read_bytes(), b'precious backup')
        pending.unlink()
        with self.c.game_lock(self.game):
            with self.assertRaises(self.c.SafetyError): self.engine.install(self.prepared, self.launcher, replace=True)
        self.assertEqual(backup.read_bytes(), b'precious backup')

    def test_manifest_decoys_unsafe_generated_zip_and_hardlinks_refused(self):
        manifest = self.game.parent.parent / 'appmanifest_365360.acf'
        manifest.write_text('"AppState" { "appid" "999" "installdir" "Other" } // "appid" "365360" "installdir" "Battle Brothers"')
        with self.assertRaises(self.c.SafetyError): self.engine.install(self.prepared, self.launcher)
        manifest.write_text('"AppState" { "appid" "365360" "installdir" "Battle Brothers" }')
        with zipfile.ZipFile(self.pkg, 'w') as z: z.writestr('../unsafe.nut', 'bad')
        self.prepared = self.c.Prepared(self.pkg, self.c.sha256(self.pkg), [])
        with self.assertRaises(self.c.SafetyError): self.engine.install(self.prepared, self.launcher)
        with zipfile.ZipFile(self.pkg, 'w') as z: z.writestr('scripts/test.nut', 'safe')
        self.prepared = self.c.Prepared(self.pkg, self.c.sha256(self.pkg), [])
        self.engine.store.mkdir(exist_ok=True)
        protected = self.game / 'win32/BattleBrothers.exe'
        os.link(protected, self.engine.store / 'backup-0')
        with self.assertRaises(self.c.SafetyError): self.engine.install(self.prepared, self.launcher)
        self.assertEqual(protected.read_bytes(), b'original-game')
        self.assertFalse((self.game / self.c.TARGETS[0]).exists())

    def test_staged_payload_mutation_aborts_without_game_writes(self):
        copy = self.c.durable_copy
        def corrupt(source, target):
            copy(source, target)
            if Path(target).name == 'new-0': Path(target).write_bytes(b'changed during staging')
        with patch.object(self.c, 'durable_copy', side_effect=corrupt):
            with self.assertRaises(self.c.SafetyError): self.engine.install(self.prepared, self.launcher)
        self.assertFalse((self.game / self.c.TARGETS[0]).exists())
        self.assertFalse((self.game / self.c.TARGETS[1]).exists())
        self.assertFalse((self.engine.store / 'journal.json').exists())

    def test_backup_and_payload_sources_cannot_alias_protected_game_files(self):
        self.engine.store.mkdir(exist_ok=True)
        # A fake prepared JSON can never authorize using game originals as a payload.
        protected = self.game / 'win32/BattleBrothers.exe'
        with zipfile.ZipFile(protected, 'w') as z: z.writestr('scripts/fixture.nut', 'fixture')
        prepared = self.c.Prepared(protected, self.c.sha256(protected), [])
        with self.assertRaises(self.c.SafetyError): self.engine.install(prepared, self.launcher)
        self.assertFalse((self.game / self.c.TARGETS[0]).exists())

    def test_discover_rejects_reparse_library_and_malformed_vdf_without_writes(self):
        steam = self.root / 'Steam'
        (steam / 'steamapps').mkdir(parents=True)
        bad = steam / 'steamapps/libraryfolders.vdf'
        bad.write_text('"libraryfolders" { "0" { "path" "C:/not closed" }')
        with self.assertRaises(self.c.SafetyError): self.c.discover_games(steam)
        self.assertFalse((steam / 'steamapps/common').exists())
        outside = self.root / 'outside'
        outside.mkdir()
        (steam / 'steamapps/libraryfolders.vdf').write_text('"libraryfolders" { "0" { "path" "' + str(outside).replace('\\', '\\\\') + '" } }')
        (steam / 'steamapps').rename(steam / 'steamapps-real')
        import subprocess
        made = subprocess.run(['cmd', '/c', 'mklink', '/J', str(steam / 'steamapps'), str(outside)], capture_output=True)
        self.assertEqual(made.returncode, 0, made.stdout)
        try:
            with self.assertRaises(self.c.SafetyError): self.c.discover_games(steam)
        finally:
            os.rmdir(steam / 'steamapps')
            (steam / 'steamapps-real').rename(steam / 'steamapps')

    def test_unexpected_installer_state_file_refuses_before_any_game_write(self):
        self.engine.store.mkdir(exist_ok=True)
        foreign_state = self.engine.store / 'unexpected.txt'
        foreign_state.write_bytes(b'foreign')
        with self.assertRaises(self.c.SafetyError): self.engine.install(self.prepared, self.launcher)
        self.assertEqual(foreign_state.read_bytes(), b'foreign')
        self.assertFalse((self.game / self.c.TARGETS[0]).exists())

    def test_reinstall_after_uninstall_reuses_original_backup(self):
        original = self.game / 'data/zdata_cn.zip'
        original.write_bytes(b'original localization')
        self.engine.install(self.prepared, self.launcher, replace=True)
        self.engine.uninstall()
        self.assertTrue((self.engine.store / 'backup-0').is_file())
        before = {str(p.relative_to(self.game)): p.read_bytes() for p in self.game.rglob('*') if p.is_file()}
        with self.assertRaises(self.c.SafetyError):
            self.engine.install(self.prepared, self.launcher)
        self.assertEqual(before, {str(p.relative_to(self.game)): p.read_bytes() for p in self.game.rglob('*') if p.is_file()})
        self.engine.install(self.prepared, self.launcher, replace=True)
        self.engine.uninstall()
        self.assertEqual(original.read_bytes(), b'original localization')

if __name__ == '__main__': unittest.main()
