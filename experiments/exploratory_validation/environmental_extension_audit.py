"""Environmental-extension and carrier-allocation diagnostics.

Public-facing entry point; executes the unchanged author-supplied calculation module.
Historical experiment number and source SHA-256: docs/EXPERIMENT_HISTORY.md.
This exploratory analysis must not be treated as the manuscript EEIO baseline.
"""
from pathlib import Path as _Path
import importlib as _importlib
import sys as _sys

_legacy = _Path(__file__).resolve().parents[1] / 'legacy'
if str(_legacy) not in _sys.path:
    _sys.path.insert(0, str(_legacy))
_impl = _importlib.import_module('_09_request7_stage2_environmental_audit_v21')
if __name__ == '__main__':
    # The legacy parser reads __doc__ for --help; show the descriptive public name.
    _impl.__doc__ = __doc__
    raise SystemExit(_impl.main())
else:
    _sys.modules[__name__] = _impl
