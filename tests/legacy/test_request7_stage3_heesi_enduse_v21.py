"""Offline regression and falsification tests for Stage 3.

The BPS domestic 2020 IO file is REAL; MySQL-like inputs are synthetic,
with only the values already disclosed in the user's execution logs.
No live MySQL connection; no mutation of existing EEIO/Stage 1/2 files.
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json

import numpy as np
import pandas as pd

from test_request7_domestic_eeio_v21 import (
    dataset_path, ensure_real_scenario_module, mock_db_tables
)
from request7_domestic_eeio_v21 import load_domestic_io
from request7_stage3_heesi_enduse_v21 import (
    ALLOCATION_METHODS, DEFAULT_ANCHORS,
    read_enduse_anchors, reconcile_heesi_with_db,
    mapping_template, read_sector_mapping, write_mapping_template,
    enduse_calibrated_model, execute_stage3, save_stage3_outputs,
)
from request7_domestic_eeio_v21 import build_domestic_research_model


def near(a, b, label, rtol=1e-9, atol=1e-4):
    if not np.isclose(float(a), float(b), rtol=rtol, atol=atol):
        raise AssertionError(f"{label} {a} != {b}")


def need_failure(fn, expected=""):
    try:
        fn()
    except (ValueError, FileExistsError) as exc:
        if expected and expected.casefold() not in str(exc).casefold():
            raise AssertionError(f"Unexpected failure reason: {exc}") from exc
    else:
        raise AssertionError(f"Expected failure containing {expected!r}")


def run():
    ensure_real_scenario_module()
    anchors = read_enduse_anchors(DEFAULT_ANCHORS)
    assert anchors.gwh == {"industry":88372.0, "household":112754.0,
                           "commercial":58159.0, "transport":292.0}
    near(anchors.electricity_mwh, 259577000.0, "official HEESI total")
    near(anchors.producer_mwh, 146823000.0, "official HEESI nonhousehold")
    print("PASS: sourced HEESI 2020 end-use ledger, household explicitly separate")

    physical_total = 1000*159121/0.613
    ledger = reconcile_heesi_with_db(physical_total, anchors)
    near(ledger["rounding_residual_mwh"], physical_total-259577000, "FEC rounding")
    assert abs(ledger["rounding_residual_mwh"]) < 500
    need_failure(lambda: reconcile_heesi_with_db(physical_total+3000, anchors), "tolerance")
    print("PASS: FEC/HEESI reconciliation; excess difference fails closed")

    real_io = load_domestic_io(2020, dataset_path(2020))
    assert real_io.leontief_error < 1e-10
    sectors, fec, conv, factors = mock_db_tables()
    sectors.loc[sectors.sector_code == "158", "sector_name"] = "Land transportation services other than rail transport"
    with TemporaryDirectory() as directory:
        tmp = Path(directory)
        path = tmp/"sector_mapping_2020_review.csv"
        write_mapping_template(sectors, path)
        m = pd.read_csv(path, dtype=str, keep_default_na=False)
        assert len(m) == 185
        assert set(m.review_status) == {"pending"}
        assert not m.approved_category.any()
        assert m.loc[m.sector_code == "145", "suggested_category"].iloc[0] == "excluded"
        assert m.loc[m.sector_code == "158", "suggested_category"].iloc[0] == "transport"
        assert m.loc[m.sector_code == "114", "suggested_category"].iloc[0] == "industry"
        need_failure(lambda: write_mapping_template(sectors, path))
        need_failure(lambda: read_sector_mapping(path, sectors), "pending")
        provisional = read_sector_mapping(path, sectors, allow_provisional=True)
        assert provisional.pending_count == 185
        assert provisional.is_provisional
        print("PASS: 185 unreviewed suggestions, gated approval, no template overwrite")

        base = build_domestic_research_model(real_io, sectors, fec, conv, factors)
        copied_energy = base.mat_finCons.copy()
        copied_factors = base.CO2_EF.copy()
        calibrated = {}
        for method in ALLOCATION_METHODS:
            model, allocation, bookkeeping = enduse_calibrated_model(base, anchors, provisional, method)
            calibrated[method] = model
            assert len(allocation) == 185
            assert model.mat_finCons[3, 144] == 0
            near(float(model.mat_finCons[3].sum()), 146823000, "nonhousehold electricity")
            for cat in ("industry", "commercial", "transport"):
                use = float(allocation.loc[allocation.heesi_enduse_category == cat,
                                            "sector_electricity_mwh"].sum())
                near(use, anchors.gwh[cat]*1000, f"HEESI {cat} conserved")
            near(float(model.mat_finCons[3].sum()) + bookkeeping["household_mwh"] +
                 bookkeeping["rounding_residual_mwh"], physical_total,
                 "full national electricity balance")
            assert np.array_equal(model.mat_finCons[:3, :], base.mat_finCons[:3, :])
            assert np.array_equal(model.io.y, real_io.y)
        assert not np.array_equal(calibrated["transactions"].mat_finCons[3],
                                  calibrated["coefficients"].mat_finCons[3])
        print("PASS: both within-category monetary proxies; exact category conservation")

        # Verify full nine-scenario run with explicit human-review warning.
        r = execute_stage3(real_io, sectors, fec, conv, factors,
                           anchors, provisional, target_codes=("114",))
        assert len(r.scenarios) == 18
        assert len(r.comparison) == 18
        assert len(r.allocations) == 370
        assert len(r.ledger) == 10
        for method in ALLOCATION_METHODS:
            frame = r.group_allocations[r.group_allocations.category_within_proxy == method]
            near(float(frame.electricity_mwh.sum()), 146823000, "group electric crosswalk")
        assert len(r.hotspots) == 2*4*185
        assert len(r.upstream) == 2*4*185
        for method in ALLOCATION_METHODS:
            cases = r.scenarios[r.scenarios.within_group_weights == method].set_index("case")
            assert set(cases.index) == {f"S{i}" for i in range(9)}
            baseline = cases.loc["S0"]
            _m = r.models[method]
            target_mwh = float(_m.mat_finCons[3, 113])
            ef = float(_m.CO2_EF[3,0])
            # INDEPENDENT equations; not from the runner's scenario calculations.
            near(cases.loc["S2","screening_reduction_vs_S0_mtco2"],
                 0.2*target_mwh*ef/1e6, "S2 saving")
            near(cases.loc["S8","screening_reduction_vs_S0_mtco2"],
                 (0.4*physical_total+0.12*target_mwh)*ef/1e6, "S8 saving")
            near(baseline["all_enduse_electricity_indicator_mtco2"],
                 physical_total*ef/1e6, "national electricity indicator at baseline")
            near(baseline["household_electricity_indicator_mtco2"],
                 112754000*ef/1e6, "household separate")
            near(baseline["electricity_proxy_mtco2"],
                 146823000*ef/1e6, "production electricity separate")
            near(baseline["fossil_plus_all_enduse_screening_mtco2"],
                 276.286304, "Stage2 screen baseline benchmark", atol=2e-5)
        assert np.array_equal(base.mat_finCons, copied_energy)
        assert np.array_equal(base.CO2_EF, copied_factors, equal_nan=True)
        print("PASS: S0-S8 scenario algebra, separate household ledger, Stage 2 baseline preserved")

        for case, group in r.hotspots.groupby(["case", "within_group_weights"]):
            near(group.origin_inventory_tco2.sum(), group.embodied_final_demand_tco2.sum(),
                 f"production-only footprint reconciliation {case}", atol=1e-2)
            assert sum(group.final_demand_million_idr < 0) == 2
        print("PASS: production-only Leontief embodied footprints, signed demand unchanged")

        destination = tmp/"exports"
        paths = save_stage3_outputs(r, destination)
        assert len(paths) == 9
        assert all(p.is_file() for p in paths)
        need_failure(lambda: save_stage3_outputs(r, destination), "overwrite")
        meta = json.loads(next(p for p in paths if p.suffix==".json").read_text())
        assert meta["unreviewed_sector_count"] == 185
        assert meta["io_csv_sha256"] == real_io.csv_sha256
        assert meta["heesi_anchor_sha256"] == anchors.sha256
        assert meta["sector_mapping_sha256"] == provisional.sha256
        print("PASS: provenance hashes, export once, overwrite guard")

        # Approval workflow rejects unaudited/malformed mappings.
        m.loc[m.sector_code == "145", "suggested_category"] = "industry"
        tamper = tmp/"sector_mapping_bad.csv"
        m.to_csv(tamper, index=False)
        need_failure(lambda: read_sector_mapping(tamper, sectors, allow_provisional=True), "145")
        # Entirely approved synthetic mapping is allowed but not called physical validation.
        m.loc[m.sector_code == "145", "suggested_category"] = "excluded"
        m["approved_category"] = m["suggested_category"]
        m["review_status"] = "approved"
        m["reviewer"] = "synthetic-test-reviewer"
        m["review_date"] = "2026-10-16"
        m["review_evidence"] = "Synthetic test note: not official sector mapping"
        approved_path = tmp/"sector_mapping_approved.csv"
        m.to_csv(approved_path, index=False)
        reviewed = read_sector_mapping(approved_path, sectors)
        assert reviewed.is_reviewed and not reviewed.is_provisional
        print("PASS: human-review fields gate publication status; no implicit approval")

        tamper_anchors = tmp/"bad_anchor.csv"
        bad = pd.read_csv(DEFAULT_ANCHORS)
        bad.loc[0,"gwh"] = float(bad.loc[0,"gwh"]) + 5_000
        bad.to_csv(tamper_anchors, index=False)
        need_failure(lambda: read_enduse_anchors(tamper_anchors), "published GWh")
        # Opposing cell edits keep the HEESI grand total unchanged but are also rejected.
        bad.loc[0, "gwh"] = 88372+10
        bad.loc[1, "gwh"] = 112754-10
        bad.to_csv(tamper_anchors, index=False)
        need_failure(lambda: read_enduse_anchors(tamper_anchors), "published GWh")
        print("PASS: HEESI anchors immutable, including offsetting tamper")

    print("ALL REQUEST #7 STAGE 3 OFFLINE TESTS PASSED")


if __name__ == "__main__":
    run()
