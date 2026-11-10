"""
ICSEEA manuscript / EEIO v2.1: read-only baseline and allocation audit.

Run from the existing Flask EEIO application root:
    python manuscript_baseline_audit_v21.py

Requires the application's existing eeio_engine.py, util_mat.py, util_db.py,
static/data/2020/io_ind_2020.csv, MySQL settings, Flask and dotenv.

No INSERT/UPDATE/DELETE, scenario execution, matrix mutation, or report export.
The existing build_model_year() may write its established static/buf/*/tmp
intermediate CSV files when building an uncached model; this script does not
modify that builder or add any other persistence.

Four diagnostic (not publication-calibrated) model variants:
  A0  Current allocation and current emissions-unit implementation.
  Z0  Reallocate ONLY electricity using transaction-row shares Z[145,j].
  A1  Current allocation with Fuel and Natural Gas direct grams x1000.
  Z1  Both diagnostic changes together.

The factor x1000 checks are CONDITIONAL on original heating-value and
emission-factor units; they must NOT be adopted as code fixes until those
units are independently verified from the data source.
"""

import numpy as np
import pandas as pd

YEAR_V21 = 2020
ELECTRICITY_SOURCE_ROW_V21 = 144           # 0-based; IO sector 145
FUEL_SOURCE_ROW_V21 = 37                  # current model only
FUEL_REFINERY_CANDIDATE_ROW_V21 = 94      # IO sector 095; exploratory
TOP_N_V21 = 20

# Explicit illustrative sector list, not a manufacturing classification.
# These are some sectors whose names indicate industrial/material production.
# Results for ALL 185 sectors are printed separately.
INDUSTRIAL_WATCHLIST_V21 = (
    "095", "096", "097", "098", "104", "108", "118"
)


def sector_code_v21(code):
    s = str(code).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s.zfill(3)


def sector_metadata_v21(model, df_sector):
    """Return code/name/group in actual model COLUMN order; check completeness."""
    n = int(model.n_sectors)
    if not hasattr(model, "sector_column_map"):
        raise ValueError("Model lacks sector_column_map; check build_model_year")
    code_to_col = {
        sector_code_v21(code): int(col)
        for code, col in model.sector_column_map.items()
    }
    if len(code_to_col) != n or set(code_to_col.values()) != set(range(n)):
        raise ValueError("sector_column_map does not cover exactly the IO columns")

    meta = pd.DataFrame({
        "column": np.arange(n, dtype=int),
        "sector_code": [""] * n,
        "sector_name": [""] * n,
        "sector_agg": [""] * n,
    })
    for record in df_sector.to_dict(orient="records"):
        code = sector_code_v21(record["sector_code"])
        if code not in code_to_col:
            continue
        col = code_to_col[code]
        meta.loc[col, "sector_code"] = code
        meta.loc[col, "sector_name"] = str(record["sector_name"]).strip()
        meta.loc[col, "sector_agg"] = str(record.get("sector_agg", "")).strip()
    if (meta["sector_code"] == "").any():
        raise ValueError("Missing sector labels; cannot safely label results")
    return meta, code_to_col


def allocate_electricity_by_transactions_v21(model):
    """Economic IO proxy: w_j=Z[145,j]/sum(Z[145,:]); Z=A*X_total."""
    n = int(model.n_sectors)
    if model.A.shape != (n, n) or model.mat_finCons.shape != (4, n):
        raise ValueError("Unexpected IO / final energy matrix dimensions")
    x = np.asarray(model.X_total, dtype=float).reshape(-1)
    if x.shape != (n,) or not np.all(np.isfinite(x)) or np.any(x <= 0):
        raise ValueError("X_total invalid; positive sector outputs required")

    a = np.asarray(model.A[ELECTRICITY_SOURCE_ROW_V21, :], dtype=float)
    z = a * x  # Exactly the corresponding transaction row if A=Z/X_total.
    if np.any(~np.isfinite(z)) or np.min(z) < -1e-8 or z.sum() <= 0:
        raise ValueError("Invalid electricity transactions or zero row total")
    if np.any(z < 0):
        raise ValueError("Negative electricity transaction: manual review needed")

    old = np.asarray(model.mat_finCons[3, :], dtype=float)
    if np.any(~np.isfinite(old)) or np.any(old < 0):
        raise ValueError("Invalid existing electricity consumption")
    total = float(old.sum())
    replacement = total * z / float(z.sum())
    if not np.isclose(replacement.sum(), total, rtol=1e-12, atol=1e-5):
        raise AssertionError("National electricity conservation failed")
    return old.copy(), replacement, a.copy(), z.copy()


