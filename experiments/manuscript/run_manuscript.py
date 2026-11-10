"""Command-line execution of unchanged EEIO v2.1 manuscript functions.

This module is an *orchestrator only* and does not modify the mathematical
implementation in scenario_batch_v21.py or manufacturing_research_core.py.
Input and accounting caveats are described in docs/METHODOLOGICAL_BOUNDARIES.md.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

TASKS = (
    "rq1-screen", "rq1-ranking", "rq2-four-case", "rq2-surface",
    "rq3-upstream", "rq4-sector-sensitivity", "rq4-cross-year", "manufacturing-m0-m8",
)


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _serializable(obj):
    try:
        import numpy as np
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, np.generic):
            return obj.item()
    except ImportError:
        pass
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def _clean_json(obj):
    """Convert non-finite diagnostic scalars to JSON null, not invalid NaN."""
    try:
        import numpy as np
        if isinstance(obj, np.ndarray):
            return _clean_json(obj.tolist())
        if isinstance(obj, np.generic):
            return _clean_json(obj.item())
    except ImportError:
        pass
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {str(k): _clean_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean_json(v) for v in obj]
    return obj


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", choices=TASKS)
    parser.add_argument("--year", choices=(2016, 2020), type=int, default=2020)
    parser.add_argument("--target-sector", default="149")
    parser.add_argument("--run-dir", type=Path, default=None,
                        help="New, non-existing output folder (default results/manuscript/<task>)")
    parser.add_argument("--quiet", action="store_true", help="Reduce per-sector console printing")
    parser.add_argument("--io", type=Path, default=Path("static/data/2020/io_ind_2020_total_BPS_verified.csv"),
                        help="Reviewed, SHA256-pinned BPS TOTAL IO file for manufacturing-m0-m8")
    parser.add_argument("--factor-set", default="LEGACY_JAMALI_2016",
                        choices=("LEGACY_JAMALI_2016", "SENS_MINUS_20", "SENS_PLUS_20"))
    args = parser.parse_args(argv)

    if args.task == "manufacturing-m0-m8" and args.year != 2020:
        parser.error("manufacturing-m0-m8 is restricted to the 2020 model")
    if args.task == "manufacturing-m0-m8" and args.target_sector == "149":
        args.target_sector = "114"  # explicit default for the manufacturing branch
    if args.task == "manufacturing-m0-m8" and args.target_sector not in ("114", "111"):
        parser.error("manufacturing-m0-m8 supports only 114 (iron/steel) or 111 (glass)")

    out = args.run_dir or Path("results/manuscript") / args.task
    if out.exists():
        parser.error(f"Output directory already exists: {out}. Choose a new --run-dir; prior runs are never overwritten.")
    # Input preflight before importing live DB code.
    years = (2016, 2020) if args.task == "rq4-cross-year" else (args.year,)
    for year in years:
        if args.task != "manufacturing-m0-m8":
            fp = Path(f"static/data/{year}/io_ind_{year}.csv")
            if not fp.is_file():
                parser.error(f"Missing required IO CSV: {fp}. See docs/DATA_REQUIREMENTS.md")
    if args.task == "manufacturing-m0-m8" and not args.io.is_file():
        parser.error(f"Missing manufacturing research verified IO: {args.io}")

    # Delayed imports keep '--help' and the top-level registry working offline.
    from dotenv import load_dotenv
    load_dotenv()
    from flask import Flask
    from util_db import db_init, tbl_to_df

    app = Flask("eeio_v21_manuscript_reproduction")
    db = db_init(app)
    out.mkdir(parents=True, exist_ok=False)
    produced: list[Path] = []

    def csv(name, df, *, index=False):
        path = out / name
        df.to_csv(path, index=index)
        produced.append(path)

    def jsonfile(name, data):
        path = out / name
        path.write_text(json.dumps(_clean_json(data), indent=2, default=_serializable, allow_nan=False) + "\n", encoding="utf-8")
        produced.append(path)

    try:
        with app.app_context():
            if args.task == "manufacturing-m0-m8":
                from manufacturing_readonly_loader import read_model_readonly
                from manufacturing_research_core import calculate_research_grid
                model, source_metadata = read_model_readonly(db, args.io, year=2020)
                package = calculate_research_grid(
                    model, sector=args.target_sector, factor_set_code=args.factor_set,
                    source_metadata=source_metadata,
                )
                import pandas as pd
                csv("manufacturing_m0_m8.csv", pd.DataFrame(package["scenarios"]))
                jsonfile("manufacturing_m0_m8_provenance.json", package)
            else:
                import scenario_batch_v21 as batch
                if args.task == "rq4-cross-year":
                    frame, comparison = batch.run_electricity_demand_year_robustness_v21(
                        db, years=(2016, 2020), demand_change_pct=-10,
                        verbose=not args.quiet,
                    )
                    csv("cross_year_2016_2020.csv", frame)
                    jsonfile("cross_year_comparison.json", comparison)
                else:
                    model = batch.build_batch_model_v21(db, args.year)
                    if args.task in ("rq1-screen", "rq1-ranking", "rq3-upstream"):
                        sectors = tbl_to_df(db, "SELECT sector_agg, sector_code, sector_name "
                                                    "FROM sector_lst WHERE sector_label='DET'")
                    if args.task == "rq1-screen":
                        data = batch.run_sector_electricity_efficiency_batch_v21(
                            model, sectors, change=-0.10, verbose=not args.quiet,
                        )
                        csv("sector_screening_185_minus10pct.csv", data)
                    elif args.task == "rq1-ranking":
                        long, score, rank, corr, stability = batch.run_sector_electricity_ranking_sensitivity_v21(
                            model, sectors, change_levels_pct=[-10, -20, -30],
                            verbose=not args.quiet,
                        )
                        for name, frame in (
                            ("ranking_555_runs.csv", long), ("score_matrix.csv", score),
                            ("rank_matrix.csv", rank), ("spearman_correlations.csv", corr),
                            ("rank_stability.csv", stability),
                        ):
                            csv(name, frame, index=name.endswith("_matrix.csv"))
                    elif args.task == "rq2-four-case":
                        cases, interaction = batch.run_combined_policy_interaction_v21(
                            model, target_sector_code=args.target_sector,
                            demand_change=-0.20, emission_factor_change=-0.20,
                        )
                        csv("four_cases_s0_s3.csv", cases)
                        jsonfile("four_case_interaction.json", interaction)
                    elif args.task == "rq2-surface":
                        long, percent, mtco2 = batch.run_electricity_policy_response_surface_all_sectors_v21(
                            model, efficiency_levels_pct=[0, -10, -20, -30, -40],
                            grid_ef_levels_pct=[0, -10, -20, -30, -40, -50],
                            verbose=not args.quiet,
                        )
                        csv("response_surface_30_cases.csv", long)
                        csv("response_surface_percent.csv", percent, index=True)
                        csv("response_surface_mtco2.csv", mtco2, index=True)
                        interaction, incremental, retained, marginal = batch.analyze_electricity_response_surface_v21(percent)
                        csv("response_surface_interaction_pp.csv", interaction, index=True)
                        csv("response_surface_incremental.csv", incremental, index=True)
                        csv("response_surface_retained_percent.csv", retained, index=True)
                        csv("response_surface_marginal.csv", marginal)
                    elif args.task == "rq3-upstream":
                        contrib, summary = batch.run_target_supply_chain_contribution_analysis_v21(
                            model, sectors, target_sector_code=args.target_sector,
                            efficiency_change_pct=0, grid_ef_change_pct=-20,
                        )
                        csv("upstream_contributions_185.csv", contrib)
                        jsonfile("upstream_summary.json", summary)
                        pareto, psummary = batch.prepare_supply_chain_pareto_v21(contrib)
                        csv("upstream_pareto.csv", pareto)
                        jsonfile("pareto_summary.json", psummary)
                        group = batch.aggregate_supply_chain_contribution_v21(contrib, expected_group_count=14)
                        csv("upstream_groups14.csv", group)
                    elif args.task == "rq4-sector-sensitivity":
                        data = batch.run_single_sector_electricity_sensitivity_v21(
                            model, sector_code=args.target_sector,
                            change_levels_pct=[-5, -10, -15, -20, -30, -40],
                        )
                        csv("sector_sensitivity_6_levels.csv", data)
                    else:
                        raise AssertionError(f"Unhandled task {args.task}")
        hashes = {}
        for y in years:
            path = (args.io if args.task == "manufacturing-m0-m8" else
                    Path(f"static/data/{y}/io_ind_{y}.csv"))
            hashes[str(path)] = _sha(path)
        base = Path(__file__).resolve().parents[2]
        manifest = {
            "task": args.task, "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "year": args.year, "target_sector": args.target_sector,
            "factor_set": args.factor_set if args.task == "manufacturing-m0-m8" else None,
            "io_sha256": hashes,
            "source_code_sha256": {
                "scenario_batch_v21.py": _sha(base / "experiments/manuscript/scenario_batch_v21.py"),
                "eeio_engine.py": _sha(base / "src/eeio_engine.py"),
                "util_mat.py": _sha(base / "src/util_mat.py"),
                "manufacturing_research_core.py": _sha(base / "src/manufacturing_research_core.py"),
            },
            "database_provenance": "Live local MySQL tables; snapshots and table-level SHA256 NOT included in this archive. Reproduction requires same source contents.",
            "model_status": "Conditional counterfactual EEIO, not a verified national GHG inventory, forecast or optimization",
            "outputs": {p.name: _sha(p) for p in produced},
        }
        jsonfile("run_manifest.json", manifest)
        print(f"Finished {args.task}: {len(produced)} files under {out}")
        return 0
    except BaseException:
        # Keep partially generated files for debugging. Do not replace older results.
        print(f"Run failed; inspect partial outputs (if any) in {out}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
