"""Request #7 / Stage 2: read-only environmental extension and hotspot audit.

Run this as a SEPARATE research program beside request7_domestic_eeio_v21.py.
No changes to old EEIO engine, cache, MySQL records, or Request #1-#6 results.

The domestic IO matrix and published final-demand column 3090 are held fixed.
Physical energy is distributed via UNVALIDATED IO monetary proxies. This is a
sensitivity/reproducibility audit, NOT an official national greenhouse gas inventory.

PowerShell examples:
    python request7_stage2_environmental_audit_v21.py --year 2020
    python request7_stage2_environmental_audit_v21.py --year 2020 --target-groups 07,08,10,11
    python request7_stage2_environmental_audit_v21.py --year 2020 --target-groups 07,08,10,11 --export-dir output/research/stage2_v21
    python test_request7_stage2_environmental_audit_v21.py
"""
from __future__ import annotations

import argparse
from itertools import combinations
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
import json

import numpy as np
import pandas as pd

from request7_domestic_eeio_v21 import (
    ENERGY_NAMES,
    N_SECTORS,
    SECTOR_CODES,
    DomesticIO,
    DomesticResearchModel,
    build_domestic_research_model,
    compute_emission_indicators,
    create_factorial_scenario,
    emission_components_tonnes,
    load_domestic_io,
    load_energy_database,
    run_industrial_factorial,
    select_target_sectors,
)

VERSION = "request7-domestic-stage2-audit-v1"
ELECTRICITY_ROW = 3
ELECTRICITY_SOURCE_SECTOR = 144  # sector code 145; zero-based array index
MODES = (
    "transactions",
    "coefficients",
    "transactions_no_self",
    "coefficients_no_self",
)
STUDY_CASES = ("S0", "S2", "S6", "S8")
# Labels transcribed from the user's official MySQL sector_agg table (2020).
GROUP_LABELS = {
    "01": "Energy", "02": "Agriculture", "03": "Wood and forest products",
    "04": "Fisheries", "05": "Mining", "06": "Food, beverage and food processing",
    "07": "Chemical industries", "08": "Metal and heavy industries",
    "09": "Building, road and constructions", "10": "Textiles",
    "11": "Electronics", "12": "Transportation", "13": "Services", "14": "Others",
}

# Unlike absolute inventory, proxy allocation is NOT a physical sector balance.
METHOD_NOTES = {
    "transactions": "Weights proportional to domestic monetary intermediate flows Z[145,j]",
    "coefficients": "Weights proportional to domestic technical coefficients A[145,j]",
    "transactions_no_self": "Diagnostic Z weights with row 145, column 145 set to zero and remaining shares renormalized; NOT a validated FEC boundary",
    "coefficients_no_self": "Diagnostic A weights with row 145, column 145 set to zero and remaining shares renormalized; NOT a validated FEC boundary",
}


@dataclass
class Stage2Results:
    models: dict[str, DomesticResearchModel]
    targets: list[str]
    robustness: pd.DataFrame
    scenarios: pd.DataFrame
    carrier_boundary: pd.DataFrame
    factor_units: pd.DataFrame
    allocation_shares: pd.DataFrame
    allocation_pairwise: pd.DataFrame
    group_electricity: pd.DataFrame
    hotspot_sectors: pd.DataFrame
    upstream_origin: pd.DataFrame
    metadata: dict[str, Any]


def _assert_close(label: str, got: float, expected: float,
                  rtol: float = 1e-10, atol: float = 1e-5) -> None:
    if not np.isclose(got, expected, rtol=rtol, atol=atol):
        raise AssertionError(f"{label}: {got!r} != {expected!r}")