def direct_co2_components_v21(model, electricity_mwh, unit_factor_test=False):
    """Compute fresh gram-valued arrays; do not write to existing model."""
    from eeio_engine import get_mat_finConsCO2

    mat = np.asarray(model.mat_finCons, dtype=float).copy()
    ef = np.asarray(model.CO2_EF, dtype=float).copy()
    if mat.shape[0] != 4 or ef.shape[0] != 4:
        raise ValueError("Expected four energy rows")
    electricity_mwh = np.asarray(electricity_mwh, dtype=float)
    if electricity_mwh.shape != (model.n_sectors,):
        raise ValueError("Unexpected electricity array shape")
    mat[3, :] = electricity_mwh
    components = np.asarray(get_mat_finConsCO2(mat, ef), dtype=float)
    if components.shape != (4, model.n_sectors):
        raise AssertionError("Unexpected direct CO2 component shape")
    if unit_factor_test:
        # Only for conditional unit-sensitivity, NOT verified corrections.
        components[1, :] *= 1000.0  # Fuel
        components[2, :] *= 1000.0  # Natural Gas
    if not np.all(np.isfinite(components)):
        raise ValueError("Non-finite emissions calculated")
    return components


def eeio_emissions_from_components_v21(model, components):
    """Replicate EEIOModel.calculate_baseline algebra without modifying B."""
    x = np.asarray(model.X_total, dtype=float).reshape(-1)
    dom = np.asarray(model.X_domOut, dtype=float).reshape(-1)
    if x.shape != dom.shape or np.any(x <= 0):
        raise ValueError("IO output vectors incompatible")
    b = (np.sum(components, axis=0) / 1_000_000.0) / x
    b_el = (components[3, :] / 1_000_000.0) / x
    # B @ L, B @ I and B_El @ A: same conventions as EEIOModel.
    total = (b @ model.L) * dom
    scope1 = b * dom
    scope2 = (b_el @ model.A) * dom
    scope3 = total - scope1 - scope2
    return {
        "national_mtco2": float(np.sum(total) / 1e6),
        "scope1_mtco2": float(np.sum(scope1) / 1e6),
        "scope2_mtco2": float(np.sum(scope2) / 1e6),
        "scope3_mtco2": float(np.sum(scope3) / 1e6),
        "direct_inventory_mtco2": float(np.sum(components) / 1e12),
        "source_contributions_mtco2": (
            np.sum(components, axis=1) / 1e12
        ),
        "sector_total_t": np.asarray(total, dtype=float),
    }


