"""Synthetic invariant for real ScenarioProcessor (no live DB or BPS data)."""
import importlib
from pathlib import Path
import sys
import types
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
if 'util_db' not in sys.modules:
    # scenario_engine imports this only for scenario loader; no DB is used here.
    module=types.ModuleType('util_db')
    module.tbl_to_df=lambda *args,**kw:None
    sys.modules['util_db']=module
scenario_engine=importlib.import_module('scenario_engine')

class ScenarioProcessorTests(unittest.TestCase):
    def test_combined_interventions_do_not_modify_baseline(self):
        from types import SimpleNamespace
        n=185
        A=np.eye(n)*0.06
        L=np.linalg.inv(np.eye(n)-A)
        F=np.ones((4,n))*100
        factors=np.array([[19.9,90.3,np.nan],[38.65,68.6,np.nan],
                          [37.3,50.,26.8],[.877,np.nan,np.nan]])
        model=SimpleNamespace(A=A,L=L,mat_finCons=F,CO2_EF=factors,
                              sector_column_map={f'{i+1:03d}':i for i in range(n)})
        baseline_F=F.copy()
        baseline_EF=factors.copy()
        baseline_A=A.copy()
        baseline_L=L.copy()
        scenario=scenario_engine.ScenarioDefinition(
            scenario_id=None, scenario_name='synthetic 20% sector efficiency, 40% grid',
            parameters=[
                scenario_engine.ScenarioParameter(None,'energy_demand_change',-.2,'fraction','04','114','SECTOR_ENERGY'),
                scenario_engine.ScenarioParameter(None,'emission_factor_change',-.4,'fraction','04',None,'ENERGY')
            ])
        processed=scenario_engine.ScenarioProcessor(model).apply(model,scenario)
        actual_changes=np.argwhere(~np.isclose(processed.mat_finCons,F))
        self.assertEqual(actual_changes.tolist(),[[3,113]])
        self.assertAlmostEqual(processed.mat_finCons[3,113],80)
        self.assertAlmostEqual(processed.CO2_EF[3,0],.877*.6)
        self.assertTrue(np.array_equal(model.mat_finCons,baseline_F))
        self.assertTrue(np.array_equal(model.CO2_EF,baseline_EF,equal_nan=True))
        self.assertTrue(np.array_equal(model.A,baseline_A))
        self.assertTrue(np.array_equal(model.L,baseline_L))
        # When efficiency and EF act on the same component, reductions overlap.
        direct_before=baseline_F[3,113]*baseline_EF[3,0]
        direct_after=processed.mat_finCons[3,113]*processed.CO2_EF[3,0]
        self.assertAlmostEqual(1-direct_after/direct_before,.52)

if __name__=='__main__':
    unittest.main()
