import importlib
from pathlib import Path
import sys
import unittest

class BuildTests(unittest.TestCase):
    def test_runtime_license_collection_extracts_embedded_zipfs_terms(self):
        import tempfile
        import zipfile
        import os
        import build
        scratch = Path.home() / 'AppData/Local/hermes/cache/scratch'
        with tempfile.TemporaryDirectory(dir=scratch) as tmp:
            base = Path(tmp) / 'python'
            (base / 'DLLs').mkdir(parents=True)
            (base / 'LICENSE.txt').write_text('Python runtime terms', encoding='utf8')
            for dll, folder in [('tcl90.dll', 'tcl_library'), ('tcl9tk90.dll', 'tk_library')]:
                with zipfile.ZipFile(base / 'DLLs' / dll, 'w') as z:
                    z.writestr(folder + '/license.terms', 'Permission to use, copy, modify Tcl/Tk ' + folder)
                    z.writestr('../../outside', 'must not extract')
            output = Path(tmp) / 'licenses'
            build.copy_runtime_licenses(output, base)
            self.assertTrue((output / 'Python-LICENSE.txt').is_file())
            self.assertIn('Permission to use', (output / 'Tcl-license.terms').read_text())
            self.assertIn('Permission to use', (output / 'Tk-license.terms').read_text())
            self.assertFalse((Path(tmp) / 'outside').exists())

    def test_runtime_license_collection_fails_closed_if_tcl_terms_absent(self):
        import tempfile
        import build
        scratch = Path.home() / 'AppData/Local/hermes/cache/scratch'
        with tempfile.TemporaryDirectory(dir=scratch) as tmp:
            base = Path(tmp) / 'python'
            base.mkdir()
            with self.assertRaises(RuntimeError):
                build.copy_runtime_licenses(Path(tmp) / 'licenses', base)

    def test_packaging_has_gui_cli_and_explicit_dependency_config_only(self):
        try: build = importlib.import_module('build')
        except ModuleNotFoundError: self.fail('build script missing')
        commands = build.commands(Path(__file__).resolve().parents[1])
        self.assertEqual(len(commands), 2)
        self.assertTrue(all(command[:3] == [sys.executable, '-m', 'PyInstaller'] for command in commands))
        self.assertIn('--windowed', commands[0])
        self.assertIn('--console', commands[1])
        self.assertTrue(all('--onedir' in command for command in commands))
        self.assertTrue(all('--onefile' not in command for command in commands))
        for command in commands:
            data = [command[i+1] for i, arg in enumerate(command) if arg == '--add-data']
            self.assertEqual([Path(arg.rsplit(';', 1)[0]).name for arg in data], ['dependencies.json'])
            self.assertEqual(Path(command[-1]).name, 'bb_cn_installer.py')

if __name__ == '__main__': unittest.main()