def audit_baseline_v21(model, df_sector, top_n=TOP_N_V21, printer=print):
    """Pure read-only matrix checks and scenario-free counterfactuals."""
    meta, code_to_col = sector_metadata_v21(model, df_sector)
    electricity_A, electricity_Z, row_A, row_Z = (
        allocate_electricity_by_transactions_v21(model)
    )
    n = model.n_sectors

    # Compare with the actual current implementation before evaluating cases.
    original = model.calculate_baseline()
    cases = {}
    for key, electricity, adjusted in (
        ("A0 current", electricity_A, False),
        ("Z0 electricity Z", electricity_Z, False),
        ("A1 provisional units", electricity_A, True),
        ("Z1 both provisional", electricity_Z, True),
    ):
        components = direct_co2_components_v21(model, electricity, adjusted)
        cases[key] = eeio_emissions_from_components_v21(model, components)

    reconstructed = cases["A0 current"]["sector_total_t"]
    actual = np.asarray(original["totalEmission"], dtype=float).reshape(-1)
    if actual.shape != (n,) or not np.allclose(
        reconstructed, actual, rtol=1e-9, atol=0.01
    ):
        raise AssertionError(
            "A0 does not reconstruct the current EEIO baseline; "
            "stop and inspect calculation-engine version"
        )

    def label(column):
        r = meta.iloc[int(column)]
        return f'{r.sector_code} | {r.sector_name}'

    printer("\n=== MANUSCRIPT BASELINE VALIDATION (READ-ONLY) ===")
    printer(f"Year: {model.year} | IO sectors: {n}")
    printer("Current EEIO national emissions (MtCO2): "
            f"{np.sum(actual) / 1e6:,.6f}")
    printer("Recomputed A0 emission baseline matches current engine: YES")
    printer("Electricity source row: 145 | " + label(144))
    printer("Economic allocation weights: A[144,j] versus Z[144,j]=A[144,j]*X_total[j]")
    printer("No IO structural coefficients, scenario history, or EF values changed")

    total_electricity = float(electricity_A.sum())
    if total_electricity <= 0:
        raise ValueError("No positive baseline electricity allocation")
    expected_A = total_electricity * row_A / row_A.sum()
    max_A_mwh_delta = float(np.max(np.abs(expected_A - electricity_A)))
    shares_A = electricity_A / total_electricity
    shares_Z = electricity_Z / total_electricity
    ranks_A = pd.Series(-electricity_A).rank(method="min").astype(int).to_numpy()
    ranks_Z = pd.Series(-electricity_Z).rank(method="min").astype(int).to_numpy()
    # Normalize rank=1 highest; rank calculated on negatives.
    diffs = shares_Z - shares_A
    total_variation = 0.5 * float(np.abs(diffs).sum()) * 100.0
    spearman = pd.Series(ranks_A).corr(pd.Series(ranks_Z), method="spearman")
    pearson = float(np.corrcoef(shares_A, shares_Z)[0, 1])
    top10_A = set(np.argsort(-electricity_A)[:10])
    top10_Z = set(np.argsort(-electricity_Z)[:10])

    printer("\n--- ELECTRICITY ALLOCATION METHOD SENSITIVITY ---")
    printer(f"Current total electricity: {total_electricity:,.4f} MWh")
    printer(f"Max difference: existing electricity vs reconstructed A weights "
            f"= {max_A_mwh_delta:,.8f} MWh")
    if not np.allclose(expected_A, electricity_A, rtol=1e-8, atol=0.01):
        printer("WARNING: current electricity allocation no longer closely "
                "matches A weights. Check the model-builder assumptions.")
    printer(f"Z-based total electricity: {electricity_Z.sum():,.4f} MWh")
    printer(f"Current method: positive sector allocations={int(np.sum(electricity_A > 0))}")
    printer(f"Z-based method: positive sector allocations={int(np.sum(electricity_Z > 0))}")
    printer(f"Allocation total variation distance: {total_variation:.3f} percentage points")
    printer(f"Sector share correlation (Pearson): {pearson:.6f}")
    printer(f"Sector rank correlation (Spearman): {spearman:.6f}")
    printer(f"Top-10 overlap: {len(top10_A & top10_Z)}/10")

    def print_top(title, data, ranks_primary, ranks_other):
        printer("\n--- " + title + " ---")
        for col in np.argsort(-data)[:min(n, top_n)]:
            col = int(col)
            printer(
                f"{label(col)} | MWh={data[col]:,.2f} | "
                f"share={data[col]/total_electricity*100:.3f}% | "
                f"rank_here={ranks_primary[col]} | rank_other={ranks_other[col]}"
            )

    print_top("TOP ELECTRICITY RECIPIENTS: CURRENT A-WEIGHTS",
              electricity_A, ranks_A, ranks_Z)
    print_top("TOP ELECTRICITY RECIPIENTS: TRANSACTION Z-WEIGHTS",
              electricity_Z, ranks_Z, ranks_A)

    printer("\n--- LARGEST SECTOR REALLOCATIONS (Z minus A) ---")
    delta = electricity_Z - electricity_A
    for col in np.argsort(-np.abs(delta))[:min(n, 15)]:
        col = int(col)
        printer(
            f"{label(col)} | A_MWh={electricity_A[col]:,.2f} | "
            f"Z_MWh={electricity_Z[col]:,.2f} | "
            f"delta_MWh={delta[col]:+,.2f} | "
            f"ranks={ranks_A[col]}->{ranks_Z[col]}"
        )

    printer("\n--- SECTOR_AGG GROUPS: FOR DEFINING MANUFACTURING ---")
    printer("No manufacturing classification is inferred from numeric codes.")
    group = meta.assign(A_MWh=electricity_A, Z_MWh=electricity_Z)
    group = group.groupby("sector_agg", dropna=False, as_index=False).agg(
        count=("sector_code", "size"),
        A_MWh=("A_MWh", "sum"),
        Z_MWh=("Z_MWh", "sum"),
    ).sort_values("Z_MWh", ascending=False)
    for _, r in group.iterrows():
        printer(
            f"sector_agg={r['sector_agg']!r} | n={int(r['count'])} | "
            f"A_MWh={r['A_MWh']:,.2f} | Z_MWh={r['Z_MWh']:,.2f}"
        )

    printer("\n--- EXPLORATORY INDUSTRIAL WATCHLIST (NOT EXHAUSTIVE) ---")
    for code in INDUSTRIAL_WATCHLIST_V21:
        if code not in code_to_col:
            continue
        col = code_to_col[code]
        printer(
            f"{label(col)} | A_MWh={electricity_A[col]:,.2f} | "
            f"Z_MWh={electricity_Z[col]:,.2f} | "
            f"rank_A={ranks_A[col]} | rank_Z={ranks_Z[col]}"
        )

    printer("\n--- FOUR BASELINE SENSITIVITY CASES ---")
    printer("Units: MtCO2, calculated with unchanged A, L, X_total and X_domOut")
    printer("A0=current; Z0=electricity transactions; A1=conditional Fuel/Gas x1000; Z1=both")
    base = cases["A0 current"]["national_mtco2"]
    for key, res in cases.items():
        printer(
            f"{key}: national={res['national_mtco2']:,.6f} | "
            f"delta_vs_A0={res['national_mtco2']-base:+,.6f} | "
            f"scope1={res['scope1_mtco2']:,.6f} | "
            f"scope2={res['scope2_mtco2']:,.6f} | "
            f"scope3={res['scope3_mtco2']:,.6f} | "
            f"direct_inventory={res['direct_inventory_mtco2']:,.6f}"
        )

    printer("\n--- DIRECT CO2 BY ENERGY CARRIER, BEFORE LEONTIEF PROPAGATION ---")
    for key in ("A0 current", "A1 provisional units"):
        values = cases[key]["source_contributions_mtco2"]
        printer(key + ": " + " | ".join(
            f"{name}={v:,.6f} MtCO2" for name, v in zip(
                ("Coal", "Fuel", "Natural Gas", "Electricity"), values
            )
        ))
    printer("NOTE: inventory sum and national modeled EEIO emissions need "
            "not be equal because the latter uses L and domestic output.")

    printer("\n--- SOURCE-UNIT CHECKS: CONDITIONAL ONLY ---")
    f = np.asarray(model.CO2_EF, dtype=float)
    fuel_expected_t_per_kl = f[1, 0] * f[1, 1] / 1000.0
    fuel_current_t_per_kl = f[1, 0] * f[1, 1] / 1_000_000.0
    gas_expected_t_per_mmbtu = f[2, 2] * f[2, 0] * f[2, 1] / 1e6
    gas_current_t_per_mmbtu = f[2, 2] * f[2, 0] * f[2, 1] / 1e9
    printer(f"Fuel: assuming HHV={f[1,0]:g} GJ/kL and EF={f[1,1]:g} "
            f"tCO2/TJ, physical expectation={fuel_expected_t_per_kl:.8f} "
            f"tCO2/kL, current engine={fuel_current_t_per_kl:.8f} tCO2/kL")
    printer(f"Natural Gas: assuming multiplier={f[2,2]:g} m3/MMBTU, "
            f"HHV={f[2,0]:g} MJ/m3, EF={f[2,1]:g} tCO2/TJ; "
            f"expectation={gas_expected_t_per_mmbtu:.8f} tCO2/MMBTU, "
            f"current engine={gas_current_t_per_mmbtu:.8f} tCO2/MMBTU")
    printer("These are conditional consistency checks. Original source "
            "documentation is required before correcting the engine.")
    printer(f"Electricity: EF={f[3,0]:g} (if tCO2/MWh); conversion row "
            "already multiplies by 1,000,000 g/tCO2.")
    printer(f"Coal: HHV={f[0,0]:g}, EF={f[0,1]:g}; present row scaling "
            "appears compatible with tonnes coal, GJ/t and tCO2/TJ.")

    # Output data for isolated automated tests / optional later export.
    printer("\n=== END MANUSCRIPT BASELINE AUDIT ===\n")
    return {
        "cases": cases,
        "electricity_A": electricity_A,
        "electricity_Z": electricity_Z,
        "metadata": meta,
        "spearman_rank": float(spearman),
        "total_variation_pp": total_variation,
    }


