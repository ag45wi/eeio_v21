"""ICSEEA EEIO v2.1: read-only BPS 2020 185-product IO table-type audit.

Run in your application root (no database connection necessary):

    python manuscript_domestic_vs_total_io_audit_v21.py \
        --current static/data/2020/io_ind_2020.csv \
        --domestic static/data/2020/io_ind_2020_domestic.csv

If you have not obtained the official BPS *domestic transactions at basic prices*
185-product table, omit --domestic to diagnose the current input alone.

CAUTIONS:
  * Compare tables with the SAME sector order, year, valuation (basic prices),
    and 185-product classification. Do not mix purchaser-price tables.
  * Do not change eeio_engine.py or scenario_batch_v21.py based on this audit.
  * This does NOT establish valid physical emissions / electricity allocations.
  * The BPS columns 7000 (domestic output) and 8000 (total supply) differ when
    imports/supply adjustments are represented. 3090 is final demand in the
    selected table's accounting boundary, not an interchangeable universal y.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import numpy as np
import pandas as pd

N = 185
SECTORS = [f'{i:03d}' for i in range(1, N+1)]
AGGREGATES = ('1800','3090','3100','4011','4012','4013','4019',
              '5011','5012','5013','5019','6090','7000','8000')
FINAL_COMPONENTS = ('3011','3012','3020','3030','3040','3050','3060')


def numeric(values):
    """Convert BPS numeric cells, treating dash/blank as zero (report-only)."""
    s = pd.Series(values, dtype='string').str.strip()
    s = s.str.replace(',', '', regex=False).str.replace(r'\s+', '', regex=True)
    s = s.str.replace('−', '-', regex=False)
    s = s.mask(s.isin(['','-','–','—']) | s.isna(), '0')
    parsed = pd.to_numeric(s, errors='coerce')
    if parsed.isna().any():
        examples = list(s[parsed.isna()].head(3))
        raise ValueError(f'Non-numeric IO values: {examples}')
    out = parsed.to_numpy(dtype=float)
    if not np.all(np.isfinite(out)):
        raise ValueError('Non-finite IO values')
    return out


def relative_l1(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    den = np.sum(np.abs(b))
    return float(np.sum(np.abs(a-b))/den) if den else float('nan')


def emit(title, left, right, p=print):
    gap = left-right
    p(f'{title}: sum(left)={left.sum():,.0f}; sum(right)={right.sum():,.0f}; '
      f'net_gap={gap.sum():+,.0f}; relative_L1={100*relative_l1(left,right):.5f}%; '
      f'rows_|gap|>1={int(np.count_nonzero(np.abs(gap)>1))}')
    return gap


@dataclass
class IOTable:
    path: str
    z: np.ndarray
    x: np.ndarray
    total_input: np.ndarray
    columns: dict
    source_rows: list

    def col(self, name):
        return self.columns.get(name)


def load_bps_185(path):
    path = str(Path(path))
    df = pd.read_csv(path, dtype='string')
    df.columns = [str(c).strip() for c in df.columns]
    if 'Sector_Code' not in df:
        raise ValueError('Requires Sector_Code column (BPS 185-product format)')
    if df.columns.duplicated().any():
        raise ValueError('Duplicate column headers')
    missing = [c for c in SECTORS+['1800','3090','7000'] if c not in df.columns]
    if missing:
        raise ValueError(f'Missing BPS columns: {missing[:12]}')
    code = df['Sector_Code'].fillna('').str.strip().str.replace(r'\.0$', '', regex=True)
    code = code.map(lambda s: s.zfill(3) if s.isdigit() and int(s)<=185 else s)
    # Work with sector_code labels rather than assuming that the first 185
    # rows are valid, but require one and only one row per sector.
    hits = {s: list(df.index[code == s]) for s in SECTORS}
    bad = [s for s, inds in hits.items() if len(inds)!=1]
    if bad:
        raise ValueError(f'Missing/duplicate sector rows: {bad[:12]}')
    sector_rows = [hits[s][0] for s in SECTORS]
    sector_frame = df.iloc[sector_rows]
    z = np.column_stack([numeric(sector_frame[c]) for c in SECTORS])
    x = numeric(sector_frame['7000'])
    cols = {c: numeric(sector_frame[c]) for c in AGGREGATES+FINAL_COMPONENTS if c in df}
    outrow_idx = list(df.index[code == '2100'])
    if len(outrow_idx)==1:
        total_input = np.array([numeric([df.loc[outrow_idx[0], c]])[0] for c in SECTORS])
    else:
        # Previous v2.1 code retrieves the final CSV row; report a warning
        # if row label 2100 is unavailable rather than silently adopting it.
        print(f'WARNING: source row 2100 not uniquely identified in {path}. '
              'Falling back to final CSV row, as the existing EEIO builder does.')
        total_input = np.array([numeric(df.iloc[[-1]][c])[0] for c in SECTORS])
    if np.any(x<=0) or np.any(total_input<=0):
        raise ValueError('Nonpositive gross output / total input detected')
    return IOTable(path, z, x, total_input, cols, list(code))


def print_table_audit(t, p=print, top_n=8):
    p(f'\n=== IO TABLE: {t.path} ===')
    p(f'185-product Z={t.z.shape}, amount units follow source CSV')
    x, zi = t.x, t.z.sum(axis=1)
    intermediate = t.col('1800')
    fd = t.col('3090')
    totald = t.col('3100')
    p(f'Sum intermediate Z={zi.sum():,.0f}; final 3090={fd.sum():,.0f}; '
      f'domestic output 7000={x.sum():,.0f}')
    emit('7000 versus total input row 2100', x, t.total_input, p)
    emit('1800 versus sum(Z rows)', intermediate, zi, p)
    gap_fd = emit('1800 + 3090 versus 7000 (DOMESTIC identity)', zi+fd, x, p)
    if totald is not None:
        emit('1800 + 3090 versus 3100 (TOTAL DEMAND identity)', zi+fd, totald, p)
        emit('3100 versus 8000 (demand/supply)', totald, t.col('8000'), p) if t.col('8000') is not None else None
    if t.col('8000') is not None:
        gap_supply=t.col('8000')-x
        p(f'8000 minus 7000 = {gap_supply.sum():+,.0f} in total; '
          f'nonzero sectors={int(np.count_nonzero(np.abs(gap_supply)>1))}')
        if t.col('4019') is not None:
            emit('(8000 - 7000) versus 4019 TOTAL IMPORTS (first-order test)',
                 gap_supply,t.col('4019'), p)
        if t.col('4019') is not None and t.col('5019') is not None and t.col('6090') is not None:
            emit('8000 versus 7000 + 4019 + 5019 + 6090 (full bridge candidate)',
                 t.col('8000'),x+t.col('4019')+t.col('5019')+t.col('6090'),p)
    if all(t.col(c) is not None for c in ('4011','4012','4013','4019')):
        emit('4011 + 4012 + 4013 versus 4019 imports',
             t.col('4011')+t.col('4012')+t.col('4013'),t.col('4019'),p)
    if all(t.col(c) is not None for c in ('5011','5012','5013','5019')):
        emit('5011 + 5012 + 5013 versus 5019 margins',
             t.col('5011')+t.col('5012')+t.col('5013'),t.col('5019'),p)
    if all(t.col(c) is not None for c in FINAL_COMPONENTS):
        emit('Sum(3011...3060) versus 3090 final demand',
             sum((t.col(c) for c in FINAL_COMPONENTS),np.zeros(N)),fd,p)
    p('Eight largest domestic-identity deviations (1800 + 3090 - 7000):')
    for j in np.argsort(-np.abs(gap_fd))[:top_n]:
        j=int(j)
        p(f'  {SECTORS[j]} | intermediate={zi[j]:,.0f} | 3090={fd[j]:,.0f} '
          f'| 7000={x[j]:,.0f} | gap={gap_fd[j]:+,.0f}')
    A=t.z/t.total_input[np.newaxis,:]
    y_balance=x-zi
    try:
        inferred = np.linalg.solve(np.eye(N)-A,fd)
        output_error = relative_l1(inferred,x)
        reconstruction_ratio = inferred.sum()/x.sum()
    except np.linalg.LinAlgError:
        output_error=float('nan')
        reconstruction_ratio=float('nan')
    p(f'Final demand 3090: negative sectors={int((fd<0).sum())}; '
      f'Leontief L@3090 vs output relative_L1={100*output_error:.6f}%; '
      f'output_sum_ratio={reconstruction_ratio:.6f}')
    p(f'Implied y=(I-A)x: negative sectors={int((y_balance<0).sum())}; '
      f'gap y vs 3090 relative_L1={100*relative_l1(y_balance,fd):.6f}%')
    return {'x':x.copy(),'z':t.z.copy(),'final_demand':fd.copy(),
            'gap_domestic_identity': gap_fd.copy(),
            'domestic_identity_rel_l1':relative_l1(zi+fd,x),
            'leontief_fd_output_rel_l1':output_error,
            'negative_residual_count':int((y_balance<0).sum())}


def compare_tables(t1, t2, p=print):
    p('\n=== TOTAL / DOMESTIC SIDE-BY-SIDE (NO MODEL MODIFICATIONS) ===')
    emit('Domestic gross output 7000: current versus candidate',t1.x,t2.x,p)
    emit('Intermediate Z: current minus domestic, aggregate sector usage',
         t1.z.sum(axis=1),t2.z.sum(axis=1),p)
    emit('Final demand 3090: current versus domestic',
         t1.col('3090'),t2.col('3090'),p)
    p(f'Current Z sum={t1.z.sum():,.0f}, domestic Z sum={t2.z.sum():,.0f}; '
      f'current 3090 sum={t1.col("3090").sum():,.0f}, '
      f'domestic 3090 sum={t2.col("3090").sum():,.0f}')
    negative_cells = int((t1.z-t2.z < -1).sum())
    p(f'Unexpected negative (current Z - domestic Z) cells (< -1): {negative_cells}')
    p('STOP: these are IO accounting tests, not an endorsement of any energy '
      'allocation, emissions factor, or manuscript scenario.')


def main():
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('--current',default='static/data/2020/io_ind_2020.csv',
                        help='Existing 2020 CSV used by EEIO v2.1')
    parser.add_argument('--domestic',default=None,
                        help='Optional BPS 185-product DOMESTIC transactions at BASIC prices CSV')
    a=parser.parse_args()
    first=load_bps_185(a.current)
    print_table_audit(first)
    if a.domestic:
        second=load_bps_185(a.domestic)
        print_table_audit(second)
        compare_tables(first,second)
    else:
        print('\nNEXT STEP: obtain BPS 2020 IO table for DOMESTIC transactions '
              'at BASIC prices, 185 products, in matching CSV format, and '
              'rerun with --domestic. The existing CSV is not modified.')


if __name__=='__main__':
    main()
