"""Installer-owned captions use ordinary punctuation; raw diagnostics stay intact."""
import ast
from pathlib import Path
import unittest


class UICharacterTests(unittest.TestCase):
    def test_installer_owned_strings_avoid_special_symbol_punctuation(self):
        root = Path(__file__).resolve().parents[1]
        unsupported = set('\u00b7\u201c\u201d\u2026\u2192')
        found = []
        for filename in ('bb_cn_installer.py', 'steam_ui.py', 'steam_options.py'):
            tree = ast.parse((root / filename).read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    symbols = unsupported.intersection(node.value)
                    if symbols:
                        found.append((filename, node.lineno, sorted(symbols)))
        self.assertEqual(found, [], 'Use plain punctuation in installer-owned UI strings')


if __name__ == '__main__':
    unittest.main()