def build_electricity_allocation_variants(
    io: DomesticIO, sectors: pd.DataFrame, fec: pd.DataFrame,
    conversions: pd.DataFrame, factors: pd.DataFrame,
    fuel_source: str = "crude",
) -> dict[str, DomesticResearchModel]:
    """Four deliberately explicit, inventory-conserving monetary allocations.

    The non-self variants are hypothetical sensitivity calculations. They do
    not assert that removing generator-to-generator transactions is correct.
    """
    z = build_domestic_research_model(io, sectors, fec, conversions, factors,
                                      allocation="transactions", fuel_source=fuel_source)
    a = build_domestic_research_model(io, sectors, fec, conversions, factors,
                                      allocation="coefficients", fuel_source=fuel_source)
    variants: dict[str, DomesticResearchModel] = {
        "transactions": z,
        "coefficients": a,
    }
    for source_name, source in (("transactions", z), ("coefficients", a)):
        mode = f"{source_name}_no_self"
        weights = (io.Z[ELECTRICITY_SOURCE_SECTOR, :].copy()
                   if source_name == "transactions" else
                   io.A[ELECTRICITY_SOURCE_SECTOR, :].copy())
        if not np.all(np.isfinite(weights)) or np.any(weights < 0):
            raise ValueError(f"Invalid {source_name} weights")
        weights[ELECTRICITY_SOURCE_SECTOR] = 0.0
        if not (float(weights.sum()) > 0):
            raise ValueError(f"Cannot compute {mode}: nonself shares are zero")
        mat = source.mat_finCons.copy()
        total_e = float(mat[ELECTRICITY_ROW].sum())
        mat[ELECTRICITY_ROW] = total_e * weights / float(weights.sum())
        _assert_close("electricity conserved under excluded-self", float(mat[3].sum()), total_e)
        if mat[3, ELECTRICITY_SOURCE_SECTOR] != 0.0:
            raise AssertionError("Power-sector self entry was not zeroed")
        variants[mode] = replace(source, mat_finCons=mat, allocation=mode)

    if tuple(variants) != MODES:
        raise AssertionError("Unexpected allocation variants")
    nominal = z.mat_finCons.sum(axis=1)
    for name, model in variants.items():
        if model.mat_finCons.shape != (4, N_SECTORS):
            raise AssertionError(f"Invalid {name} energy shape")
        if not np.allclose(model.mat_finCons.sum(axis=1), nominal, rtol=1e-11, atol=1e-5):
            raise AssertionError(f"Allocation {name} failed national carrier conservation")
        if not np.array_equal(model.io.x, io.x) or not np.array_equal(model.io.y, io.y):
            raise AssertionError("Domestic IO must be unchanged")
    return variants


def compare_allocation_methods(
    models: dict[str, DomesticResearchModel]
) -> pd.DataFrame:
    """Full-sector share/rank stability for 6 pairwise allocation comparisons."""
    rows = []
    for a_name, b_name in combinations(MODES, 2):
        a = models[a_name].mat_finCons[ELECTRICITY_ROW]
        b = models[b_name].mat_finCons[ELECTRICITY_ROW]
        weights_a = a / a.sum()
        weights_b = b / b.sum()
        ra = _rank_desc(a)
        rb = _rank_desc(b)
        top_a = set(np.argsort(-a, kind="stable")[:10].tolist())
        top_b = set(np.argsort(-b, kind="stable")[:10].tolist())
        rows.append({
            "allocation_A": a_name, "allocation_B": b_name,
            "share_total_variation_pct": 50.0 * float(np.abs(weights_a - weights_b).sum()),
            "share_pearson": float(pd.Series(weights_a).corr(pd.Series(weights_b))),
            "rank_spearman": float(pd.Series(ra).corr(pd.Series(rb))),
            "top10_overlap": len(top_a.intersection(top_b)),
            "sectors": N_SECTORS,
            "physical_validity": "UNDETERMINED; BOTH ARE ECONOMIC ALLOCATION PROXIES",
        })
    return pd.DataFrame(rows)


def electricity_by_sector_group(
    allocations: pd.DataFrame,
) -> pd.DataFrame:
    """Aggregate allocation by real sector_agg metadata; not all manufacture."""
    grouped = (allocations
               .groupby(["electricity_allocation_mode", "sector_agg"], as_index=False)
               .agg(n_sectors=("sector_code", "count"),
                    electricity_mwh=("electricity_mwh", "sum")))
    grouped["group_name"] = grouped.sector_agg.map(GROUP_LABELS).fillna("Not in known mapping")
    total = grouped.groupby("electricity_allocation_mode")["electricity_mwh"].transform("sum")
    grouped["share_of_national_electricity_pct"] = grouped.electricity_mwh / total * 100.0
    grouped["electricity_group_rank"] = (
        grouped.groupby("electricity_allocation_mode")["electricity_mwh"]
        .rank(method="first", ascending=False).astype(int)
    )
    grouped["focus_industrial_subset"] = grouped.sector_agg.isin(("07", "08", "10", "11"))
    return grouped


