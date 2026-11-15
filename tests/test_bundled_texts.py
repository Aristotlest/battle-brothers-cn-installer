import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from test_core import SCRATCH
import installer_core as core
import build_onefile as build


class BundledTextsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'translation-source.zip'
        with zipfile.ZipFile(self.source, 'w') as z:
            z.writestr('source/LICENSE', 'GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007')
            z.writestr('source/zh_CN.UTF-8/json/scripts/test.nut', '{"a":"汉化"}')
        self.pin = patch.dict(core.PINS['texts'], {'sha256':core.sha256(self.source)})
        self.pin.start()
        self.addCleanup(self.pin.stop)

    def test_valid_embedded_texts_never_fetch_or_write_cache(self):
        with patch.object(core, 'BUNDLED_TEXTS', self.source, create=True), patch.object(core, 'fetch_pinned', side_effect=AssertionError('network forbidden')):
            self.assertTrue(hasattr(core, 'translation_archive'), 'bundled resource resolution missing')
            result = core.translation_archive(self.root / 'unused-cache')
            self.assertEqual(result, self.source)
            self.assertFalse((self.root/'unused-cache').exists())

    def test_corrupt_embedded_source_fails_without_online_fallback(self):
        self.source.write_bytes(b'bad embedded bytes')
        with patch.object(core,'BUNDLED_TEXTS', self.source, create=True), patch.object(core,'fetch_pinned') as fetch:
            self.assertTrue(hasattr(core,'translation_archive'))
            with self.assertRaises(core.SafetyError):core.translation_archive(self.root/'cache')
            fetch.assert_not_called()

    def test_source_checkout_without_bundle_uses_official_pinned_download(self):
        cache = self.root / 'cache'
        with patch.object(core,'BUNDLED_TEXTS',self.root/'absent.zip',create=True), patch.object(core,'fetch_pinned',return_value=self.source) as fetch:
            self.assertTrue(hasattr(core,'translation_archive'))
            self.assertEqual(core.translation_archive(cache),self.source)
            fetch.assert_called_once_with(core.PINS['texts'],cache/'translation-source.zip')

    def test_build_stages_exact_source_license_and_attribution(self):
        self.assertTrue(hasattr(build,'stage_texts'), 'source payload staging missing')
        stage = build.stage_texts(self.root/'stage',self.source)
        self.assertEqual(core.sha256(stage/'translation-source.zip'),core.sha256(self.source))
        self.assertIn('GNU GENERAL PUBLIC LICENSE',(stage/'TRANSLATION-LICENSE.txt').read_text(encoding='utf-8'))
        notice=(stage/'TRANSLATION-NOTICE.txt').read_text(encoding='utf-8')
        self.assertIn(core.PINS['texts']['commit'],notice)
        self.assertIn('shabbywu',notice)
        self.assertIn('https://github.com/shabbywu/Battle-Brothers-CN',notice)
        args=build.command(self.root,self.root/'licenses')
        self.assertIn(str(self.root/'build/onefile-resources')+';resources',args)

    def test_build_rejects_hash_mismatch_before_staging(self):
        self.assertTrue(hasattr(build,'stage_texts'))
        self.source.write_bytes(b'untrusted')
        with self.assertRaises(core.SafetyError):build.stage_texts(self.root/'stage',self.source)
        self.assertFalse((self.root/'stage/translation-source.zip').exists())
