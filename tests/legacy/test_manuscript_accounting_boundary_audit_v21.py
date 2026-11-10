"""Run from repo/container with numpy and pandas; no Flask/MySQL required."""
import ast
import importlib.util
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd

root = Path(__file__).resolve().parents[2] / 'src'
engine = root / 'eeio_engine.py'
module = ast.parse(engine.read_text(encoding='utf-8'))
func = next(n for n in module.body
            if isinstance(n, ast.FunctionDef) and n.name == 'get_mat_finConsCO2')
namespace = {'np': np}
exec(compile(ast.Module(body=[func], type_ignores=[]), str(engine), 'exec'), namespace)
stub = types.ModuleType('eeio_engine')
stub.get_mat_finConsCO2 = namespace['get_mat_finConsCO2']
sys.modules['eeio_engine'] = stub
sys.path.insert(0, str(root.parent / 'experiments' / 'manuscript'))
from manuscript_accounting_boundary_audit_v21 import audit_accounting_boundaries_v21

n = 185
rng = np.random.default_rng(777)
A = np.zeros((n, n))
A[144, :] = rng.uniform(0.0008, 0.0025, n)
A[36, 144] = 0.03
A[37, 95] = 0.04
A[38, 144] = 0.02
X = np.linspace(1e5, 2e5, n)
L = np.linalg.inv(np.eye(n) - A)
y = (np.eye(n) - A) @ X
assert np.all(y >= 0)
F = np.zeros((4, n))
F[0, 144] = 5e5
F[1, 95] = 5e5
F[2, 144] = 5e5
F[3, :] = 2.2e6 * A[144, :] / A[144, :].sum()
EF = np.array([[19.9, 90.3, np.nan],
               [38.65, 68.6, np.nan],
               [37.3, 50., 26.8],
               [.877, np.nan, np.nan]])

class Model:
    n_sectors = n
    year = 2020
    sector_column_map = {f'{j+1:03d}': j for j in range(n)}

m = Model()
m.A, m.L, m.X_total, m.X_domOut = A, L, X, y.copy()
m.mat_finCons, m.CO2_EF = F, EF
sector_df = pd.DataFrame({
    'sector_code': [f'{j+1:03d}' for j in range(n)],
    'sector_name': [f'Test sector {j+1}' for j in range(n)],
    'sector_agg': ['02' if j < 100 else '11' for j in range(n)],
})
backup = {k: np.asarray(getattr(m, k)).copy()
          for k in ('A','L','X_total','X_domOut','mat_finCons','CO2_EF')}
text = []
result = audit_accounting_boundaries_v21(m, sector_df, printer=text.append)
assert result['relative_l1_final_demand'] < 1e-10
assert result['relative_l1_reconstruction'] < 1e-10
assert 0 < result['electricity_z_self_share'] < 1
assert 0 < result['electricity_a_self_share'] < 1
assert result['electricity_z_no_self'][144] == 0
assert result['electricity_a_no_self'][144] == 0
assert np.isclose(result['electricity_a_no_self'].sum(), F[3, :].sum())
assert np.isclose(result['electricity_z_no_self'].sum(), F[3, :].sum())
assert result['conditional_total']['national_mtco2'] > result['current_total']['national_mtco2']
for key, old in backup.items():
    assert np.array_equal(old, getattr(m, key), equal_nan=True), key
assert any('SCOPE-LABEL CONSISTENCY' in line for line in text)
assert any('IO OUTPUT AND FINAL-DEMAND' in line for line in text)
# Use a deliberately different output vector to verify detection works.
m.X_domOut = X.copy()
other = audit_accounting_boundaries_v21(m, sector_df, printer=lambda s: None)
assert other['relative_l1_final_demand'] > 0.0
assert other['relative_l1_reconstruction'] > 0.0
print('ACCOUNTING_BOUNDARY_SYNTHETIC_TESTS_PASS')
for line in text[:21]:
    print(line)
