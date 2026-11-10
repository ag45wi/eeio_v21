"""Offline Stage 3B regressions, using the real BPS 2020 domestic IO CSV.

No MySQL, no write to source mapping, and no changes to legacy model results.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np
import pandas as pd
from openpyxl import load_workbook
from request7_stage3b_mapping_evidence_audit_v21 import (
    audit, _save_artifacts, make_sources, checksum, evidence_tags
)

ROOT=Path(__file__).resolve().parents[2]

def file_path(name, nested):
    p=ROOT/nested
    if p.exists(): return p
    p=ROOT/name
    if p.exists(): return p
    raise FileNotFoundError(f"Required Stage3B test fixture missing: {name}; copy to application root")

IO=file_path('io_ind_2020_domestic.csv','static/data/2020/io_ind_2020_domestic.csv')
MAPPING=file_path('sector_mapping_2020_proposed_v2_UNAPPROVED.csv','output/research/stage3/sector_mapping_2020_proposed_v2_UNAPPROVED.csv')
NOTES=file_path('sector_mapping_2020_review_notes.csv','output/research/stage3/sector_mapping_2020_review_notes.csv')
ANCHORS=file_path('heesi2021_electricity_enduse_2020.csv','heesi2021_electricity_enduse_2020.csv')


def run():
    orig={p:checksum(p) for p in (IO,MAPPING,NOTES,ANCHORS)}
    results=audit(IO,MAPPING,NOTES,ANCHORS)
    frame,priority,critical,groups,construction,transport,report=results
    assert len(frame)==185 and len(priority)==71 and len(critical)==40
    assert frame.sector_code.is_unique and (frame.review_status=='pending').all()
    assert frame.approved_category.str.strip().eq('').all()
    assert frame.physical_2020_sector_mwh_status.str.startswith('NOT OBSERVED').all()
    assert np.isclose(frame.transaction_gwh.sum(),146823,atol=1e-6)
    assert np.isclose(frame.coefficient_gwh.sum(),146823,atol=1e-6)
    assert np.isclose(report['target_34_transaction_gwh'],34007.195341299,atol=1e-5)
    assert np.isclose(report['target_34_coefficient_gwh'],37745.013821894,atol=1e-5)
    print('PASS: domestic IO, 185 pending mapping rows, 71 high priority, benchmark target allocations')
    assert np.isclose(report['construction_transaction_gwh'],19501.41755306,atol=1e-5)
    assert np.isclose(report['construction_coefficient_gwh'],2078.570562227,atol=1e-5)
    assert np.isclose(transport.pln_rail_traction_sensitivity_gwh.sum(),292,atol=1e-12)
    assert np.isclose(transport.delta_rail_focus_minus_transaction_gwh.sum(),0,atol=1e-10)
    assert np.isclose(transport.delta_rail_focus_minus_coefficient_gwh.sum(),0,atol=1e-10)
    print('PASS: construction sensitivity, conditional rail-focused allocation conserves 292 GWh')
    with TemporaryDirectory() as temp:
        outputs=_save_artifacts(Path(temp),*results[:-1],report,make_sources())
        assert len(outputs)==10
        fname=Path(temp)/outputs['workbook']
        x=load_workbook(fname,data_only=False)
        assert {'Priority 71','All 185 Evidence','Assumptions','Physical MWh Intake','Critical comparisons','Official Source Register'}<=set(x.sheetnames)
        ws=x['All 185 Evidence']
        cols=[c.value for c in ws[1]]
        diffcol=cols.index('allocation_gap_gwh')+1
        assert str(ws.cell(2,diffcol).value).startswith('=')
        wdata=load_workbook(fname,data_only=True)
        for tab, dataframe in [('All 185 Evidence',frame),('Priority 71',priority),('Impact Critical',critical)]:
            sht=wdata[tab]
            assert np.isclose(sht['P2'].value,float(dataframe.iloc[0]['transaction_gwh']-dataframe.iloc[0]['coefficient_gwh']),atol=1e-8)
            assert np.isclose(sht['Q2'].value,abs(sht['P2'].value),atol=1e-8)
            assert np.isclose(sht['S2'].value,float(dataframe.iloc[0]['trans_s2_20pct_electricity_mtco2']),atol=1e-8)
        assert x['Physical MWh Intake'].max_row==186
        f=pd.read_csv(Path(temp)/outputs['all'],dtype={'sector_code':str})
        assert len(f)==185
        assert f['approved_category'].isna().all() and f['review_status'].eq('pending').all()
        physical=pd.read_csv(Path(temp)/outputs['physical'])
        assert physical['observed_electricity_mwh'].isna().all()
        with (Path(temp)/outputs['metrics']).open(encoding='utf-8') as handle:
            d=json.load(handle)
        assert d['diagnostics']['pending_count']==185
        try:_save_artifacts(Path(temp),*results[:-1],report,make_sources())
        except FileExistsError:pass
        else:raise AssertionError('Existing exported research results can be overwritten')
    print('PASS: 10 evidence exports, workbook formulas/intake, no invented measurements or approvals, overwrite guard')
    with TemporaryDirectory() as temp:
        src=pd.read_csv(MAPPING,dtype=str,keep_default_na=False)
        src.loc[0,'review_status']='approved'
        t=Path(temp)/'tampered.csv';src.to_csv(t,index=False)
        try:audit(IO,t,NOTES,ANCHORS)
        except ValueError:pass
        else:raise AssertionError('Silent reviewer auto-approval was accepted')
        src.loc[0,'review_status']='pending';src.loc[0,'suggested_category']='transport'
        src.to_csv(t,index=False)
        try:audit(IO,t,NOTES,ANCHORS)
        except ValueError:pass
        else:raise AssertionError('Mapping altered without corresponding review metadata')
    print('PASS: fail-closed approvals and category tampering detection')
    assert all(checksum(p)==orig[p] for p in orig)
    print('PASS: original BPS IO, HEESI anchors, Stage 3 mapping and notes unchanged')
    print('ALL REQUEST #7 STAGE 3B SOURCE REVIEW TESTS PASSED')

if __name__=='__main__':run()