def factor_unit_hypotheses(ef: np.ndarray) -> pd.DataFrame:
    """Physical unit RECONCILIATION only, not proof of source metadata.

    Explicit assumptions:
      Coal HHV GJ/tonne; Fuel HHV GJ/kL; gas multiplier m3/MMBTU;
      gas HHV MJ/m3; fossil EF tCO2/TJ; electricity EF tCO2/MWh.
    """
    ef = np.asarray(ef, dtype=float)
    if ef.shape != (4, 3):
        raise ValueError("CO2_EF must be 4x3")
    # Current engine conventions, in tCO2 per physical model unit.
    current = [
        ef[0, 0] * ef[0, 1] / 1_000.0,
        ef[1, 0] * ef[1, 1] / 1_000_000.0,
        ef[2, 2] * ef[2, 0] * ef[2, 1] / 1_000_000_000.0,
        ef[3, 0],
    ]
    expected = [
        ef[0, 0] * ef[0, 1] / 1_000.0,
        ef[1, 0] * ef[1, 1] / 1_000.0,
        ef[2, 2] * ef[2, 0] * ef[2, 1] / 1_000_000.0,
        ef[3, 0],
    ]
    physical_units = ("tonne", "kL", "MMBTU", "MWh")
    assumed = (
        "HHV GJ/tonne; EF tCO2/TJ",
        "HHV GJ/kL; EF tCO2/TJ",
        "multiplier m3/MMBTU; HHV MJ/m3; EF tCO2/TJ",
        "electricity EF tCO2/MWh; generation boundary NOT checked",
    )
    rows = []
    for i, name in enumerate(ENERGY_NAMES):
        ratio = (expected[i] / current[i]) if current[i] else float("nan")
        rows.append({
            "carrier": name, "physical_model_unit": physical_units[i],
            "assumed_source_factor_units": assumed[i],
            "current_tco2_per_unit": current[i],
            "conditional_expected_tco2_per_unit": expected[i],
            "expected_over_current": ratio,
            "validation_status": "UNVERIFIED SOURCE UNITS",
        })
    return pd.DataFrame(rows)


def energy_carrier_boundary(model: DomesticResearchModel) -> pd.DataFrame:
    """Component emissions and generator-assigned amounts, not GHG Scopes.

    No numerical figure here is claimed to quantify electricity double counting.
    Generator fossil-input combustion is a *potential* overlap witness only.
    """
    rows = []
    for mode in ("legacy", "provisional"):
        components = emission_components_tonnes(model.mat_finCons, model.CO2_EF, mode)
        sum_total = float(components.sum())
        for i, carrier in enumerate(ENERGY_NAMES):
            rows.append({
                "factor_mode": mode,
                "carrier": carrier,
                "model_physical_energy_total": float(model.mat_finCons[i].sum()),
                "source_inventory_mtco2": float(components[i].sum() / 1e6),
                "assigned_to_electricity_sector_mtco2": float(components[i, ELECTRICITY_SOURCE_SECTOR] / 1e6),
                "all_carriers_combined_mtco2": sum_total / 1e6,
                "boundary_status": "PROXY / NOT A VERIFIED NATIONAL GHG INVENTORY",
            })
    return pd.DataFrame(rows)


