"""Synthetic checks; runs without the EEIO database or Flask imports."""
import io
from types import SimpleNamespace
import numpy as np
import pandas as pd
from manuscript_io_final_demand_audit_v21 import audit_official_final_demand_v21


def build_example(mismatch=False):
    A = np.array([[0.2, 0.1, 0.0], [0.05, 0.2, 0.15], [0.0, 0.1, 0.15]], float)
    x = np.array([100., 250., 300.])
    Z = A * x[np.newaxis, :]
    interm = Z.sum(axis=1)
    final = x - interm
    if mismatch:
        final = final + np.array([0., 10., 0.])
    B = np.array([0.2, 0.4, 0.7])
    model = SimpleNamespace(
        year=2020, A=A, L=np.linalg.inv(np.eye(3)-A),
        X_total=x, X_domOut=x.copy(), B=B,
        mat_finCons=np.array([[5., 6., 7.], [1., 2., 3.],
                              [3., 4., 5.], [2., 3., 4.]]),
        CO2_EF=np.array([[19.9, 90.3, np.nan],
                         [38.65, 68.6, np.nan],
                         [37.3, 50., 26.8],
                         [0.877, np.nan, np.nan]]),
        n_sectors=3,
        sector_column_map={'001': 0, '002': 1, '003': 2},
    )
    df_sector = pd.DataFrame({
        'sector_code': ['001', '002', '003'],
        'sector_name': ['Test 1', 'Test 2', 'Test 3'],
        'sector_agg': ['01', '02', '03']
    })
    df_io = pd.DataFrame({
        '1800': interm,
        '3090': final,
        '3100': interm+final,
        '7000': x,
        '8000': x,
        '3011': 0.2*final,
    })
    return model, df_io, df_sector


def test_case(mismatch, expected_mismatch):
    model, df, sectors = build_example(mismatch=mismatch)
    out = io.StringIO()
    result = audit_official_final_demand_v21(model, df, sectors,
                                              printer=lambda x: print(x, file=out))
    assert np.isclose(result['distance_1800_vs_Zrow'], 0.)
    assert (np.isclose(result['distance_3090_vs_y_star'], 0.)) != expected_mismatch
    assert (result['distance_Leontief_3090_vs_x'] > 0.) == expected_mismatch
    assert 'END FINAL-DEMAND RECONCILIATION' in out.getvalue()
    assert np.allclose(model.X_total, [100.,250.,300.])
    assert model.X_domOut[0] == 100.


if __name__ == '__main__':
    test_case(mismatch=False, expected_mismatch=False)
    test_case(mismatch=True, expected_mismatch=True)
    print('PASS: baseline IO balances and mismatched final-demand vector; no mutation')
