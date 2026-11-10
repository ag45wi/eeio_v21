"""EEIO v2.1 Request #7 / Stage 3B: source-aided, READ-ONLY mapping review.

Outputs review artifacts, not a revised or approved Stage 3 mapping.
No MySQL, no scenario mutation, no database writes, no network calls.
Official external sources are referenced, not used to invent sector-level MWh.

Run:
 python request7_stage3b_mapping_evidence_audit_v21.py \
  --io static/data/2020/io_ind_2020_domestic.csv \
  --mapping output/research/stage3/sector_mapping_2020_proposed_v2_UNAPPROVED.csv \
  --notes sector_mapping_2020_review_notes.csv \
  --anchors heesi2021_electricity_enduse_2020.csv \
  --outdir output/research/stage3b
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.utils import get_column_letter
from openpyxl.chart import BarChart, Reference
from openpyxl.worksheet.datavalidation import DataValidation

from request7_domestic_eeio_v21 import load_domestic_io
from request7_stage3_heesi_enduse_v21 import read_enduse_anchors

VERSION = "request7-stage3b-mapping-evidence-v1"
HEESI_URL = "https://www.esdm.go.id/assets/media/content/content-handbook-of-energy-and-economic-statistics-of-indonesia-2021.pdf"
PLN_URL = "https://web.pln.co.id/statics/uploads/2021/07/Statistik-PLN-2020.pdf"
BPS_MANUFACTURING_URL = "https://www.bps.go.id/id/publication/2022/09/30/206f24701aea1e4a26b6f1ff/statistik-industri-manufaktur-indonesia-2020.html"
GROUP_ANCHOR = {"industry": 88372., "commercial":58159., "transport":292.}
TRANSPORT_CODES = ("157", "158", "159", "160", "161", "162")
CONSTRUCTION_CODES = ("149", "150", "151", "152", "153")
PRIORITY_CODES = ("145", "149", "153", "114", "157", "095", "037", "038", "039", "096", "077", "158", "132", "133", "134")
TARGET_GROUPS = {"07", "08", "10", "11"}


def checksum(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _as_code(s: pd.Series) -> pd.Series:
    s = s.astype("string").str.strip().str.zfill(3)
    if not s.str.fullmatch(r"\d{3}").all():
        raise ValueError("Invalid sector codes")
    return s


def evidence_tags(row: pd.Series) -> tuple[str, str, str, str]:
    theme = str(row["issue_theme"])
    name = str(row["sector_name"])
    cat = str(row["suggested_category"])
    if str(row["sector_code"]) == "145":
        return ("Generation own-use distinct from final electricity", "HIGH_BOUNDARY_RISK",
                "Do not allocate HEESI final-use electricity to generation; independently check actual own-use; keep excluded provisional.", HEESI_URL)
    if theme.startswith("Primary production"):
        return ("No matched standalone primary-production electricity end-use category established", "UNRESOLVED_CATEGORY",
                "HEESI industry examples do not establish which farm/fishery activities belong; do not auto-exclude or auto-approve.", HEESI_URL)
    if theme.startswith("Construction"):
        return ("Construction explicitly included in HEESI industry definition", "BROAD_CATEGORY_SUPPORTED_NOT_MWH",
                "Retain provisional industry grouping; acquire observed construction electricity before treating IO-weight shares as physical.", HEESI_URL)
    if theme.startswith("Transport"):
        return ("HEESI transport defined by activity across sectors; PLN traction =292.44 GWh", "ACTIVITY_CROSSWALK_REQUIRED",
                "Keep mapping pending; rail-focused 292 GWh is a separate sensitivity only, not an approved crosswalk.", HEESI_URL + " | " + PLN_URL)
    if theme.startswith("Energy supply"):
        return ("Energy transformation and own-use have distinct balance rows", "HIGH_BOUNDARY_RISK",
                "Reconcile direct final energy vs generation/refinery own-use and losses; IO product labels alone insufficient.", HEESI_URL)
    if theme.startswith("Manufactured product") or "Manufacturing" in theme or theme.startswith("Mixed manufacturing"):
        return ("Manufacturing activity supported in principle; repair services may be mixed", "BROAD_CATEGORY_SUPPORTED_NOT_MWH",
                "Verify KBLI/IO product meaning; manufacturing 2020 BPS survey covers large/medium establishments only.", BPS_MANUFACTURING_URL)
    if theme.startswith("Ambiguous") or theme.startswith("Mixed production"):
        return ("Industrial/commercial split not observed by IO-product label", "UNRESOLVED_CATEGORY",
                "Obtain activity and utility-billing split before approval; no source-based MWh currently.", HEESI_URL)
    if theme.startswith("Industrial process"):
        return ("Industrial processes listed in HEESI", "BROAD_CATEGORY_SUPPORTED_NOT_MWH",
                "Use BPS 2020 manufacturing energy-by-KBLI as potential partial validation, not universal calibration.", HEESI_URL + " | " + BPS_MANUFACTURING_URL)
    if cat == "commercial":
        return ("HEESI includes markets/hotels/finance/government/schools/hospitals", "BROAD_CATEGORY_CANDIDATE",
                "Check that product output represents commercial activities; obtain consumption evidence.", HEESI_URL)
    return ("HEESI industrial activities broadly cover manufacturing/non-energy mining", "BROAD_CATEGORY_CANDIDATE",
            "No observed product-level electricity figure; confirm BPS KBLI mapping and consumption coverage.", HEESI_URL + " | " + BPS_MANUFACTURING_URL)


def audit(io_path: Path, mapping_path: Path, notes_path: Path, anchor_path: Path):
    io = load_domestic_io(2020, io_path)
    anchors = read_enduse_anchors(anchor_path)
    original = pd.read_csv(mapping_path, dtype="string", keep_default_na=False, encoding="utf-8-sig")
    notes = pd.read_csv(notes_path, dtype="string", keep_default_na=False, encoding="utf-8-sig")
    for table, label in ((original,"mapping"),(notes,"notes")):
        if len(table) != 185:
            raise ValueError(f"{label} requires 185 rows")
        table["sector_code"] = _as_code(table["sector_code"])
        if table["sector_code"].duplicated().any() or tuple(table["sector_code"]) != tuple(f"{i:03d}" for i in range(1,186)):
            raise ValueError(f"{label} codes must be 001..185 exactly once and in order")
    if original["review_status"].str.lower().eq("approved").any():
        raise ValueError("This read-only audit accepts UNAPPROVED review mappings only; approval must be external and documented")
    if not original["approved_category"].str.strip().eq("").all():
        raise ValueError("Unreviewed mapping should not contain a silent approved_category")
    if not original["review_status"].eq("pending").all():
        raise ValueError("All proposed v2 mapping rows must remain pending")
    if not original["suggested_category"].isin([*GROUP_ANCHOR,"excluded"]).all():
        raise ValueError("Unknown HEESI grouping")
    if original.loc[original["sector_code"].eq("145"),"suggested_category"].iloc[0] != "excluded":
        raise ValueError("Electricity sector 145 must remain excluded from final-use allocation")
    if not original["sector_name"].str.strip().equals(notes["sector_name"].str.strip()):
        raise ValueError("Mapping and notes sector names disagree")
    if not original["sector_agg"].str.strip().equals(notes["sector_agg"].str.strip()):
        raise ValueError("Mapping and notes aggregate groups disagree")
    if not original["suggested_category"].equals(notes["proposed_category"]):
        raise ValueError("Mapping and notes proposed categories differ")
    if {x:float(anchors.gwh[x]) for x in GROUP_ANCHOR} != GROUP_ANCHOR:
        raise ValueError("HEESI 2020 anchors do not match source-based published figures")
    frame=original.merge(notes[["sector_code","priority","issue_theme","review_reason"]],on="sector_code",validate="one_to_one")
    cats = frame["suggested_category"].astype(str).to_numpy()
    alloc = {}
    for method, weights in (("transactions", io.Z[144,:]),("coefficients",io.A[144,:])):
        if np.any(weights < 0) or not np.all(np.isfinite(weights)):
            raise ValueError("Nonpositive/invalid electricity IO weights")
        v=np.zeros(185,float)
        for category,gwh in GROUP_ANCHOR.items():
            mask=(cats==category)
            if mask.sum()==0 or weights[mask].sum()<=0:
                raise ValueError(f"No usable weight for {category}")
            v[mask]=gwh*weights[mask]/weights[mask].sum() # GWh
            if not np.isclose(v[mask].sum(), gwh,atol=1e-6):
                raise AssertionError(f"{method} {category} not conserved")
        if v[144] != 0.0 or not np.isclose(v.sum(),sum(GROUP_ANCHOR.values()),atol=1e-5):
            raise AssertionError("Producer electricity allocation failed reconciliation")
        alloc[method]=v
    frame["transaction_gwh"]=alloc["transactions"]
    frame["coefficient_gwh"]=alloc["coefficients"]
    frame["allocation_gap_gwh"]=frame["transaction_gwh"]-frame["coefficient_gwh"]
    frame["abs_gap_gwh"]=np.abs(frame["allocation_gap_gwh"])
    frame["gwh_max_alloc"]=np.maximum(frame["transaction_gwh"],frame["coefficient_gwh"])
    frame["trans_s2_20pct_electricity_mtco2"]=frame["transaction_gwh"]*1000*0.2*0.877/1e6
    frame["coeff_s2_20pct_electricity_mtco2"]=frame["coefficient_gwh"]*1000*0.2*0.877/1e6
    frame["is_selected_34"]=frame["sector_agg"].isin(TARGET_GROUPS) & frame["sector_code"].isin(
        ("077", "078", "079", "080", "081", "082", "083", "084", "085", "095", "096", "097", "098", "099", "100", "101", "102", "103", "104", "105", "114", "115", "116", "117", "118", "119", "120", "121", "123", "124", "125", "126", "127", "131"))
    tags=[evidence_tags(row) for _,row in frame.iterrows()]
    frame["source_claim"]=[v[0] for v in tags]
    frame["evidence_tier"]=[v[1] for v in tags]
    frame["specific_next_evidence"]=[v[2] for v in tags]
    frame["official_source_url"]=[v[3] for v in tags]
    frame["physical_2020_sector_mwh_status"]="NOT OBSERVED: IO allocation proxy only"
    frame["reviewer_action"]="REVIEW PENDING - source evidence and reviewer required"
    frame["mapping_source_status"]="v2 suggestion unchanged; no approval granted"
    frame["critical_review"]=(frame["sector_code"].isin(PRIORITY_CODES) |
        (frame["abs_gap_gwh"]>=1000) | (frame["gwh_max_alloc"]>=5000))
    frame["review_order_score"]=(100*frame["critical_review"].astype(int) +
        30*(frame["priority"]=="High").astype(int) +
        10*(frame["priority"]=="Medium").astype(int) +
        10*np.log1p(frame["abs_gap_gwh"]))
    frame=frame.sort_values(["review_order_score","sector_code"],ascending=[False,True]).reset_index(drop=True)

    code_order=frame.set_index("sector_code")
    construction=code_order.loc[list(CONSTRUCTION_CODES)].reset_index().copy()
    transport=code_order.loc[list(TRANSPORT_CODES)].reset_index().copy()
    transport["pln_rail_traction_sensitivity_gwh"] = np.where(transport["sector_code"]=="157",292.0,0.0)
    transport["delta_rail_focus_minus_transaction_gwh"] = transport["pln_rail_traction_sensitivity_gwh"]-transport["transaction_gwh"]
    transport["delta_rail_focus_minus_coefficient_gwh"] = transport["pln_rail_traction_sensitivity_gwh"]-transport["coefficient_gwh"]
    construction_trans= float(construction["transaction_gwh"].sum())
    construction_coeff= float(construction["coefficient_gwh"].sum())
    critical=frame.loc[frame["critical_review"]].copy()
    priority=frame.loc[frame["priority"]=="High"].copy()
    groups=frame.groupby(["priority","issue_theme","evidence_tier"],dropna=False).agg(
        sectors=("sector_code","count"), transaction_gwh=("transaction_gwh","sum"),
        coefficient_gwh=("coefficient_gwh","sum"), abs_sum_allocation_gwh=("abs_gap_gwh","sum")
    ).reset_index().sort_values("abs_sum_allocation_gwh",ascending=False)
    benchmarks={
        "io_source_sha256":io.csv_sha256,
        "mapping_source_sha256":checksum(mapping_path),
        "notes_source_sha256":checksum(notes_path),
        "anchors_source_sha256":checksum(anchor_path),
        "sectors":int(len(frame)),"pending_count":int((frame["review_status"]=="pending").sum()),
        "high_priority_from_review_notes":len(priority),"critical_by_impact_or_name":len(critical),
        "producer_electricity_gwh":float(sum(GROUP_ANCHOR.values())),
        "household_electricity_gwh":float(anchors.gwh["household"]),
        "target_34_transaction_gwh":float(frame.loc[frame["is_selected_34"],"transaction_gwh"].sum()),
        "target_34_coefficient_gwh":float(frame.loc[frame["is_selected_34"],"coefficient_gwh"].sum()),
        "construction_transaction_gwh": construction_trans,
        "construction_coefficient_gwh": construction_coeff,
        "construction_ratio_trans_to_coeff": construction_trans/construction_coeff,
        "primary_production_transaction_gwh":float(frame.loc[frame["issue_theme"].eq("Primary production: end-use not established"),"transaction_gwh"].sum()),
        "primary_production_coefficient_gwh":float(frame.loc[frame["issue_theme"].eq("Primary production: end-use not established"),"coefficient_gwh"].sum()),
        "heesi_transport_electricity_gwh":292.0,"pln_2020_traction_tariff_gwh":292.44,
        "traction_difference_gwh":0.44,
        "transport_rail_transactions_gwh":float(transport.loc[transport["sector_code"]=="157","transaction_gwh"].iloc[0]),
        "transport_rail_coefficients_gwh":float(transport.loc[transport["sector_code"]=="157","coefficient_gwh"].iloc[0]),
        "pln_tariff_I_1_to_I_4_gwh":72239.85,
        "heesi_industry_gwh":88372.0,
        "source_coverage_warning":"PLN electricity sold to tariff classes differs from HEESI final energy boundaries. No automatic 185-sector calibration.",
    }
    if abs(benchmarks["target_34_transaction_gwh"]-34007.195341299)>0.001:
        raise AssertionError("Transaction target benchmark changed vs user's v2 Stage 3 log")
    if abs(benchmarks["target_34_coefficient_gwh"]-37745.013821894)>0.001:
        raise AssertionError("Coefficient target benchmark changed vs user's v2 Stage 3 log")
    if not np.isclose(transport["pln_rail_traction_sensitivity_gwh"].sum(),292.0):
        raise AssertionError("Rail-focused sensitivity is not electricity-conserving")
    if not (frame["approved_category"].str.strip()=="").all():
        raise AssertionError("Mapping approvals were changed")
    return frame, priority, critical, groups, construction, transport, benchmarks


def _sheet(wb:Workbook, name:str, df:pd.DataFrame, special=False):
    ws=wb.create_sheet(name)
    cols=list(df.columns)
    ws.append(cols)
    for vals in df.itertuples(index=False,name=None):
        row=[]
        for v in vals:
            if pd.isna(v): row.append("")
            elif isinstance(v,(np.integer,)): row.append(int(v))
            elif isinstance(v,(np.floating,)): row.append(float(v))
            elif isinstance(v,(np.bool_,)): row.append(bool(v))
            else: row.append(v)
        ws.append(row)
    ws.freeze_panes="D2" if len(cols)>5 else "A2"
    ws.auto_filter.ref=ws.dimensions
    ws.sheet_view.showGridLines=False
    ws.row_dimensions[1].height=30
    header=PatternFill("solid",fgColor="16263E")
    for c in ws[1]:
        c.font=Font(color="FFFFFF",bold=True,size=10)
        c.fill=header
        c.alignment=Alignment(vertical="center",wrap_text=True)
    for c_idx,name in enumerate(cols,1):
        if name in ("sector_name","issue_theme","source_claim","specific_next_evidence","review_reason"):
            width=45
        elif "url" in name or "evidence" in name:
            width=47
        elif name in ("sector_code","sector_agg","priority"):
            width=14
        elif "gwh" in name or "mtco2" in name:
            width=21
        else:width=24
        ws.column_dimensions[get_column_letter(c_idx)].width=width
    # readable, lightly striped; source-locked data green; caution fields orange.
    for i in range(2,ws.max_row+1):
        for c in ws[i]:
            if i%2==0: c.fill=PatternFill("solid",fgColor="F3F7FB")
            if c.column in [cols.index(k)+1 for k in ("sector_name","issue_theme","source_claim","specific_next_evidence") if k in cols]:
                c.alignment=Alignment(vertical="top",wrap_text=True)
                ws.row_dimensions[i].height=42
            if isinstance(c.value,(float,int)) and not isinstance(c.value,bool):
                c.number_format='#,##0.000;[Red](#,##0.000);-'
            if c.column in (cols.index("priority")+1,) if "priority" in cols else ():
                if c.value=="High":
                    c.font=Font(color="A25400",bold=True)
            if "review" in str(cols[c.column-1]) or "evidence" in str(cols[c.column-1]):
                if c.value=="pending" or c.value=="NOT OBSERVED: IO allocation proxy only":
                    c.font=Font(color="B66A17",bold=True)
            if c.column == cols.index("sector_code")+1 if "sector_code" in cols else False:
                c.number_format='@'
    return ws



def _cache_xlsx_formula_values(path: Path, names_to_frames: dict[str, pd.DataFrame], sheet_names: list[str]) -> None:
    """Cache four independently verified arithmetic Excel formulas without removing formulas.

    This avoids relying on a user's Excel instance for values to appear in previews.
    Excel still recalculates when opened; no external workbook engine is needed.
    """
    patches={}
    for name,df in names_to_frames.items():
        sheet_num=sheet_names.index(name)+1
        lookup={}
        for i,r in enumerate(df.itertuples(index=False),start=2):
            t=float(r.transaction_gwh)
            c=float(r.coefficient_gwh)
            lookup[f"P{i}"]=t-c
            lookup[f"Q{i}"]=abs(t-c)
            lookup[f"S{i}"]=t*1000*.20*.877/1e6
            lookup[f"T{i}"]=c*1000*.20*.877/1e6
        patches[f"xl/worksheets/sheet{sheet_num}.xml"]=lookup
    pat=re.compile(r'(<c r="([A-Z]+[0-9]+)"[^>]*><f>[^<]*</f><v>)([^<]*)(</v></c>)')
    fd,tmp=tempfile.mkstemp(prefix="stage3b_cache_",suffix=".xlsx",dir=path.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(path,"r") as source,zipfile.ZipFile(tmp,"w") as target:
            for info in source.infolist():
                content=source.read(info.filename)
                if info.filename in patches:
                    text=content.decode("utf-8")
                    replacements=[0]
                    def replace(m):
                        address=m.group(2)
                        if address not in patches[info.filename]:
                            return m.group(0)
                        replacements[0]+=1
                        return m.group(1)+format(patches[info.filename][address],".16g")+m.group(4)
                    text=pat.sub(replace,text)
                    if replacements[0] != len(patches[info.filename]):
                        raise AssertionError(f"Formula cache incomplete on {info.filename}: {replacements[0]}")
                    content=text.encode("utf-8")
                target.writestr(info,content)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def _save_artifacts(outdir:Path, frame, priority, critical, groups, construction, transport, benchmarks, sources):
    outdir.mkdir(parents=True,exist_ok=True)
    output_files={
      "all":"stage3b_all_185_evidence_review.csv",
      "priority":"stage3b_71_priority_sectors.csv",
      "critical":"stage3b_impact_critical_sectors.csv",
      "themes":"stage3b_risk_summary_by_theme.csv",
      "construction":"stage3b_construction_weight_sensitivity.csv",
      "transport":"stage3b_transport_traction_sensitivity.csv",
      "physical":"stage3b_2020_physical_electricity_intake_BLANK.csv",
      "sources":"stage3b_source_register.csv",
      "metrics":"stage3b_diagnostic_metrics.json",
      "workbook":"stage3b_evidence_review_workbook.xlsx",
    }
    if any((outdir/f).exists() for f in output_files.values()):
        raise FileExistsError("Refusing to overwrite existing Stage 3B output(s). Choose a new --outdir.")
    frame.to_csv(outdir/output_files["all"],index=False,encoding="utf-8-sig")
    priority.to_csv(outdir/output_files["priority"],index=False,encoding="utf-8-sig")
    critical.to_csv(outdir/output_files["critical"],index=False,encoding="utf-8-sig")
    groups.to_csv(outdir/output_files["themes"],index=False,encoding="utf-8-sig")
    construction.to_csv(outdir/output_files["construction"],index=False,encoding="utf-8-sig")
    transport.to_csv(outdir/output_files["transport"],index=False,encoding="utf-8-sig")
    with (outdir/output_files["metrics"]).open("x",encoding="utf-8") as f:
        json.dump({"version":VERSION,"diagnostics":benchmarks,"warnings":[
            "All 185 mapping rows remain PENDING and unchanged.",
            "No physical sector-level electricity consumption has been supplied.",
            "HEESI end-use activity definitions are not equivalent to BPS IO product sectors.",
            "292 GWh rail-focused case is an illustrative allocation based on a cross-source proximity, NOT a verified direct measurement of sector 157 electricity.",
            "PLN tariff-class industrial sales and HEESI industrial end-use totals are not equivalent accounting populations.",
            "Sector-specific kWh must be mapped through documented KBLI/IO concordance with coverage adjustments."
        ]},f,indent=2)
    physical=frame[["sector_code","sector_name","sector_agg","suggested_category"]].copy()
    for col in ["observed_year","observed_electricity_mwh","observation_unit","measurement_scope", "KBLI_code", "BPS_IO_to_KBLI_bridge_source", "data_source_url", "source_page_table", "survey_coverage", "verification_status","reviewer_notes"]:
        physical[col]=""
    physical["verification_status"]="NO_SECTOR_MEASUREMENT"
    physical.to_csv(outdir/output_files["physical"],index=False,encoding="utf-8-sig")
    sources.to_csv(outdir/output_files["sources"],index=False,encoding="utf-8-sig")
    wb=Workbook(); wb.remove(wb.active)
    summary=pd.DataFrame([
        ("Version",VERSION,"Read-only; NO category or approval changes"),
        ("IO source SHA256",benchmarks["io_source_sha256"],"Verified domestic 2020 IO"),
        ("Mapping source SHA256",benchmarks["mapping_source_sha256"],"185 pending; never overwritten"),
        ("High priority mapping entries",benchmarks["high_priority_from_review_notes"],"Source review still required"),
        ("Impact-critical flagged entries",benchmarks["critical_by_impact_or_name"],"Mechanical prioritization, not evidence"),
        ("HEESI industry GWh",88372,"End-use category only"),
        ("HEESI commercial GWh",58159,"End-use category only"),
        ("HEESI transport GWh",292,"End-use category only"),
        ("HEESI household GWh",112754,"OUTSIDE 185 producing sectors"),
        ("Producer electricity GWh",146823,"No household electricity allocated to producers"),
        ("Target 34 transaction GWh",benchmarks["target_34_transaction_gwh"],"Reproduced v2 benchmark"),
        ("Target 34 coefficients GWh",benchmarks["target_34_coefficient_gwh"],"Reproduced v2 benchmark"),
        ("Construction transaction GWh",benchmarks["construction_transaction_gwh"],"Economic proxy, not physical"),
        ("Construction coefficients GWh",benchmarks["construction_coefficient_gwh"],"Economic proxy, not physical"),
        ("PLN 2020 traction tariff GWh",292.44,"PLN tariff data, different reporting system"),
        ("HEESI transport electricity GWh",292,"Rounded end-use data"),
        ("BPS manufacturing source","BPS publication 6103019","2020 survey covers medium/large manufacturers, not all economy"),
    ],columns=["Check","Value","Interpretation"])
    _sheet(wb,"Read Me & Gates",summary)
    s=wb["Read Me & Gates"];s.column_dimensions["A"].width=39;s.column_dimensions["B"].width=70;s.column_dimensions["C"].width=88
    assump=pd.DataFrame([
        ("industrial_electricity_efficiency_fraction",0.20,"fraction","Illustrative S2 assumed 20% reduction"),
        ("electricity_ef_tco2_per_mwh",0.877,"tCO2/MWh","Legacy MySQL electricity emission factor; NOT verified"),
        ("mwh_per_gwh",1000.0,"MWh/GWh","Unit conversion"),
        ("tco2_per_mtco2",1000000.0,"tCO2/MtCO2","Unit conversion"),
        ("heesi_transport_gwh",292.0,"GWh","HEESI 2021: 2020 transport electricity"),
        ("pln_traction_tariff_gwh",292.44,"GWh","PLN 2020 Statistics, Table 14"),
    ],columns=["Assumption","Value","Unit","Interpretation/source"])
    _sheet(wb,"Assumptions",assump)
    ass=wb["Assumptions"];ass.column_dimensions["A"].width=50;ass.column_dimensions["D"].width=75
    for n in range(2,8):ass.cell(n,2).font=Font(color="1565C0",bold=True)
    _sheet(wb,"Priority 71",priority)
    _sheet(wb,"Impact Critical",critical)
    _sheet(wb,"All 185 Evidence",frame)
    _sheet(wb,"Construction 149-153",construction)
    _sheet(wb,"Transport 157-162",transport)
    _sheet(wb,"Issue Theme Summary",groups)
    _sheet(wb,"Physical MWh Intake",physical)
    _sheet(wb,"Official Source Register",sources)
    # Reviewer entry: new physical input sheet; blue inputs, no implied approvals.
    intake=wb["Physical MWh Intake"]
    for label in ("observed_electricity_mwh","observed_year","data_source_url","KBLI_code", "survey_coverage"):
        i=list(physical.columns).index(label)+1
        for row in intake.iter_rows(min_row=2,min_col=i,max_col=i):
            for cell in row: cell.font=Font(color="1565C0")
    dv=DataValidation(type="list",formula1='"NO_SECTOR_MEASUREMENT,PROVISIONAL_DATA,EXTERNALLY_VERIFIED"')
    dv.error="Use the listed verification status";dv.errorTitle="Evidence status";dv.showErrorMessage=True
    intake.add_data_validation(dv)
    dv.add(f"{get_column_letter(list(physical.columns).index('verification_status')+1)}2:{get_column_letter(list(physical.columns).index('verification_status')+1)}186")
    # Explicit separation between imported numbers and analyst-calculated differences.
    for tab in ["All 185 Evidence","Priority 71","Impact Critical"]:
        ws=wb[tab];cols=[x.value for x in ws[1]]
        trans_col=get_column_letter(cols.index("transaction_gwh")+1)
        coeff_col=get_column_letter(cols.index("coefficient_gwh")+1)
        diff_col=get_column_letter(cols.index("allocation_gap_gwh")+1)
        abs_col=get_column_letter(cols.index("abs_gap_gwh")+1)
        s2a_col=get_column_letter(cols.index("trans_s2_20pct_electricity_mtco2")+1)
        s2b_col=get_column_letter(cols.index("coeff_s2_20pct_electricity_mtco2")+1)
        for i in range(2,ws.max_row+1):
            ws[f"{diff_col}{i}"]=f"={trans_col}{i}-{coeff_col}{i}"
            ws[f"{abs_col}{i}"]=f"=ABS({diff_col}{i})"
            ws[f"{s2a_col}{i}"]=f"={trans_col}{i}*Assumptions!$B$4*Assumptions!$B$2*Assumptions!$B$3/Assumptions!$B$5"
            ws[f"{s2b_col}{i}"]=f"={coeff_col}{i}*Assumptions!$B$4*Assumptions!$B$2*Assumptions!$B$3/Assumptions!$B$5"
        ws.conditional_formatting.add(f"{abs_col}2:{abs_col}{ws.max_row}", ColorScaleRule(start_type="min",start_color="FFFFFF",end_type="max",end_color="FDE3BB"))
    # Cross-check plot uses the imported totals; no publication-grade sector MWh implied.
    plot=wb.create_sheet("Critical comparisons")
    for row in [("Diagnostic group","Transaction GWh","Coefficient GWh"),
                ("Construction 149-153",benchmarks["construction_transaction_gwh"],benchmarks["construction_coefficient_gwh"]),
                ("Primary producers (review-flagged)",benchmarks["primary_production_transaction_gwh"],benchmarks["primary_production_coefficient_gwh"]),
                ("34 selected industry targets",benchmarks["target_34_transaction_gwh"],benchmarks["target_34_coefficient_gwh"])]:
        plot.append(row)
    plot.column_dimensions["A"].width=45
    plot.column_dimensions["B"].width=22
    plot.column_dimensions["C"].width=22
    plot.sheet_view.showGridLines=False
    for cell in plot[1]:
        cell.font=Font(color="FFFFFF",bold=True)
        cell.fill=PatternFill("solid",fgColor="16263E")
    chart=BarChart()
    chart.title="Electricity allocation assumptions: not measured GWh"
    chart.y_axis.title="Allocated electricity (GWh)"
    chart.x_axis.title="Flagged sectors"
    chart.add_data(Reference(plot,min_col=2,max_col=3,min_row=1,max_row=4),titles_from_data=True)
    chart.set_categories(Reference(plot,min_col=1,min_row=2,max_row=4))
    chart.width=19;chart.height=11
    plot.add_chart(chart,"E2")
    wb.calculation.fullCalcOnLoad=True
    final_workbook=outdir/output_files["workbook"]
    wb.save(final_workbook)
    _cache_xlsx_formula_values(final_workbook,
        {"Priority 71":priority, "Impact Critical":critical, "All 185 Evidence":frame},wb.sheetnames)
    return output_files


def make_sources() -> pd.DataFrame:
    return pd.DataFrame([
        {"source":"HEESI 2021","year":2021,"support":"2020 final electricity end-use anchors; official definitions of industry, commerce, transport, own-use and losses; glossary pp. 115-126","url":HEESI_URL,"limitation":"No 185-product electricity consumption table"},
        {"source":"PLN Statistics 2020","year":2020,"support":"Table 14: traction tariff T-1 electricity = 292.44 GWh; industrial tariff I-1..I-4 sales = 72,239.85 GWh","url":PLN_URL,"limitation":"Tariff/customer-class coverage differs from HEESI's economy-wide end-use categories"},
        {"source":"BPS Manufacturing Industry Statistics 2020","year":2020,"support":"Documents electricity and fuel used by medium/large manufacturing establishments; KBLI2020","url":BPS_MANUFACTURING_URL,"limitation":"Not all economy; need actual table and 185-product IO-to-KBLI concordance; no figures assumed"},
    ])


def main():
    p=argparse.ArgumentParser(description="Read-only HEESI 2020 Stage 3B sector mapping evidence audit")
    p.add_argument("--io",default="static/data/2020/io_ind_2020_domestic.csv")
    p.add_argument("--mapping",default="output/research/stage3/sector_mapping_2020_proposed_v2_UNAPPROVED.csv")
    p.add_argument("--notes",default="sector_mapping_2020_review_notes.csv")
    p.add_argument("--anchors",default="heesi2021_electricity_enduse_2020.csv")
    p.add_argument("--outdir",default="output/research/stage3b/source_review_v1")
    a=p.parse_args()
    sources={str(v):checksum(v) for v in [a.io,a.mapping,a.notes,a.anchors]}
    frame, priority, critical, groups, construction, transport, report=audit(Path(a.io),Path(a.mapping),Path(a.notes),Path(a.anchors))
    files=_save_artifacts(Path(a.outdir),frame,priority,critical,groups,construction,transport,report,make_sources())
    for f,h in sources.items():
        if checksum(f)!=h: raise AssertionError(f"Input unexpectedly changed during audit: {f}")
    print(f"STAGE 3B AUDIT COMPLETE | year=2020 | IO 185 domestic products | inputs unchanged")
    print(f"Review coverage: {report['high_priority_from_review_notes']} original HIGH, {report['critical_by_impact_or_name']} impact-critical, 185 PENDING")
    print(f"Producer: {report['producer_electricity_gwh']:,.0f} GWh | household outside: {report['household_electricity_gwh']:,.0f} GWh")
    print(f"34 industrial targets: transactions={report['target_34_transaction_gwh']:,.3f} GWh; coefficients={report['target_34_coefficient_gwh']:,.3f} GWh")
    print(f"Construction products 149-153: transactions={report['construction_transaction_gwh']:,.3f} GWh; coefficients={report['construction_coefficient_gwh']:,.3f} GWh")
    print(f"Transport: HEESI={report['heesi_transport_electricity_gwh']:.2f} GWh; PLN traction tariff={report['pln_2020_traction_tariff_gwh']:.2f} GWh (cross-source comparison, not assumed identity)")
    print(f"Rail 157 allocated: transactions={report['transport_rail_transactions_gwh']:.3f} GWh; coefficients={report['transport_rail_coefficients_gwh']:.3f} GWh; all-rail 292 GWh is an UNAPPROVED stress test")
    print(f"Files created in {a.outdir}: {len(files)}; no legacy changes")

if __name__=="__main__":
    main()
