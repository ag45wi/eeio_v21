"""Request #7 Stage 3: source-anchored 2020 electricity end-use allocation.

READ FIRST
----------
* 2020 electricity category totals come from HEESI 2021, sections 5.1.1,
  5.2.1, 5.3.1, 5.4.1 (GWh); household electricity stays OUTSIDE 185 IO sectors.
* IO transactions/coefficients allocate within categories as MONETARY PROXIES.
* A machine-suggested 185-sector classification is NEVER pre-approved.
  Approval must be evidenced by a human-reviewed mapping CSV. --allow-provisional
  enables exploratory diagnostics only, never validated research claims.
* National energy FEC / factor conversions retain the previous Stage 2 meaning.
  Fuel/gas assumptions and generation-electricity overlap remain UNRESOLVED.
* The original EEIO app, cache, DB, 2016 files and Request #1-#6 are untouched.

Example PowerShell commands, from the EEIO application root:
  python request7_stage3_heesi_enduse_v21.py --check-anchors
  python request7_stage3_heesi_enduse_v21.py --init-mapping output/research/stage3/sector_mapping_2020_review.csv
  python request7_stage3_heesi_enduse_v21.py --mapping output/research/stage3/sector_mapping_2020_review.csv --audit-mapping
  python request7_stage3_heesi_enduse_v21.py --mapping output/research/stage3/sector_mapping_2020_review.csv --allow-provisional --run-scenarios --target-groups 07,08,10,11
  python test_request7_stage3_heesi_enduse_v21.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from request7_domestic_eeio_v21 import (
    SECTOR_CODES, N_SECTORS, DomesticIO, DomesticResearchModel,
    build_domestic_research_model, load_domestic_io, load_energy_database,
    run_industrial_factorial, select_target_sectors, sector_metadata,
    compute_emission_indicators,
)
from request7_stage2_environmental_audit_v21 import (
    sector_footprint_breakdown, selected_final_demand_upstream_origin,
)

VERSION = "request7-domestic-heesi-stage3-v1"
END_USE_CATEGORIES = ("industry", "commercial", "transport", "household")
PRODUCER_CATEGORIES = END_USE_CATEGORIES[:3]
ALLOCATION_METHODS = ("transactions", "coefficients")
EXPECTED_HEESI_2020_GWH = {
    "industry": 88372.0, "household": 112754.0,
    "commercial": 58159.0, "transport": 292.0,
}
OFFICIAL_HEESI_2021_URL = (
    "https://www.esdm.go.id/assets/media/content/"
    "content-handbook-of-energy-and-economic-statistics-of-indonesia-2021.pdf"
)
DEFAULT_ANCHORS = Path(__file__).with_name("heesi2021_electricity_enduse_2020.csv")
#DEFAULT_ANCHORS = Path(__file__).with_name("static/data/2020/heesi2021_electricity_enduse_2020.csv")
STUDY_CASES = ("S0", "S2", "S6", "S8")
MAPPING_COLUMNS = (
    "sector_code", "sector_name", "sector_agg", "suggested_category",
    "approved_category", "review_status", "mapping_rationale", "reviewer", "review_date", "review_evidence"
)
NONPRODUCTION_CATEGORY = "excluded"
SOURCE_ROW_ELECTRICITY = 144  # BPS product 145


def _sha_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _close(label: str, actual: float, expected: float,
           rtol: float = 1e-10, atol: float = 1e-4) -> None:
    if not np.isclose(actual, expected, rtol=rtol, atol=atol):
        raise AssertionError(f"{label}: {actual:.10f} != {expected:.10f}")


@dataclass(frozen=True)
class EndUseAnchors:
    year: int
    gwh: dict[str, float]
    source_url: str
    source_title: str
    source_sections: dict[str, str]
    path: str
    sha256: str

    @property
    def electricity_mwh(self) -> float:
        return 1000 * sum(self.gwh.values())

    @property
    def producer_mwh(self) -> float:
        return 1000 * sum(self.gwh[c] for c in PRODUCER_CATEGORIES)


def read_enduse_anchors(path: str | Path = DEFAULT_ANCHORS) -> EndUseAnchors:
    path = Path(path)
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    required = {"year", "category", "gwh", "unit", "source_title", "source_section", "source_url"}
    if not required.issubset(frame.columns):
        raise ValueError(f"HEESI anchor file missing fields: {required - set(frame.columns)}")
    if len(frame) != 4 or set(frame["category"]) != set(END_USE_CATEGORIES):
        raise ValueError("HEESI anchor file must have exactly four distinct end-use categories")
    if any(frame["year"] != "2020"):
        raise ValueError("Stage 3 anchors are only verified for calendar year 2020")
    if any(frame["unit"] != "GWh"):
        raise ValueError("HEESI end-use values must be in GWh")
    if frame["source_url"].nunique() != 1 or frame["source_title"].nunique() != 1:
        raise ValueError("End-use categories must share traceable publication metadata")
    numeric = pd.to_numeric(frame["gwh"], errors="raise").to_numpy(float)
    if not np.all(np.isfinite(numeric)) or np.any(numeric < 0):
        raise ValueError("HEESI anchors must be finite and nonnegative")
    published = dict(zip(frame["category"], numeric.tolist()))
    if published != EXPECTED_HEESI_2020_GWH:
        raise ValueError(
            "HEESI 2020 anchor values do not match the cited published GWh "
            f"figures (expected {EXPECTED_HEESI_2020_GWH}, found {published}). "
            "Do not silently revise this source dataset."
        )
    if frame["source_url"].iloc[0] != OFFICIAL_HEESI_2021_URL:
        raise ValueError("HEESI 2020 electricity provenance URL does not match the verified original")
    return EndUseAnchors(
        year=2020,
        gwh=dict(zip(frame["category"], numeric.tolist())),
        source_url=frame["source_url"].iloc[0],
        source_title=frame["source_title"].iloc[0],
        source_sections=dict(zip(frame["category"], frame["source_section"])),
        path=str(path.resolve()), sha256=_sha_file(path),
    )


def reconcile_heesi_with_db(total_db_mwh: float, anchors: EndUseAnchors,
                            max_rounding_gap_gwh: float = 2.0) -> dict[str, float]:
    """NO silent rescaling of published HEESI numbers; residual is separate."""
    if not np.isfinite(total_db_mwh) or total_db_mwh <= 0:
        raise ValueError("National database electricity quantity must be positive")
    residual_mwh = float(total_db_mwh - anchors.electricity_mwh)
    if abs(residual_mwh) > 1000.0 * max_rounding_gap_gwh:
        raise ValueError(
            f"HEESI vs FEC difference {residual_mwh/1000:.3f} GWh exceeds "
            f"rounding tolerance {max_rounding_gap_gwh:.3f} GWh; do not calibrate"
        )
    return {
        "db_total_mwh": float(total_db_mwh),
        "heesi_total_mwh": anchors.electricity_mwh,
        "producer_enduse_mwh": anchors.producer_mwh,
        "household_mwh": anchors.gwh["household"] * 1000.0,
        "rounding_residual_mwh": residual_mwh,
        "residual_pct_of_db": 100.0 * residual_mwh / total_db_mwh,
    }


def suggest_enduse_category(code: str, agg: str, name: str) -> tuple[str, str]:
    """An UNREVIEWED heuristic, never evidence of physical end-use category."""
    name_low = str(name).casefold().strip()
    group = str(agg).strip().zfill(2)
    if code == "145":
        return "excluded", "Power supply product 145: avoid allocating final-use electricity to its own generating sector; human review required"
    if group in {"03", "05", "06", "07", "08", "09", "10", "11"}:
        return "industry", f"Sector aggregate group {group}: industry candidate only; production/energy-balance boundary needs review"
    if group == "12":
        if any(token in name_low for token in ("transport services", "freight", "ferry", "land transportation")):
            return "transport", "Transport service label within group 12; confirm HEESI transport boundary"
        return "industry", "Transport group contains manufactured transport products too; this guess requires review"
    if group == "13":
        if any(token in name_low for token in ("rail transport services", "transportation support services")):
            return "transport", "Transport-related service name; requires review because HEESI and IO are not coterminous"
        return "commercial", "Services aggregate; could also include industrial repair or transport activities"
    if group == "14":
        keywords = ("rubber", "tire", "printing", "plastic", "medicine", "equipment", "appliance", "furniture", "goods")
        if any(word in name_low for word in keywords):
            return "industry", "Other-sector manufacturing-like label; HUMAN REVIEW REQUIRED"
        return "commercial", "Other-sector default commercial; heterogeneous group requires review"
    if group == "01":
        return "industry", "Energy supply/extraction could consume industrial electricity; explicit source validation required"
    if group in {"02", "04"}:
        return "industry", "Primary-production placeholder; HEESI industry category may not match this IO group"
    return "commercial", f"Fallback for unrecognized sector aggregate {group}; must review before use"


def mapping_template(sector_frame: pd.DataFrame) -> pd.DataFrame:
    """185 suggested mappings, ALL status=pending; never generate approvals."""
    metadata = sector_metadata(sector_frame)
    proposed = [suggest_enduse_category(row.sector_code, row.sector_agg, row.sector_name)
                for row in metadata.itertuples(index=False)]
    result = metadata.copy()
    result["suggested_category"] = [c for c, _ in proposed]
    result["approved_category"] = ""
    result["review_status"] = "pending"
    result["mapping_rationale"] = [desc for _, desc in proposed]
    result["reviewer"] = ""
    result["review_date"] = ""
    result["review_evidence"] = ""
    return result[list(MAPPING_COLUMNS)]


def write_mapping_template(sector_frame: pd.DataFrame, target: str | Path) -> Path:
    target = Path(target)
    frame = mapping_template(sector_frame)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", newline="", encoding="utf-8-sig") as handle:
        frame.to_csv(handle, index=False)
    return target


@dataclass
class ReviewedMapping:
    frame: pd.DataFrame
    effective_category: tuple[str, ...]
    pending_count: int
    approved_count: int
    is_reviewed: bool
    is_provisional: bool
    path: str
    sha256: str


def read_sector_mapping(path: str | Path, sectors: pd.DataFrame,
                        allow_provisional: bool = False) -> ReviewedMapping:
    path = Path(path)
    frame = pd.read_csv(path, dtype="string", keep_default_na=False, encoding="utf-8-sig")
    if not set(MAPPING_COLUMNS).issubset(frame.columns):
        raise ValueError(f"Mapping file missing columns: {set(MAPPING_COLUMNS) - set(frame.columns)}")
    if len(frame) != N_SECTORS or frame["sector_code"].duplicated().any():
        raise ValueError("Mapping must contain exactly 185 unique sector rows")
    frame = frame[list(MAPPING_COLUMNS)].copy()
    frame["sector_code"] = frame["sector_code"].str.strip().str.zfill(3)
    if tuple(frame["sector_code"]) != SECTOR_CODES:
        raise ValueError("Mapping sector codes must be 001..185 in order; do not reorder or omit")
    meta = sector_metadata(sectors)
    for field in ("sector_name", "sector_agg"):
        if not (frame[field].astype(str).str.strip().to_numpy() ==
                meta[field].astype(str).str.strip().to_numpy()).all():
            raise ValueError(f"Mapping {field} must match live sector_lst metadata")
    statuses = frame["review_status"].str.strip().str.lower()
    if not statuses.isin(["pending", "approved"]).all():
        raise ValueError("review_status must be either 'pending' or 'approved'")
    approved = statuses == "approved"
    pending = ~approved
    for row in frame.loc[approved].itertuples(index=False):
        if str(row.approved_category) not in (*PRODUCER_CATEGORIES, "excluded"):
            raise ValueError(f"Approved sector {row.sector_code} needs approved_category")
        if (not str(row.mapping_rationale).strip()
                or not str(row.reviewer).strip()
                or not str(row.review_date).strip()
                or not str(row.review_evidence).strip()):
            raise ValueError(
                f"Approved sector {row.sector_code} requires rationale, "
                "reviewer, review_date, and independent review_evidence"
            )
    if pending.any() and not allow_provisional:
        raise ValueError(
            f"Mapping contains {int(pending.sum())} pending sectors. "
            "Human review required or use --allow-provisional for EXPLORATORY ONLY."
        )
    categories: list[str] = []
    for row in frame.itertuples(index=False):
        category = (str(row.approved_category).strip()
                    if str(row.review_status).lower().strip() == "approved"
                    else str(row.suggested_category).strip())
        if category not in (*PRODUCER_CATEGORIES, "excluded"):
            raise ValueError(f"Sector {row.sector_code} category invalid: {category!r}")
        categories.append(category)
    if categories[SOURCE_ROW_ELECTRICITY] != "excluded":
        raise ValueError("Sector 145 must stay excluded from final end-use allocation in Stage 3")
    if not all(c in categories for c in PRODUCER_CATEGORIES):
        raise ValueError("Mapping lacks at least one industry, commercial or transport recipient")
    frame["effective_category"] = categories
    return ReviewedMapping(
        frame=frame, effective_category=tuple(categories),
        pending_count=int(pending.sum()), approved_count=int(approved.sum()),
        is_reviewed=not pending.any(), is_provisional=bool(pending.any()),
        path=str(path.resolve()), sha256=_sha_file(path),
    )


def audit_mapping_unreviewed(path: str | Path, sectors: pd.DataFrame) -> pd.DataFrame:
    """Show suggestions and pending status without authorizing calibration."""
    frame = pd.read_csv(path, dtype="string", keep_default_na=False, encoding="utf-8-sig")
    meta = sector_metadata(sectors)
    if len(frame) != N_SECTORS or tuple(frame["sector_code"].str.zfill(3)) != SECTOR_CODES:
        raise ValueError("Review template does not contain 185 ordered sector codes")
    if any(frame["sector_name"].str.strip() != meta["sector_name"].astype("string").str.strip()):
        raise ValueError("Review template names do not match MySQL sector list")
    summary = (frame.groupby(["suggested_category", "review_status"], dropna=False)
               .size().reset_index(name="n_sectors"))
    return summary


def enduse_calibrated_model(base: DomesticResearchModel, anchors: EndUseAnchors,
                            mapping: ReviewedMapping,
                            method: str) -> tuple[DomesticResearchModel, pd.DataFrame, dict[str, float]]:
    """Calibrate sector-category totals; keep household/rounding residual OUTSIDE IO."""
    if base.year != 2020 or anchors.year != 2020:
        raise ValueError("HEESI 2020 calibration is only valid for 2020 domestic IO")
    if method not in ALLOCATION_METHODS:
        raise ValueError(f"Unknown within-category method: {method}")
    physical_db_mwh = float(base.mat_finCons[3].sum())
    ledger = reconcile_heesi_with_db(physical_db_mwh, anchors)
    weights = (base.io.Z[SOURCE_ROW_ELECTRICITY, :].copy()
               if method == "transactions" else base.io.A[SOURCE_ROW_ELECTRICITY, :].copy())
    if not np.all(np.isfinite(weights)) or np.any(weights < 0):
        raise ValueError("Electricity source economic IO row contains negative/invalid entries")
    categories = np.array(mapping.effective_category, dtype=object)
    alloc = np.zeros(N_SECTORS, dtype=float)
    for category in PRODUCER_CATEGORIES:
        mask = categories == category
        denom = float(weights[mask].sum())
        if not np.isfinite(denom) or denom <= 0:
            raise ValueError(f"Cannot allocate HEESI {category}: zero monetary weight in mapped sector group")
        alloc[mask] = (1000.0 * anchors.gwh[category] * weights[mask] / denom)
        _close(f"HEESI {category} calibration", float(alloc[mask].sum()),
               anchors.gwh[category] * 1000.0)
    if alloc[SOURCE_ROW_ELECTRICITY] != 0.0:
        raise AssertionError("Electricity sector must not receive end-use electricity")
    _close("producer HEESI electricity", float(alloc.sum()), anchors.producer_mwh)
    _close("DB versus producer+household+rounding ledger",
           float(alloc.sum()+ledger["household_mwh"]+ledger["rounding_residual_mwh"]),
           physical_db_mwh)
    mat = base.mat_finCons.copy()
    mat[3, :] = alloc
    model = replace(base, mat_finCons=mat, allocation=f"heesi2021_{method}")
    if not np.array_equal(base.mat_finCons[:3, :], model.mat_finCons[:3, :]):
        raise AssertionError("Other carrier matrices were modified")
    sector_frame = model.sector_meta.copy()
    sector_frame["heesi_enduse_category"] = categories
    sector_frame["mapping_review_status"] = mapping.frame["review_status"].astype(str).to_numpy()
    sector_frame["category_within_proxy"] = method
    sector_frame["sector_electricity_mwh"] = alloc
    sector_frame["within_category_share_pct"] = np.array([
        100.0 * alloc[i] / (1000.0 * anchors.gwh[c]) if c in PRODUCER_CATEGORIES else 0.0
        for i,c in enumerate(categories)
    ])
    sector_frame["is_power_sector"] = sector_frame["sector_code"] == "145"
    return model, sector_frame, ledger


@dataclass
class Stage3Results:
    models: dict[str, DomesticResearchModel]
    legacy: DomesticResearchModel
    mapping: ReviewedMapping
    anchors: EndUseAnchors
    targets: list[str]
    allocations: pd.DataFrame
    group_allocations: pd.DataFrame
    scenarios: pd.DataFrame
    comparison: pd.DataFrame
    ledger: pd.DataFrame
    hotspots: pd.DataFrame
    upstream: pd.DataFrame
    metadata: dict[str, Any]


def execute_stage3(io: DomesticIO, sectors: pd.DataFrame,
                   fec: pd.DataFrame, conversions: pd.DataFrame, factors: pd.DataFrame,
                   anchors: EndUseAnchors, mapping: ReviewedMapping,
                   target_codes: tuple[str, ...] | None = None,
                   target_groups: tuple[str, ...] | None = None,
                   fuel_source: str = "crude", factor_mode: str = "legacy",
                   methods: tuple[str, ...] = ALLOCATION_METHODS) -> Stage3Results:
    """Source-category-anchored 2020 scenarios; never mutate historical results."""
    if not methods or any(s not in ALLOCATION_METHODS for s in methods):
        raise ValueError("methods must be nonempty subset of transactions/coefficients")
    legacy = build_domestic_research_model(io, sectors, fec, conversions, factors,
                                           allocation="transactions", fuel_source=fuel_source)
    orig_mat = legacy.mat_finCons.copy()
    orig_ef = legacy.CO2_EF.copy()
    calibrated: dict[str, DomesticResearchModel] = {}
    alloc_tables: list[pd.DataFrame] = []
    ledger_reference = None
    for method in methods:
        model, sector_profile, ledger = enduse_calibrated_model(legacy, anchors, mapping, method)
        calibrated[method] = model
        alloc_tables.append(sector_profile)
        ledger_reference = ledger

    # All targets must truly be within the mapped INDUSTRY category, not merely
    # a similarly named group. This prevents applying industrial efficiency to
    # non-industry electricity categories.
    reference_model = next(iter(calibrated.values()))
    targets = select_target_sectors(reference_model, target_codes, target_groups)
    for target in targets:
        if mapping.effective_category[int(target)-1] != "industry":
            raise ValueError(f"Target {target} is not in HEESI industry classification")
    ledger = ledger_reference
    assert ledger is not None
    legacy_results = run_industrial_factorial(legacy, targets, factor_mode=factor_mode)
    legacy_index = legacy_results.set_index("case")
    outputs = []
    comparisons = []
    ledger_rows = []
    hotspot_frames = []
    upstream_frames = []
    for method, model in calibrated.items():
        before_mat = model.mat_finCons.copy()
        before_ef = model.CO2_EF.copy()
        result = run_industrial_factorial(model, targets, factor_mode=factor_mode)
        ref_electricity = float(model.mat_finCons[3].sum())
        for scenario_row in result.itertuples(index=False):
            grid_pct = float(scenario_row.grid_ef_pct)
            ef = float(model.CO2_EF[3,0]) * (1.0 + grid_pct/100.0)
            household_mt = ledger["household_mwh"] * ef / 1e6
            rounding_mt = ledger["rounding_residual_mwh"] * ef / 1e6
            producer_mt = float(scenario_row.electricity_proxy_mtco2)
            all_enduse_mt = producer_mt + household_mt + rounding_mt
            total_screening_mt = float(scenario_row.combustion_proxy_mtco2) + all_enduse_mt
            sum_expected_base = (ledger["db_total_mwh"]*model.CO2_EF[3,0] / 1e6)
            if scenario_row.case == "S0":
                _close("national DB electricity times EF", all_enduse_mt, sum_expected_base,
                       rtol=1e-10, atol=1e-4)
            outputs.append({**scenario_row._asdict(),
                "within_group_weights": method,
                "mapping_status": "provisional" if mapping.is_provisional else "reviewed_mapping_within_group_proxy",
                "production_linked_indicator_mtco2": float(scenario_row.combined_indicator_mtco2),
                "household_electricity_indicator_mtco2": household_mt,
                "rounding_residual_electricity_indicator_mtco2": rounding_mt,
                "all_enduse_electricity_indicator_mtco2": all_enduse_mt,
                "fossil_plus_all_enduse_screening_mtco2": total_screening_mt,
                "sector_electricity_input_mwh": ref_electricity,
                "household_electricity_input_mwh": ledger["household_mwh"],
                "rounding_residual_mwh": ledger["rounding_residual_mwh"],
                "national_db_electricity_mwh": ledger["db_total_mwh"],
                "selected_industry_mwh": float(model.mat_finCons[3, [int(c)-1 for c in targets]].sum()),
            })
            old = legacy_index.loc[scenario_row.case]
            comparisons.append({
                "case": scenario_row.case, "within_group_weights": method,
                "stage2_uncalibrated_screening_mtco2": float(old.combined_indicator_mtco2),
                "stage3_production_linked_mtco2": float(scenario_row.combined_indicator_mtco2),
                "stage3_all_enduse_screening_mtco2": total_screening_mt,
                "stage3_minus_stage2_screening_mtco2": total_screening_mt - float(old.combined_indicator_mtco2),
                "stage2_target_electricity_mwh": float(legacy.mat_finCons[3, [int(c)-1 for c in targets]].sum()),
                "stage3_target_electricity_mwh": float(model.mat_finCons[3, [int(c)-1 for c in targets]].sum()),
            })
        # End-use bookkeeping is a separate ledger, NOT a Leontief sector.
        for cat in (*PRODUCER_CATEGORIES, "household", "rounding_residual"):
            amount = (anchors.gwh[cat]*1000.0 if cat in PRODUCER_CATEGORIES or cat == "household"
                      else ledger["rounding_residual_mwh"])
            ledger_rows.append({"year": 2020, "method": method, "category": cat,
                                "electricity_mwh": amount,
                                "io_production_linked": cat in PRODUCER_CATEGORIES,
                                "source": "HEESI 2021" if cat != "rounding_residual" else "FEC conversion minus HEESI rounded sum",
                                "source_section": anchors.source_sections.get(cat, "not-applicable")})
        # Keep electricity scenario effects and embodied footprints separate.
        from request7_domestic_eeio_v21 import create_factorial_scenario
        from scenario_engine import ScenarioProcessor, ScenarioValidator
        processor = ScenarioProcessor(model)
        case_options = {"S0":(0,0), "S2":(-20,0), "S6":(0,-40), "S8":(-20,-40)}
        for case in STUDY_CASES:
            efficiency, grid = case_options[case]
            scenario = create_factorial_scenario(targets, efficiency, grid)
            ScenarioValidator.validate(scenario)
            scenario_inputs = processor.apply(model, scenario)
            footprints = sector_footprint_breakdown(
                model, scenario_inputs.mat_finCons, scenario_inputs.CO2_EF,
                factor_mode, case)
            footprints["within_group_weights"] = method
            footprints["mapping_status"] = "provisional" if mapping.is_provisional else "reviewed_mapping_within_group_proxy"
            hotspot_frames.append(footprints)
            upstream = selected_final_demand_upstream_origin(
                model, targets, scenario_inputs.mat_finCons,
                scenario_inputs.CO2_EF, factor_mode, case)
            upstream["within_group_weights"] = method
            upstream_frames.append(upstream)
        if not np.array_equal(model.mat_finCons, before_mat) or not np.array_equal(model.CO2_EF, before_ef, equal_nan=True):
            raise AssertionError("Stage 3 mutated calibrated model baseline")
    if not np.array_equal(legacy.mat_finCons, orig_mat) or not np.array_equal(legacy.CO2_EF, orig_ef, equal_nan=True):
        raise AssertionError("Stage 3 modified Stage 2 benchmark")
    all_allocations = pd.concat(alloc_tables, ignore_index=True)
    group_allocations = (all_allocations
        .groupby(["category_within_proxy", "sector_agg", "heesi_enduse_category"],
                 as_index=False)
        .agg(n_sectors=("sector_code", "size"),
             electricity_mwh=("sector_electricity_mwh", "sum")))
    group_allocations["share_of_producer_electricity_pct"] = (
        100.0 * group_allocations["electricity_mwh"] / anchors.producer_mwh
    )
    scenarios_df = pd.DataFrame(outputs)
    base_by_method = scenarios_df[scenarios_df.case=="S0"].set_index("within_group_weights")
    scenarios_df["screening_reduction_vs_S0_mtco2"] = scenarios_df.apply(
        lambda r: float(base_by_method.loc[r.within_group_weights, "fossil_plus_all_enduse_screening_mtco2"]
                        - r.fossil_plus_all_enduse_screening_mtco2), axis=1)
    metadata = {
        "version": VERSION, "year": 2020,
        "io_type": "BPS domestic transactions at basic prices, 185 products",
        "io_csv_sha256": io.csv_sha256,
        "heesi_source": anchors.source_url, "heesi_sections": anchors.source_sections,
        "heesi_anchor_sha256": anchors.sha256,
        "sector_mapping_sha256": mapping.sha256,
        "mapping_status": "provisional" if mapping.is_provisional else "reviewed_mapping_monetary_proxy",
        "unreviewed_sector_count": mapping.pending_count,
        "within_group_proxy_methods": list(calibrated),
        "fuel_source": fuel_source, "factor_mode": factor_mode,
        "target_sectors": targets,
        "national_electricity_reconciliation_mwh": ledger,
        "methodology_limitations": [
            "HEESI totals anchor category electricity but IO monetary weights remain sector proxies",
            "Household and small FEC/HEESI reconciliation residual remain OUTSIDE domestic IO production sectors",
            "The fossil-plus-electricity indicator is a screening diagnostic, NOT a GHG Protocol inventory",
            "Potential electricity EF and generation combustion overlap remains unresolved",
            "Fuel/Gas conversion and physical carrier use remain unverified",
            "IO x and y, production structures, and baseline input data are fixed across scenarios",
            "Machine-proposed category classification cannot become validated without human review",
        ],
    }
    return Stage3Results(
        models=calibrated, legacy=legacy, mapping=mapping, anchors=anchors,
        targets=targets, allocations=all_allocations, group_allocations=group_allocations,
        scenarios=scenarios_df, comparison=pd.DataFrame(comparisons),
        ledger=pd.DataFrame(ledger_rows),
        hotspots=pd.concat(hotspot_frames, ignore_index=True),
        upstream=pd.concat(upstream_frames, ignore_index=True),
        metadata=metadata)


def print_stage3_summary(result: Stage3Results, top: int = 8) -> None:
    print("\n=== REQUEST #7 / STAGE 3: HEESI-ANCHORED 2020 ELECTRICITY ===")
    print(f"IO hash: {result.legacy.io.csv_sha256}; anchor hash: {result.anchors.sha256}")
    print(f"Classification: {result.mapping.approved_count} approved / {result.mapping.pending_count} pending; "
          f"STATUS={'PROVISIONAL' if result.mapping.is_provisional else 'REVIEWED (but proxy weights)'}")
    ref = next(iter(result.models.values()))
    totals = reconcile_heesi_with_db(float(result.legacy.mat_finCons[3].sum()), result.anchors)
    print("\n--- HEESI END-USE LEDGER ---")
    for cat in END_USE_CATEGORIES:
        print(f"{cat:>13}: {result.anchors.gwh[cat]:,.3f} GWh (HEESI 2021)")
    print(f"DB electricity total: {totals['db_total_mwh']/1000:,.3f} GWh")
    print(f"HEESI aggregate:      {totals['heesi_total_mwh']/1000:,.3f} GWh")
    print(f"Reporting residual:   {totals['rounding_residual_mwh']/1000:,.6f} GWh (separate, not assigned)")
    print(f"Electricity entered into 185 sectors: {ref.mat_finCons[3].sum()/1000:,.3f} GWh")
    print(f"Household electricity outside IO:   {totals['household_mwh']/1000:,.3f} GWh")
    print("\n--- MANUSCRIPT TARGETS ---")
    print(f"Industrial targets: {len(result.targets)} codes: {', '.join(result.targets)}")
    for method, model in result.models.items():
        subset = result.allocations[result.allocations.category_within_proxy==method]
        print(f"[{method}] target electricity={float(model.mat_finCons[3, [int(s)-1 for s in result.targets]].sum())/1000:,.3f} GWh; "
              f"generator end-use allocation={model.mat_finCons[3,SOURCE_ROW_ELECTRICITY]:.0f} MWh")
        print(subset.nlargest(top, "sector_electricity_mwh")[["sector_code", "sector_name",
             "heesi_enduse_category", "sector_electricity_mwh"]].to_string(index=False,
                      float_format=lambda v: f"{v:,.2f}"))
    print("\n--- PRODUCER ELECTRICITY BY IO AGGREGATE AND HEESI CLASS ---")
    for method in result.models:
        group = (result.group_allocations
                 [result.group_allocations.category_within_proxy == method]
                 .sort_values("electricity_mwh", ascending=False).head(top))
        print(f"[{method}]")
        print(group[["sector_agg", "heesi_enduse_category", "n_sectors",
                     "electricity_mwh", "share_of_producer_electricity_pct"]]
              .to_string(index=False, float_format=lambda v: f"{v:,.3f}"))
    print("\n--- STAGE 2 / STAGE 3 COMPARISON ---")
    show = result.comparison[result.comparison.case.isin(["S0","S2","S6","S8"])]
    print(show[["case", "within_group_weights", "stage2_uncalibrated_screening_mtco2",
                "stage3_production_linked_mtco2", "stage3_all_enduse_screening_mtco2",
                "stage3_target_electricity_mwh"]].to_string(index=False,
                   float_format=lambda v: f"{v:,.6f}"))
    print("\n--- PRODUCTION-LINKED EMBODIED HOTSPOTS (NOT HOUSEHOLD) ---")
    selection = result.hotspots[(result.hotspots.case == "S0") &
                                (result.hotspots.within_group_weights == next(iter(result.models)))]
    print(selection.nlargest(top, "embodied_final_demand_tco2")[["sector_code", "sector_name",
         "embodied_final_demand_tco2", "upstream_for_final_demand_tco2"]].to_string(
         index=False, float_format=lambda v: f"{v:,.2f}"))
    print("STOP: Category anchors are sourced, but 185-sector assignment is a monetary PROXY.")
    print("STOP: Fossil factors and generation EF overlap remain unverified; no GHG Scope labels.")
    print("STOP: Household electricity is tracked separately and not Leontief-propagated.")


def save_stage3_outputs(result: Stage3Results, out_dir: str | Path) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = (f"request7_stage3_2020_{result.legacy.io.csv_sha256[:10]}_"
           f"{result.mapping.sha256[:10]}_{result.anchors.sha256[:10]}_"
           f"{result.metadata['fuel_source']}_{result.metadata['factor_mode']}")
    pieces: dict[str, Any] = {
        "heesi_enduse_reconciliation.csv": result.ledger,
        "sector_mapping_audit.csv": result.mapping.frame,
        "sector_electricity_allocation.csv": result.allocations,
        "group_electricity_crosswalk.csv": result.group_allocations,
        "scenario_screening_S0_S8.csv": result.scenarios,
        "stage2_stage3_comparison.csv": result.comparison,
        "production_embodied_hotspots.csv": result.hotspots,
        "upstream_selected_sectors.csv": result.upstream,
        "assumptions_and_provenance.json": result.metadata,
    }
    paths = [out_dir / f"{tag}_{name}" for name in pieces]
    existing = [str(p) for p in paths if p.exists()]
    if existing:
        raise FileExistsError("Stage 3 export refuses to overwrite existing outputs: " + ", ".join(existing))
    made = []
    try:
        for (filename, data), path in zip(pieces.items(), paths):
            with path.open("x", newline="", encoding="utf-8") as handle:
                if filename.endswith(".json"):
                    json.dump(data, handle, indent=2)
                else:
                    data.to_csv(handle, index=False)
            made.append(path)
    except BaseException:
        for p in made:
            p.unlink(missing_ok=True)
        raise
    return made


def _make_database_tables(year: int):
    from flask import Flask
    from dotenv import load_dotenv
    from util_db import db_init
    load_dotenv()
    app = Flask(__name__)
    db = db_init(app)
    with app.app_context():
        # Table results are returned before context closes; only SELECT queries.
        return load_energy_database(db, year)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--year", choices=(2020,), type=int, default=2020,
                   help="Stage 3 HEESI anchors currently verified for 2020 only")
    p.add_argument("--io", type=Path, default=None)
    p.add_argument("--anchors", type=Path, default=DEFAULT_ANCHORS)
    p.add_argument("--check-anchors", action="store_true", help="Verify source ledger without DB")
    p.add_argument("--init-mapping", type=Path, default=None, help="Create 185-row PENDING review template; new file only")
    p.add_argument("--audit-mapping", action="store_true", help="Report mapping statuses without running scenarios")
    p.add_argument("--mapping", type=Path, default=None, help="185-row reviewed or provisional mapping CSV")
    p.add_argument("--allow-provisional", action="store_true", help="Explicitly allow unreviewed sector guesses; exploratory only")
    p.add_argument("--run-scenarios", action="store_true", help="Run 9 cases under both allocation weights")
    p.add_argument("--weights", choices=("both", "transactions", "coefficients"), default="both")
    p.add_argument("--fuel-source", choices=("crude", "refinery"), default="crude")
    p.add_argument("--factor-mode", choices=("legacy", "provisional"), default="legacy")
    p.add_argument("--target-sector", action="append", dest="targets", default=None)
    p.add_argument("--target-groups", default=None,
                   help="Comma-separated sector_agg codes, for example 07,08,10,11")
    p.add_argument("--export-dir", type=Path, default=None,
                   help="Export eight new CSV/JSON files without overwriting prior research")
    p.add_argument("--top", type=int, default=8)
    args = p.parse_args(argv)
    anchors = read_enduse_anchors(args.anchors)
    print(f"HEESI end-use anchors (2020) verified: total {anchors.electricity_mwh/1000:,.0f} GWh; "
          f"industrial {anchors.gwh['industry']:,.0f}; household {anchors.gwh['household']:,.0f}; "
          f"commercial {anchors.gwh['commercial']:,.0f}; transport {anchors.gwh['transport']:,.0f}")
    if args.check_anchors:
        return 0
    io_path = args.io or Path(f"static/data/{args.year}/io_ind_{args.year}_domestic.csv")
    io = load_domestic_io(args.year, io_path)
    print(f"Domestic 2020 IO: 185 sectors; L@y residual={io.leontief_error:.10%}; source={io.csv_sha256[:16]}")
    sectors, fec, conversions, factors = _make_database_tables(args.year)
    if args.init_mapping is not None:
        target = write_mapping_template(sectors, args.init_mapping)
        print(f"Created UNREVIEWED mapping: {target} (185 pending sectors, zero approved)")
        print("Human review: populate approved_category, review_status=approved, reviewer, review_date and review_evidence")
        return 0
    if args.mapping is None:
        p.error("Supply --mapping PATH or first generate --init-mapping PATH")
    if args.audit_mapping:
        print("\n--- MAPPING REVIEW STATUS (NO SCENARIO) ---")
        print(audit_mapping_unreviewed(args.mapping, sectors).to_string(index=False))
        return 0
    mapping = read_sector_mapping(args.mapping, sectors, allow_provisional=args.allow_provisional)
    if not args.run_scenarios:
        print(f"Mapping parsed: {mapping.approved_count} approved, {mapping.pending_count} pending")
        print("Use --run-scenarios for exploratory / reviewed-classification benchmark")
        return 0
    methods = ALLOCATION_METHODS if args.weights == "both" else (args.weights,)
    result = execute_stage3(
        io, sectors, fec, conversions, factors, anchors, mapping,
        target_codes=tuple(args.targets) if args.targets else None,
        target_groups=tuple(x.strip() for x in args.target_groups.split(",")) if args.target_groups else None,
        fuel_source=args.fuel_source, factor_mode=args.factor_mode, methods=methods,
    )
    print_stage3_summary(result, top=args.top)
    if args.export_dir:
        paths = save_stage3_outputs(result, args.export_dir)
        print("\nCreated Stage 3 research files:")
        for f in paths:
            print("  ", f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
