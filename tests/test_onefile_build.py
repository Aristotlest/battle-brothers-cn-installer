"""Single executable packaging contract, independent of legacy onedir builds."""
import importlib
from pathlib import Path
import sys
import unittest


class SingleExecutableBuildTests(unittest.TestCase):
    def test_public_build_is_one_windowed_exe_with_embedded_notices(self):
        try:
            build = importlib.import_module('build_onefile')
        except ModuleNotFoundError:
            self.fail('Single EXE build is not implemented')
        root = Path(__file__).resolve().parents[1]
        licenses = root / 'build' / 'onefile-licenses'
        command = build.command(root, licenses)
        self.assertEqual(command[:3], [sys.executable, '-m', 'PyInstaller'])
        self.assertIn('--onefile', command)
        self.assertIn('--windowed', command)
        self.assertNotIn('--onedir', command)
        self.assertNotIn('--console', command)
        self.assertEqual(command[command.index('--name') + 1], 'BB-CN-Installer')
        data = [command[i + 1].rsplit(';', 1) for i, arg in enumerate(command) if arg == '--add-data']
        for name in ('dependencies.json', 'LICENSE', 'THIRD_PARTY_NOTICES.md'):
            self.assertIn([str(root / name), '.'], data)
        self.assertIn([str(licenses), 'runtime-licenses'], data)
        self.assertEqual(Path(command[-1]).name, 'bb_cn_installer.py')


if __name__ == '__main__':
    unittest.main()