def _rank_desc(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    # Stable first-tie ranking, 1-based (does not conflate equal-value sectors).
    result = np.empty(len(values), dtype=int)
    order = np.argsort(-values, kind="stable")
    result[order] = np.arange(1, len(values) + 1)
    return result


def sector_footprint_breakdown(model: DomesticResearchModel, mat: np.ndarray,
                               ef: np.ndarray, factor_mode: str,
                               case: str) -> pd.DataFrame:
    """Separate sector-assigned environmental extension and final-demand attribution.

    For final-demand sector j:
      direct = f_j*y_j
      upstream = y_j * sum_i f_i*(L_ij - delta_ij)
      embodied = direct + upstream
    Here f_i = attributed emission inventory_i / gross output_i.

    These are EEIO algebraic partitions, NOT GHG Protocol scope categories.
    Final demand can be negative and is NEVER clamped.
    """
    components = emission_components_tonnes(mat, ef, factor_mode)
    source = components.sum(axis=0)
    x, y, l = model.io.x, model.io.y, model.io.L
    if np.any(x <= 0):
        raise ValueError("Gross output must be positive")
    direct_intensity = source / x
    full_intensity = direct_intensity @ l
    direct_final = direct_intensity * y
    upstream_final = (full_intensity - direct_intensity) * y
    embodied = direct_final + upstream_final
    _assert_close("embodied national equals source inventory", float(embodied.sum()),
                  float(source.sum()), atol=1e-3)
    result = model.sector_meta.copy()
    result["case"] = case
    result["final_demand_million_idr"] = y
    result["negative_final_demand"] = y < 0
    result["origin_inventory_tco2"] = source
    result["origin_combustion_tco2"] = components[:3].sum(axis=0)
    result["origin_electricity_ef_tco2"] = components[3]
    result["embodied_final_demand_tco2"] = embodied
    result["own_direct_for_final_demand_tco2"] = direct_final
    result["upstream_for_final_demand_tco2"] = upstream_final
    result["direct_intensity_tco2_per_million_idr"] = direct_intensity
    result["full_supply_chain_intensity_tco2_per_million_idr"] = full_intensity
    result["origin_inventory_rank"] = _rank_desc(source)
    result["embodied_final_demand_rank"] = _rank_desc(embodied)
    return result


def selected_final_demand_upstream_origin(
    model: DomesticResearchModel, targets: list[str], mat: np.ndarray,
    ef: np.ndarray, factor_mode: str, case: str,
) -> pd.DataFrame:
    """Origin-sector decomposition of *selected final-demand sector footprints*.

    This does NOT mean that electricity saving at the selected input sectors is
    restricted to final demand for these sectors; intervention and FD attribution
    are different concepts. Negative FD values remain signed.
    """
    indices = [int(s) - 1 for s in targets]
    y_targets = np.zeros(N_SECTORS, dtype=float)
    y_targets[indices] = model.io.y[indices]
    source = emission_components_tonnes(mat, ef, factor_mode).sum(axis=0)
    intensity = source / model.io.x
    demanded_output = model.io.L @ y_targets
    own_direct = intensity * y_targets
    upstream = intensity * (demanded_output - y_targets)
    total = own_direct + upstream
    expected = float((intensity @ model.io.L) @ y_targets)
    _assert_close(f"{case}: selected target origin attribution", float(total.sum()), expected,
                  atol=1e-4)
    result = model.sector_meta.copy()
    result["case"] = case
    result["origin_direct_tco2"] = own_direct
    result["origin_upstream_tco2"] = upstream
    result["origin_total_tco2"] = total
    result["upstream_rank"] = _rank_desc(upstream)
    result["total_rank"] = _rank_desc(total)
    result["selected_final_demand_total_tco2"] = expected
    result["selected_negative_final_demand_sectors"] = int((y_targets < 0).sum())
    return result


def _new_scenario_inputs(model: DomesticResearchModel, targets: list[str],
                         efficiency: int, grid_ef: int):
    from scenario_engine import ScenarioProcessor, ScenarioValidator
    params = create_factorial_scenario(targets, efficiency, grid_ef)
    ScenarioValidator.validate(params)
    return ScenarioProcessor(model).apply(model, params)


def _case_name_to_pct(case: str) -> tuple[int, int]:
    return {"S0": (0, 0), "S2": (-20, 0), "S6": (0, -40), "S8": (-20, -40)}[case]


def _postprocess_hotspot_rank_changes(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    base = (out[out["case"] == "S0"]
            .set_index("sector_code")[["origin_inventory_rank", "embodied_final_demand_rank",
                                        "origin_inventory_tco2", "embodied_final_demand_tco2"]])
    for rank_col in ("origin_inventory_rank", "embodied_final_demand_rank"):
        out[rank_col + "_change_vs_S0"] = (
            out[rank_col].to_numpy() -
            out["sector_code"].map(base[rank_col]).to_numpy()
        )
    for metric in ("origin_inventory_tco2", "embodied_final_demand_tco2"):
        out[metric + "_change_vs_S0"] = (
            out[metric].to_numpy() -
            out["sector_code"].map(base[metric]).to_numpy()
        )
    return out


def execute_stage2(
    io: DomesticIO, sectors: pd.DataFrame, fec: pd.DataFrame,
    conversions: pd.DataFrame, factors: pd.DataFrame,
    target_codes: tuple[str, ...] | None = None,
    target_groups: tuple[str, ...] | None = None,
    fuel_source: str = "crude",
    focus_allocation: str = "transactions",
    focus_factor_mode: str = "legacy",
) -> Stage2Results:
    """Run complete Stage 2 analysis entirely in memory; no database writes."""
    if focus_allocation not in MODES:
        raise ValueError(f"Unknown focus allocation: {focus_allocation}")
    if focus_factor_mode not in ("legacy", "provisional"):
        raise ValueError(f"Unknown factor mode: {focus_factor_mode}")
    models = build_electricity_allocation_variants(
        io, sectors, fec, conversions, factors, fuel_source=fuel_source
    )
    focus = models[focus_allocation]
    targets = select_target_sectors(focus, target_codes, target_groups)
    if "145" in targets:
        raise ValueError("Sector 145 cannot be a selected efficiency target in a "
                         "four-mode audit: the no_self methods set its electricity baseline to zero")
    indexes = np.array([int(code) - 1 for code in targets], dtype=int)
    base_energy = {mode: mod.mat_finCons.copy() for mode, mod in models.items()}
    base_ef = {mode: mod.CO2_EF.copy() for mode, mod in models.items()}

    # No allocation mode may alter any national energy total or national baseline.
    baseline_inventories: dict[str, float] = {}
    detail_frames: list[pd.DataFrame] = []
    robustness_rows = []
    for allocation, model in models.items():
        e = model.mat_finCons[3]
        for factor_mode in ("legacy", "provisional"):
            scenario_table = run_industrial_factorial(model, targets, factor_mode=factor_mode)
            scenario_table["electricity_allocation_mode"] = allocation
            scenario_table["electricity_self_excluded"] = allocation.endswith("_no_self")
            detail_frames.append(scenario_table)
            case = scenario_table.set_index("case")
            baseline = float(case.loc["S0", "combined_indicator_mtco2"])
            if factor_mode in baseline_inventories:
                _assert_close("national baseline invariant to monetary allocation",
                              baseline, baseline_inventories[factor_mode])
            else:
                baseline_inventories[factor_mode] = baseline
            robustness_rows.append({
                "year": io.year, "electricity_allocation_mode": allocation,
                "factor_mode": factor_mode, "fuel_source": fuel_source,
                "target_n": len(targets),
                "national_electricity_mwh": float(e.sum()),
                "electricity_self_mwh": float(e[ELECTRICITY_SOURCE_SECTOR]),
                "electricity_self_share_pct": 100 * float(e[ELECTRICITY_SOURCE_SECTOR] / e.sum()),
                "target_electricity_mwh": float(e[indexes].sum()),
                "target_electricity_share_pct": 100 * float(e[indexes].sum() / e.sum()),
                "s0_indicator_mtco2": baseline,
                "s2_indicator_mtco2": float(case.loc["S2", "combined_indicator_mtco2"]),
                "s6_indicator_mtco2": float(case.loc["S6", "combined_indicator_mtco2"]),
                "s8_indicator_mtco2": float(case.loc["S8", "combined_indicator_mtco2"]),
                "s2_avoided_mtco2": baseline - float(case.loc["S2", "combined_indicator_mtco2"]),
                "s8_avoided_mtco2": baseline - float(case.loc["S8", "combined_indicator_mtco2"]),
                "s2_reduction_pct": float(case.loc["S2", "reduction_pct_vs_S0"]),
                "s8_reduction_pct": float(case.loc["S8", "reduction_pct_vs_S0"]),
                "method_status": "IO MONETARY PROXY: UNVALIDATED PHYSICAL ALLOCATION",
            })
    robustness = pd.DataFrame(robustness_rows)
    scenarios = pd.concat(detail_frames, ignore_index=True)

    # Profile full 185-sector electricity allocations under each method.
    allocation_frames = []
    for allocation, model in models.items():
        d = model.sector_meta.copy()
        d["electricity_allocation_mode"] = allocation
        d["electricity_mwh"] = model.mat_finCons[ELECTRICITY_ROW]
        d["electricity_share_pct"] = 100 * d["electricity_mwh"] / model.mat_finCons[3].sum()
        d["electricity_rank"] = _rank_desc(model.mat_finCons[3])
        d["is_target"] = d["sector_code"].isin(targets)
        d["is_power_sector"] = d["sector_code"] == "145"
        allocation_frames.append(d)
    allocations = pd.concat(allocation_frames, ignore_index=True)
    pairwise = compare_allocation_methods(models)
    group_energy = electricity_by_sector_group(allocations)
    for mode, grouped in group_energy.groupby("electricity_allocation_mode"):
        _assert_close(f"{mode}: group electricity balance", float(grouped.electricity_mwh.sum()),
                      float(models[mode].mat_finCons[3].sum()))

    hotspot_frames = []
    upstream_frames = []
    for case in STUDY_CASES:
        efficiency, grid_ef = _case_name_to_pct(case)
        inputs = _new_scenario_inputs(focus, targets, efficiency, grid_ef)
        hotspot_frames.append(sector_footprint_breakdown(
            focus, inputs.mat_finCons, inputs.CO2_EF, focus_factor_mode, case
        ))
        upstream_frames.append(selected_final_demand_upstream_origin(
            focus, targets, inputs.mat_finCons, inputs.CO2_EF, focus_factor_mode, case
        ))
    hotspots = _postprocess_hotspot_rank_changes(pd.concat(hotspot_frames, ignore_index=True))
    upstream = pd.concat(upstream_frames, ignore_index=True)

    # Independent checks of model-array immutability and conservation.
    for mode, mod in models.items():
        if not np.array_equal(mod.mat_finCons, base_energy[mode]):
            raise AssertionError(f"{mode}: baseline energy mutated")
        if not np.array_equal(mod.CO2_EF, base_ef[mode], equal_nan=True):
            raise AssertionError(f"{mode}: baseline EF mutated")
    if len(scenarios) != 4 * 2 * 9 or len(hotspots) != N_SECTORS * 4:
        raise AssertionError("Stage 2 produced incomplete scenario or hotspot output")
    for case, grp in hotspots.groupby("case"):
        _assert_close(f"{case}: FD=source inventory", float(grp["embodied_final_demand_tco2"].sum()),
                      float(grp["origin_inventory_tco2"].sum()), atol=1e-3)

    metadata = {
        "stage2_version": VERSION,
        "base_runner_version": "request7-domestic-research-v1",
        "year": io.year,
        "io_type": "domestic", "io_csv": io.filename, "io_sha256": io.csv_sha256,
        "leontief_identity_relative_l1": io.leontief_error,
        "source_sector_for_electricity": "145",
        "electricity_allocation_methods": list(MODES),
        "method_descriptions": METHOD_NOTES,
        "focus_allocation": focus_allocation,
        "focus_factor_mode": focus_factor_mode,
        "fuel_source": fuel_source,
        "target_codes": targets,
        "negative_domestic_final_demand_sectors": [
            SECTOR_CODES[i] for i, yy in enumerate(io.y) if yy < 0
        ],
        "factor_units": "unverified; provisional x1000 Fuel/Gas used as sensitivity only",
        "double_counting_magnitude": "NOT QUANTIFIABLE FROM CURRENT EEIO EXTENSIONS",
        "model_scope_labels": "None; direct/upstream denote EEIO algebraic decomposition, not GHG Protocol Scope categories",
        "fixed_output_assumption": "Interventions change the environmental extension only; domestic IO structure, output and final demand are held fixed (no rebound/adoption/capacity simulation)",
        "scenarios": list(STUDY_CASES),
        "physical_emissions_boundary": "UNVALIDATED",
        "interpretation": "Exploratory robustness and sector-attribution analysis only; no publication-validated national CO2 value",
        "legacy_compatibility": "Original Request #1-#6 scripts, data and historical results unchanged",
    }
    return Stage2Results(
        models=models, targets=targets, robustness=robustness,
        scenarios=scenarios, carrier_boundary=energy_carrier_boundary(focus),
        factor_units=factor_unit_hypotheses(focus.CO2_EF),
        allocation_shares=allocations, allocation_pairwise=pairwise,
        group_electricity=group_energy, hotspot_sectors=hotspots,
        upstream_origin=upstream, metadata=metadata,
    )


def show_stage2_summary(result: Stage2Results, top: int = 10) -> None:
    top = int(top)
    if top < 1 or top > N_SECTORS:
        raise ValueError("top must be between 1 and 185")
    m = result.metadata
    print("\n=== REQUEST #7 STAGE 2: ENVIRONMENTAL EXTENSION AUDIT ===")
    print(f"Year {m['year']} | domestic IO | source checksum {m['io_sha256']}")
    print(f"Targets: {', '.join(result.targets)} ({len(result.targets)} sector(s))")
    print(f"Focusing sector attribution on allocation={m['focus_allocation']}, "
          f"factor_mode={m['focus_factor_mode']}, fuel_source={m['fuel_source']}")
    print("The 4 electricity methods are MONETARY PROXIES; 'no_self' methods are not validated corrections.")
    compact_cols = ["electricity_allocation_mode", "factor_mode",
                    "electricity_self_share_pct", "target_electricity_mwh",
                    "s0_indicator_mtco2", "s2_avoided_mtco2", "s8_avoided_mtco2",
                    "s8_reduction_pct"]
    print("\n--- ALLOCATION x FACTOR ROBUSTNESS: S0 / S2 / S8 ---")
    print(result.robustness[compact_cols].to_string(index=False,
                                                    float_format=lambda v: f"{v:,.5f}"))
    print("\n--- PAIRWISE ELECTRICITY ALLOCATION INSTABILITY ---")
    print(result.allocation_pairwise[["allocation_A", "allocation_B",
                                      "share_total_variation_pct", "rank_spearman",
                                      "top10_overlap"]].to_string(index=False,
                                           float_format=lambda v: f"{v:,.3f}"))
    print("Top10 overlap is a count out of 10; variation is % of national electricity reallocated.")
    print("\n--- ELECTRICITY ALLOCATION BY SECTOR_AGG ---")
    for mode in MODES:
        subset = result.group_electricity[
            result.group_electricity["electricity_allocation_mode"] == mode
        ].sort_values("electricity_group_rank")
        print(f"[{mode}]")
        print(subset[["sector_agg", "group_name", "n_sectors", "electricity_mwh",
                      "share_of_national_electricity_pct"]].to_string(index=False,
                                           float_format=lambda v: f"{v:,.2f}"))
    print("Groups 07/08/10/11 are an INDUSTRIAL SUBSET, not all manufacturing.")
    print("\n--- FACTOR UNIT RECONCILIATION (CONDITIONAL ASSUMPTIONS) ---")
    print(result.factor_units[["carrier", "physical_model_unit",
                               "current_tco2_per_unit", "conditional_expected_tco2_per_unit",
                               "expected_over_current"]].to_string(index=False,
                                                                    float_format=lambda v: f"{v:,.8f}"))
    print("Unit metadata must be sourced before replacing any existing EF conversion.")
    print("\n--- ENERGY-CARRIER BOUNDARY ---")
    print(result.carrier_boundary[["factor_mode", "carrier", "source_inventory_mtco2",
                                   "assigned_to_electricity_sector_mtco2"]].to_string(index=False,
                                                                                      float_format=lambda v: f"{v:,.5f}"))
    print("WARNING: generator-assigned fossil combustion is a possible overlap signal, "
          "NOT a measured double-counting amount.")
    print("\n--- ELECTRICITY RECIPIENT HOTSPOTS BY ALLOCATION METHOD ---")
    for mode in MODES:
        sub = result.allocation_shares[result.allocation_shares["electricity_allocation_mode"] == mode]
        top_alloc = sub.nsmallest(top, "electricity_rank")
        print(f"[{mode}]")
        print(top_alloc[["sector_code", "sector_name", "sector_agg", "electricity_mwh",
                         "electricity_share_pct"]].to_string(index=False,
                                                               float_format=lambda v: f"{v:,.3f}"))
    print("\n--- HOTSPOTS: SECTOR-ASSIGNED EXTENSION VS FINAL-DEMAND FOOTPRINT ---")
    for case in ("S0", "S8"):
        sub = result.hotspot_sectors[result.hotspot_sectors["case"] == case]
        print(f"[{case}] Largest sector-assigned environmental-extension proxies (NOT smokestack emissions):")
        print(sub.nsmallest(top, "origin_inventory_rank")[["sector_code", "sector_name", "sector_agg",
                                                         "origin_inventory_tco2", "origin_inventory_rank"]].to_string(
              index=False, float_format=lambda v: f"{v:,.2f}"))
        print(f"[{case}] Largest final-demand embodied footprints (SIGNED):")
        print(sub.nsmallest(top, "embodied_final_demand_rank")[["sector_code", "sector_name", "sector_agg",
                                                               "embodied_final_demand_tco2",
                                                               "upstream_for_final_demand_tco2",
                                                               "embodied_final_demand_rank"]].to_string(
              index=False, float_format=lambda v: f"{v:,.2f}"))
    focus_base = result.hotspot_sectors[result.hotspot_sectors["case"] == "S0"]
    n_negative = int((focus_base["final_demand_million_idr"] < 0).sum())
    print(f"Signed final demand: {n_negative} negative sectors retained; NEVER clamped or normalized away.")
    print("\n--- UPSTREAM ORIGIN FOR SELECTED FINAL-DEMAND SECTORS ---")
    for case in ("S0", "S8"):
        sub = result.upstream_origin[result.upstream_origin["case"] == case]
        print(f"[{case}] Selected-sector embodied sum: {sub['origin_total_tco2'].sum()/1e6:,.5f} MtCO2; "
              f"upstream component: {sub['origin_upstream_tco2'].sum()/1e6:,.5f} MtCO2")
        print(sub.nsmallest(top, "upstream_rank")[["sector_code", "sector_name", "sector_agg",
                                                   "origin_upstream_tco2", "origin_total_tco2"]].to_string(
              index=False, float_format=lambda v: f"{v:,.2f}"))
    print("\nINTERPRETATION STOP RULES:")
    print("- IO balance is verified; physical allocation of sector FEC is NOT verified.")
    print("- Factor x1000 mode is CONDITIONAL; baseline emission values are NOT validated national estimates.")
    print("- Electricity-associated EF may overlap power-sector combustion; overlap magnitude is NOT identified.")
    print("- Sector-assigned extension hotspots, embodied final-demand hotspots, and upstream attribution are DIFFERENT concepts.")
    print("- IO output and final demand remain FIXED; no rebound, technology adoption, or capital-stock changes.")
    print("- Legacy Request #1-#6 baseline and caches were not modified.")
    print("=== END STAGE 2 ===")


def save_stage2_outputs(result: Stage2Results, out_dir: str | Path) -> list[Path]:
    """Export new files only, with source checksum, assumptions, and STOP flags."""
    base = Path(out_dir)
    base.mkdir(parents=True, exist_ok=True)
    m = result.metadata
    name = f"request7_stage2_{m['year']}_{m['io_sha256'][:10]}_{m['fuel_source']}_{m['focus_allocation']}_{m['focus_factor_mode']}_n{len(result.targets)}"
    items: dict[str, Any] = {
        "allocation_robustness.csv": result.robustness,
        "scenarios_all_modes.csv": result.scenarios,
        "carrier_boundary.csv": result.carrier_boundary,
        "factor_unit_hypotheses.csv": result.factor_units,
        "electricity_sector_allocations.csv": result.allocation_shares,
        "allocation_pairwise_comparison.csv": result.allocation_pairwise,
        "group_electricity_shares.csv": result.group_electricity,
        "sector_hotspots_S0_S2_S6_S8.csv": result.hotspot_sectors,
        "upstream_origin_S0_S2_S6_S8.csv": result.upstream_origin,
        "assumptions_and_provenance.json": m,
    }
    paths = [base / f"{name}_{suffix}" for suffix in items]
    present = [str(p) for p in paths if p.exists()]
    if present:
        raise FileExistsError("Refusing to overwrite existing Stage 2 results: " + ", ".join(present))
    written = []
    try:
        for (suffix, content), path in zip(items.items(), paths):
            with path.open("x", encoding="utf-8", newline="") as f:
                if suffix.endswith(".json"):
                    json.dump(content, f, indent=2)
                else:
                    content.to_csv(f, index=False)
            written.append(path)
    except BaseException:
        # Remove only files created by THIS invocation, never pre-existing ones.
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return paths


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--year", type=int, choices=(2016, 2020), default=2020)
    p.add_argument("--io", type=Path, default=None,
                   help="Verified BPS domestic CSV; default static/data/<year>/io_ind_<year>_domestic.csv")
    p.add_argument("--fuel-source", choices=("crude", "refinery"), default="crude")
    p.add_argument("--target-sector", action="append", dest="targets", default=None,
                   help="Repeat to select multiple sectors; default 114")
    p.add_argument("--target-groups", default=None,
                   help="Comma-separated sector_agg IDs, e.g. 07,08,10,11")
    p.add_argument("--focus-allocation", choices=MODES, default="transactions")
    p.add_argument("--focus-factor-mode", choices=("legacy", "provisional"), default="legacy")
    p.add_argument("--top", type=int, default=8,
                   help="Top ranked sectors printed per mode (default 8)")
    p.add_argument("--export-dir", type=Path, default=None,
                   help="New results only, no overwrites; includes CSV and provenance JSON")
    args = p.parse_args(argv)
    if not (1 <= args.top <= N_SECTORS):
        p.error("--top must be within 1..185")
    io_path = args.io or Path(f"static/data/{args.year}/io_ind_{args.year}_domestic.csv")
    io = load_domestic_io(args.year, io_path)
    print(f"DOMESTIC IO verified: {args.year} | source SHA256={io.csv_sha256} "
          f"| L@y={io.leontief_error:.10%}")

    # Actual user database is only ever queried with SELECT statements.
    from flask import Flask
    from dotenv import load_dotenv
    from util_db import db_init
    load_dotenv()
    app = Flask(__name__)
    db = db_init(app)
    with app.app_context():
        tables = load_energy_database(db, args.year)
        result = execute_stage2(
            io, *tables,
            target_codes=tuple(args.targets) if args.targets else None,
            target_groups=tuple(s.strip() for s in args.target_groups.split(","))
                          if args.target_groups else None,
            fuel_source=args.fuel_source,
            focus_allocation=args.focus_allocation,
            focus_factor_mode=args.focus_factor_mode,
        )
        show_stage2_summary(result, top=args.top)
        if args.export_dir:
            paths = save_stage2_outputs(result, args.export_dir)
            print("\nCreated new research outputs (without overwriting files):")
            for path in paths:
                print("  ", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