def main():
    from flask import Flask
    from dotenv import load_dotenv
    from eeio_engine import get_eeio_model
    from util_mat import build_model_year
    from util_db import db_init, tbl_to_df

    load_dotenv()
    app = Flask(__name__)
    db = db_init(app)
    with app.app_context():
        year = YEAR_V21
        name = f"io_ind_{year}.csv"
        model = get_eeio_model(
            cache_key=(year, name),
            model_builder=lambda: build_model_year(
                db=db, fname_io=name, year=year
            ),
        )
        sector_df = tbl_to_df(
            db,
            "SELECT sector_agg, sector_code, sector_name "
            "FROM sector_lst WHERE sector_label='DET'"
        )
        # Only SELECT queries in this script. Model construction is the
        # preexisting read/build path and may write its own tmp CSVs.
        co2_df = tbl_to_df(
            db,
            "SELECT energy_type, heat_content_hhv, emission_factor, "
            "multiplier FROM co2_factor"
        )
        conversion_df = tbl_to_df(
            db,
            "SELECT energy_type, unit, multiplier_factor, note "
            "FROM conversion_factor"
        )
        print("\n=== FACTOR RECORDS USED FOR INTERPRETATION ===")
        print(co2_df.to_string(index=False))
        print(conversion_df.to_string(index=False))
        audit_baseline_v21(model, sector_df)


if __name__ == "__main__":
    main()
