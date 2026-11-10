"""Synthetic unit tests only; no MySQL, no application dependencies."""
import ast
import sys
import types
import importlib.util
from pathlib import Path
import numpy as np
import pandas as pd

engine_path = Path(__file__).resolve().parents[2] / 'src' / 'eeio_engine.py'
engine_tree = ast.parse(engine_path.read_text(encoding='utf-8'))
func = next(node for node in engine_tree.body if isinstance(node,ast.FunctionDef)
            and node.name == 'get_mat_finConsCO2')
ns = {'np':np}
exec(compile(ast.Module(body=[func], type_ignores=[]), str(engine_path),'exec'),ns)
stub = types.ModuleType('eeio_engine')
stub.get_mat_finConsCO2 = ns['get_mat_finConsCO2']
sys.modules['eeio_engine'] = stub

spec = importlib.util.spec_from_file_location('audit',str(Path(__file__).resolve().parents[2] / 'experiments' / 'manuscript' / '_03_manuscript_baseline_audit_v21.py'))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

n=185
rng=np.random.default_rng(2020)
A=np.zeros((n,n))
A[144,:]=rng.uniform(.002,.04,n)
A[37,6]=.1
A[37,51]=.2
A[38,97]=.03
A[36,80]=.08
# Distinct output to make A and Z allocation different
X_total=np.geomspace(1e3, 1e6, n)
X_domOut=np.linspace(1e3,1e5,n)
L=np.linalg.inv(np.eye(n)-A)
finCons=np.zeros((4,n))
finCons[0,80]=1000
finCons[1,6]=500_000
finCons[1,51]=750_000
finCons[2,97]=50_000
finCons[3,:] = 2_500_000 * A[144,:]/A[144,:].sum()
EF=np.array([[19.9,90.3,np.nan],[38.65,68.6,np.nan], [37.3,50.,26.8],[.877,np.nan,np.nan]])

class Model:
    n_sectors=n
    year=2020
    sector_column_map={f'{i+1:03d}':i for i in range(n)}
    def calculate_baseline(self):
        baseline = mod.direct_co2_components_v21(self, finCons[3,:])
        d=mod.eeio_emissions_from_components_v21(self,baseline)
        return {'totalEmission':d['sector_total_t']}

m=Model()
m.A, m.L, m.X_total, m.X_domOut, m.mat_finCons, m.CO2_EF=A,L,X_total,X_domOut,finCons,EF
sector_df=pd.DataFrame({'sector_code':[f'{i+1:03d}' for i in range(n)],
                        'sector_name':[f'Industry or sector {i+1}' for i in range(n)],
                        'sector_agg':['Manufacturing' if i>50 and i<134 else 'Other' for i in range(n)]})

old = {k:getattr(m,k).copy() for k in ['A','L','X_total','X_domOut','mat_finCons','CO2_EF']}
old_A, new_Z, coeff, Z = mod.allocate_electricity_by_transactions_v21(m)
assert np.isclose(old_A.sum(),new_Z.sum(),rtol=1e-12)
assert not np.allclose(old_A,new_Z)
assert np.allclose(Z, A[144,:] * X_total)

lines=[]
r=mod.audit_baseline_v21(m, sector_df, top_n=3, printer=lines.append)
assert any('Recomputed A0 emission baseline matches current engine: YES' in s for s in lines)
assert any('FOUR BASELINE SENSITIVITY CASES' in s for s in lines)
assert len(r['cases'])==4
assert r['cases']['A1 provisional units']['national_mtco2'] > r['cases']['A0 current']['national_mtco2']
for k,v in old.items():
    assert np.array_equal(v,getattr(m,k), equal_nan=True),k
for k, val in r['cases'].items():
    assert np.isclose(val['scope1_mtco2']+val['scope2_mtco2']+val['scope3_mtco2'], val['national_mtco2'])
    assert len(val['sector_total_t'])==n
# Confirm source-component ratios conditional on EF interpretation.
C0=mod.direct_co2_components_v21(m, finCons[3,:], False)
C1=mod.direct_co2_components_v21(m, finCons[3,:], True)
assert np.allclose(C1[1,:], 1000*C0[1,:])
assert np.allclose(C1[2,:], 1000*C0[2,:])
assert np.allclose(C1[0,:], C0[0,:])
assert np.allclose(C1[3,:], C0[3,:])
print('BASELINE_AUDIT_SYNTHETIC_TESTS_PASS')
for line in lines[:19]:
    print(line)
print('--- CASES ---')
for label,r in r['cases'].items():
    print(label, f"{r['national_mtco2']:.6f}")
