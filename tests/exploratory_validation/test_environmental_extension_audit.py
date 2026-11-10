"""Public descriptive launcher for the unchanged legacy environmental-extension and carrier-allocation diagnostics checks.

Requires the input fixtures documented in docs/DATA_REQUIREMENTS.md.
"""
from pathlib import Path as _Path
import importlib as _importlib
import sys as _sys

_root = _Path(__file__).resolve().parents[2]
for _part in ('src', 'experiments/manuscript', 'experiments/legacy',
              'experiments/exploratory_validation', 'tests/legacy'):
    _p = str(_root / _part)
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
_impl = _importlib.import_module('test_request7_stage2_environmental_audit_v21')
if __name__ == '__main__':
    raise SystemExit(_impl.main() if hasattr(_impl, 'main') else _impl.run())
else:
    _sys.modules[__name__] = _impl
