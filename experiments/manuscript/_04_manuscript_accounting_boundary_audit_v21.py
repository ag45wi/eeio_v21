"""EEIO v2.1 / ICSEEA: read-only energy boundary and output-vector audit.

Use after manuscript_baseline_audit_v21.py. Run in EEIO app root:
    python manuscript_accounting_boundary_audit_v21.py

Does not modify the EEIO model, databases, scenario history, or codebase.
The existing model builder may write its normal temporary CSV files.

This audit tests three unresolved scientific questions:
  (1) Does IO column 7000 represent a compatible Leontief final-demand vector?
  (2) Is an electricity-generator's own IO transaction a final electricity use?
  (3) Do the software Scope 1 labels contain grid-electricity indirect CO2?

All 'self excluded' and conditional x1000 cases are sensitivities, not fixes.
"""

import numpy as np
import pandas as pd

from manuscript_baseline_audit_v21 import (
    allocate_electricity_by_transactions_v21,
    direct_co2_components_v21,
    eeio_emissions_from_components_v21,
    sector_metadata_v21,
)

YEAR_V21 = 2020
ELECTRICITY_INDEX = 144  # sector 145, zero-based


def _percent(numer, denom):
    return 100.0 * float(numer) / float(denom) if denom else float('nan')


def _relative_l1(a, b):
    aa, bb = np.asarray(a, float), np.asarray(b, float)
    norm = np.sum(np.abs(bb))
    return float(np.sum(np.abs(aa - bb)) / norm) if norm > 0 else float('nan')


def _summarize_sectors(title, vals, meta, p=print, count=12):
    p('\n--- ' + title + ' ---')
    for idx in np.argsort(-np.asarray(vals, float))[:count]:
        idx = int(idx)
        r = meta.iloc[idx]
        p(f"{r['sector_code']} | {r['sector_name']} | "
          f"sector_agg={r['sector_agg']} | MWh={float(vals[idx]):,.2f}")


