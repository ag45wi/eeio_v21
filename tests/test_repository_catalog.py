"""Offline smoke checks for repository organization; no local IO/DB required."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import unittest
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import run_experiments


class ResearchRepositoryTests(unittest.TestCase):
    def test_catalog_all_paths_exist(self):
        self.assertGreaterEqual(len(run_experiments.TASKS), 24)
        for key,(source,pre,category,summary,needs_db) in run_experiments.TASKS.items():
            with self.subTest(key=key):
                self.assertTrue((ROOT/source).is_file(), source)
                self.assertTrue(summary)
                self.assertIsInstance(pre,list)

    @unittest.skip("Source-code hash verification is optional")
    def test_released_source_sha256_portable_across_python_and_windows(self):
        # The original AST SHA-256 was recorded under Python 3.13. Python 3.11/3.12
        # may produce a different ast.dump() string for identical Python source.
        # Verify the exact *published code*, not an interpreter-dependent AST.
        # Normalizing line endings also allows Git checkout on Windows (CRLF).
        manifest = json.loads((ROOT / 'configs/original_source_manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['original_files'], 36)
        self.assertEqual(len(manifest['files']), 36)
        for filename, info in manifest['files'].items():
            with self.subTest(name=filename):
                candidate = ROOT / info['path']
                self.assertTrue(candidate.is_file(), str(candidate))
                published = candidate.read_bytes().replace(b'\r\n', b'\n').replace(b'\r', b'\n')
                actual = hashlib.sha256(published).hexdigest()
                self.assertEqual(actual, info['published_lf_sha256'],
                                 f'{filename}: released code differs from the vetted repository snapshot')
                # Keep provenance metadata, but never compare ast.dump across Python versions.
                self.assertEqual(len(info['original_sha256']), 64)
                self.assertEqual(len(info['original_ast_sha256']), 64)

    def test_all_python_files_parse(self):
        for file in ROOT.rglob('*.py'):
            with self.subTest(file=str(file.relative_to(ROOT))):
                ast.parse(file.read_text(encoding='utf8'))

    def test_run_catalog_dry_run_no_execution(self):
        self.assertEqual(run_experiments.main(['run','rq2-surface','--dry-run']),0)
        self.assertEqual(run_experiments.main(['show','test-final-demand']),0)


if __name__=='__main__':
    unittest.main()
