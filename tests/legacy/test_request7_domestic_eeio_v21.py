"""Offline regression tests for isolated domestic Request #7 implementation.

Run:
  python test_request7_domestic_eeio_v21.py

Tests use original uploaded 2016/2020 domestic CSV files, plus a mock READ-ONLY
energy database layer. They do not connect to or modify the user's MySQL server.
"""

from __future__ import annotations

from pathlib import Path
import copy
import json
import sys
from tempfile import TemporaryDirectory
from types import ModuleType

import numpy as np
import pandas as pd

from request7_domestic_eeio_v21 import (
    SECTOR_CODES, load_domestic_io, build_domestic_research_model,
    select_target_sectors, emission_components_tonnes,
    compute_emission_indicators, run_industrial_factorial,
    save_exploratory_result,
)

HERE = Path(__file__).resolve().parents[2]


def dataset_path(year: int, domestic: bool = True) -> Path:
    filename = f"io_ind_{year}{'_domestic' if domestic else ''}.csv"
    standard = HERE / "static" / "data" / str(year) / filename
    fallback = HERE / filename
    for path in (standard, fallback):
        if path.is_file():
            return path
    raise FileNotFoundError(
        f"Missing {filename}; copy the verified BPS dataset to "
        f"static/data/{year}/{filename}"
    )


def mock_db_tables():
    sector = pd.DataFrame({
        "sector_agg": ["08" if i==114 else "07" if i==95 else "01" if i==145 else "12" if i==158 else "14"
                       for i in range(1,186)],
        "sector_code": list(SECTOR_CODES),
        "sector_name": [f"Synthetic sector {i:03d}" for i in range(1,186)],
    })
    fec = pd.DataFrame({"energy_type_name": ["Electricity", "Fuel", "Coal", "Natural Gas"],
                        "fec_val": [159121.0, 222339.0, 113416.0, 97476.0]})
    conv = pd.DataFrame({"energy_type": ["Natural Gas", "Coal", "Electricity", "Fuel"],
                         "multiplier_factor": [0.1796, 4.2, 0.613, 6.9612]})
    factors = pd.DataFrame({
        "energy_type": ["Fuel", "Coal", "Electricity", "Natural Gas"],
        "heat_content_hhv": [38.65, 19.9, 0.877, 37.3],
        "emission_factor": [68.6, 90.3, None, 50.0],
        "multiplier": [None, None, None, 26.8],
    })
    return sector, fec, conv, factors


def ensure_real_scenario_module():
    # Import actual ScenarioProcessor and ScenarioValidator even without the
    # Flask/MySQL environment that util_db expects in this offline container.
    if "util_db" not in sys.modules:
        util_db_mock = ModuleType("util_db")
        util_db_mock.tbl_to_df = lambda *args, **kwargs: None
        sys.modules["util_db"] = util_db_mock
    import scenario_engine
    assert scenario_engine.ScenarioProcessor.__module__ == "scenario_engine"


