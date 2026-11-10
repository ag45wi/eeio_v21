"""Single public command for EEIO-v2.1 research scripts and legacy tests.

Examples:
    python run_experiments.py list
    python run_experiments.py check-setup
    python run_experiments.py run rq1-screen --dry-run
    python run_experiments.py run rq1-screen -- --quiet --run-dir results/my_screen
    python run_experiments.py run test-final-demand
"""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
PATHS = ("src", "experiments/manuscript", "experiments/exploratory_validation", "experiments/legacy", "tests/legacy")
MANUSCRIPT_RUNNER = "experiments/manuscript/run_manuscript.py"

# Importable without any EEIO, NumPy, Flask or MySQL dependency.
TASKS = {
    "rq1-screen": (MANUSCRIPT_RUNNER, ["rq1-screen"], "Manuscript", "185-sector screening at -10% electricity", True),
    "rq1-ranking": (MANUSCRIPT_RUNNER, ["rq1-ranking"], "Manuscript", "555 runs for 10/20/30% ranking robustness", True),
    "rq2-four-case": (MANUSCRIPT_RUNNER, ["rq2-four-case"], "Manuscript", "S0-S3 electricity demand/grid EF interaction", True),
    "rq2-surface": (MANUSCRIPT_RUNNER, ["rq2-surface"], "Manuscript", "30 economy-wide two-factor combinations", True),
    "rq3-upstream": (MANUSCRIPT_RUNNER, ["rq3-upstream"], "Manuscript", "Upstream contribution/Pareto for sector 149", True),
    "rq4-sector-sensitivity": (MANUSCRIPT_RUNNER, ["rq4-sector-sensitivity"], "Manuscript", "Sector 149 reduction levels 5-40%", True),
    "rq4-cross-year": (MANUSCRIPT_RUNNER, ["rq4-cross-year"], "Manuscript", "2016 vs 2020 economy-wide -10% comparison", True),
    "manufacturing-m0-m8": (MANUSCRIPT_RUNNER, ["manufacturing-m0-m8"], "Manuscript", "Read-only 3x3 iron/steel or glass pilot", True),
    "audit-baseline": ("experiments/manuscript/_03_manuscript_baseline_audit_v21.py", [], "Audit", "Original EEIO allocation and factor checks", True),
    "audit-boundaries": ("experiments/manuscript/_04_manuscript_accounting_boundary_audit_v21.py", [], "Audit", "Scope/output/electricity self-allocation audit", True),
    "audit-final-demand": ("experiments/manuscript/_05_manuscript_io_final_demand_audit_v21.py", [], "Audit", "IO 3090/7000 accounting diagnostic", True),
    "audit-imports": ("experiments/manuscript/_06x_manuscript_io_import_bridge_audit_v21.py", [], "Audit", "Domestic imports and supply-use bridge checks", True),
    "audit-io-table-types": ("experiments/manuscript/_07_manuscript_domestic_vs_total_io_audit_v21.py", [], "Audit", "Compare official total and domestic IO CSVs", False),
    "audit-domestic-io": ("experiments/exploratory_validation/domestic_io_model.py", [], "Exploratory", "Isolated 185-product domestic EEIO model", False),
    "audit-environmental-extensions": ("experiments/exploratory_validation/environmental_extension_audit.py", [], "Exploratory", "Environmental extension/allocation sensitivity", True),
    "audit-heesi-allocation": ("experiments/exploratory_validation/heesi_energy_allocation.py", [], "Exploratory", "Source-anchored HEESI electricity allocations", True),
    "validate-sector-mapping": ("experiments/exploratory_validation/sector_mapping_validation.py", [], "Exploratory", "Source-aided review of 185-sector mapping", False),
    "audit-baseline-energy": ("experiments/exploratory_validation/baseline_energy_diagnostics.py", [], "Exploratory", "Static/live legacy allocation diagnostic", False),
    "test-baseline": ("tests/legacy/test_manuscript_baseline_audit_v21.py", [], "Offline tests", "Synthetic baseline allocation checks", False),
    "test-boundaries": ("tests/legacy/test_manuscript_accounting_boundary_audit_v21.py", [], "Offline tests", "Synthetic accounting-boundary checks", False),
    "test-final-demand": ("tests/legacy/test_manuscript_io_final_demand_audit_v21.py", [], "Offline tests", "Synthetic final-demand balances and negative cases", False),
    "test-imports": ("tests/legacy/test_manuscript_io_import_bridge_audit_v21.py", [], "Offline tests", "Synthetic import/supply reconciliation", False),
    "test-domestic-io": ("tests/exploratory_validation/test_domestic_io_model.py", [], "Fixture tests", "Requires BPS 2016/2020 total and domestic CSVs", False),
    "test-environmental-extensions": ("tests/exploratory_validation/test_environmental_extension_audit.py", [], "Fixture tests", "Requires BPS 2016/2020 domestic CSVs", False),
    "test-heesi-allocation": ("tests/exploratory_validation/test_heesi_energy_allocation.py", [], "Fixture tests", "Requires domestic IO and source-anchored HEESI CSV", False),
    "test-sector-mapping": ("tests/exploratory_validation/test_sector_mapping_validation.py", [], "Fixture tests", "Requires domestic IO, mapping, notes, anchors", False),
}


