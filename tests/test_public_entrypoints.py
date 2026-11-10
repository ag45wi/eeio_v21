"""Verify descriptive public API, CLI backwards compatibility, and archival provenance."""
import contextlib
from io import StringIO
import json
from pathlib import Path
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import run_experiments

class PublicEntrypointTests(unittest.TestCase):
    def test_public_catalog_uses_descriptive_names(self):
        buf = StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(run_experiments.main(['list']), 0)
        shown = buf.getvalue()
        self.assertNotIn('request7', shown.casefold())
        for name in ('audit-domestic-io', 'audit-environmental-extensions',
                     'audit-heesi-allocation', 'validate-sector-mapping',
                     'audit-baseline-energy', 'test-domestic-io',
                     'test-environmental-extensions', 'test-heesi-allocation',
                     'test-sector-mapping'):
            self.assertIn(name, shown)
            self.assertEqual(run_experiments.main(['run', name, '--dry-run']), 0)

    def test_historical_cli_aliases_resolve_to_new_entries(self):
        for historical, current in run_experiments.LEGACY_TASK_ALIASES.items():
            with self.subTest(historical=historical):
                buf = StringIO()
                with contextlib.redirect_stdout(buf):
                    self.assertEqual(run_experiments.main(['show', historical]), 0)
                self.assertIn(current, buf.getvalue())
                self.assertIn('deprecated', buf.getvalue())

    def test_original_five_modules_kept_under_legacy(self):
        manifest=json.loads((ROOT/'configs/original_source_manifest.json').read_text())
        for original in ('_08_request7_domestic_eeio_v21.py',
                         '_09_request7_stage2_environmental_audit_v21.py',
                         '_10_request7_stage3_heesi_enduse_v21.py',
                         '_11_request7_stage3b_mapping_evidence_audit_v21.py',
                         '_12_request7_baseline_readonly_probe.py'):
            with self.subTest(original=original):
                self.assertTrue(manifest['files'][original]['path'].startswith('experiments/legacy/'))
                self.assertTrue((ROOT/manifest['files'][original]['path']).is_file())
        self.assertFalse((ROOT/'experiments/request7').exists())

    def test_public_adapters_reference_legacy_sources(self):
        expected = {
           'domestic_io_model.py': '_08_request7_domestic_eeio_v21',
           'environmental_extension_audit.py': '_09_request7_stage2_environmental_audit_v21',
           'heesi_energy_allocation.py': '_10_request7_stage3_heesi_enduse_v21',
           'sector_mapping_validation.py': '_11_request7_stage3b_mapping_evidence_audit_v21',
           'baseline_energy_diagnostics.py': '_12_request7_baseline_readonly_probe',
        }
        for public, original in expected.items():
            with self.subTest(public=public):
                content = (ROOT/'experiments/exploratory_validation'/public).read_text()
                self.assertIn(f"import_module('{original}')", content)
                self.assertNotIn('numpy', content)

if __name__ == '__main__':
    unittest.main()
