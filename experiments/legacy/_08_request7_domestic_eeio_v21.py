"""Request #7: isolated domestic-transaction research branch for EEIO v2.1.

GOALS
-----
* Preserve the legacy EEIO v2.1 application and Request #1-#6 calculations.
* Use the BPS 185-product domestic IO matrix Z_dom and domestic final demand 3090.
* Validate x == Z_dom @ 1 + y_dom and L_dom @ y_dom == x BEFORE simulation.
* Build experimental energy allocation using clearly labelled economic proxies.
* Reuse existing v2.1 ScenarioParameter / ScenarioProcessor without DB writes.
* Run an exploratory 3x3 industrial electricity-efficiency / grid-EF experiment.

LIMITATIONS
-----------
* National FEC is distributed with IO monetary proxies. It is NOT observed
  sector-specific physical final energy consumption.
* The source data's Fuel/Gas factor units have not been independently verified.
* The electricity EF is assigned to electricity consumers while power-generation
  emissions may also be present. The combined indicator is NOT a validated
  national inventory and no conventional GHG Scope 1/2/3 is claimed.
* This file does not change existing eeio_engine.py, util_mat.py, the existing
  scenario_batch_v21.py, historical database rows, or global EEIO caches.

RUN (from Flask EEIO application root)
-------------------------------------
  python request7_domestic_eeio_v21.py --year 2020 --io-only
  python request7_domestic_eeio_v21.py --year 2020
  python request7_domestic_eeio_v21.py --year 2020 --run-scenarios
  python request7_domestic_eeio_v21.py --year 2020 --run-scenarios \\
      --target-sector 114 --allocation transactions --factor-mode legacy

Optional saved outputs (new, non-overwriting paths only):
  python request7_domestic_eeio_v21.py --year 2020 --run-scenarios \\
      --export-dir output/research/request7_domestic
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import json

import numpy as np
import pandas as pd

N_SECTORS = 185
SECTOR_CODES = tuple(f"{i:03d}" for i in range(1, N_SECTORS + 1))
ENERGY_NAMES = ("Coal", "Fuel", "Natural Gas", "Electricity")
ENERGY_TYPE_CODES = {"Coal": "01", "Fuel": "02", "Natural Gas": "03", "Electricity": "04"}
# Corresponding rows of the 185-product IO matrix (zero-based):
IO_ENERGY_SOURCE_ROWS = {"Coal": 36, "Fuel": 37, "Natural Gas": 38, "Electricity": 144}
INDUSTRIAL_GROUPS_ILLUSTRATIVE = ("07", "08", "10", "11")
VERSION = "request7-domestic-research-v1"


def normalize_code(code: object) -> str:
    s = str(code).strip()
    if s.endswith(".0"):
        s = s[:-2]
    if not s.isdigit() or not (1 <= int(s) <= N_SECTORS):
        raise ValueError(f"Invalid sector code: {code!r}")
    return f"{int(s):03d}"


def numeric_column(df: pd.DataFrame, key: str) -> np.ndarray:
    """Parse BPS data without discarding negative signs or replacing bad text."""
    if key not in df.columns:
        raise ValueError(f"IO column {key} missing")
    s = df[key].astype("string").str.strip()
    s = s.str.replace(",", "", regex=False).str.replace("−", "-", regex=False)
    s = s.mask(s.isna() | s.isin(["", "-", "—", "–"]), "0")
    vals = pd.to_numeric(s, errors="coerce")
    if vals.isna().any():
        raise ValueError(f"Invalid numeric cells in IO column {key}: "
                         f"{s[vals.isna()].head(3).tolist()}")
    out = vals.to_numpy(dtype=np.float64)
    if not np.all(np.isfinite(out)):
        raise ValueError(f"IO column {key} has non-finite values")
    return out


def relative_l1(a: np.ndarray, b: np.ndarray) -> float:
    a, b = np.asarray(a), np.asarray(b)
    denom = float(np.abs(b).sum())
    return float(np.abs(a - b).sum() / denom) if denom else float("nan")


@dataclass
class DomesticIO:
    year: int
    filename: str
    csv_sha256: str
    Z: np.ndarray
    A: np.ndarray
    L: np.ndarray
    x: np.ndarray
    y: np.ndarray
    io_identity_error: float
    leontief_error: float


def load_domestic_io(year: int, csv_path: str | Path) -> DomesticIO:
    """Strict IO reader: no cache, no temporary file, no MySQL dependency."""
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"Domestic IO CSV missing: {path}")
    df = pd.read_csv(path, dtype="string", keep_default_na=False)
    if df.columns.duplicated().any():
        raise ValueError("Duplicate IO column headers")
    required = set(SECTOR_CODES) | {"Sector_Code", "1800", "3090", "7000", "2100"}
    # 2100 is a BPS ROW code, not a required column.
    required.remove("2100")
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"Required BPS columns missing: {sorted(missing)[:10]}")

    row_codes = df["Sector_Code"].str.strip()
    for i, code in enumerate(SECTOR_CODES):
        if row_codes.iloc[i] != code:
            raise ValueError(
                f"Expected IO row {i} to have code {code}; found {row_codes.iloc[i]!r}"
            )
    if len(df) != 194 or tuple(row_codes.iloc[:N_SECTORS]) != SECTOR_CODES:
        raise ValueError("Unexpected BPS table row layout; need 185 sectors + 9 aggregate rows")
    if (row_codes == "2100").sum() != 1:
        raise ValueError("BPS IO total input row 2100 missing or duplicated")

    sectors = df.iloc[:N_SECTORS]
    z = np.column_stack([numeric_column(sectors, c) for c in SECTOR_CODES])
    x = numeric_column(sectors, "7000")
    y = numeric_column(sectors, "3090")
    intermediate = numeric_column(sectors, "1800")
    total_input_row = df.loc[row_codes == "2100"]
    input_x = np.array([
        numeric_column(total_input_row, c)[0] for c in SECTOR_CODES
    ])
    if np.any(x <= 0) or np.any(input_x <= 0):
        raise ValueError("IO sector output vector has zero or negative entries")

    def ensure_close(label: str, a: np.ndarray, b: np.ndarray) -> None:
        # Source values are integer million IDR. Allow at most 1 million IDR
        # per sector (rounding tolerance), never silently rebalance data.
        maximum = float(np.max(np.abs(a - b)))
        if maximum > 1.0:
            raise ValueError(f"{label}: non-reconciling IO data (max difference {maximum:g})")

    ensure_close("Product row intermediate sum", z.sum(axis=1), intermediate)
    ensure_close("Domestic demand identity", intermediate + y, x)
    ensure_close("BPS total-input vs output", input_x, x)

    if "3100" in df and "8000" in df:
        ensure_close("Domestic total demand", numeric_column(sectors, "3100"), x)
        ensure_close("Domestic total supply", numeric_column(sectors, "8000"), x)

    a = z / x[np.newaxis, :]
    ident_minus_a = np.eye(N_SECTORS) - a
    try:
        l = np.linalg.inv(ident_minus_a)
    except np.linalg.LinAlgError as exc:
        raise ValueError("Domestic Leontief system is singular") from exc
    output_reconstructed = l @ y
    leontief_error = relative_l1(output_reconstructed, x)
    io_error = relative_l1(intermediate + y, x)
    if not np.isfinite(leontief_error) or leontief_error > 1e-8:
        raise ValueError(f"Domestic L@y disagrees with output (rel L1={leontief_error:.3g})")
    return DomesticIO(
        year=int(year), filename=str(path.resolve()),
        csv_sha256=sha256(path.read_bytes()).hexdigest(),
        Z=z, A=a, L=l, x=x, y=y,
        io_identity_error=io_error, leontief_error=leontief_error,
    )


def _normalize_energy_rows(table: pd.DataFrame, name_col: str, required_cols: tuple[str, ...]):
    if name_col not in table or any(col not in table for col in required_cols):
        raise ValueError(f"Missing columns in database factor table: {name_col}/{required_cols}")
    normalized = table.copy()
    normalized[name_col] = normalized[name_col].astype(str).str.strip().str.casefold()
    if normalized[name_col].duplicated().any():
        raise ValueError(f"Duplicate energy names in table {name_col}")
    normalized = normalized.set_index(name_col)
    missing = {n.casefold() for n in ENERGY_NAMES} - set(normalized.index)
    if missing:
        raise ValueError(f"Missing energy types: {sorted(missing)}")
    return normalized.loc[[n.casefold() for n in ENERGY_NAMES], list(required_cols)]


def load_energy_database(db, year: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """READ-ONLY database queries. Return sector, FEC, conversion, EF tables."""
    from util_db import tbl_to_df
    sectors = tbl_to_df(db, """
        SELECT sector_agg, sector_code, sector_name
        FROM sector_lst WHERE sector_label='DET'
    """)
    fec = tbl_to_df(db, f"""
        SELECT f.year, f.fec_val, e.energy_type_name
        FROM fec_byyear AS f
        LEFT JOIN energy_lst AS e ON f.energy_type=e.energy_type_code
        WHERE f.year={int(year)}
    """)
    conv = tbl_to_df(db, """
        SELECT energy_type, unit, multiplier_factor, note FROM conversion_factor
    """)
    ef = tbl_to_df(db, """
        SELECT energy_type, heat_content_hhv, emission_factor, multiplier
        FROM co2_factor
    """)
    return sectors, fec, conv, ef


def sector_metadata(sectors_df: pd.DataFrame) -> pd.DataFrame:
    required = {"sector_code", "sector_name", "sector_agg"}
    if not required.issubset(sectors_df.columns):
        raise ValueError("sector_lst lacks required columns")
    frame = sectors_df[list(required)].copy()
    frame["sector_code"] = frame["sector_code"].map(normalize_code)
    frame["sector_agg"] = frame["sector_agg"].astype(str).str.strip().str.zfill(2)
    if len(frame) != N_SECTORS or frame["sector_code"].duplicated().any():
        raise ValueError("sector_lst detail codes must cover exactly 185 unique products")
    frame = frame.set_index("sector_code").reindex(list(SECTOR_CODES))
    if frame["sector_name"].isna().any():
        raise ValueError("sector_lst missing some BPS product codes")
    return frame.reset_index()


@dataclass
class DomesticResearchModel:
    """Not an EEIOModel; intentionally separate from legacy engine/caches."""
    io: DomesticIO
    mat_finCons: np.ndarray
    CO2_EF: np.ndarray
    sector_meta: pd.DataFrame
    allocation: str
    fuel_source: str
    national_fec: dict
    conv_factors: dict
    ef_assumption: str

    @property
    def sector_column_map(self):
        return {code: i for i, code in enumerate(SECTOR_CODES)}

    @property
    def n_sectors(self):
        return N_SECTORS

    @property
    def A(self):
        return self.io.A

    @property
    def L(self):
        return self.io.L

    @property
    def X_total(self):
        return self.io.x

    @property
    def final_demand_domestic(self):
        return self.io.y

    @property
    def year(self):
        return self.io.year


def build_domestic_research_model(
        io: DomesticIO,
        sector_df: pd.DataFrame,
        fec_df: pd.DataFrame,
        conversion_df: pd.DataFrame,
        factor_df: pd.DataFrame,
        allocation: str = "transactions",
        fuel_source: str = "crude",
) -> DomesticResearchModel:
    """Build isolated energy proxy inventory; NEVER touch legacy model state."""
    if allocation not in ("transactions", "coefficients"):
        raise ValueError("allocation must be 'transactions' or 'coefficients'")
    if fuel_source not in ("crude", "refinery"):
        raise ValueError("fuel_source must be 'crude' or 'refinery'")

    sectors = sector_metadata(sector_df)
    fec = _normalize_energy_rows(fec_df, "energy_type_name", ("fec_val",))
    conv = _normalize_energy_rows(conversion_df, "energy_type", ("multiplier_factor",))
    ef = _normalize_energy_rows(
        factor_df, "energy_type", ("heat_content_hhv", "emission_factor", "multiplier")
    )
    national_fec = pd.to_numeric(fec["fec_val"], errors="raise").to_numpy(float)
    conversions = pd.to_numeric(conv["multiplier_factor"], errors="raise").to_numpy(float)
    co2_ef = ef.apply(pd.to_numeric, errors="coerce").to_numpy(float)
    if (np.any(~np.isfinite(national_fec)) or np.any(national_fec < 0)
            or np.any(~np.isfinite(conversions)) or np.any(conversions <= 0)):
        raise ValueError("Invalid national FEC or conversion factors")
    # Required factor cells (NaNs in unused columns are legitimate).
    for r, c in ((0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1), (2, 2), (3, 0)):
        if not np.isfinite(co2_ef[r, c]) or co2_ef[r, c] <= 0:
            raise ValueError(f"Invalid CO2 factor {ENERGY_NAMES[r]} column {c}")

    row_map = dict(IO_ENERGY_SOURCE_ROWS)
    if fuel_source == "refinery":
        row_map["Fuel"] = 94
    mat = np.zeros((4, N_SECTORS), dtype=float)
    for index, energy in enumerate(ENERGY_NAMES):
        source_row = row_map[energy]
        raw_weights = (io.Z[source_row, :].copy() if allocation == "transactions"
                       else io.A[source_row, :].copy())
        if np.any(raw_weights < 0) or np.any(~np.isfinite(raw_weights)):
            raise ValueError(f"Energy allocation row contains negative/nonfinite weights: {energy}")
        total_weight = float(raw_weights.sum())
        if total_weight <= 0:
            raise ValueError(f"Energy allocation row sum <= 0: {energy}")
        # National FEC source: 1000 BOE, / BOE-per-unit -> physical units.
        mat[index, :] = (1000.0 * national_fec[index] / conversions[index]
                         * raw_weights / total_weight)
        if not np.isclose(mat[index, :].sum(),
                          1000.0 * national_fec[index] / conversions[index],
                          rtol=1e-12, atol=1e-5):
            raise AssertionError(f"FEC not conserved for {energy}")
    return DomesticResearchModel(
        io=io, mat_finCons=mat, CO2_EF=co2_ef, sector_meta=sectors,
        allocation=allocation, fuel_source=fuel_source,
        national_fec=dict(zip(ENERGY_NAMES, national_fec.tolist())),
        conv_factors=dict(zip(ENERGY_NAMES, conversions.tolist())),
        ef_assumption="unverified DB factor units",
    )


def emission_components_tonnes(mat_fincons: np.ndarray, ef: np.ndarray,
                                factor_mode: str) -> np.ndarray:
    """Physical carrier contributions, not GHG protocol scopes.

    Mirrors get_mat_finConsCO2 of current EEIO engine when factor_mode='legacy'.
    For factor_mode='provisional', Fuel and Gas emitted grams are x1000; this
    tests UNVERIFIED conventional source units, not a confirmed engine fix.
    """
    if factor_mode not in ("legacy", "provisional"):
        raise ValueError("factor_mode must be 'legacy' or 'provisional'")
    mat = np.asarray(mat_fincons, dtype=float)
    ef = np.asarray(ef, dtype=float)
    if mat.shape != (4, N_SECTORS) or ef.shape != (4, 3):
        raise ValueError("Energy and CO2 factor arrays must be 4x185 and 4x3")
    if not np.all(np.isfinite(mat)) or np.any(mat < -1e-10):
        raise ValueError("Invalid model energy consumption")
    if not np.isfinite(ef[3, 0]) or ef[3, 0] < 0:
        raise ValueError("Invalid electricity factor")
    grams = np.empty_like(mat)
    grams[0] = mat[0] * 1000.0 * ef[0, 0] * ef[0, 1]
    grams[1] = mat[1] * ef[1, 0] * ef[1, 1]
    grams[2] = mat[2] * ef[2, 2] * ef[2, 0] / 1000.0 * ef[2, 1]
    grams[3] = mat[3] * ef[3, 0] * 1_000_000.0
    if factor_mode == "provisional":
        grams[1:3, :] *= 1000.0
    # Convert grams -> tonnes, same convention as the existing engine.
    return grams / 1_000_000.0


def compute_emission_indicators(model: DomesticResearchModel,
                                mat_fincons: np.ndarray,
                                ef: np.ndarray, factor_mode: str) -> dict:
    components = emission_components_tonnes(mat_fincons, ef, factor_mode)
    # Decomposition is by emission-factor input rows, NOT GHG Scope 1/2/3.
    combustion_t = components[0:3, :].sum(axis=0)
    electricity_t = components[3, :]
    combined_t = combustion_t + electricity_t
    x, y, l = model.io.x, model.io.y, model.io.L
    def footprint(vector):
        b = vector / x
        return (b @ l) * y
    footprints = {
        "combustion": footprint(combustion_t),
        "electricity": footprint(electricity_t),
        "combined": footprint(combined_t),
    }
    # For a consistent domestic Leontief table, national total footprints and
    # source inventories are identical (though sector attribution differs).
    for name, vector in (("combustion", combustion_t),
                         ("electricity", electricity_t),
                         ("combined", combined_t)):
        observed = float(footprints[name].sum())
        expected = float(vector.sum())
        if not np.isclose(observed, expected, rtol=1e-9, atol=1e-4):
            raise AssertionError(f"Domestic accounting identity failed for {name}")
    return {
        "combined_indicator_mtco2": float(footprints["combined"].sum() / 1e6),
        "combustion_proxy_mtco2": float(footprints["combustion"].sum() / 1e6),
        "electricity_proxy_mtco2": float(footprints["electricity"].sum() / 1e6),
        "carrier_direct_mtco2": dict(zip(
            ENERGY_NAMES, (components.sum(axis=1) / 1e6).tolist()
        )),
        "footprint_by_sector_t": footprints["combined"],
        "combustion_by_sector_t": footprints["combustion"],
        "electricity_by_sector_t": footprints["electricity"],
    }


def _scenario_parameter(code: str, fraction: float, energy_code: str,
                        target_scope: str, sector_code: str | None = None):
    # Reuse the same v2.1 parameter DTO, not duplicated scenario logic.
    from scenario_engine import ScenarioParameter
    return ScenarioParameter(
        parameter_id=None, parameter_code=code, value=fraction, unit="%",
        energy_type_code=energy_code, target_scope=target_scope,
        sector_code=sector_code,
    )


def select_target_sectors(model: DomesticResearchModel,
                          target_codes: tuple[str, ...] | None,
                          target_groups: tuple[str, ...] | None) -> list[str]:
    if target_codes and target_groups:
        raise ValueError("Choose target sectors OR target groups, not both")
    if target_codes:
        codes = sorted({normalize_code(s) for s in target_codes})
    elif target_groups:
        groups = {str(g).zfill(2) for g in target_groups}
        codes = [str(row.sector_code) for row in model.sector_meta.itertuples(index=False)
                 if str(row.sector_agg) in groups]
        if not codes:
            raise ValueError(f"No sectors found in groups {sorted(groups)}")
    else:
        codes = ["114"]  # illustrative manufacturing target; NOT selected as hotspot.
    for code in codes:
        col = int(code) - 1
        if not np.isfinite(model.mat_finCons[3, col]) or model.mat_finCons[3, col] <= 0:
            raise ValueError(f"Target sector {code} has nonpositive baseline electricity")
    return codes


def create_factorial_scenario(target_sectors: list[str],
                              efficiency_pct: float, grid_ef_pct: float):
    if not (-100.0 < efficiency_pct <= 0.0 and -100.0 < grid_ef_pct <= 0.0):
        raise ValueError("Expected percentage reductions strictly between -100% and 0%")
    params = []
    if efficiency_pct:
        for code in target_sectors:
            params.append(_scenario_parameter(
                "energy_demand_change", efficiency_pct / 100.0,
                ENERGY_TYPE_CODES["Electricity"], "SECTOR_ENERGY", sector_code=code,
            ))
    if grid_ef_pct:
        params.append(_scenario_parameter(
            "emission_factor_change", grid_ef_pct / 100.0,
            ENERGY_TYPE_CODES["Electricity"], "ENERGY",
        ))
    return SimpleNamespace(
        scenario_id=None,
        scenario_name=f"Research efficiency {efficiency_pct:g}% gridEF {grid_ef_pct:g}%",
        scenario_types=[], parameters=params,
    )


def run_industrial_factorial(model: DomesticResearchModel,
                             target_sectors: list[str],
                             factor_mode: str = "legacy") -> pd.DataFrame:
    """Nine exploratory scenarios S0-S8, using original ScenarioProcessor."""
    from scenario_engine import ScenarioProcessor, ScenarioValidator
    rows = []
    baseline_energy_copy = model.mat_finCons.copy()
    baseline_ef_copy = model.CO2_EF.copy()
    # Unique row definitions in the order agreed for the ICSEEA experiment.
    cases = [("S0", 0, 0), ("S1", -10, 0), ("S2", -20, 0),
             ("S3", 0, -20), ("S4", -10, -20), ("S5", -20, -20),
             ("S6", 0, -40), ("S7", -10, -40), ("S8", -20, -40)]
    processor = ScenarioProcessor(model)
    baseline = compute_emission_indicators(model, model.mat_finCons, model.CO2_EF,
                                           factor_mode=factor_mode)
    for sid, efficiency_pct, grid_pct in cases:
        scenario = create_factorial_scenario(target_sectors, efficiency_pct, grid_pct)
        ScenarioValidator.validate(scenario)
        inputs = processor.apply(model, scenario)
        indicators = compute_emission_indicators(
            model, inputs.mat_finCons, inputs.CO2_EF, factor_mode=factor_mode
        )
        changed_energy = int(np.count_nonzero(
            ~np.isclose(inputs.mat_finCons, model.mat_finCons, rtol=1e-12, atol=1e-8)
        ))
        expected_energy = len(target_sectors) if efficiency_pct else 0
        if changed_energy != expected_energy:
            raise AssertionError(f"{sid}: unexpected energy-matrix changes: "
                                 f"{changed_energy} != {expected_energy}")
        if not np.allclose(inputs.mat_finCons[:3, :], model.mat_finCons[:3, :]):
            raise AssertionError(f"{sid}: non-electric energy rows changed unexpectedly")
        mask = np.ones(N_SECTORS, dtype=bool)
        mask[[int(s) - 1 for s in target_sectors]] = False
        if not np.allclose(inputs.mat_finCons[3, mask], model.mat_finCons[3, mask]):
            raise AssertionError(f"{sid}: non-target electricity changed unexpectedly")
        expected_ef = model.CO2_EF[3, 0] * (1 + grid_pct / 100.0)
        if not np.isclose(inputs.CO2_EF[3, 0], expected_ef):
            raise AssertionError(f"{sid}: electricity EF parameter mismatch")
        rows.append({
            "case": sid, "year": model.year, "io_type": "domestic",
            "model_version": VERSION, "allocation": model.allocation,
            "fuel_source": model.fuel_source, "factor_mode": factor_mode,
            "target_sectors": ",".join(target_sectors),
            "efficiency_pct": efficiency_pct, "grid_ef_pct": grid_pct,
            "combustion_proxy_mtco2": indicators["combustion_proxy_mtco2"],
            "electricity_proxy_mtco2": indicators["electricity_proxy_mtco2"],
            "combined_indicator_mtco2": indicators["combined_indicator_mtco2"],
            "difference_from_S0_mtco2": (
                indicators["combined_indicator_mtco2"] -
                baseline["combined_indicator_mtco2"]
            ),
            "reduction_pct_vs_S0": (
                100.0 * (baseline["combined_indicator_mtco2"] -
                         indicators["combined_indicator_mtco2"]) /
                baseline["combined_indicator_mtco2"]
                if baseline["combined_indicator_mtco2"] > 0 else float("nan")
            ),
            "changed_energy_cells": changed_energy,
        })
    if not np.array_equal(model.mat_finCons, baseline_energy_copy):
        raise AssertionError("Model energy matrix unexpectedly changed")
    if not np.array_equal(model.CO2_EF, baseline_ef_copy, equal_nan=True):
        raise AssertionError("Model emission factors unexpectedly changed")
    output = pd.DataFrame(rows)
    baseline_e = output.loc[output.case == "S0", "combined_indicator_mtco2"].iloc[0]
    lookup = {(int(row.efficiency_pct), int(row.grid_ef_pct)):
              float(row.combined_indicator_mtco2) for row in output.itertuples()}
    output["nonadditive_interaction_mtco2"] = [
        float(lookup[(int(row.efficiency_pct), 0)] +
              lookup[(0, int(row.grid_ef_pct))] - baseline_e -
              lookup[(int(row.efficiency_pct), int(row.grid_ef_pct))])
        for row in output.itertuples()
    ]
    return output


def baseline_report(model: DomesticResearchModel) -> None:
    io = model.io
    print("\n=== REQUEST #7: DOMESTIC EEIO RESEARCH PREFLIGHT ===")
    print(f"Year: {io.year} | IO: DOMESTIC (185 products) | mode: {VERSION}")
    print(f"CSV: {io.filename}")
    print(f"Source SHA256: {io.csv_sha256}")
    print(f"IO balance relative L1: {io.io_identity_error:.12%}")
    print(f"L_dom @ y_3090 vs domestic output relative L1: {io.leontief_error:.12%}")
    print(f"Domestic final demand negative sectors: {int((io.y < 0).sum())} (PRESERVED)")
    print(f"Allocation = {model.allocation!r} (ECONOMIC PROXY; NOT measured sector FEC)")
    print(f"Fuel source = {model.fuel_source!r} (unvalidated physical classification)")
    print("National FEC physical units: Coal tonne, Fuel kL, Gas MMBTU, Electricity MWh")
    print("No DB changes, no legacy cache, no application temp-buffer writes")
    for idx, name in enumerate(ENERGY_NAMES):
        print(f"  {name:12s}: {model.mat_finCons[idx].sum():,.3f} model units")
    self_share = (model.mat_finCons[3, 144] / model.mat_finCons[3].sum())
    print(f"Electricity -> Electricity sector self-allocation: {self_share:.2%} (BOUNDARY FLAG)")
    for factor_mode in ("legacy", "provisional"):
        stats = compute_emission_indicators(
            model, model.mat_finCons, model.CO2_EF, factor_mode=factor_mode
        )
        print(f"  factor_mode={factor_mode:11s} | "
              f"combustion={stats['combustion_proxy_mtco2']:,.6f} | "
              f"electricity={stats['electricity_proxy_mtco2']:,.6f} | "
              f"combined={stats['combined_indicator_mtco2']:,.6f} MtCO2")
    print("STOP RULE: Neither factor case nor combined indicator is publication-validated.")
    print("STOP RULE: Do not label carrier splits as GHG Scope 1/2/3.")


def save_exploratory_result(df: pd.DataFrame, model: DomesticResearchModel,
                            target_codes: list[str], factor_mode: str,
                            export_dir: Path):
    """Exports ONLY to a new path; exclusive creation protects old outputs."""
    export_dir.mkdir(parents=True, exist_ok=True)
    stem = (f"request7_domestic_{model.year}_{model.allocation}_"
            f"{model.fuel_source}_{factor_mode}_"
            f"sectors-{'-'.join(target_codes)}_{model.io.csv_sha256[:10]}")
    csv_path = export_dir / f"{stem}.csv"
    meta_path = export_dir / f"{stem}.json"
    if csv_path.exists() or meta_path.exists():
        raise FileExistsError("Refusing to overwrite existing research result: "
                              f"{csv_path} / {meta_path}")
    with csv_path.open("x", encoding="utf-8", newline="") as handle:
        df.to_csv(handle, index=False)
    metadata = {
        "version": VERSION, "year": model.year, "io_type": "domestic",
        "source_csv": model.io.filename, "sha256": model.io.csv_sha256,
        "allocation": model.allocation, "fuel_source": model.fuel_source,
        "factor_mode": factor_mode, "target_sectors": target_codes,
        "status": "EXPLORATORY; NOT PUBLICATION-VALIDATED",
        "limitations": [
            "Monetary IO energy allocation not physical sector measurement",
            "Fuel/Natural Gas emission source units not independently confirmed",
            "Electricity EF possibly overlaps generation combustion emissions",
            "No GHG Protocol scope labels used",
        ],
    }
    with meta_path.open("x", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2)
    print(f"Saved new research result: {csv_path}")
    print(f"Saved provenance metadata: {meta_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, choices=(2016, 2020), default=2020)
    parser.add_argument("--io", type=Path, default=None,
                        help="Path to verified domestic IO CSV, default static/data/<year>/io_ind_<year>_domestic.csv")
    parser.add_argument("--io-only", action="store_true",
                        help="Validate IO without importing Flask or connecting MySQL")
    parser.add_argument("--allocation", choices=("transactions", "coefficients"),
                        default="transactions", help="Economic allocation proxy; neither is measured sector energy")
    parser.add_argument("--fuel-source", choices=("crude", "refinery"), default="crude")
    parser.add_argument("--factor-mode", choices=("legacy", "provisional"), default="legacy")
    parser.add_argument("--target-sector", action="append", dest="target_sectors", default=None,
                        help="Repeat for more targets; default 114 (Iron and steels)")
    parser.add_argument("--target-groups", default=None,
                        help="Comma-separated sector_agg codes, e.g. 07,08,10,11; not combined with --target-sector")
    parser.add_argument("--run-scenarios", action="store_true",
                        help="Run exploratory S0-S8 (not validated national inventory estimates)")
    parser.add_argument("--export-dir", type=Path, default=None,
                        help="Optional NEW directory to save research-only scenario results")
    args = parser.parse_args(argv)
    io_path = args.io or Path(f"static/data/{args.year}/io_ind_{args.year}_domestic.csv")
    io = load_domestic_io(args.year, io_path)
    print(f"DOMESTIC IO verified: year={args.year}, sectors={N_SECTORS}, "
          f"balance={io.io_identity_error:.9%}, L@y error={io.leontief_error:.9%}")
    print(f"Source SHA256: {io.csv_sha256}")
    if args.io_only:
        if args.run_scenarios or args.export_dir:
            raise ValueError("--io-only cannot execute or export energy scenarios")
        return 0

    # App dependencies are intentionally imported ONLY when needed.
    from flask import Flask
    from dotenv import load_dotenv
    from util_db import db_init
    load_dotenv()
    app = Flask(__name__)
    db = db_init(app)
    with app.app_context():
        sectors_df, fec_df, conv_df, ef_df = load_energy_database(db, args.year)
        model = build_domestic_research_model(
            io, sectors_df, fec_df, conv_df, ef_df,
            allocation=args.allocation, fuel_source=args.fuel_source,
        )
        baseline_report(model)
        codes = select_target_sectors(
            model,
            tuple(args.target_sectors) if args.target_sectors else None,
            tuple(g.strip() for g in args.target_groups.split(",")) if args.target_groups else None,
        )
        print(f"Selected illustrative industrial target sectors: {', '.join(codes)}")
        print("Baseline target electricity (MWh): "
              f"{model.mat_finCons[3, [int(c)-1 for c in codes]].sum():,.3f}")
        if not args.run_scenarios:
            print("Research preflight complete. Add --run-scenarios for EXPLORATORY S0-S8.")
            return 0
        df = run_industrial_factorial(model, codes, factor_mode=args.factor_mode)
        display_cols = ["case", "efficiency_pct", "grid_ef_pct",
                        "combined_indicator_mtco2", "combustion_proxy_mtco2",
                        "electricity_proxy_mtco2", "reduction_pct_vs_S0",
                        "nonadditive_interaction_mtco2", "changed_energy_cells"]
        print("\n=== INDUSTRIAL ELECTRICITY x GRID-EF SCENARIOS (EXPLORATORY ONLY) ===")
        print(df[display_cols].to_string(index=False, float_format=lambda v: f"{v:,.6f}"))
        print("These are sensitivity results with UNVALIDATED FEC/CO2 boundaries.")
        if args.export_dir:
            save_exploratory_result(df, model, codes, args.factor_mode, args.export_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
