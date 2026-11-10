"""Read-only import and supply-use accounting audit for ICSEEA / EEIO v2.1.

Run from the original EEIO application project root:
    python manuscript_io_import_bridge_audit_v21.py

This diagnostic DOES NOT select a new Leontief demand vector, update model
arrays, execute scenarios, or modify MySQL. The app's existing model builder
might create its usual temporary cache files on a cold build.

BPS 2020 (185-product) codes, basic-price transactions:
  1800 intermediate demand; 3011-3060 final-demand categories;
  3090 total final demand; 3100 total demand;
  4011 goods imports; 4012 services imports; 4013 import adjustment;
  4019 total imports; 5011-5013 margins/transport; 5019 total margins;
  6090 net product taxes; 7000 domestic output; 8000 total supply.

CAUTION: Values computed as 3090 minus imports are MATHEMATICAL
DIAGNOSTICS, not automatically validated domestic final demand. If Z includes
imported intermediate inputs, adjusting final demand alone is insufficient
for a domestic Leontief model.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from _03_manuscript_baseline_audit_v21 import sector_metadata_v21
from _05_manuscript_io_final_demand_audit_v21 import _as_numeric_column, _rel_l1

YEAR_V21 = 2020
FINAL_COMPONENT_CODES = ('3011', '3012', '3020', '3030', '3040', '3050', '3060')
IMPORT_COMPONENT_CODES = ('4011', '4012', '4013')
MARGIN_COMPONENT_CODES = ('5011', '5012', '5013')
ALL_CODES = (
    '1800', *FINAL_COMPONENT_CODES, '3090', '3100',
    *IMPORT_COMPONENT_CODES, '4019', *MARGIN_COMPONENT_CODES,
    '5019', '6090', '7000', '8000',
)


def _diagnostic_report(label, vec, meta, reference=None, printer=print,
                       max_rows=8, tol=1.0):
    """Show signed aggregate and largest sector residuals (no transformation)."""
    v = np.asarray(vec, dtype=float).reshape(-1)
    if not np.all(np.isfinite(v)):
        raise ValueError(f'{label}: nonfinite numerical values')
    pos = float(v[v > 0].sum())
    neg = float(v[v < 0].sum())
    printer(f'{label}: sum={v.sum():,.2f}; abs_sum={np.abs(v).sum():,.2f}; '
            f'positive_sum={pos:,.2f}; negative_sum={neg:,.2f}; '
            f'nonzero_>1={int((np.abs(v) > tol).sum())}')
    if reference is not None:
        printer(f'  relative L1 vs reference={100*_rel_l1(v, reference):,.6f}%')
    for idx in np.argsort(-np.abs(v))[:max_rows]:
        j = int(idx)
        if abs(v[j]) <= tol:
            break
        row = meta.iloc[j]
        printer(f'  {row.sector_code} | {row.sector_name} | residual={v[j]:+,.2f}')


def audit_io_import_bridge_v21(model, df_io, df_sector, printer=print):
    """Discriminate import/supply bridge from inconsistent aggregate cells."""
    p = printer
    n = int(model.n_sectors)
    meta, _ = sector_metadata_v21(model, df_sector)
    x = np.asarray(model.X_total, dtype=float).reshape(-1)
    A = np.asarray(model.A, dtype=float)
    L = np.asarray(model.L, dtype=float)
    if x.shape != (n,) or A.shape != (n, n) or L.shape != (n, n):
        raise ValueError('Model IO arrays have incompatible shapes')
    if (not np.isfinite(x).all() or not np.isfinite(A).all()
            or not np.isfinite(L).all() or (x <= 0).any()):
        raise ValueError('Nonfinite or nonpositive IO quantities')
    vals = {code: _as_numeric_column(df_io, code, n, p) for code in ALL_CODES}
    required = ('1800', '3090', '3100', '4019', '7000', '8000')
    missing = [k for k in required if vals[k] is None]
    if missing:
        raise ValueError('Missing essential CSV columns: ' + ', '.join(missing))

    def component_sum(codes):
        if any(vals[k] is None for k in codes):
            return None
        return sum((vals[k] for k in codes), np.zeros(n, dtype=float))

    z = A * x[np.newaxis, :]
    interm_z = z.sum(axis=1)
    y_star = x - interm_z
    inter = vals['1800']
    final = vals['3090']
    total_demand = vals['3100']
    imports = vals['4019']
    output = vals['7000']
    supply = vals['8000']
    final_components = component_sum(FINAL_COMPONENT_CODES)
    import_components = component_sum(IMPORT_COMPONENT_CODES)
    margin_components = component_sum(MARGIN_COMPONENT_CODES)

    p('\n=== ICSEEA: IMPORT / SUPPLY ACCOUNTING BRIDGE AUDIT (READ-ONLY) ===')
    p(f'Year={model.year}; sectors={n}; monetary units unchanged from source CSV')
    p('No assumptions that 3090-minus-imports is genuine domestic final demand.')
    p('BPS publishes distinct 185-product total-transaction and domestic-transaction tables.')
    p('\n--- MATCH EXISTING MODEL TO SOURCE ---')
    p(f'7000 vs model X_total: L1 error={100*_rel_l1(output,x):,.8f}%')
    p(f'1800 vs reconstructed intermediate row totals: '
      f'L1 error={100*_rel_l1(inter,interm_z):,.8f}%')
    p(f'sum model domestic output x = {x.sum():,.2f}')
    p(f'sum CSV total supply 8000 = {supply.sum():,.2f}')

    p('\n--- NATIONAL AGGREGATE ACCOUNTING GAPS ---')
    gaps = (
        ('Supply-minus-output: 8000 - 7000', supply - output),
        ('Total imports: 4019', imports),
        ('Published-final minus implied y*: 3090 - (x-Z1)', final - y_star),
        ('Demand components-minus-total: 1800 + 3090 - 3100',
         inter + final - total_demand),
        ('Total demand-minus-supply: 3100 - 8000', total_demand - supply),
        ('Total demand-minus-output: 3100 - 7000', total_demand - output),
    )
    for label, v in gaps:
        p(f'{label}: {float(np.sum(v)):,.2f}')
    p('A small NATIONAL difference alone does not establish a correct '
      'SECTOR-BY-SECTOR import bridge.')

    p('\n--- ROW-WISE PRIMARY ACCOUNTING IDENTITIES ---')
    checks = (
        ('1800 - reconstructed Z row sums', inter-interm_z),
        ('3100 - 8000', total_demand - supply),
        ('1800 + 3090 - 3100', inter + final-total_demand),
        ('8000 - 7000 - 4019 (supply bridge before margins/tax)',
         supply-output-imports),
        ('3090 - y* - 4019 (final-demand/import proxy)',
         final-y_star-imports),
    )
    for label, residual in checks:
        _diagnostic_report(label, residual, meta, printer=p)

    p('\n--- IMPORT, FINAL DEMAND, AND MARGIN COMPONENTS ---')
    if import_components is not None:
        _diagnostic_report('4011 + 4012 + 4013 - 4019',
                           import_components-imports, meta, printer=p)
    else:
        p('One or more import components 4011/4012/4013 are missing')
    if final_components is not None:
        _diagnostic_report('3011+3012+3020+3030+3040+3050+3060 - 3090',
                           final_components-final, meta, printer=p)
    else:
        p('One or more final-demand components are missing')
    if vals['5019'] is not None and margin_components is not None:
        _diagnostic_report('5011 + 5012 + 5013 - 5019',
                           margin_components-vals['5019'], meta, printer=p)
    else:
        p('Some margin/transport components missing; cannot check 5019')
    if vals['5019'] is not None and vals['6090'] is not None:
        _diagnostic_report('8000 - (7000 + 4019 + 5019 + 6090)',
                           supply-output-imports-vals['5019']-vals['6090'],
                           meta, printer=p)

    p('\n--- DIAGNOSTIC LEONTIEF INPUTS (NOT ADOPTED) ---')
    candidates = (
        ('7000 gross output [KNOWN NOT FINAL DEMAND]', output),
        ('3090 published final demand [unadjusted]', final),
        ('3090 - 4019 [import-only mathematical test]', final-imports),
        ('3090 - (8000 - 7000) [supply-gap mathematical test]',
         final-(supply-output)),
        ('y* = x - Z1 [exact IO residual]', y_star),
    )
    for name, y in candidates:
        lx = L @ y
        p(f'{name}: sum={y.sum():,.2f}; negative_sectors={int((y < 0).sum())}; '
          f'error L@y versus observed x={100*_rel_l1(lx,x):,.6f}%; '
          f'error y versus implied y*={100*_rel_l1(y,y_star):,.6f}%')
    p('\n--- SELECTED-ROW IMPORT BRIDGE ---')
    interesting = ('095', '096', '114', '121', '128', '145', '158', '168')
    for code in interesting:
        subset = meta.index[meta['sector_code'] == code].tolist()
        if not subset:
            continue
        j = int(subset[0])
        p(f'{code} | {meta.iloc[j].sector_name} | 3090={final[j]:,.0f} '
          f'| 4019={imports[j]:,.0f} | y*={y_star[j]:,.0f} '
          f'| (3090-4019)-y*={final[j]-imports[j]-y_star[j]:+,.0f}')

    p('\n--- INTERPRETATION / DECISION GATES ---')
    p('1. Match the source CSV explicitly against the BPS 2020 TOTAL and '
      'DOMESTIC 185-product basic-price tables.')
    p('2. If total transactions include imports in intermediate inputs, '
      'do not fix the domestic Leontief model by subtracting imports '
      'from 3090 alone: the intermediate Z/A matrix also needs the '
      'correct domestic-origin boundary.')
    p('3. Investigate row-wise deviations for 3090 vs its seven components '
      'and 1800+3090 vs 3100; never hide or force these balances.')
    p('4. Do not run paper S0-S8 or adopt Fuel/Gas corrections before '
      'unit and electricity-emission boundaries are independently verified.')
    p('=== END IMPORT / SUPPLY ACCOUNTING BRIDGE AUDIT ===\n')
    return {
        'supply_minus_output': (supply-output).copy(),
        'imports': imports.copy(),
        'published_final': final.copy(),
        'implied_final': y_star.copy(),
        'supply_vs_import_residual': (supply-output-imports).copy(),
        'final_vs_import_residual': (final-y_star-imports).copy(),
        'component_final_residual': (
            (final_components-final).copy() if final_components is not None else None
        ),
        'total_demand_residual': (inter+final-total_demand).copy(),
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
        io_name = f'io_ind_{YEAR_V21}.csv'
        model = get_eeio_model(
            cache_key=(YEAR_V21, io_name),
            model_builder=lambda: build_model_year(db=db, fname_io=io_name,
                                                   year=YEAR_V21),
        )
        df_io = pd.read_csv(f'static/data/{YEAR_V21}/{io_name}', thousands=',')
        df_sector = tbl_to_df(
            db,
            "SELECT sector_agg, sector_code, sector_name "
            "FROM sector_lst WHERE sector_label='DET'",
        )
        audit_io_import_bridge_v21(model, df_io, df_sector)


if __name__ == '__main__':
    main()