def main():
    ensure_real_scenario_module()
    sector, fec, conv, factors = mock_db_tables()

    for year in (2016, 2020):
        path = dataset_path(year, domestic=True)
        io = load_domestic_io(year, path)
        assert io.io_identity_error == 0.0
        assert io.leontief_error < 1e-11
        assert io.Z.shape == (185, 185)
        assert np.all(io.x > 0)
        assert int((io.y < 0).sum()) == (0 if year == 2016 else 2)
        print(f"PASS: {year} BPS domestic IO reconciles with y_3090")

        total = dataset_path(year, domestic=False)
        try:
            load_domestic_io(year, total)
        except ValueError as exc:
            assert "reconcil" in str(exc)
        else:
            raise AssertionError("Total-transactions CSV should not be accepted as domestic")
        print(f"PASS: {year} total-transactions CSV rejected by fail-closed IO gate")

        for allocation in ("transactions", "coefficients"):
            for fuel_source in ("crude", "refinery"):
                model = build_domestic_research_model(
                    io, sector, fec, conv, factors,
                    allocation=allocation, fuel_source=fuel_source,
                )
                source_matrix = model.mat_finCons.copy()
                source_ef = model.CO2_EF.copy()
                assert source_matrix.shape == (4, 185)
                expected_totals = {
                    "Coal": 1000 * 113416 / 4.2,
                    "Fuel": 1000 * 222339 / 6.9612,
                    "Natural Gas": 1000 * 97476 / 0.1796,
                    "Electricity": 1000 * 159121 / 0.613,
                }
                for i, name in enumerate(("Coal", "Fuel", "Natural Gas", "Electricity")):
                    assert np.isclose(model.mat_finCons[i].sum(), expected_totals[name], rtol=1e-12)
                assert model.mat_finCons[3, 113] > 0
                assert model.mat_finCons[1, 157] == 0.0 if fuel_source == "crude" else model.mat_finCons[1, 157] > 0
                emissions_legacy = compute_emission_indicators(
                    model, model.mat_finCons, model.CO2_EF, "legacy"
                )
                emissions_provisional = compute_emission_indicators(
                    model, model.mat_finCons, model.CO2_EF, "provisional"
                )
                assert np.isclose(
                    emissions_legacy["electricity_proxy_mtco2"],
                    emissions_provisional["electricity_proxy_mtco2"], rtol=1e-12
                )
                for idx, name in [(1, "Fuel"), (2, "Natural Gas")]:
                    a = emissions_legacy["carrier_direct_mtco2"][name]
                    b = emissions_provisional["carrier_direct_mtco2"][name]
                    assert np.isclose(b, a*1000, rtol=1e-12)
                target = select_target_sectors(model, ("114",), None)
                assert target == ["114"]
                assert select_target_sectors(model, None, ("08",)) == ["114"]
                surface = run_industrial_factorial(model, target, factor_mode="legacy")
                assert list(surface["case"]) == [f"S{i}" for i in range(9)]
                assert set(surface.changed_energy_cells) == {0, 1}
                assert list(surface["changed_energy_cells"]) == [0,1,1,0,1,1,0,1,1]
                assert surface.iloc[0].combined_indicator_mtco2 > surface.iloc[-1].combined_indicator_mtco2
                assert np.all(np.isfinite(surface.combined_indicator_mtco2))
                assert np.array_equal(model.mat_finCons, source_matrix)
                assert np.array_equal(model.CO2_EF, source_ef, equal_nan=True)
                assert np.isclose(surface.iloc[0].combined_indicator_mtco2,
                                  emissions_legacy["combined_indicator_mtco2"])
                print(f"PASS: {year} {allocation}/{fuel_source} nine scenarios, "
                      "parameter separation, immutable baseline")
        if year == 2020:
            with TemporaryDirectory() as temp:
                model = build_domestic_research_model(io, sector, fec, conv, factors)
                result = run_industrial_factorial(model, ["114"])
                path = Path(temp)
                save_exploratory_result(result, model, ["114"], "legacy", path)
                c = list(path.glob("*.csv"))
                j = list(path.glob("*.json"))
                assert len(c) == 1 and len(j) == 1
                metadata = json.loads(j[0].read_text())
                assert metadata["sha256"] == io.csv_sha256
                try:
                    save_exploratory_result(result, model, ["114"], "legacy", path)
                except FileExistsError:
                    pass
                else:
                    raise AssertionError("Export must not overwrite existing outputs")
                assert len(pd.read_csv(c[0])) == 9
                print("PASS: provenance export and overwrite guard")

    # Fail on tampered CSV cells or sector row changes.
    with TemporaryDirectory() as temp:
        df = pd.read_csv(dataset_path(2020, domestic=True), dtype=str)
        df.loc[0, "3090"] = str(int(df.loc[0, "3090"]) + 2)
        modified = Path(temp) / "bad.csv"
        df.to_csv(modified, index=False)
        try:
            load_domestic_io(2020, modified)
        except ValueError as exc:
            assert "reconcil" in str(exc)
        else:
            raise AssertionError("Two-unit final-demand tampering should fail")
        print("PASS: strict IO balance gate rejects tampered CSV")

    print("ALL REQUEST #7 DOMESTIC RESEARCH TESTS PASSED")


if __name__ == "__main__":
    main()