# Backwards-compatible CLI flags from the historical request-numbered development workflow.
# They are accepted but deliberately NOT included in the public `list` output.
LEGACY_TASK_ALIASES = {
    "request7-domestic": "audit-domestic-io",
    "request7-stage2": "audit-environmental-extensions",
    "request7-stage3": "audit-heesi-allocation",
    "request7-stage3b": "validate-sector-mapping",
    "request7-baseline-probe": "audit-baseline-energy",
    "test-request7-domestic": "test-domestic-io",
    "test-request7-stage2": "test-environmental-extensions",
    "test-request7-stage3": "test-heesi-allocation",
    "test-request7-stage3b": "test-sector-mapping",
}


def _environment() -> dict[str, str]:
    env = os.environ.copy()
    additions = [str(ROOT / part) for part in PATHS]
    env["PYTHONPATH"] = os.pathsep.join(additions + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def doctor() -> int:
    print("EEIO v2.1 research repository - non-invasive environment check")
    print("Python:", sys.version.split()[0], "| repository:", ROOT)
    checks = {
        "numpy": "numpy", "pandas": "pandas", "python-dotenv": "dotenv",
        "Flask": "flask", "flask-mysqldb": "flask_mysqldb",
        "mysqlclient": "MySQLdb", "Flask-Mail": "flask_mail",
        "plotly": "plotly", "matplotlib": "matplotlib", "openpyxl": "openpyxl",
        "kaleido (optional, static figures)": "kaleido",
    }
    print("\nPython dependencies:")
    for label, name in checks.items():
        print(f"  [{'OK' if importlib.util.find_spec(name) is not None else 'MISSING'}] {label}")
    print("\nLocal configuration (values NOT displayed):")
    for key in ("APP_KEY", "DB_USER", "DB_PASSWORD", "DB_NAME"):
        print(f"  {key}: {'set in process environment' if os.environ.get(key) else 'not set in process environment (may be provided by .env)'}")
    print("\nVersioned inputs are under data/inputs; source scripts use local static/data after data-prepare.")
    print("Expected original-code input locations:")
    for path in (
        "data/inputs/2016/io_ind_2016.csv",
        "data/inputs/2020/io_ind_2020.csv",
        "data/inputs/2016/io_ind_2016_domestic.csv",
        "data/inputs/2020/io_ind_2020_domestic.csv",
        "data/inputs/2020/io_ind_2020_total_BPS_verified.csv",
        "data/inputs/auxiliary/heesi2021_electricity_enduse_2020.csv",
        "data/exploratory/sector_mapping/sector_mapping_2020_proposed_v2_0.csv",
        "data/exploratory/sector_mapping/sector_mapping_2020_review_notes.csv",
    ):
        print(f"  [{'present' if (ROOT / path).is_file() else 'missing'}] {path}")
    print("\nThis check does not connect to MySQL or run simulations.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    command = parser.add_subparsers(dest="command", required=True)
    command.add_parser("list", help="List every supported experiment, audit and regression test")
    command.add_parser("check-setup", help="Check packages, inputs and DB environment without connecting")
    command.add_parser("doctor", help=argparse.SUPPRESS)  # Legacy alias
    command.add_parser("verify-offline", help="Run repository, v2 data, and four synthetic checks (no DB)")
    command.add_parser("data-check", help="Verify packaged input hashes and sanitized SQL (no DB)")
    command.add_parser("verify-data-backed", help="Run domestic-IO and environmental-extension regressions (no MySQL)")
    prepare = command.add_parser("data-prepare", help="Copy versioned inputs into local static/data for the original code")
    prepare.add_argument("--force", action="store_true", help="Allow replacing a different local staged file")
    prepare.add_argument("--dry-run", action="store_true", help="No filesystem writes")
    show = command.add_parser("show", help="Describe a task and its resolved script")
    show.add_argument("task", help="One experiment name from the 'list' output")
    run = command.add_parser("run", help="Execute one named task; use -- to pass experiment flags")
    run.add_argument("task", help="One experiment name from the 'list' output")
    run.add_argument("--dry-run", action="store_true", help="Print exact invocation without executing")
    args, extra = parser.parse_known_args(argv)
    if args.command in ("list", "check-setup", "doctor", "show", "verify-offline", "verify-data-backed", "data-check", "data-prepare") and extra:
        parser.error("Unexpected extra arguments: " + " ".join(extra))
    if args.command == "list":
        print(f"{'TASK':<28} {'FAMILY':<14} PURPOSE")
        print("-" * 98)
        for key, (script, fixed, category, explanation, needs_db) in TASKS.items():
            print(f"{key:<28} {category:<14} {explanation}")
        return 0
    if args.command in ("check-setup", "doctor"):
        return doctor()
    if args.command in ("data-check", "data-prepare"):
        sys.path.insert(0, str(ROOT / "scripts"))
        from data_tools import check_inputs, stage_inputs
        try:
            if args.command == "data-check":
                check_inputs()
            else:
                stage_inputs(force=args.force, dry_run=args.dry_run)
        except (ValueError, FileExistsError, OSError) as exc:
            print("Data preparation error:", exc, file=sys.stderr)
            return 2
        return 0
    if args.command == "verify-data-backed":
        sys.path.insert(0, str(ROOT / "scripts"))
        from data_tools import stage_inputs
        try:
            stage_inputs(quiet=True)
        except (ValueError, FileExistsError, OSError) as exc:
            print("Input preparation failed:", exc, file=sys.stderr)
            return 2
        print("Running real-IO regression tests with synthetic DB-like inputs; no MySQL connection.", flush=True)
        for task in ("test-domestic-io", "test-environmental-extensions"):
            script, fixed, *_ = TASKS[task]
            result = subprocess.run([sys.executable, str(ROOT / script), *fixed], cwd=ROOT, env=_environment(), check=False)
            if result.returncode:
                print("Fixture regression failed:", task, file=sys.stderr)
                return result.returncode
        print("Both data-backed exploratory regressions passed. Legacy manuscript replication remains unverified.")
        return 0
    if args.command == "verify-offline":
        checks = [[sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"]]
        for test_task in ("test-baseline", "test-boundaries", "test-final-demand", "test-imports"):
            script, fixed, *_ = TASKS[test_task]
            checks.append([sys.executable, str(ROOT / script), *fixed])
        print("Running offline checks against the published inputs; no MySQL connection is used.", flush=True)
        for i, cmd in enumerate(checks, start=1):
            print(f"\n=== Offline check {i}/{len(checks)}: {cmd[1]} ===", flush=True)
            code = subprocess.run(cmd, cwd=ROOT, env=_environment(), check=False).returncode
            if code:
                print(f"Offline check failed with exit {code}; stopping.", file=sys.stderr)
                return code
        print("\nAll repository, input-integrity, and synthetic checks passed. This does not reproduce manuscript values.")
        return 0
    canonical = LEGACY_TASK_ALIASES.get(args.task, args.task)
    if canonical not in TASKS:
        parser.error(f"Unknown experiment '{args.task}'. Use 'list' to see supported names.")
    if canonical != args.task:
        print(f"Legacy command '{args.task}' is deprecated; use '{canonical}' instead.", flush=True)
    script, fixed, category, explanation, needs_db = TASKS[canonical]
    target = ROOT / script
    if not target.is_file():
        print(f"Missing expected task script: {target}", file=sys.stderr)
        return 2
    if args.command == "show":
        print("Task:", canonical)
        print("Family:", category)
        print("Purpose:", explanation)
        print("Script:", script)
        print("Needs live MySQL:", "yes" if needs_db else "depends on optional flags or no")
        print("Command:", f"python run_experiments.py run {canonical} -- [extra script flags]")
        print("Documentation: README.md and docs/EXPERIMENT_INDEX.md")
        return 0
    if extra and extra[0] == "--":
        extra = extra[1:]
    cmd = [sys.executable, str(target), *fixed, *extra]
    display = " ".join(["python", script, *fixed, *extra])
    print("Experiment:", canonical, flush=True)
    print("Resolved command:", display, flush=True)
    print("Working directory:", ROOT, flush=True)
    if args.dry_run:
        print("DRY RUN: no code executed and no database contacted.")
        return 0
    try:
        return subprocess.run(cmd, cwd=ROOT, env=_environment(), check=False).returncode
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
