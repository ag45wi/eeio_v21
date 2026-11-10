"""Compatibility import alias: no changes to the supplied audit calculation."""
import importlib as _importlib
import sys as _sys
_impl = _importlib.import_module("_03_manuscript_baseline_audit_v21")
if __name__ == "__main__":
    _impl.main()
else:
    _sys.modules[__name__] = _impl
