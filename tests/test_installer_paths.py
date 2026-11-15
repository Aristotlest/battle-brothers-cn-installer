"""Read-only path discovery; all filesystem mutations are isolated fixtures."""
import hashlib
import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import installer_core as core


SCRATCH = Path(os.environ.get('BBCN_TEST_SCRATCH', str(Path.home() / 'AppData/Local/hermes/cache/scratch')))


class FakePlatform:
    def __init__(self, steam):
        self.root = steam

    def steam(self):
        return self.root

    def version(self, path):
        return core.PINS['game_version']

    def running(self) -> bool:
        return False


class PathDiscoveryTests(unittest.TestCase):
    def setUp(self):
        try:
            self.paths = importlib.import_module('installer_paths')
        except ModuleNotFoundError:
            self.fail('installer_paths read-only discovery is not implemented')
        self.tmp = tempfile.TemporaryDirectory(prefix='bbcn-paths-', dir=SCRATCH)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.steam = self.root / 'Steam'
        self.downloads = self.root / 'Downloads'
        self.platform = FakePlatform(self.steam)

    def detect(self, game=None, **kwargs):
        return self.paths.detect_paths(game, platform=self.platform,
                                       downloads_roots=[self.downloads], **kwargs)

    def make_game(self, library=None, name='Battle Brothers'):
        game = (library or self.steam) / 'steamapps/common' / name
        (game / 'win32').mkdir(parents=True)
        (game / 'win32/BattleBrothers.exe').write_bytes(b'game fixture')
        (game / 'win32/steam_api.dll').write_bytes(b'api fixture')
        (game.parent.parent / 'appmanifest_365360.acf').write_text(
            '"AppState" { "appid" "365360" "installdir" "' + name + '" }')
        return game

    def test_unique_supported_steam_game_is_selected(self):
        game = self.make_game()
        result = self.detect()
        self.assertEqual(result['games'], [game])
        self.assertEqual(result['game'], game)
        self.assertFalse((game / '.bb-cn-installer').exists())

    def test_explicit_game_selects_valid_choice_without_guessing_among_libraries(self):
        first = self.make_game()
        second = self.make_game(self.root / 'Library', 'Battle Brothers Other')
        (self.steam / 'steamapps/libraryfolders.vdf').write_text(
            '"libraryfolders" { "1" { "path" "' + second.parents[2].as_posix() + '" } }')
        ambiguous = self.detect()
        self.assertEqual(ambiguous['games'], sorted([first, second]))
        self.assertIsNone(ambiguous['game'])
        self.assertIn('多个', ambiguous['note'])
        self.assertEqual(self.detect(second)['game'], second)

    def launcher(self, directory, content=b'pinned launcher fixture', name='bb-launcher-steam.exe'):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_bytes(content)
        return path

    def pin_fixture(self):
        pin = patch.dict(core.PINS['launcher'], {
            'sha256': hashlib.sha256(b'pinned launcher fixture').hexdigest()})
        pin.start()
        self.addCleanup(pin.stop)

    def test_discovered_external_source_prepares_and_installs_with_existing_launcher(self):
        import subprocess
        import zipfile
        self.pin_fixture()
        game = self.make_game()
        (game / 'data').mkdir()
        (game / 'data/data_001.dat').write_bytes(b'original data fixture')
        installed = self.launcher(game / 'win32')
        downloaded = self.launcher(self.downloads)
        self.launcher(self.downloads / 'bad', b'unknown')
        self.launcher(self.downloads / 'renamed', name='another.exe')
        result = self.detect()
        cache = self.root / 'cache'
        cache.mkdir()
        translator = cache / 'bb-translator-windows.zip'
        texts = cache / 'translation-source.zip'
        with zipfile.ZipFile(translator, 'w') as archive:
            archive.writestr('Windows/Release/bb_translator.exe', 'never executed fixture')
        with zipfile.ZipFile(texts, 'w') as archive:
            archive.writestr('texts/zh_CN.UTF-8/json/scripts/test.nut', '{}')
        def convert(command, **kwargs):
            stage = Path(command[command.index('--game-dir') + 1])
            with zipfile.ZipFile(stage / 'data/zdata_cn.zip', 'w') as archive:
                archive.writestr('scripts/test.nut', 'translated fixture')
            return subprocess.CompletedProcess(command, 0, b'', b'')
        engine = core.Engine(result['game'], platform=self.platform)
        with patch.dict(core.PINS['translator'], {'sha256': core.sha256(translator)}), \
                patch.dict(core.PINS['texts'], {'sha256': core.sha256(texts)}):
            prepared = core.prepare(engine, result['launcher'], cache, runner=convert)
        # A pinned installed file is not ownership or replacement permission.
        with self.assertRaises(core.SafetyError):
            engine.install(prepared, result['launcher'], accept_warnings=True)
        engine.install(prepared, result['launcher'], replace=True, accept_warnings=True)
        self.assertTrue(engine.check()['healthy'])
        self.assertEqual(result['launchers'], [installed, downloaded])
        self.assertEqual(result['launcher'], downloaded)
        self.assertFalse(result['launcher'].is_relative_to(game))
        self.assertEqual((game / 'data/data_001.dat').read_bytes(), b'original data fixture')

    def test_installed_only_launcher_is_reported_but_not_selected_as_a_source(self):
        self.pin_fixture()
        game = self.make_game()
        installed = self.launcher(game / 'win32')
        result = self.detect()
        self.assertEqual(result['launchers'], [installed])
        self.assertIsNone(result['launcher'])
        self.assertIn('已安装', result['note'])
        self.assertIn('游戏目录之外', result['note'])
        self.assertFalse((game / '.bb-cn-installer').exists())

    def test_downloads_search_is_depth_bounded_prunes_heavy_trees_and_sorts(self):
        self.pin_fixture()
        third = self.launcher(self.downloads / 'z/second/third')
        first = self.launcher(self.downloads / 'a')
        self.launcher(self.downloads / 'z/second/third/fourth')
        for name in ('node_modules', '.git', '.cache', '__pycache__', 'build', 'dist',
                     '.venv', 'BB-CN-build-deps', 'battle-brothers-cn-installer'):
            self.launcher(self.downloads / name)
        repo = self.downloads / 'other-repository'
        self.launcher(repo)
        (repo / '.git').mkdir()
        result = self.detect()
        self.assertEqual(result['launchers'], [first, third])
        self.assertEqual(result['launcher'], first)

    def test_hardlinked_manifest_is_rejected_for_discovery_and_explicit_game(self):
        game = self.make_game()
        manifest = game.parent.parent / 'appmanifest_365360.acf'
        os.link(manifest, self.root / 'manifest-copy.acf')
        self.assertEqual(self.detect()['games'], [])
        self.assertIsNone(self.detect(game)['game'])

    def test_linked_discovery_metadata_is_never_read(self):
        game = self.make_game()
        manifest = game.parent.parent / 'appmanifest_365360.acf'
        os.link(manifest, self.root / 'manifest-copy.acf')
        read_text = Path.read_text
        def guarded(path, *args, **kwargs):
            self.assertNotEqual(path, manifest, 'must reject metadata links before reading')
            return read_text(path, *args, **kwargs)
        with patch.object(Path, 'read_text', guarded):
            self.assertIsNone(self.detect()['game'])

    def test_default_downloads_uses_known_folder_without_scanning_home(self):
        self.pin_fixture()
        redirected = self.root / '重定向下载'
        expected = self.launcher(redirected)
        with patch.object(self.paths, '_known_downloads', return_value=redirected, create=True):
            result = self.paths.detect_paths(platform=self.platform)
        self.assertEqual(result['launcher'], expected)

    def test_unknown_launcher_hash_and_renamed_pinned_bytes_are_not_selected(self):
        self.pin_fixture()
        self.launcher(self.downloads, b'unknown executable')
        self.launcher(self.downloads / 'renamed', name='bb-launcher-gog.exe')
        self.assertEqual(self.detect()['launchers'], [])
        self.assertIsNone(self.detect()['launcher'])

    def test_unsupported_explicit_game_does_not_fall_back_to_other_game(self):
        supported = self.make_game()
        rejected = self.make_game(self.root / 'Other')
        version = self.platform.version
        self.platform.version = lambda path: '0.0.0.0' if path.is_relative_to(rejected) else version(path)
        result = self.detect(rejected)
        self.assertEqual(result['games'], [supported])
        self.assertIsNone(result['game'])
        self.assertIn('校验', result['note'])

    def test_bad_manifest_and_running_game_are_not_supported_candidates(self):
        game = self.make_game()
        manifest = game.parent.parent / 'appmanifest_365360.acf'
        for content in ('"AppState" { "appid" "999" "installdir" "Battle Brothers" }',
                        '"AppState" { "appid" "365360" "installdir" "Other" }',
                        '"AppState" {'):
            with self.subTest(content=content):
                manifest.write_text(content)
                self.assertIsNone(self.detect(game)['game'])
        manifest.write_text('"AppState" { "appid" "365360" "installdir" "Battle Brothers" }')
        self.platform.running = lambda: True
        self.assertIsNone(self.detect(game)['game'])

    def test_hardlinked_launcher_is_rejected_before_hashing(self):
        self.pin_fixture()
        launcher = self.launcher(self.downloads)
        os.link(launcher, self.root / 'alias.exe')
        with patch.object(self.paths, 'sha256', side_effect=AssertionError('must not hash links')):
            self.assertEqual(self.detect()['launchers'], [])

    def test_downloads_symlink_directory_is_never_listed(self):
        self.pin_fixture()
        outside = self.root / 'outside'
        self.launcher(outside)
        self.downloads.mkdir()
        alias = self.downloads / 'shortcut'
        try:
            alias.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest('symlink creation unavailable: ' + str(exc))
        iterdir = Path.iterdir
        def guarded(path):
            self.assertNotEqual(path, alias, 'must not list linked directories')
            return iterdir(path)
        with patch.object(Path, 'iterdir', guarded):
            self.assertEqual(self.detect()['launchers'], [])

    def test_libraryfolders_hardlink_is_rejected_before_read(self):
        self.make_game()
        folder = self.steam / 'steamapps/libraryfolders.vdf'
        folder.write_text('"libraryfolders" {}')
        os.link(folder, self.root / 'folders-copy.vdf')
        with patch.object(Path, 'read_text', side_effect=AssertionError('metadata must not be read')):
            self.assertEqual(self.detect()['games'], [])

    def test_known_downloads_api_falls_back_to_home_when_unavailable(self):
        import ctypes
        with patch.object(ctypes, 'WinDLL', side_effect=OSError('unavailable'), create=True):
            self.assertEqual(self.paths._known_downloads(), Path.home() / 'Downloads')

    def test_default_platform_is_used_without_starting_processes(self):
        game = self.make_game()
        with patch.object(self.paths, 'WindowsPlatform', return_value=self.platform) as factory:
            result = self.paths.detect_paths(downloads_roots=[])
        factory.assert_called_once_with()
        self.assertEqual(result['game'], game)

    def test_detection_does_not_write_or_execute_anything(self):
        self.pin_fixture()
        game = self.make_game()
        launcher = self.launcher(self.downloads)
        snapshot = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        import builtins
        import subprocess
        real_open = builtins.open
        def readonly(path, mode='r', *args, **kwargs):
            self.assertFalse(any(flag in mode for flag in 'wax+'), 'discovery attempted a write')
            return real_open(path, mode, *args, **kwargs)
        with patch.object(builtins, 'open', readonly), \
                patch.object(subprocess, 'run', side_effect=AssertionError('must not execute')), \
                patch.object(Path, 'mkdir', side_effect=AssertionError('must not create directories')):
            result = self.detect(game)
        self.assertEqual(result['launcher'], launcher)
        self.assertEqual(snapshot, {p.relative_to(self.root): p.read_bytes()
                                    for p in self.root.rglob('*') if p.is_file()})

    def test_malformed_vdf_shapes_fail_closed_instead_of_crashing(self):
        game = self.make_game()
        manifest = game.parent.parent / 'appmanifest_365360.acf'
        manifest.write_text('"AppState" "not an object"')
        self.assertIsNone(self.detect(game)['game'])

    def test_missing_game_still_explained_when_launcher_is_found(self):
        self.pin_fixture()
        self.launcher(self.downloads)
        result = self.detect()
        self.assertIsNone(result['game'])
        self.assertIn('游戏', result['note'])

    def test_steam_game_symlink_is_rejected_before_directory_probe(self):
        game = self.make_game()
        moved = game.with_name('Real Game')
        game.rename(moved)
        try:
            game.symlink_to(moved, target_is_directory=True)
        except OSError as exc:
            self.skipTest('symlink creation unavailable: ' + str(exc))
        is_dir = Path.is_dir
        def guarded(path, *args, **kwargs):
            self.assertNotEqual(path, game, 'must reject linked game before probing it')
            return is_dir(path, *args, **kwargs)
        with patch.object(Path, 'is_dir', guarded):
            self.assertEqual(self.detect()['games'], [])

    def test_empty_locations_return_complete_unselected_result(self):
        result = self.detect()
        self.assertEqual(set(result), {'games', 'game', 'launchers', 'launcher', 'note'})
        self.assertEqual(result['games'], [])
        self.assertIsNone(result['game'])
        self.assertEqual(result['launchers'], [])
        self.assertIsNone(result['launcher'])
        self.assertIsInstance(result['note'], str)
        self.assertTrue(result['note'])


if __name__ == '__main__':
    unittest.main()
