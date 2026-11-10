"""Synthetic tests of read-only import bridge diagnostics, no Flask/MySQL."""
from copy import deepcopy
from io import StringIO
from types import SimpleNamespace
import numpy as np
import pandas as pd

from manuscript_io_import_bridge_audit_v21 import audit_io_import_bridge_v21


def make_synthetic(with_final_component_mismatch=False):
    n=3
    A=np.array([[.1,.05,.02],[.04,.2,.01],[.03,.05,.2]])
    x=np.array([100.,250.,300.]); Z=A*x[np.newaxis,:]
    i=Z.sum(axis=1); implied=x-i
    imports=np.array([10.,20.,15.]); margin=np.array([2.,-1.,-1.]); taxes=np.zeros(n)
    final=implied+imports  # total-transactions table, consistent on purpose
    sup=x+imports+margin
    # published final-demand component contributions add up to the total
    components=[final*.4,final*.05,final*.05,final*.3,final*.1,final*.08,final*.02]
    if with_final_component_mismatch:
        components[0]=components[0]+np.array([0,0,8.])
    io_codes={'1800':i,'3090':final,'3100':i+final,
              '4011':imports*.8, '4012':imports*.2, '4013':np.zeros(n),
              '4019':imports,'5011':margin,'5012':np.zeros(n),'5013':np.zeros(n),
              '5019':margin,'6090':taxes,'7000':x,'8000':sup}
    io_codes.update(dict(zip(('3011','3012','3020','3030','3040','3050','3060'),components)))
    io=pd.DataFrame(io_codes)
    sector=pd.DataFrame({'sector_code':['001','002','003'],
                         'sector_name':['alpha','beta','gamma'],
                         'sector_agg':['01','02','03']})
    m=SimpleNamespace(year=2020,n_sectors=n,A=A,L=np.linalg.inv(np.eye(n)-A),
                      X_total=x,sector_column_map={'001':0,'002':1,'003':2})
    return m,io,sector


def test():
    m,io,sector=make_synthetic()
    data_before=deepcopy(io)
    A=m.A.copy()
    logs=StringIO()
    r=audit_io_import_bridge_v21(m,io,sector,printer=lambda v:logs.write(v+'\n'))
    assert np.allclose(r['imports'],[10,20,15])
    assert np.allclose(r['supply_minus_output'],[12,19,14])
    assert np.allclose(r['final_vs_import_residual'],0)
    assert np.allclose(r['component_final_residual'],0)
    assert np.allclose(r['total_demand_residual'],0)
    assert 'import-only mathematical test' in logs.getvalue()
    assert io.equals(data_before) and np.array_equal(m.A,A)

    m2,io2,sector2=make_synthetic(with_final_component_mismatch=True)
    r2=audit_io_import_bridge_v21(m2,io2,sector2,printer=lambda _:None)
    assert np.isclose(r2['component_final_residual'].sum(),8.)
    print('PASS: import bridge, signed margins, component discrepancy, and read-only behavior')


if __name__=='__main__':
    test()