def audit_accounting_boundaries_v21(model, df_sector, printer=print):
    """Inspect model semantics without mutating any model arrays."""
    p = printer
    meta, code_to_col = sector_metadata_v21(model, df_sector)
    n = int(model.n_sectors)
    if n != 185:
        raise ValueError(f'Expected 185 sectors, got {n}')
    if code_to_col.get('145') != ELECTRICITY_INDEX:
        raise AssertionError('Electricity model column does not match row index 144')

    A = np.asarray(model.A, dtype=float)
    L = np.asarray(model.L, dtype=float)
    x = np.asarray(model.X_total, dtype=float).reshape(-1)
    q = np.asarray(model.X_domOut, dtype=float).reshape(-1)
    if any(v.shape != (n,) for v in (x, q)):
        raise ValueError('Output vectors have unexpected shapes')
    if not all(np.all(np.isfinite(v)) for v in (x, q, A, L)):
        raise ValueError('Non-finite input array')
    if np.any(x <= 0):
        raise ValueError('Nonpositive sector gross output')

    identity = np.eye(n)
    implied_y = (identity - A) @ x
    reconstructed_x = L @ q
    identity_x = L @ implied_y

    p('\n=== ICSEEA MANUSCRIPT: ACCOUNTING BOUNDARY AUDIT (READ-ONLY) ===')
    p(f'Model year: {model.year} | IO sectors: {n}')
    p('No database edits, emission factor edits, or model matrix mutation.')

    p('\n--- IO OUTPUT AND FINAL-DEMAND VECTOR CONSISTENCY ---')
    p('Current EEIO model uses L @ X_domOut, where X_domOut is CSV column 7000.')
    p('Implied IO final-demand balance y* = (I - A) @ X_total.')
    p(f'Sum X_total (IO currency units): {x.sum():,.5f}')
    p(f'Sum X_domOut / column 7000: {q.sum():,.5f}')
    p(f'Sum implied y*: {implied_y.sum():,.5f}')
    p(f'Negative entries: column 7000={np.count_nonzero(q < 0)}, '
      f'implied y*={np.count_nonzero(implied_y < 0)}')
    p(f'Column 7000 vs implied y*: relative L1 difference: '
      f'{_relative_l1(q, implied_y)*100:,.3f}%')
    p(f'Column 7000 vs X_total: relative L1 difference: '
      f'{_relative_l1(q, x)*100:,.3f}%')
    p(f'L @ column7000 vs X_total: relative L1 difference: '
      f'{_relative_l1(reconstructed_x, x)*100:,.3f}%')
    p(f'L @ y* vs X_total (identity check): relative L1 difference: '
      f'{_relative_l1(identity_x, x)*100:,.8f}%')
    p(f'Ratio sum(L @ column7000) / sum(X_total): '
      f'{reconstructed_x.sum()/x.sum():,.6f}')
    p('CAUTION: The mathematical balance y* is a diagnostic residual, not '
      'proof that it matches an official published final-demand concept.')
    diffs = np.abs(q - implied_y)
    p('Largest discrepancies: column 7000 vs implied final-demand balance:')
    for idx in np.argsort(-diffs)[:8]:
        idx = int(idx)
        r = meta.iloc[idx]
        p(f"  {r['sector_code']} | {r['sector_name']} | "
          f"column7000={q[idx]:,.4g} | implied_y={implied_y[idx]:,.4g} | "
          f"X_total={x[idx]:,.4g}")

    old, el_z, coeff, z = allocate_electricity_by_transactions_v21(model)
    total = float(old.sum())
    own = ELECTRICITY_INDEX
    assert total > 0
    weights_a_excl = np.asarray(coeff, float).copy()
    weights_z_excl = np.asarray(z, float).copy()
    weights_a_excl[own] = 0.0
    weights_z_excl[own] = 0.0
    if weights_a_excl.sum() <= 0 or weights_z_excl.sum() <= 0:
        raise ValueError('No electricity-sector purchases outside self-sector')
    el_a_no_self = total * weights_a_excl / weights_a_excl.sum()
    el_z_no_self = total * weights_z_excl / weights_z_excl.sum()
    assert np.isclose(el_a_no_self.sum(), total, rtol=1e-12)
    assert np.isclose(el_z_no_self.sum(), total, rtol=1e-12)

    p('\n--- ELECTRICITY SECTOR SELF-ALLOCATION / FINAL-USE BOUNDARY ---')
    p(f'National FEC electricity represented: {total:,.2f} MWh')
    p(f'IO Electricity A[144,144]: {A[own, own]:,.12f}')
    p(f'Electricity own-sector allocation A: {old[own]:,.2f} MWh '
      f'({_percent(old[own], total):,.3f}%)')
    p(f'Electricity own-sector allocation Z: {el_z[own]:,.2f} MWh '
      f'({_percent(el_z[own], total):,.3f}%)')
    p(f'A-based nonself allocation: {total-old[own]:,.2f} MWh')
    p(f'Z-based nonself allocation: {total-el_z[own]:,.2f} MWh')
    p('Self-excluded A and Z are HYPOTHETICAL reallocations, not validated '
      'treatments of power-plant own use.')

    _summarize_sectors('TOP ELECTRICITY RECIPIENTS, A EXCLUDING POWER SELF',
                       el_a_no_self, meta, p)
    _summarize_sectors('TOP ELECTRICITY RECIPIENTS, Z EXCLUDING POWER SELF',
                       el_z_no_self, meta, p)

    components = direct_co2_components_v21(model, old, False)
    components_adjusted = direct_co2_components_v21(model, old, True)
    sum_direct = np.sum(components, axis=0)
    scope1_from_purchased_electricity = (components[3, :] / 1e6 / x) * q
    scope1_total = (sum_direct / 1e6 / x) * q

    p('\n--- EMISSIONS INVENTORY & SCOPE-LABEL CONSISTENCY ---')
    def diagnostic_components(label, mat):
        all_result = eeio_emissions_from_components_v21(model, mat)
        fossil_mat = mat.copy()
        fossil_mat[3, :] = 0.0
        fossil_result = eeio_emissions_from_components_v21(model, fossil_mat)
        electricity_mat = np.zeros_like(mat)
        electricity_mat[3, :] = mat[3, :]
        electricity_result = eeio_emissions_from_components_v21(model, electricity_mat)
        p(f'{label}: direct fossil combustion inventory = '
          f'{mat[:3, :].sum()/1e12:,.4f} MtCO2; '
          f'electricity-EF inventory = {mat[3, :].sum()/1e12:,.4f} MtCO2')
        p(f'{label}: EEIO weighted total = {all_result["national_mtco2"]:,.4f} MtCO2; '
          f'fossil-only = {fossil_result["national_mtco2"]:,.4f} MtCO2; '
          f'electricity-EF-only = {electricity_result["national_mtco2"]:,.4f} MtCO2')
        return all_result

    original = diagnostic_components('Current grams conventions', components)
    adjusted = diagnostic_components('Conditional Fuel/Gas x1000', components_adjusted)
    p(f'Electricity-EF emissions embedded in current model Scope 1: '
      f'{scope1_from_purchased_electricity.sum()/1e6:,.4f} MtCO2 '
      f'({_percent(scope1_from_purchased_electricity.sum(),scope1_total.sum()):,.2f}% '
      f'of model Scope 1).')
    p('ACCOUNTING FLAG: conventional GHG Scope 1 excludes emissions assigned '
      'to purchased electricity, whereas the current software Scope 1 includes '
      'the electricity-EF row. Labels require clarification.')
    p('ACCOUNTING FLAG: if electricity EF already includes upstream generation '
      'emissions, propagating it together with generation-sector combustion '
      'emissions may overlap. A physical emissions boundary is required.')

    p('\n--- SECTOR_AGG LABELS: SOURCE METADATA EXAMPLES ---')
    for group, sub in meta.groupby('sector_agg', sort=True):
        examples = '; '.join(
            f"{r['sector_code']} {r['sector_name']}"
            for _, r in sub.head(5).iterrows()
        )
        p(f"sector_agg={group!r} | count={len(sub)} | first examples: {examples}")

    p('\n--- INTERPRETIVE GATES ---')
    p('1. Verify the official Indonesian IO definition of column 7000, '
      'and reconcile it with the Leontief final-demand vector.')
    p('2. Identify the meaning of Electricity->Electricity IO transactions '
      'and whether national FEC includes that quantity.')
    p('3. Confirm factor physical units and distinguish direct combustion '
      'emissions from purchased-electricity emissions before claiming GHG scopes.')
    p('4. Only then select manufacturing hotspots and run S0-S8.')
    p('=== END ACCOUNTING BOUNDARY AUDIT ===\n')
    return {
        'implied_y': implied_y,
        'relative_l1_final_demand': _relative_l1(q, implied_y),
        'relative_l1_reconstruction': _relative_l1(reconstructed_x, x),
        'electricity_a_self_share': float(old[own]/total),
        'electricity_z_self_share': float(el_z[own]/total),
        'electricity_a_no_self': el_a_no_self,
        'electricity_z_no_self': el_z_no_self,
        'current_total': original,
        'conditional_total': adjusted,
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
        fname_io = f'io_ind_{YEAR_V21}.csv'
        model = get_eeio_model(
            cache_key=(YEAR_V21, fname_io),
            model_builder=lambda: build_model_year(
                db=db, fname_io=fname_io, year=YEAR_V21
            ),
        )
        sector_df = tbl_to_df(
            db,
            "SELECT sector_agg, sector_code, sector_name "
            "FROM sector_lst WHERE sector_label='DET'"
        )
        # Readable lookup for sector-group codes without assuming schema columns.
        try:
            agg_df = tbl_to_df(db, 'SELECT * FROM sector_agg ORDER BY id')
            print('\n=== OFFICIAL SECTOR_AGG TABLE ===')
            print(agg_df.to_string(index=False))
        except Exception as exc:
            print('Sector aggregation metadata unavailable; continuing with '
                  f'sector_lst grouping: {type(exc).__name__}: {exc}')
        audit_accounting_boundaries_v21(model, sector_df)


if __name__ == '__main__':
    main()
