"""Reproducible offline tests for Stage 2 Request #7.

Uses ACTUAL 2016 and 2020 BPS domestic CSVs with SYNTHETIC MySQL-like
sector metadata and the user's disclosed factor/FEC values. No DB access.

Run from app root:
    python test_request7_stage2_environmental_audit_v21.py
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json

import numpy as np

# Helper builds the same read-only synthetic tables used by Request #7 tests.
from test_request7_domestic_eeio_v21 import (
    dataset_path, ensure_real_scenario_module, mock_db_tables
)
from request7_domestic_eeio_v21 import (
    load_domestic_io, emission_components_tonnes
)
from request7_stage2_environmental_audit_v21 import (
    MODES, ELECTRICITY_SOURCE_SECTOR, STUDY_CASES,
    build_electricity_allocation_variants, execute_stage2,
    factor_unit_hypotheses, save_stage2_outputs,
    show_stage2_summary
)

REAL_34_SECTORS = (
    "077 078 079 080 081 082 083 084 085 095 096 097 098 099 100 101 "
    "102 103 104 105 114 115 116 117 118 119 120 121 123 124 125 126 127 131"
).split()
assert len(REAL_34_SECTORS) == 34


def _assert_close(a, b, label="", rtol=1e-10, atol=1e-6):
    if not np.isclose(a, b, rtol=rtol, atol=atol):
        raise AssertionError(f"{label}: {a:.12f} != {b:.12f}")


def run():
    ensure_real_scenario_module()
    sec, fec, conv, factors = mock_db_tables()
    ef = factors.set_index("energy_type")
    # Fully independent numerical ratio assertions, not shared with production helper.
    nominal = np.array([19.9*90.3/1000, 38.65*68.6/1000000,
                        26.8*37.3*50/1000000000, 0.877])
    expected = np.array([19.9*90.3/1000, 38.65*68.6/1000,
                         26.8*37.3*50/1000000, 0.877])

    for year in (2016, 2020):
        io = load_domestic_io(year, dataset_path(year))
        initial_hash = io.csv_sha256
        model_variants = build_electricity_allocation_variants(io, sec, fec, conv, factors)
        assert tuple(model_variants) == MODES
        originals = {method: (v.mat_finCons.copy(), v.CO2_EF.copy())
                     for method, v in model_variants.items()}
        print(f"PASS: {year} four electricity allocation variants loaded, no IO changes")
        target = ("114",)
        r = execute_stage2(io, sec, fec, conv, factors, target_codes=target)
        assert r.targets == ["114"]
        assert len(r.robustness) == 8
        assert len(r.scenarios) == 72
        assert len(r.allocation_shares) == 185 * 4
        assert len(r.allocation_pairwise) == 6
        assert len(r.group_electricity) == 4 * 5  # 5 synthetic group IDs
        assert all(r.allocation_pairwise.share_total_variation_pct >= 0)
        assert len(r.hotspot_sectors) == 185 * 4
        assert len(r.upstream_origin) == 185 * 4
        assert set(r.scenarios["case"]) == {f"S{i}" for i in range(9)}
        assert set(r.upstream_origin["case"]) == set(STUDY_CASES)
        assert io.csv_sha256 == initial_hash
        assert np.array_equal(io.y, r.models["transactions"].io.y)
        if year == 2020:
            assert sum(io.y < 0) == 2
        else:
            assert sum(io.y < 0) == 0
        print(f"PASS: {year} factor x allocation grid and signed y retained")

        expected_electric_total = 1000*159121/0.613
        for mode, model in r.models.items():
            _assert_close(model.mat_finCons[3].sum(), expected_electric_total,
                          f"{year}/{mode}: total electricity")
            assert np.all(model.mat_finCons >= 0)
            if mode.endswith("_no_self"):
                assert model.mat_finCons[3, ELECTRICITY_SOURCE_SECTOR] == 0
            else:
                assert model.mat_finCons[3, ELECTRICITY_SOURCE_SECTOR] > 0
            assert np.array_equal(model.io.x, io.x)
            assert np.array_equal(model.io.y, io.y)
            _assert_close(model.mat_finCons[:, :].sum(axis=1)[1],
                          1000*222339/6.9612, f"{year}/{mode}: fuel conserved")
        print(f"PASS: {year} electricity no-self cases conserve national totals")

        physical = r.factor_units.set_index("carrier")
        for j, carrier in enumerate(("Coal", "Fuel", "Natural Gas", "Electricity")):
            _assert_close(physical.loc[carrier, "current_tco2_per_unit"], nominal[j])
            _assert_close(physical.loc[carrier, "conditional_expected_tco2_per_unit"], expected[j])
            _assert_close(physical.loc[carrier, "expected_over_current"], expected[j]/nominal[j])
        assert physical.loc["Fuel", "expected_over_current"] > 999
        assert physical.loc["Natural Gas", "expected_over_current"] > 999
        print(f"PASS: {year} conditional fuel/gas 1000x physics audit, not assumed verified")

        g = r.robustness
        for factor_mode in ("legacy", "provisional"):
            sub = g[g.factor_mode == factor_mode]
            baseline = sub.s0_indicator_mtco2.iloc[0]
            _assert_close(sub.s0_indicator_mtco2.max(), baseline)
            _assert_close(sub.s0_indicator_mtco2.min(), baseline)
            for row in sub.itertuples(index=False):
                # INDEPENDENT equation from direct electricityEF units and
                # 3x3 scenario design; does not reuse ScenarioProcessor.
                direct_target_mt = row.target_electricity_mwh * 0.877 / 1e6
                _assert_close(row.s2_avoided_mtco2, 0.2*direct_target_mt,
                              f"{year}/{row.electricity_allocation_mode}: S2", rtol=1e-9)
                _assert_close(row.s8_avoided_mtco2,
                              0.4*(259577487.76508972*0.877/1e6) + 0.12*direct_target_mt,
                              f"{year}/{row.electricity_allocation_mode}: S8", rtol=1e-9)
        print(f"PASS: {year} independent S2/S8 avoided-emissions equations")

        for case, grp in r.hotspot_sectors.groupby("case"):
            _assert_close(grp.origin_inventory_tco2.sum(), grp.embodied_final_demand_tco2.sum(),
                          f"{year}/{case}: national attribution", atol=1e-2)
            assert np.allclose(grp.own_direct_for_final_demand_tco2 +
                               grp.upstream_for_final_demand_tco2,
                               grp.embodied_final_demand_tco2, rtol=1e-10, atol=1e-4)
            assert sorted(grp.origin_inventory_rank) == list(range(1,186))
            assert sorted(grp.embodied_final_demand_rank) == list(range(1,186))
            if year == 2020:
                assert int(grp.negative_final_demand.sum()) == 2
        for case, grp in r.upstream_origin.groupby("case"):
            _assert_close(grp.origin_direct_tco2.sum()+grp.origin_upstream_tco2.sum(),
                          grp.origin_total_tco2.sum(),
                          f"{year}/{case}: upstream attribution", atol=1e-4)
        print(f"PASS: {year} origin/direct/upstream/embodied identities for 4 scenarios")

        for mode, model in model_variants.items():
            orig_mat, orig_ef = originals[mode]
            assert np.array_equal(model.mat_finCons, orig_mat)
            assert np.array_equal(model.CO2_EF, orig_ef, equal_nan=True)
        print(f"PASS: {year} all four baseline energy/EF arrays unchanged")

        if year == 2020:
            ref = g[(g.electricity_allocation_mode == "transactions") &
                    (g.factor_mode == "legacy")].iloc[0]
            _assert_close(ref.target_electricity_mwh, 3_805_487.069, "2020 target 114", atol=0.01)
            _assert_close(ref.s0_indicator_mtco2, 276.286304, "2020 S0", atol=1e-5)
            _assert_close(ref.s8_indicator_mtco2, 184.826032, "2020 S8", atol=1e-5)
            _assert_close(ref.electricity_self_share_pct, 59.44, "2020 self share", rtol=5e-4)
            print("PASS: 2020 regression matches user MySQL log S0/S8 and target electricity")

            # Run the 34 exact sector IDs from the user's real MySQL printout.
            # These user-grounded targets are not inferred from synthetic group labels.
            big = execute_stage2(io, sec, fec, conv, factors,
                                 target_codes=tuple(REAL_34_SECTORS))
            br = big.robustness[(big.robustness.electricity_allocation_mode == "transactions") &
                                (big.robustness.factor_mode == "legacy")].iloc[0]
            _assert_close(br.target_electricity_mwh, 13_488_841.739, "2020 34 target MWh", atol=0.01)
            _assert_close(br.s8_indicator_mtco2, 183.806956, "2020 34 target S8", atol=1e-5)
            print("PASS: 34-sector benchmark matches real user log S8 and total target MWh")

            # Test preservation of previous stage reports and export guard.
            with TemporaryDirectory() as tmp:
                paths = save_stage2_outputs(r, Path(tmp))
                assert len(paths) == 10
                assert all(p.exists() for p in paths)
                assert json.loads([p for p in paths if p.suffix==".json"][0].read_text())[
                    "io_sha256"] == io.csv_sha256
                assert len([p for p in paths if p.name.endswith("scenarios_all_modes.csv")]) == 1
                try:
                    save_stage2_outputs(r, tmp)
                except FileExistsError:
                    pass
                else:
                    raise AssertionError("Stage2 export must never overwrite a previous result")
                assert len(list(Path(tmp).iterdir())) == 10
            print("PASS: 2020 ten Stage 2 files exported with provenance; overwrite refused")

    print("ALL REQUEST #7 STAGE 2 ENVIRONMENTAL AUDIT TESTS PASSED")


if __name__ == "__main__":
    run()
