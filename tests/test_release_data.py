"""Offline validation of the v2 research data release; requires no live MySQL."""
from __future__ import annotations
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import data_tools

class ReleaseDataTests(unittest.TestCase):
    def test_source_inputs_and_sanitized_schema(self):
        result=data_tools.check_inputs(quiet=True)
        self.assertEqual(result['verified_input_files'],10)
        self.assertEqual(result['research_schema_tables'],19)
        self.assertEqual(result['reference_seed_tables'],7)

    def test_version_separation_and_verified_manufacturing_file(self):
        m=json.loads((ROOT/'data/metadata/input_manifest.json').read_text())
        d=m['io_2020_legacy_vs_author_verified']
        self.assertEqual(d['numeric_cells_different'],106)
        self.assertEqual(d['sign_only_differences'],106)
        self.assertEqual(d['header_labels_different'],8)
        self.assertNotEqual(m['files']['data/inputs/2020/io_ind_2020.csv']['sha256'],
                            m['files']['data/inputs/2020/io_ind_2020_total_BPS_verified.csv']['sha256'])

    def test_local_stage_copies_exact_content_without_source_mutation(self):
        with TemporaryDirectory() as folder:
            local=Path(folder)/'static/data'
            plan=data_tools.stage_inputs(local,dry_run=True,quiet=True)
            self.assertEqual(len(plan),10)
            self.assertFalse(local.exists())
            made=data_tools.stage_inputs(local,quiet=True)
            self.assertEqual(len(made),10)
            self.assertEqual(data_tools.sha256_file(local/'2020/io_ind_2020.csv'),
                             data_tools.sha256_file(ROOT/'data/inputs/2020/io_ind_2020.csv'))
            # A tampered staged file must not be overwritten without explicit consent.
            (local/'2020/io_ind_2020.csv').write_text('tampered')
            with self.assertRaises(FileExistsError):data_tools.stage_inputs(local,quiet=True)
            data_tools.stage_inputs(local,force=True,quiet=True)
            self.assertEqual(data_tools.sha256_file(local/'2020/io_ind_2020.csv'),
                             data_tools.sha256_file(ROOT/'data/inputs/2020/io_ind_2020.csv'))

    def test_no_private_dump_or_empty_cached_2020_outputs(self):
        db=(ROOT/'database/research_schema.sql').read_text()+'\n'+(ROOT/'database/research_seed.sql').read_text()
        self.assertNotIn('CREATE TABLE `user`',db)
        self.assertNotIn('INSERT INTO `user`',db)
        self.assertNotIn('pbkdf2:',db)
        self.assertFalse(list((ROOT/'static/buf').rglob('*.csv')) if (ROOT/'static/buf').exists() else [])
        inventory=json.loads((ROOT/'data/metadata/buffer_inventory.json').read_text())
        self.assertEqual(len(inventory['files']),55)
        self.assertIn('buf/2020/mat_B.csv',inventory['flagged_2020_numerical_gaps'])

if __name__=='__main__':unittest.main()
