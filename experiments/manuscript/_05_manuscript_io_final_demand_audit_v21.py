"""ICSEEA / EEIO v2.1: read-only official IO final-demand reconciliation.

Run from the existing EEIO application root:
    python manuscript_io_final_demand_audit_v21.py

BPS 2020 table codes (verify original CSV provenance before model changes):
  1800 total intermediate demand; 3090 total final demand;
  3100 total demand; 7000 domestic output at basic prices;
  8000 total supply.

No scenario, database mutations, or in-memory model changes. The existing
build_model_year() may write its ordinary temporary CSV files on cache miss.

Purpose: identify whether the 3090 final-demand column is compatible with
THE SAME intermediate matrix and domestic/total transaction accounting used
in the existing model. Do NOT automatically substitute 3090 for 7000.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from manuscript_baseline_audit_v21 import (
    sector_metadata_v21,
)

YEAR_V21 = 2020
SOURCE_CODES = ('1800', '3090', '3100', '7000', '8000')
CONTEXT_CODES = (
    '3011', '3012', '3020', '3030', '3040', '3050', '3060',
    '4011', '4012', '4013', '4019', '5019', '6090',
)


def _rel_l1(observed, reference):
    a = np.asarray(observed, dtype=float).reshape(-1)
    b = np.asarray(reference, dtype=float).reshape(-1)
    denominator = float(np.sum(np.abs(b)))
    return float(np.sum(np.abs(a-b)) / denominator) if denominator else float('nan')


def _as_numeric_column(df, code, n, log=print):
    """Parse one BPS-named column, reporting dashes instead of concealing them."""
    if code not in df.columns:
        return None
    src = df[code].iloc[:n]
    if len(src) != n:
        raise ValueError(f'CSV column {code} contains {len(src)} instead of {n} rows')
    clean = src.astype('string').str.strip().str.replace(',', '', regex=False)
    clean = clean.str.replace('\u2212', '-', regex=False)
    clean = clean.str.replace(r'\s+', '', regex=True)
    placeholders = clean.isin(['', '-', '\u2013', '\u2014']) | clean.isna()
    count_placeholders = int(placeholders.sum())
    if count_placeholders:
        log(f'  CODE {code}: {count_placeholders} blank/dash placeholders interpreted '
            'as zero for DIAGNOSTIC PURPOSES ONLY')
    clean = clean.mask(placeholders, '0')
    values = pd.to_numeric(clean, errors='coerce')
    bad = int(values.isna().sum())
    if bad:
        examples = src.loc[values.isna()].head(3).tolist()
        raise ValueError(f'Column {code} contains {bad} nonnumeric cells: {examples}')
    result = values.to_numpy(dtype=float)
    if not np.isfinite(result).all():
        raise ValueError(f'Column {code} contains nonfinite values')
    return result


def audit_official_final_demand_v21(model, df_io, df_sector, printer=print):
    """Audit CSV row-aggregate columns against A, x, L; return read-only data."""
    p = printer
    meta, _ = sector_metadata_v21(model, df_sector)
    n = int(model.n_sectors)
    A = np.asarray(model.A, dtype=float)
    L = np.asarray(model.L, dtype=float)
    x = np.asarray(model.X_total, dtype=float).reshape(-1)
    q = np.asarray(model.X_domOut, dtype=float).reshape(-1)
    if A.shape != (n, n) or L.shape != (n, n) or x.shape != (n,) or q.shape != (n,):
        raise ValueError('Unexpected model dimensions')
    if not all(np.isfinite(v).all() for v in (A, L, x, q)) or np.any(x <= 0):
        raise ValueError('Invalid model arrays or nonpositive outputs')

    z = A * x[np.newaxis, :]
    inter_model = z.sum(axis=1)
    y_star = x - inter_model
    raw = {code: _as_numeric_column(df_io, code, n, p)
           for code in SOURCE_CODES + CONTEXT_CODES}
    missing = [code for code in ('1800', '3090', '7000') if raw[code] is None]
    if missing:
        raise ValueError('Required BPS accounting columns missing: ' + ', '.join(missing))
    intermediate = raw['1800']
    final_official = raw['3090']
    gross_domestic = raw['7000']

    p('\n=== ICSEEA: OFFICIAL IO FINAL-DEMAND RECONCILIATION (READ-ONLY) ===')
    p(f'Year={model.year} | sectors={n} | monetary units as in the source CSV')
    p('BPS codes: 1800 intermediate; 3090 final; 3100 total demand; '
      '7000 domestic output; 8000 total supply')
    p('The candidate 3090 vector is NOT automatically accepted as model-compatible.')
    p('\n--- SOURCE CSV STRUCTURE ---')
    p('First header names: ' + ', '.join(str(s) for s in list(df_io.columns[:8])))
    p(f'CSV rows: {len(df_io)}; columns: {len(df_io.columns)}')
    p('Official aggregate column availability: ' +
      ', '.join(f'{code}={"YES" if raw[code] is not None else "NO"}'
                for code in SOURCE_CODES))

    p('\n--- RECONCILIATION CHECKS ---')
    comparisons = [
        ('CSV 7000 vs model gross output X_total', gross_domestic, x),
        ('CSV 7000 vs model X_domOut', gross_domestic, q),
        ('CSV 1800 vs sum intermediate transactions Z[row,:]', intermediate, inter_model),
        ('CSV 3090 vs implied y* = (I-A) @ X_total', final_official, y_star),
        ('CSV 1800 + 3090 vs model gross output', intermediate+final_official, x),
    ]
    if raw['3100'] is not None:
        comparisons.append(('CSV 1800 + 3090 vs CSV 3100',
                            intermediate+final_official, raw['3100']))
        comparisons.append(('CSV 3100 vs CSV 7000', raw['3100'], gross_domestic))
    if raw['8000'] is not None:
        comparisons.append(('CSV 8000 vs CSV 7000', raw['8000'], gross_domestic))

    for name, a, b in comparisons:
        p(f'{name}: rel.L1={100*_rel_l1(a,b):,.6f}%; '
          f'sum(a)={np.sum(a):,.2f}; sum(b)={np.sum(b):,.2f}')

    p('\n--- FINAL-DEMAND VECTOR COMPARISON ---')
    for name, vector in (
        ('current X_domOut (7000)', q),
        ('CSV final demand (3090)', final_official),
        ('implied IO balance y*', y_star),
    ):
        implied_x = L @ vector
        p(f'{name}: sum={vector.sum():,.3f}; negative_sectors={int((vector<0).sum())}; '
          f'min={vector.min():,.3f}; '
          f'Leontief_output_error={100*_rel_l1(implied_x,x):,.6f}%; '
          f'sum(L@vector)/sum(x)={implied_x.sum()/x.sum():,.6f}')

    p('\n--- LARGEST FINAL-DEMAND RESIDUALS: CSV 3090 MINUS y* ---')
    resid = final_official - y_star
    for idx in np.argsort(-np.abs(resid))[:16]:
        idx = int(idx)
        r = meta.iloc[idx]
        p(f'{r.sector_code} | {r.sector_name} | 3090={final_official[idx]:,.4g} '
          f'| y*={y_star[idx]:,.4g} | diff={resid[idx]:+,.4g} '
          f'| intermediate={inter_model[idx]:,.4g}')

    p('\n--- SELECTED ACCOUNTING-BRIDGE COLUMNS (DESCRIPTIVE ONLY) ---')
    for code in CONTEXT_CODES:
        vec = raw[code]
        if vec is not None:
            p(f'{code}: sum={vec.sum():,.5f}; negative={int((vec<0).sum())}; '
              f'nonzero={int(np.count_nonzero(vec))}')

    p('\n--- ALTERNATIVE EMISSION WEIGHTING (NO MODEL CHANGES) ---')
    # The alternate factors are reproduced algebraically from the unchanged
    # *current* Fuel/Gas gram terms, without importing the MySQL-dependent engine.
    b_current = np.asarray(model.B, dtype=float).reshape(-1)
    fec = np.asarray(model.mat_finCons, dtype=float)
    ef = np.asarray(model.CO2_EF, dtype=float)
    if fec.shape != (4, n) or ef.shape != (4, 3):
        raise ValueError('Unexpected FEC or CO2 factor shapes')
    fuel_g = fec[1, :] * ef[1, 0] * ef[1, 1]
    gas_g = fec[2, :] * ef[2, 2] * ef[2, 0] / 1000.0 * ef[2, 1]
    b_provisional = b_current + (999.0 * (fuel_g + gas_g) / 1e6 / x)
    for label, b in (
        ('CURRENT FACTOR CONVENTIONS', b_current),
        ('PROVISIONAL FUEL/GAS x1000', b_provisional),
    ):
        p(label + ':')
        upstream = b @ L
        direct_total = float((b @ x)/1e6)
        for vname, vec in (
            ('current 7000 used as demand', q),
            ('official CSV 3090 candidate', final_official),
            ('implied y*=(I-A)x', y_star),
        ):
            t = upstream * vec
            p(f'  {vname}: total={t.sum()/1e6:,.6f} MtCO2; '
              f'negative_contributions={int((t < 0).sum())}; '
              f'positive_sum={t[t>0].sum()/1e6:,.6f}; '
              f'negative_sum={t[t<0].sum()/1e6:,.6f}')
        p(f'  direct inventory B @ x: {direct_total:,.6f} MtCO2')

    p('\n--- INTERPRETATION / STOP RULES ---')
    p('1. 7000 is domestic output; do not use as a final-demand vector in BLy.')
    p('2. Check whether 3090 and 1800 reconcile with this CSV\'s domestic/total '
      'transaction and import/margin boundary. A 3090 mismatch is NOT '
      'automatically an error in the published BPS data.')
    p('3. Negative residual final-demand entries can reflect imports, trade or '
      'accounting adjustments; do not clamp them to zero.')
    p('4. The x1000 Fuel/Gas test is conditional; the physical emissions '
      'boundary and electricity double-counting still require resolution.')
    p('5. Do not run manuscript S0-S8 until an IO-consistent baseline and '
      'appropriate emissions boundary have been selected.')
    p('=== END FINAL-DEMAND RECONCILIATION ===\n')
    return {
        'y_star': y_star.copy(),
        'official_3090': final_official.copy(),
        'intermediate_1800': intermediate.copy(),
        'distance_3090_vs_y_star': _rel_l1(final_official, y_star),
        'distance_1800_vs_Zrow': _rel_l1(intermediate, inter_model),
        'distance_Leontief_3090_vs_x': _rel_l1(L@final_official, x),
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
            "FROM sector_lst WHERE sector_label='DET'"
        )
        audit_official_final_demand_v21(model, df_io, df_sector)


if __name__ == '__main__':
    main()
