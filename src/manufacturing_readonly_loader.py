"""Pure, SELECT-only staging model loader for the original v2.1 core.

No build_model_year() invocation (that method writes buffer CSVs). No app cache,
no INSERT/UPDATE/commit; no permission to mutate co2_factor or history.
Requires the uploaded, SHA256-pinned verified 2020 TOTAL IO file.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from eeio_engine import (calc_matrix_A, get_mat_finEnerCons, get_mat_finCons,
                         get_mat_finConsCO2, build_eeio_model)

EXPECTED_IO_SHA256 = '80bfaa2ed8f9e4cfc98da617a2182272a722af0e1c7f4fa5f558368f4ec69832'
SECTOR_CODES = [f'{x:03d}' for x in range(1, 186)]
CARRIERS = ('Coal', 'Fuel', 'Natural Gas', 'Electricity')
REQUIRED_CO2 = np.array([[1,1,0],[1,1,0],[1,1,1],[1,0,0]], dtype=bool)

SQL_SECTOR = ("SELECT sector_code, sector_name, sector_agg FROM sector_lst "
              "WHERE sector_label = %s ORDER BY sector_code")
SQL_FEC = ("SELECT e.energy_type_name, f.fec_val FROM fec_byyear AS f "
           "LEFT JOIN energy_lst AS e ON e.energy_type_code = f.energy_type "
           "WHERE f.year = %s")
SQL_CONVERSION = 'SELECT energy_type, multiplier_factor FROM conversion_factor'
SQL_CO2 = 'SELECT energy_type, heat_content_hhv, emission_factor, multiplier FROM co2_factor'


def _read(db, sql: str, parameters: tuple = ()) -> pd.DataFrame:
    if not sql.lstrip().upper().startswith('SELECT'):
        raise RuntimeError('Research loader only allows SELECT queries')
    cursor = db.connection.cursor()
    try:
        cursor.execute(sql, parameters)
        if not cursor.description:
            raise ValueError('Database SELECT returned no column information')
        columns = [col[0] for col in cursor.description]
        records = cursor.fetchall()
    finally:
        cursor.close()
    return pd.DataFrame(list(records), columns=columns)


def _ordered_unique(data, key, expected, numeric_column):
    if key not in data or numeric_column not in data:
        raise ValueError(f'Columns missing in {key} table')
    ids = data[key].astype(str).str.strip()
    if data.empty or ids.duplicated().any() or set(ids) != set(expected):
        raise ValueError(f'{key}: expected exactly {expected}, without duplicates')
    df = data.copy()
    df[key] = ids
    vals = pd.to_numeric(df.set_index(key).reindex(expected)[numeric_column], errors='raise').to_numpy(float)
    if vals.shape != (len(expected),) or not np.isfinite(vals).all():
        raise ValueError(f'{numeric_column}: nonfinite or missing values')
    return vals


def read_model_readonly(db, io_path: Path, year: int = 2020):
    if year != 2020:
        raise ValueError('Staging case is approved for model year 2020 only')
    io_path = Path(io_path)
    raw_sha = hashlib.sha256(io_path.read_bytes()).hexdigest()
    if raw_sha != EXPECTED_IO_SHA256:
        raise ValueError('IO file SHA256 does not match reviewed 2020 TOTAL BPS CSV; fail closed')
    io = pd.read_csv(io_path, thousands=',', dtype={'Sector_Code': str})
    if io.shape != (194, 207) or io['Sector_Code'].iloc[:185].tolist() != SECTOR_CODES:
        raise ValueError('Unexpected IO shape or product code ordering')
    if io['Sector_Code'].iloc[-1] != '2100':
        raise ValueError('Expected gross output final row 2100')
    if any(code not in io for code in SECTOR_CODES + ['7000']):
        raise ValueError('Missing IO sector column or domestic output column 7000')

    sectors = _read(db, SQL_SECTOR, ('DET',))
    sectors['sector_code'] = sectors['sector_code'].astype(str).str.zfill(3)
    if sectors['sector_code'].tolist() != SECTOR_CODES:
        raise ValueError('MySQL sector_lst does not match the exact 001-185 IO column ordering')
    X = io.loc[:, SECTOR_CODES].to_numpy(dtype=float)
    Z = X[:185, :]
    X_total = X[-1, :]
    X_domOut = io['7000'].iloc[:185].to_numpy(dtype=float)
    if not np.isfinite(Z).all() or not np.isfinite(X_total).all() or np.any(X_total <= 0):
        raise ValueError('Invalid BPS transaction matrix or gross output')
    if not np.isfinite(X_domOut).all():
        raise ValueError('Invalid domestic output column')
    A = calc_matrix_A(Z, X_total)

    fec_data = _read(db, SQL_FEC, (int(year),))
    fec = _ordered_unique(fec_data, 'energy_type_name', CARRIERS, 'fec_val')
    if np.any(fec < 0):
        raise ValueError('Negative carrier total energy consumption')
    conversion_data = _read(db, SQL_CONVERSION)
    conversion_data['energy_type'] = conversion_data['energy_type'].astype(str).str.strip().str.lower()
    conv = _ordered_unique(conversion_data, 'energy_type', tuple(x.lower() for x in CARRIERS), 'multiplier_factor')
    if np.any(conv <= 0) or not np.isclose(conv[3], .613, rtol=0, atol=1e-12):
        raise ValueError('Electricity conversion differs from the reviewed legacy 0.613: unit gate')
    co2 = _read(db, SQL_CO2)
    if co2.empty or co2['energy_type'].duplicated().any() or set(co2['energy_type']) != set(CARRIERS):
        raise ValueError('Expected exactly four unique legacy CO2 rows')
    factors = co2.set_index('energy_type').reindex(CARRIERS)[
        ['heat_content_hhv', 'emission_factor', 'multiplier']
    ].to_numpy(dtype=float)
    if factors.shape != (4,3) or not np.isfinite(factors[REQUIRED_CO2]).all():
        raise ValueError('Required CO2 coefficients must be finite; optional NULL allowed')
    if np.any(factors[REQUIRED_CO2] < 0):
        raise ValueError('Required CO2 coefficients must not be negative')
    if not np.isclose(factors[3,0], .877, rtol=0, atol=1e-12):
        raise ValueError('Current database electricity coefficient differs from 0.877; review before use')

    fin_energy = get_mat_finEnerCons(A, fec)
    energy_consumption = get_mat_finCons(fin_energy, conv)
    emissions_raw = get_mat_finConsCO2(energy_consumption, factors)
    model = build_eeio_model(A, emissions_raw, X_total, X_domOut,
                             mat_finCons=energy_consumption, CO2_EF=factors,
                             year=year)
    model.sector_column_map = {code: idx for idx, code in enumerate(SECTOR_CODES)}
    if not np.isfinite(model.mat_finCons).all():
        raise ValueError('Nonfinite sectoral energy allocations')
    # Important invariant: independent of the selected research grid sensitivity.
    factor_safe = [[None if np.isnan(v) else float(v) for v in row] for row in factors]
    metadata_raw = {
        'io_sha256': raw_sha, 'model_year': year, 'io_type': 'BPS2020_TOTAL',
        'sector_codes': sectors['sector_code'].tolist(),
        'fec_order': list(CARRIERS), 'fec_national_values_unverified_units': fec.tolist(),
        'conversion_values_legacy': conv.tolist(), 'legacy_co2_matrix': factor_safe,
        'electricity_ef_source_year': 2016,
        'electricity_ef_source_region': 'JAMALI',
    }
    metadata_text = json.dumps(metadata_raw, sort_keys=True, separators=(',', ':'), allow_nan=False)
    metadata = {
        'io_sha256': raw_sha,
        'read_only_baseline_fingerprint': hashlib.sha256(metadata_text.encode()).hexdigest(),
        'model_year': year,
        'io_type': 'BPS 2020 TOTAL',
        'electricity_conversion_status': '0.613 legacy carrier conversion (MWh inferred)',
        'factor_table_status': 'LEGACY UNVERSIONED co2_factor; no DB writes or changes',
        'source_data_provenance': 'Live SELECTs from fec_byyear, conversion_factor, co2_factor, sector_lst',
    }
    return model, metadata
