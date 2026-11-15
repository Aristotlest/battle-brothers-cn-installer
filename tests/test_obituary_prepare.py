"""Post-conversion integration; payload bytes here are test doubles, not game data."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
import installer_core as core
import bb_cn_installer as app


class ObituaryPrepareTests(unittest.TestCase):
    def test_loading_old_prepared_payload_rejects_unfixed_bytecode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'zdata_cn.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('scripts/entity/tactical/player.cnut', b'old synthetic bytecode')
            manifest = root / 'prepared.json'
            manifest.write_text(json.dumps({'sha256': core.sha256(archive), 'warnings': []}), encoding='utf-8')
            with patch('installer_core.verify_obituary_package', create=True, side_effect=core.SafetyError('old payload')) as verify:
                with self.assertRaises(core.SafetyError):
                    app.load_prepared(manifest)
            verify.assert_called_once_with(archive)

    def test_unknown_bytecode_aborts_without_changing_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'zdata_cn.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('scripts/entity/tactical/player.cnut', b'unknown shape')
            before = archive.read_bytes()
            with patch('obituary_patch.patch_player', side_effect=ValueError('unknown shape')):
                with self.assertRaises(core.SafetyError):
                    core.repair_obituary_package(archive)
            self.assertEqual(before, archive.read_bytes())
            self.assertEqual([p.name for p in archive.parent.iterdir()], ['zdata_cn.zip'])

    def test_missing_player_aborts_without_changing_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'zdata_cn.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('scripts/other.cnut', b'not a supported conversion')
            before = archive.read_bytes()
            with self.assertRaises(core.SafetyError):
                core.repair_obituary_package(archive)
            self.assertEqual(before, archive.read_bytes())

    def test_repair_replaces_only_player_and_returns_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'zdata_cn.zip'
            with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as z:
                z.writestr('scripts/entity/tactical/player.cnut', b'old synthetic bytecode')
                z.writestr('scripts/untouched.cnut', b'keep unchanged' * 100)
            repair = getattr(core, 'repair_obituary_package', None)
            self.assertTrue(callable(repair), 'missing actual post-conversion repair')
            report = {'status': 'patched', 'function': 'getObituaryInfo', 'changed_instructions': 1}
            with patch('obituary_patch.patch_player', return_value=(b'new synthetic bytecode' * 100, report)) as apply:
                result = repair(archive)
            apply.assert_called_once_with(b'old synthetic bytecode')
            self.assertEqual(result, report)
            with zipfile.ZipFile(archive) as z:
                self.assertEqual(z.read('scripts/entity/tactical/player.cnut'), b'new synthetic bytecode' * 100)
                self.assertEqual(z.read('scripts/untouched.cnut'), b'keep unchanged' * 100)
                self.assertIsNone(z.testzip())


if __name__ == '__main__':
    unittest.main()
