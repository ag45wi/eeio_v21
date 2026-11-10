"""
scenario_batch_v21.py

Research-only batch analysis for EEIO v2.1.

This module:
    - does NOT create scenario definitions
    - does NOT create scenario history
    - does NOT modify the Flask UI
    - reuses the existing v2.1 ScenarioProcessor
    - runs independent sector-specific counterfactuals

Current experiment:
    Electricity demand reduction
    applied independently to all 185 sectors.
"""

import numpy as np
import pandas as pd
from types import SimpleNamespace
from plotly.subplots import make_subplots
import plotly.graph_objects as go
import os
import MySQLdb
import textwrap
import sys

from config_scenario import (
    ENERGY_MAPPING,
    MWH_TO_TJ
)

from scenario_engine import (
    ScenarioParameter,
    ScenarioProcessor
)

from eeio_engine import get_eeio_model

from util_mat import (
    build_model_year,
    get_sector
)

from util_db import (
    db_init,
    tbl_to_df
)

# ============================================================
# Response surface experiment settings
# ============================================================
DEFAULT_ELECTRICITY_EFFICIENCY_LEVELS_PCT_V21 = [
    0, -10, -20, -30, -40
]
DEFAULT_GRID_EF_LEVELS_PCT_V21 = [
    0, -10, -20, -30, -40, -50
]
DEFAULT_EFFICIENCY_TARGET_SECTOR_V21 = "149"

# ============================================================
# Batch constants
# ============================================================
ELECTRICITY_ENERGY_CODE_V21 = "04"
DEFAULT_CHANGE_V21 = -0.10

# ============================================================
# Transport electrification assumptions
# ============================================================
# Sector 158 is the detailed sector used as the road-transport
# representation for this research-only counterfactual.
ROAD_TRANSPORT_SECTOR_CODE_V21 = "158"

# Current v2.1 energy mapping:
#   02 = Fuel
#   04 = Electricity
FUEL_ENERGY_CODE_V21 = "02"

# Illustrative electricity / displaced-fuel energy ratio.
# Basis used in Request #7:
#   Indonesia new-LDV fuel consumption = 8.1 Lge/100 km
#   EV electricity consumption = 0.189 kWh/km
#   1 Lge = 32 MJ
# Therefore:
#   18.9 / (8.1 * 32 / 3.6) = 0.2625
#
# This is a technology-based illustrative conversion ratio,
# not an Indonesia-wide fleet-average parameter.
EV_ELECTRICITY_TO_FUEL_ENERGY_RATIO_V21 = 0.2625

# Electricity values in mat_finCons are MWh in the current
# batch convention. Reuse the authoritative conversion already
# defined in config_scenario.py.
MWH_TO_TJ_V21 = MWH_TO_TJ

# The model divides the FEC allocation (thousand BOE) by the
# conversion_factor multiplier (BOE/kL) and multiplies by 1000.
# Thus the Fuel model row is in kL.
# The conversion factor table identifies Fuel in kL.
# The recorded Fuel HHV of 38.65 is interpreted as GJ/kL,
# hence 0.03865 TJ/kL. Validate its source units before publication.
# This does NOT fix the zero Fuel allocation in Sector 158.
FUEL_MAT_FINCONS_TO_TJ_V21 = 0.03865

# Set to True only for the staged Request #7 diagnostic.
RUN_TRANSPORT_ELECTRIFICATION_DIAGNOSTIC_V21 = True


# ============================================================
# Helper: normalize sector code
# ============================================================
def normalize_sector_code_v21(
        value
    ):
    """
    Return sector code in standard 3-digit form.
    """

    if value is None:
        return None

    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]

    return text.zfill(3)

# ============================================================
# Helper: normalize model sector map
# ============================================================
def get_sector_column_map_v21(
        model
    ):
    """
    Normalize model.sector_column_map keys to
    3-digit sector-code strings.
    """

    if (0):
        print(
            "-- ENERGY_MAPPING:",
            ENERGY_MAPPING
        )
        print(
            "-- electricity mapping:",
            ENERGY_MAPPING[
                ELECTRICITY_ENERGY_CODE_V21
            ],
            type(
                ENERGY_MAPPING[
                    ELECTRICITY_ENERGY_CODE_V21
                ]
            )
        )

    return {
        normalize_sector_code_v21(code):
            int(column)
        for code, column
        in model.sector_column_map.items()
    }


# ============================================================
# Build one in-memory batch scenario
# ============================================================
def build_batch_scenario_v21(
        sector_code,
        change
    ):
    """
    Build one temporary in-memory scenario.

    No ScenarioDefinition is created.
    No scenario_id is required.
    No database record is created.
    """

    sector_code = normalize_sector_code_v21(sector_code)

    parameter = ScenarioParameter(
        parameter_id=None,
        parameter_code="energy_demand_change",
        value=float(change),
        unit="%",
        energy_type_code=ELECTRICITY_ENERGY_CODE_V21,
        sector_code=sector_code,
        target_scope="SECTOR_ENERGY"
    )

    # ScenarioProcessor only needs the
    # parameter collection for this batch case.
    #
    # We nevertheless provide a few normal
    # Scenario attributes for compatibility.

    return SimpleNamespace(
        scenario_id=None,
        scenario_name="Batch electricity efficiency",
        scenario_types=[],
        parameters=[parameter]
    )


# ============================================================
# Prepare and validate the 185 sectors
# ============================================================
def prepare_batch_sectors_v21(
        model,
        df_sector
    ):
    """
    Prepare exactly one detailed-sector record
    for every model sector.
    Returns:
        [
            {
                "sector_code": ...,
                "sector_name": ...,
                "column": ...
            },
            ...
        ]
    """

    required_columns = {"sector_code","sector_name"}
    missing_columns = (
        required_columns - set(df_sector.columns)
    )

    if missing_columns:
        raise ValueError(
            "df_sector is missing required columns: "
            +
            ", ".join(sorted(missing_columns))
        )

    sector_column_map = get_sector_column_map_v21(model)

    records = []
    seen_codes = set()
    for _, row in df_sector.iterrows():
        sector_code = (
            normalize_sector_code_v21(row["sector_code"])
        )

        if sector_code in seen_codes:
            raise ValueError(
                "Duplicate sector code in "
                f"df_sector: {sector_code}"
            )

        seen_codes.add(sector_code)
        if sector_code not in sector_column_map:
            raise ValueError(
                f"Sector {sector_code} exists "
                "in df_sector but not in "
                "model.sector_column_map."
            )

        records.append(
            {
                "sector_code":
                    sector_code,
                "sector_name":
                    str(row["sector_name"]),
                "column":
                    sector_column_map[sector_code]
            }
        )

    # --------------------------------------------------------
    # Require exact model/metadata alignment
    # --------------------------------------------------------
    if len(records) != model.n_sectors:
        raise ValueError(
            "Sector count mismatch: "
            f"metadata={len(records)}, "
            f"model={model.n_sectors}"
        )

    missing_codes = (
        set(sector_column_map) - seen_codes
    )

    if missing_codes:
        raise ValueError(
            "The following model sectors are "
            "missing from df_sector: "
            +
            ", ".join(sorted(missing_codes)
            )
        )

    # --------------------------------------------------------
    # Sort according to matrix column
    # --------------------------------------------------------
    records.sort(
        key=lambda item: item["column"]
    )

    return records


# ============================================================
# Calculate one sector case
# ============================================================

def run_single_sector_electricity_batch_v21(
        model,
        sector_code,
        change=-0.10,
        baseline_result=None
    ):
    """
    Run one independent sector-specific
    electricity-demand intervention.

    Example:
        Electricity / Sector 077 / -10%

    Returns a dictionary containing:
        - target electricity change
        - economy-wide emission change
        - Scope 1/2/3 changes
        - matrix changed-cell count
    """

    sector_code = normalize_sector_code_v21(sector_code)
    sector_column_map = get_sector_column_map_v21(model)

    if sector_code not in sector_column_map:
        raise ValueError(f"Unknown sector code: {sector_code}")

    sector_column = int(sector_column_map[sector_code])

    # --------------------------------------------------------
    # Baseline is calculated only once
    # --------------------------------------------------------
    if baseline_result is None:
        baseline_result = (model.calculate_baseline())

    # --------------------------------------------------------
    # Build temporary scenario
    # --------------------------------------------------------
    scenario = (
        build_batch_scenario_v21(sector_code,change)
    )

    # --------------------------------------------------------
    # Use the existing v2.1 ScenarioProcessor
    # --------------------------------------------------------
    processor = ScenarioProcessor(model)
    inputs = processor.apply(model,scenario)

    # --------------------------------------------------------
    # Use the existing EEIO calculation engine
    # --------------------------------------------------------
    scenario_result = (
        model.calculate_scenario_from_inputs(inputs)
    )
    #print("--ELECTRICITY_ENERGY_CODE_V21:", ELECTRICITY_ENERGY_CODE_V21)
    #print("--ENERGY_MAPPING[ELECTRICITY_ENERGY_CODE_V21]:", ENERGY_MAPPING[ELECTRICITY_ENERGY_CODE_V21])
    # --------------------------------------------------------
    # Electricity matrix row
    # --------------------------------------------------------
    electricity_row = int(
        ENERGY_MAPPING[ELECTRICITY_ENERGY_CODE_V21]["finCons_row"]
    )
    if (0):
        print(
            "-- electricity_row:",
            electricity_row,
            type(electricity_row)
        )
        print(
            "-- sector_column:",
            sector_column,
            type(sector_column)
        )
        print(
            "-- mat_finCons shape:",
            model.mat_finCons.shape
        )

    # --------------------------------------------------------
    # Target electricity
    # --------------------------------------------------------
    baseline_electricity = float(
        model.mat_finCons[electricity_row,sector_column]
    )
    scenario_electricity = float(
        inputs.mat_finCons[electricity_row,sector_column]
    )
    target_electricity_reduction = (
        baseline_electricity - scenario_electricity
    )

    # --------------------------------------------------------
    # Verify target equation
    # --------------------------------------------------------
    expected_electricity = (
        baseline_electricity * ( 1.0 + float(change))
    )

    if not np.isclose(
            scenario_electricity, expected_electricity,
            rtol=1e-10, atol=1e-10
        ):
        raise RuntimeError(
            "Target electricity calculation "
            "does not match expected value for "
            f"sector {sector_code}: "
            f"expected={expected_electricity}, "
            f"actual={scenario_electricity}"
        )

    # --------------------------------------------------------
    # Matrix-level validation
    # --------------------------------------------------------
    changed_cells = int(
        np.count_nonzero(
            ~np.isclose(
                inputs.mat_finCons, model.mat_finCons,
                rtol=1e-10, atol=1e-10
            )
        )
    )

    # --------------------------------------------------------
    # Economy-wide emissions
    # --------------------------------------------------------
    baseline_total = float(
        np.sum(baseline_result["totalEmission"])
    )
    scenario_total = float(
        np.sum(scenario_result["totalEmission"])
    )
    economy_wide_reduction = baseline_total - scenario_total
    economy_wide_reduction_pct = (
        economy_wide_reduction / baseline_total * 100.0
        if baseline_total != 0
        else np.nan
    )

    # --------------------------------------------------------
    # Electricity emission leverage
    # --------------------------------------------------------
    target_electricity_reduction_tj = (
        target_electricity_reduction * MWH_TO_TJ
    )
    economy_wide_reduction_ktco2 = (
        economy_wide_reduction / 1_000
    )
    if target_electricity_reduction_tj > 0:
        emission_leverage_ktco2_per_tj = (
            economy_wide_reduction_ktco2 / target_electricity_reduction_tj
        )
    else:
        emission_leverage_ktco2_per_tj = np.nan

    # --------------------------------------------------------
    # Scope 1
    # --------------------------------------------------------
    baseline_scope1 = float(
        np.sum(baseline_result["emissionScope1"])
    )
    scenario_scope1 = float(
        np.sum(scenario_result["emissionScope1"])
    )
    scope1_reduction = (baseline_scope1-scenario_scope1)

    # --------------------------------------------------------
    # Scope 2
    # --------------------------------------------------------
    baseline_scope2 = float(
        np.sum(baseline_result["emissionScope2"])
    )
    scenario_scope2 = float(
        np.sum(scenario_result["emissionScope2"])
    )
    scope2_reduction = (baseline_scope2-scenario_scope2)

    # --------------------------------------------------------
    # Scope 3
    # --------------------------------------------------------
    baseline_scope3 = float(
        np.sum(baseline_result["emissionScope3"])
    )
    scenario_scope3 = float(
        np.sum(scenario_result["emissionScope3"])
    )
    scope3_reduction = (
        baseline_scope3
        -
        scenario_scope3
    )

    # --------------------------------------------------------
    # Return
    # --------------------------------------------------------
    return {
        "sector_code":
            sector_code,
        "baseline_electricity_mwh":
            baseline_electricity,
        "scenario_electricity_mwh":
            scenario_electricity,
        "target_electricity_reduction_mwh":
            target_electricity_reduction,
        "baseline_electricity_tj":
            baseline_electricity * MWH_TO_TJ,
        "scenario_electricity_tj":
            scenario_electricity * MWH_TO_TJ,
        "target_electricity_reduction_tj":
            target_electricity_reduction_tj,
        "baseline_total_emission_mtco2":
            baseline_total / 1_000_000,
        "scenario_total_emission_mtco2":
            scenario_total / 1_000_000,
        "economy_wide_reduction_ktco2":
            economy_wide_reduction_ktco2,
        "economy_wide_reduction_mtco2":
            economy_wide_reduction / 1_000_000,
        "economy_wide_reduction_pct":
            economy_wide_reduction_pct,
        "emission_leverage_ktco2_per_tj":
            emission_leverage_ktco2_per_tj,
        "scope1_reduction_ktco2":
            scope1_reduction / 1_000,
        "scope2_reduction_ktco2":
            scope2_reduction / 1_000,
        "scope3_reduction_ktco2":
            scope3_reduction / 1_000,
        "changed_cells":
            changed_cells,

    }

# ============================================================
# Run all 185 independent cases
# ============================================================
def run_sector_electricity_efficiency_batch_v21(
        model,
        df_sector,
        change=-0.10,
        verbose=True
    ):
    """
    Run the same electricity-demand intervention
    independently across all detailed sectors.

    No scenario definitions or histories are created.
    """

    if change <= -1.0:
        raise ValueError(
            "change must be greater than -1.0."
        )

    # --------------------------------------------------------
    # Calculate baseline once
    # --------------------------------------------------------
    baseline_result = (
        model.calculate_baseline()
    )

    # --------------------------------------------------------
    # Validate and prepare 185 sectors
    # --------------------------------------------------------
    sectors = (
        prepare_batch_sectors_v21(model, df_sector)
    )

    results = []
    total = len(sectors)

    for i, sector in enumerate(sectors,start=1):
        sector_code = sector["sector_code"]
        sector_name = sector["sector_name"]
        if verbose:
            print(
                f"[{i}/{total}] "
                f"{sector_code} - "
                f"{sector_name}"
            )

        result = (
            run_single_sector_electricity_batch_v21(
                model=model,
                sector_code=sector_code,
                change=change,
                baseline_result=baseline_result
            )
        )
        #print("--result", result)

        result["sector_name"] = (sector_name)
        results.append(result)

    df_result = pd.DataFrame(results)

    # --------------------------------------------------------
    # Rank by economy-wide CO2 reduction
    # --------------------------------------------------------
    if (0):
        if (len(df_result) == 0):
            print("--Empty df_result")
        else:
            print("--df_result", df_result.shape)
            print(df_result.columns.to_list())

    df_result = (
        df_result
        .sort_values(
            by=[
                "economy_wide_reduction_mtco2",
                "target_electricity_reduction_tj",
                "sector_code"
            ],
            ascending=[False, False, True]
        )
        .reset_index(drop=True)
    )

    # --------------------------------------------------------
    # Add rank
    # --------------------------------------------------------
    df_result.insert(
        0, "rank", np.arange(1,len(df_result) + 1)
    )

    # --------------------------------------------------------
    # Final column order
    # --------------------------------------------------------
    columns = [
        "rank",
        "sector_code",
        "sector_name",
        "baseline_electricity_tj",
        "scenario_electricity_tj",
        "target_electricity_reduction_tj",
        "baseline_total_emission_mtco2",
        "scenario_total_emission_mtco2",
        "economy_wide_reduction_ktco2",
        "economy_wide_reduction_mtco2",
        "economy_wide_reduction_pct",
        "emission_leverage_ktco2_per_tj",
        "scope1_reduction_ktco2",
        "scope2_reduction_ktco2",
        "scope3_reduction_ktco2",
        "changed_cells"
    ]
    df_result = df_result[columns]

    # --------------------------------------------------------
    # Final validation
    # --------------------------------------------------------
    if len(df_result) != model.n_sectors:
        raise RuntimeError(
            "Final batch result contains "
            f"{len(df_result)} sectors, "
            f"but model contains "
            f"{model.n_sectors}."
        )

    return df_result

# ============================================================
# Build/get model
# ============================================================
def build_batch_model_v21(db, year):
    """
    Build or retrieve the reusable EEIO model
    for one selected year.
    """
    fname_io = f"io_ind_{year}.csv"
    cache_key = (year,fname_io)
    return get_eeio_model(
        cache_key=cache_key,
        model_builder=lambda:
            build_model_year(
                db=db,fname_io=fname_io,year=year
            )
    )


# ============================================================
# Complete batch wrapper
# ============================================================
def run_batch_for_year_v21(
        db, year, change=-0.10, verbose=True
    ):
    """
    Run the complete 185-sector experiment
    for one EEIO model year.
    """

    model = build_batch_model_v21(db, year)
    df_sector = get_sector(db)

    return (
        run_sector_electricity_efficiency_batch_v21(
            model=model,
            df_sector=df_sector,
            change=change,
            verbose=verbose
        )
    )

# ============================================================
# Export
# ============================================================
def save_batch_results_csv_v21(
        df_result,
        filepath
    ):
    """
    Save batch results as CSV.
    """
    df_result.to_csv(filepath, index=False)

def save_batch_results_excel_v21(
        df_result,
        filepath
    ):
    """
    Save batch results as Excel.
    """

    df_result.to_excel(
        filepath, index=False, sheet_name="Sector Ranking"
    )


# ============================================================
# Controlled Policy Interaction Analysis
# ============================================================
def build_batch_scenario_from_parameters_v21(
        parameters,
        scenario_name
    ):
    """
    Build a temporary in-memory scenario from a list
    of ScenarioParameter objects.

    No scenario definition or history is created.
    """

    return SimpleNamespace(
        scenario_id=None,
        scenario_name=scenario_name,
        scenario_types=[],
        parameters=parameters
    )

def get_target_electricity_reduction_tj_v21(
        model,
        inputs,
        parameters
    ):
    """
    Calculate the physical electricity reduction represented
    by an energy_demand_change parameter.

    The underlying model mat_finCons electricity values are
    in MWh; the returned value is converted to TJ.

    Returns
    -------
    reduction_tj
        Target electricity reduction in TJ.

    target_sector_code
        Sector code if the intervention is sector-specific,
        otherwise None.
    """
    demand_parameter = next(
        (
            parameter
            for parameter in parameters
            if (
                parameter.parameter_code == "energy_demand_change"
                and
                parameter.energy_type_code == ELECTRICITY_ENERGY_CODE_V21
            )
        ),
        None
    )

    # --------------------------------------------------------
    # No electricity-demand intervention
    # --------------------------------------------------------
    if demand_parameter is None:
        return 0.0, None

    electricity_row = int(
        ENERGY_MAPPING[ELECTRICITY_ENERGY_CODE_V21]["finCons_row"]
    )

    # --------------------------------------------------------
    # Sector-specific demand intervention
    # --------------------------------------------------------
    if (demand_parameter.target_scope=="SECTOR_ENERGY"):
        sector_code = (
            normalize_sector_code_v21(demand_parameter.sector_code)
        )
        sector_column_map = (
            get_sector_column_map_v21(model)
        )

        if sector_code not in sector_column_map:
            raise ValueError(
                "Unknown target sector: "
                f"{sector_code}"
            )

        sector_column = int(
            sector_column_map[sector_code]
        )
        baseline_electricity = float(
            model.mat_finCons[electricity_row,sector_column]
        )
        scenario_electricity = float(
            inputs.mat_finCons[electricity_row,sector_column]
        )

        reduction_mwh = (baseline_electricity - scenario_electricity)
        reduction_tj = (reduction_mwh * MWH_TO_TJ)

        return (
            reduction_tj,
            sector_code
        )

    # --------------------------------------------------------
    # Economy-wide demand intervention
    # --------------------------------------------------------
    if (demand_parameter.target_scope == "ENERGY"):
        baseline_electricity = float(
            np.sum(model.mat_finCons[electricity_row,:])
        )
        scenario_electricity = float(
            np.sum(inputs.mat_finCons[electricity_row,:])
        )
        reduction_mwh = (baseline_electricity - scenario_electricity)
        reduction_tj = (reduction_mwh * MWH_TO_TJ)

        return (
            reduction_tj,
            None
        )

    return 0.0, None

def run_parameterized_policy_case_v21(
        model,
        parameters,
        scenario_name,
        baseline_result=None
    ):
    """
    Execute one temporary parameterized policy case.

    The case is processed through the same v2.1
    ScenarioProcessor and EEIOModel calculation engine
    used by the Flask application.

    No database scenario/history record is created.
    """
    # --------------------------------------------------------
    # Baseline
    # --------------------------------------------------------
    if baseline_result is None:
        baseline_result = model.calculate_baseline()

    # --------------------------------------------------------
    # Temporary scenario
    # --------------------------------------------------------
    scenario = (
        build_batch_scenario_from_parameters_v21(parameters, scenario_name)
    )

    # --------------------------------------------------------
    # Scenario processing
    # --------------------------------------------------------
    processor = ScenarioProcessor(model)
    inputs = processor.apply(model,scenario)

    if (1):
        print ("TEMPORARY 2016 MATRIX DIAGNOSTIC")
        # ============================================================
        # TEMPORARY 2016 MATRIX DIAGNOSTIC
        # ============================================================
        electricity_row = int(
            ENERGY_MAPPING[ELECTRICITY_ENERGY_CODE_V21]["finCons_row"]
        )

        baseline_electricity = model.mat_finCons[electricity_row,:]
        scenario_electricity = inputs.mat_finCons[electricity_row,:]

        diff = scenario_electricity - baseline_electricity
        changed_columns = np.where(
            ~np.isclose(diff,0.0,rtol=1e-10,atol=1e-10)
        )[0]

        zero_baseline_columns = np.where(
            np.isclose(baseline_electricity,0.0,rtol=1e-10,atol=1e-10)
        )[0]

        print()
        print("=== ELECTRICITY MATRIX DIAGNOSTIC ===")
        print("Model year:",model.year)
        print("Electricity row:",electricity_row)
        print("Baseline total:",np.sum(baseline_electricity))
        print("Scenario total:", np.sum(scenario_electricity))
        print("Changed columns:",len(changed_columns))
        print("Zero-baseline columns:",len(zero_baseline_columns))
        print("Physical change (%):",
            ((np.sum(scenario_electricity)-np.sum(baseline_electricity)
                )
                /np.sum(baseline_electricity) * 100)
        )
        print()
        print("=== FIRST 10 CHANGED SECTORS ===")
        reverse_sector_map = {
            int(column): code
            for code, column
            in model.sector_column_map.items()
        }

        for column in changed_columns[:10]:
            sector_code = reverse_sector_map[int(column)]
            baseline_value = float(baseline_electricity[column])
            scenario_value = float(scenario_electricity[column])
            print(
                sector_code,
                "baseline =", baseline_value,
                "scenario =",scenario_value,
                "change =", scenario_value- baseline_value
            )

        print()
        print("=== ZERO-BASELINE ELECTRICITY SECTORS ===")
        for column in zero_baseline_columns:
            sector_code = reverse_sector_map[int(column)]
            print(
                sector_code,
                "baseline =",baseline_electricity[column],
                "scenario =",scenario_electricity[column]
            )

    # --------------------------------------------------------
    # EEIO calculation
    # --------------------------------------------------------
    scenario_result = (
        model.calculate_scenario_from_inputs(inputs)
    )

    # --------------------------------------------------------
    # Emission totals
    # --------------------------------------------------------
    baseline_total = float(
        np.sum(baseline_result["totalEmission"])
    )
    scenario_total = float(
        np.sum(scenario_result["totalEmission"])
    )
    emission_reduction = (baseline_total-scenario_total)
    emission_reduction_mtco2 = (emission_reduction/1_000_000)
    emission_reduction_ktco2 = (emission_reduction/1_000)

    reduction_pct = (
        emission_reduction/baseline_total*100.0
        if baseline_total != 0
        else np.nan
    )

    # --------------------------------------------------------
    # Scope 1
    # --------------------------------------------------------
    baseline_scope1 = float(
        np.sum(baseline_result["emissionScope1"])
    )
    scenario_scope1 = float(
        np.sum(scenario_result["emissionScope1"])
    )
    scope1_reduction_ktco2 = (
        (baseline_scope1 - scenario_scope1)/1_000
    )

    # --------------------------------------------------------
    # Scope 2
    # --------------------------------------------------------
    baseline_scope2 = float(
        np.sum(baseline_result["emissionScope2"])
    )
    scenario_scope2 = float(
        np.sum(scenario_result["emissionScope2"])
    )
    scope2_reduction_ktco2 = (
        (baseline_scope2 - scenario_scope2)/1_000
    )

    # --------------------------------------------------------
    # Scope 3
    # --------------------------------------------------------
    baseline_scope3 = float(
        np.sum(baseline_result["emissionScope3"])
    )
    scenario_scope3 = float(
        np.sum(scenario_result["emissionScope3"])
    )
    scope3_reduction_ktco2 = (
        (baseline_scope3 - scenario_scope3)/1_000
    )

    # --------------------------------------------------------
    # Matrix validation
    # --------------------------------------------------------
    changed_cells = int(
        np.count_nonzero(
            ~np.isclose(inputs.mat_finCons,model.mat_finCons,rtol=1e-10,atol=1e-10)
        )
    )

    # --------------------------------------------------------
    # Target electricity
    # --------------------------------------------------------
    (target_electricity_reduction_tj,target_sector_code) = (
        get_target_electricity_reduction_tj_v21(model,inputs,parameters)
    )

    # --------------------------------------------------------
    # Return
    # --------------------------------------------------------
    return {
        "scenario_name":
            scenario_name,
        "target_sector_code":
            target_sector_code,
        "baseline_total_emission_mtco2":
            baseline_total / 1_000_000,
        "scenario_total_emission_mtco2":
            scenario_total / 1_000_000,
        "emission_reduction_mtco2":
            emission_reduction_mtco2,
        "emission_reduction_ktco2":
            emission_reduction_ktco2,
        "national_reduction_pct":
            reduction_pct,
        "target_electricity_reduction_tj":
            target_electricity_reduction_tj,
        "scope1_reduction_ktco2":
            scope1_reduction_ktco2,
        "scope2_reduction_ktco2":
            scope2_reduction_ktco2,
        "scope3_reduction_ktco2":
            scope3_reduction_ktco2,
        "changed_cells":
            changed_cells
    }

def run_combined_policy_interaction_v21(
        model,
        target_sector_code="149",
        demand_change=-0.20,
        emission_factor_change=-0.20
    ):
    """
    Controlled three-policy experiment:
        S1 = electricity demand reduction
             in target sector
        S2 = electricity emission-factor reduction
             across all sectors
        S3 = S1 + S2

    Returns
    -------
    df_cases
        Baseline + S1 + S2 + S3 results.
    interaction
        Additive interaction analysis.
    """

    target_sector_code = normalize_sector_code_v21(target_sector_code)

    # --------------------------------------------------------
    # Baseline
    # --------------------------------------------------------
    baseline_result = model.calculate_baseline()

    # --------------------------------------------------------
    # Parameter 1: Electricity demand
    # --------------------------------------------------------
    demand_parameter = (
        ScenarioParameter(
            parameter_id=None,
            parameter_code=
                "energy_demand_change",
            value=
                float(demand_change),
            unit="%",
            energy_type_code=
                ELECTRICITY_ENERGY_CODE_V21,
            sector_code=
                target_sector_code,
            target_scope=
                "SECTOR_ENERGY"
        )
    )

    # --------------------------------------------------------
    # Parameter 2: Electricity emission factor
    # --------------------------------------------------------
    emission_factor_parameter = (
        ScenarioParameter(
            parameter_id=None,
            parameter_code=
                "emission_factor_change",
            value=
                float(emission_factor_change),
            unit="%",
            energy_type_code=
                ELECTRICITY_ENERGY_CODE_V21,
            sector_code=None,
            target_scope=
                "ENERGY"
        )
    )

    # --------------------------------------------------------
    # S1 - Efficiency only
    # --------------------------------------------------------
    result_s1 = (
        run_parameterized_policy_case_v21(
            model=model,
            parameters=[
                demand_parameter
            ],
            scenario_name=
                "S1 - Electricity efficiency",
            baseline_result=
                baseline_result
        )
    )
    result_s1["case"] = "S1"

    # --------------------------------------------------------
    # S2 - Grid decarbonization only
    # --------------------------------------------------------
    result_s2 = (
        run_parameterized_policy_case_v21(
            model=model,
            parameters=[emission_factor_parameter],
            scenario_name="S2 - Electricity decarbonization",
            baseline_result=baseline_result
        )
    )
    result_s2["case"] = "S2"

    # --------------------------------------------------------
    # S3 - Combined
    # --------------------------------------------------------
    result_s3 = (
        run_parameterized_policy_case_v21(
            model=model,
            parameters=[
                demand_parameter,
                emission_factor_parameter
            ],
            scenario_name="S3 - Combined",
            baseline_result=baseline_result
        )
    )

    result_s3["case"] = "S3"
    # --------------------------------------------------------
    # Baseline row
    # --------------------------------------------------------
    baseline_total_mtco2 = (
        float(np.sum(baseline_result["totalEmission"])) / 1_000_000
    )

    baseline_row = {
        "case":
            "S0",
        "scenario_name":
            "S0 - Baseline",
        "target_sector_code":
            None,
        "baseline_total_emission_mtco2":
            baseline_total_mtco2,
        "scenario_total_emission_mtco2":
            baseline_total_mtco2,
        "emission_reduction_mtco2":
            0.0,
        "emission_reduction_ktco2":
            0.0,
        "national_reduction_pct":
            0.0,
        "target_electricity_reduction_tj":
            0.0,
        "scope1_reduction_ktco2":
            0.0,
        "scope2_reduction_ktco2":
            0.0,
        "scope3_reduction_ktco2":
            0.0,
        "changed_cells":
            0
    }

    # --------------------------------------------------------
    # Case table
    # --------------------------------------------------------
    case_results = [baseline_row,result_s1,result_s2,result_s3]
    df_cases = pd.DataFrame(case_results)
    df_cases = df_cases[
        [
            "case",
            "scenario_name",
            "target_sector_code",
            "target_electricity_reduction_tj",
            "baseline_total_emission_mtco2",
            "scenario_total_emission_mtco2",
            "emission_reduction_ktco2",
            "emission_reduction_mtco2",
            "national_reduction_pct",
            "scope1_reduction_ktco2",
            "scope2_reduction_ktco2",
            "scope3_reduction_ktco2",
            "changed_cells"
        ]
    ]

    # --------------------------------------------------------
    # Extract individual effects
    # --------------------------------------------------------
    R1 = result_s1["emission_reduction_mtco2"]
    R2 = result_s2["emission_reduction_mtco2"]
    R3 = result_s3["emission_reduction_mtco2"]

    # --------------------------------------------------------
    # Additive expectation
    # --------------------------------------------------------
    expected_additive = R1 + R2
    # --------------------------------------------------------
    # Interaction contrast
    # --------------------------------------------------------
    interaction_contrast = R3 - expected_additive

    # --------------------------------------------------------
    # Relative interaction
    # --------------------------------------------------------
    interaction_pct = (
        interaction_contrast/expected_additive*100.0
        if expected_additive != 0
        else np.nan
    )

    # --------------------------------------------------------
    # Combined-to-additive ratio
    # --------------------------------------------------------
    combined_additive_ratio = (
        R3 / expected_additive
        if expected_additive != 0
        else np.nan
    )

    # --------------------------------------------------------
    # Classification
    # --------------------------------------------------------
    tolerance = 1e-12
    if interaction_contrast > tolerance:
        interaction_class = "Positive interaction"
    elif interaction_contrast < -tolerance:
        interaction_class = "Negative interaction"
    else:
        interaction_class = "Additive"

    # --------------------------------------------------------
    # Scope-level interaction
    # --------------------------------------------------------
    scope1_interaction = (
        result_s3["scope1_reduction_ktco2"]
        -
        result_s1["scope1_reduction_ktco2"]
        -
        result_s2["scope1_reduction_ktco2"]
    )

    scope2_interaction = (
        result_s3["scope2_reduction_ktco2"]
        -
        result_s1["scope2_reduction_ktco2"]
        -
        result_s2["scope2_reduction_ktco2"]
    )

    scope3_interaction = (
        result_s3["scope3_reduction_ktco2"]
        -
        result_s1["scope3_reduction_ktco2"]
        -
        result_s2["scope3_reduction_ktco2"]
    )

    interaction = {
        "demand_change_pct":
            demand_change * 100,
        "emission_factor_change_pct":
            emission_factor_change * 100,
        "target_sector_code":
            target_sector_code,
        "s1_reduction_mtco2":
            R1,
        "s2_reduction_mtco2":
            R2,
        "s3_reduction_mtco2":
            R3,
        "expected_additive_reduction_mtco2":
            expected_additive,
        "interaction_contrast_mtco2":
            interaction_contrast,
        "interaction_pct_of_additive":
            interaction_pct,
        "combined_to_additive_ratio":
            combined_additive_ratio,
        "interaction_class":
            interaction_class,
        "scope1_interaction_ktco2":
            scope1_interaction,
        "scope2_interaction_ktco2":
            scope2_interaction,
        "scope3_interaction_ktco2":
            scope3_interaction
    }

    return (
        df_cases,
        interaction
    )

'''
def build_electricity_response_parameters_v21(
        efficiency_change_pct,
        grid_ef_change_pct,
        target_sector_code="149"
    ):
    """
    Build the parameters for one point in the
    electricity-efficiency / grid-emission-factor
    response surface.

    efficiency_change_pct:
        Electricity demand change in the target sector,
        expressed as percentage, e.g. -20.

    grid_ef_change_pct:
        Electricity emission-factor change across all
        sectors, expressed as percentage, e.g. -30.
    """

    parameters = []
    # --------------------------------------------------------
    # Electricity efficiency
    # --------------------------------------------------------
    if efficiency_change_pct != 0:
        parameters.append(
            ScenarioParameter(
                parameter_id=None,
                parameter_code=
                    "energy_demand_change",
                value=
                    float(efficiency_change_pct) / 100.0,
                unit="%",
                energy_type_code=
                    ELECTRICITY_ENERGY_CODE_V21,
                sector_code=
                    normalize_sector_code_v21(target_sector_code),
                target_scope="SECTOR_ENERGY"
            )
        )

    # --------------------------------------------------------
    # Grid emission-factor reduction
    # --------------------------------------------------------
    if grid_ef_change_pct != 0:
        parameters.append(
            ScenarioParameter(
                parameter_id=None,
                parameter_code=
                    "emission_factor_change",
                value=
                    float(grid_ef_change_pct) / 100.0,
                unit="%",
                energy_type_code=
                    ELECTRICITY_ENERGY_CODE_V21,
                sector_code=None,
                target_scope=
                    "ENERGY"
            )
        )

    return parameters
'''

def build_electricity_response_parameters_v21(
        efficiency_change_pct,
        grid_ef_change_pct,
        target_sector_code="149",
        efficiency_target_scope="SECTOR_ENERGY"
    ):
    """
    Build parameters for one point in the electricity
    efficiency / grid emission-factor response surface.

    efficiency_target_scope:
        "SECTOR_ENERGY" -> one target sector
        "ENERGY"        -> all sectors
    """

    parameters = []
    # --------------------------------------------------------
    # Electricity efficiency
    # --------------------------------------------------------
    if efficiency_change_pct != 0:
        if efficiency_target_scope == "SECTOR_ENERGY":
            if target_sector_code is None:
                raise ValueError(
                    "target_sector_code is required "
                    "for SECTOR_ENERGY."
                )

            sector_code = normalize_sector_code_v21(target_sector_code)

        elif efficiency_target_scope == "ENERGY":
            sector_code = None
        else:
            raise ValueError(
                "Unsupported efficiency target scope: "
                f"{efficiency_target_scope}"
            )

        parameters.append(
            ScenarioParameter(
                parameter_id=None,
                parameter_code=
                    "energy_demand_change",
                value=
                    float(efficiency_change_pct) / 100.0,
                unit="%",
                energy_type_code=
                    ELECTRICITY_ENERGY_CODE_V21,
                sector_code=
                    sector_code,
                target_scope=
                    efficiency_target_scope
            )
        )

    # --------------------------------------------------------
    # Grid emission-factor reduction
    # --------------------------------------------------------
    if grid_ef_change_pct != 0:
        parameters.append(
            ScenarioParameter(
                parameter_id=None,
                parameter_code=
                    "emission_factor_change",
                value=
                    float(grid_ef_change_pct) / 100.0,
                unit="%",
                energy_type_code=
                    ELECTRICITY_ENERGY_CODE_V21,
                sector_code=None,
                target_scope=
                    "ENERGY"
            )
        )

    return parameters


def run_electricity_policy_response_surface_v21(
        model,
        target_sector_code=
            DEFAULT_EFFICIENCY_TARGET_SECTOR_V21,
        efficiency_levels_pct=None,
        grid_ef_levels_pct=None,
        verbose=True,
        efficiency_target_scope="SECTOR_ENERGY"
    ):
    """
    Run a two-factor electricity policy response surface.

    Factor 1:
        Electricity demand reduction in one target sector.

    Factor 2:
        Electricity emission-factor reduction across
        all sectors.

    Every combination of factor levels is evaluated
    independently.

    No scenario definitions or scenario histories
    are created.
    """

    # --------------------------------------------------------
    # Default levels
    # --------------------------------------------------------
    if efficiency_levels_pct is None:
        efficiency_levels_pct = DEFAULT_ELECTRICITY_EFFICIENCY_LEVELS_PCT_V21

    if grid_ef_levels_pct is None:
        grid_ef_levels_pct = DEFAULT_GRID_EF_LEVELS_PCT_V21

    efficiency_levels_pct = list(efficiency_levels_pct)
    grid_ef_levels_pct = list(grid_ef_levels_pct)

    # --------------------------------------------------------
    # Validate levels
    # --------------------------------------------------------
    for value in (efficiency_levels_pct + grid_ef_levels_pct):
        if value <= -100:
            raise ValueError(
                "Intervention level must be "
                "greater than -100%."
            )

    # --------------------------------------------------------
    # Baseline once
    # --------------------------------------------------------
    baseline_result = model.calculate_baseline()
    baseline_total = float(np.sum(baseline_result["totalEmission"]))

    results = []
    total_runs = (
        len(efficiency_levels_pct) * len(grid_ef_levels_pct)
    )

    run_number = 0
    # --------------------------------------------------------
    # Full-factorial grid
    # --------------------------------------------------------
    for grid_ef_pct in grid_ef_levels_pct:
        for efficiency_pct in (efficiency_levels_pct):
            run_number += 1
            if verbose:
                print(
                    f"[{run_number}/{total_runs}] "
                    f"Efficiency={efficiency_pct}% | "
                    f"Grid EF={grid_ef_pct}%"
                )

            # ------------------------------------------------
            # Baseline point
            # ------------------------------------------------
            if (efficiency_pct == 0 and grid_ef_pct == 0):
                scenario_total = baseline_total
                scenario_result = baseline_result
                changed_cells = 0
                target_electricity_reduction_tj = 0.0
            else:
                parameters = (
                    build_electricity_response_parameters_v21(
                        efficiency_change_pct=efficiency_pct,
                        grid_ef_change_pct=grid_ef_pct,
                        target_sector_code=target_sector_code,
                        efficiency_target_scope=efficiency_target_scope
                    )
                )

                scenario_name = (
                    "RS "
                    f"EE{efficiency_pct:+g}% "
                    f"EF{grid_ef_pct:+g}%"
                )

                result = (
                    run_parameterized_policy_case_v21(
                        model=model,
                        parameters=parameters,
                        scenario_name=scenario_name,
                        baseline_result=baseline_result
                    )
                )
                scenario_total = (
                    result["scenario_total_emission_mtco2"] * 1_000_000
                )
                scenario_result = result
                changed_cells = (
                    result["changed_cells"]
                )
                target_electricity_reduction_tj = (
                    result["target_electricity_reduction_tj"]
                )

            # ------------------------------------------------
            # Emission reduction
            # ------------------------------------------------
            emission_reduction = (
                baseline_total - scenario_total
            )
            emission_reduction_mtco2 = (
                emission_reduction / 1_000_000
            )
            emission_reduction_ktco2 = (
                emission_reduction / 1_000
            )

            national_reduction_pct = (
                emission_reduction / baseline_total * 100.0
                if baseline_total != 0
                else np.nan
            )

            # ------------------------------------------------
            # Scope reductions
            # ------------------------------------------------
            if (efficiency_pct == 0 and grid_ef_pct == 0):
                scope1_reduction = 0.0
                scope2_reduction = 0.0
                scope3_reduction = 0.0
            else:
                scope1_reduction = result["scope1_reduction_ktco2"]
                scope2_reduction = result["scope2_reduction_ktco2"]
                scope3_reduction = result["scope3_reduction_ktco2"]

            # ------------------------------------------------
            # Record
            # ------------------------------------------------
            results.append(
                {
                    "efficiency_change_pct":
                        efficiency_pct,
                    "grid_ef_change_pct":
                        grid_ef_pct,
                    "target_sector_code":
                        (
                            target_sector_code
                            if efficiency_pct != 0
                            else None
                        ),
                    "target_electricity_reduction_tj":
                        target_electricity_reduction_tj,
                    "baseline_total_emission_mtco2":
                        baseline_total/1_000_000,
                    "scenario_total_emission_mtco2":
                        scenario_total/1_000_000,
                    "emission_reduction_ktco2":
                        emission_reduction_ktco2,
                    "emission_reduction_mtco2":
                        emission_reduction_mtco2,
                    "national_reduction_pct":
                        national_reduction_pct,
                    "scope1_reduction_ktco2":
                        scope1_reduction,
                    "scope2_reduction_ktco2":
                        scope2_reduction,
                    "scope3_reduction_ktco2":
                        scope3_reduction,
                    "changed_cells":
                        changed_cells
                }
            )

    # --------------------------------------------------------
    # Long-format result
    # --------------------------------------------------------
    df_long = pd.DataFrame(results)

    # --------------------------------------------------------
    # Validate number of combinations
    # --------------------------------------------------------
    expected_runs = len(efficiency_levels_pct) * len(grid_ef_levels_pct)

    if len(df_long) != expected_runs:
        raise RuntimeError(
            "Response surface contains "
            f"{len(df_long)} results; "
            f"expected {expected_runs}."
        )

    # --------------------------------------------------------
    # National reduction surface
    # --------------------------------------------------------
    df_surface = (
        df_long
        .pivot(
            index="grid_ef_change_pct",
            columns="efficiency_change_pct",
            values="national_reduction_pct"
        )
        .reindex(
            index=grid_ef_levels_pct,
            columns=efficiency_levels_pct
        )
    )

    # --------------------------------------------------------
    # Emission reduction surface
    # --------------------------------------------------------
    df_emission_surface = (
        df_long
        .pivot(
            index="grid_ef_change_pct",
            columns="efficiency_change_pct",
            values="emission_reduction_mtco2"
        )
        .reindex(
            index=grid_ef_levels_pct,
            columns=efficiency_levels_pct
        )
    )

    return (
        df_long,
        df_surface,
        df_emission_surface
    )


def analyze_electricity_response_surface_v21(
        df_surface
    ):
    """
    Derive interaction and marginal-efficiency
    information from the national-reduction surface.

    df_surface:
        Rows    = grid EF change
        Columns = electricity efficiency change
        Values  = national reduction (%)
    """

    efficiency_levels = list(df_surface.columns)
    grid_ef_levels = list(df_surface.index)

    # --------------------------------------------------------
    # Interaction surface
    #
    # I(d,e) =
    # R(d,e) - R(d,0) - R(0,e)
    # --------------------------------------------------------
    interaction_surface = (
        pd.DataFrame(
            index=grid_ef_levels,
            columns=efficiency_levels,
            dtype=float
        )
    )

    # --------------------------------------------------------
    # Incremental efficiency benefit
    #
    # R(d,e) - R(0,e)
    # --------------------------------------------------------
    incremental_efficiency_surface = (
        pd.DataFrame(
            index=grid_ef_levels,
            columns=efficiency_levels,
            dtype=float
        )
    )

    # --------------------------------------------------------
    # Retained efficiency benefit
    #
    # [R(d,e) - R(0,e)] / R(d,0) * 100
    # --------------------------------------------------------
    retained_efficiency_surface = (
        pd.DataFrame(
            index=grid_ef_levels,
            columns=efficiency_levels,
            dtype=float
        )
    )

    for grid_ef_pct in grid_ef_levels:
        ef_only = (df_surface.loc[grid_ef_pct,0])

        for efficiency_pct in (efficiency_levels):
            combined = (df_surface.loc[grid_ef_pct, efficiency_pct])
            efficiency_only = (df_surface.loc[0, efficiency_pct])

            # ----------------------------------------------
            # Interaction
            # ----------------------------------------------
            interaction_surface.loc[grid_ef_pct,efficiency_pct] = (
                combined - efficiency_only - ef_only
            )

            # ----------------------------------------------
            # Additional efficiency benefit
            # ----------------------------------------------
            incremental = combined-ef_only
            incremental_efficiency_surface.loc[grid_ef_pct,efficiency_pct] = incremental

            # ----------------------------------------------
            # Retained fraction
            # ----------------------------------------------
            if efficiency_only != 0:
                retained = incremental/efficiency_only*100.0
            else:
                retained = np.nan

            retained_efficiency_surface.loc[grid_ef_pct,efficiency_pct] = retained

    # --------------------------------------------------------
    # Marginal 10-percentage-point efficiency increments
    # --------------------------------------------------------
    marginal_records = []
    for grid_ef_pct in grid_ef_levels:
        for i in range(1, len(efficiency_levels)):
            previous_level = efficiency_levels[i - 1]
            current_level = efficiency_levels[i]

            marginal_gain = (
                df_surface.loc[grid_ef_pct,current_level]
                -
                df_surface.loc[grid_ef_pct, previous_level]
            )

            marginal_records.append(
                {
                    "grid_ef_change_pct":
                        grid_ef_pct,
                    "efficiency_from_pct":
                        previous_level,
                    "efficiency_to_pct":
                        current_level,
                    "additional_efficiency_pct_points":
                        abs(current_level - previous_level),
                    "additional_national_reduction_pct_points":
                        marginal_gain
                }
            )

    df_marginal = pd.DataFrame(marginal_records)

    return (
        interaction_surface,
        incremental_efficiency_surface,
        retained_efficiency_surface,
        df_marginal
    )

def analyze_response_surface_thresholds_v21(
        df_surface,
        target_levels_pct=None
    ):
    """
    Identify policy combinations that reach selected
    national CO2-reduction targets.

    Two outputs are produced:

    1. df_threshold_by_grid
       For each national target and each fixed grid
       emission-factor reduction, reports the minimum
       electricity-efficiency reduction available within
       the discrete response-surface grid.

    2. df_minimum_combinations
       Reports all combinations achieving the target
       with the minimum combined percentage-point magnitude.

    Note:
        "combined percentage-point magnitude" is only
        a descriptive measure of the discrete grid distance.
        It should not be interpreted as an economic or
        implementation cost.
    """

    if target_levels_pct is None:
        target_levels_pct = [
            10, 20, 30, 40, 50
        ]

    # --------------------------------------------------------
    # Preserve policy levels but order them from least
    # to most stringent.
    # --------------------------------------------------------
    efficiency_levels = sorted(
        list(df_surface.columns),
        key=lambda x: abs(float(x))
    )

    grid_levels = sorted(
        list(df_surface.index),
        key=lambda x: abs(float(x))
    )

    # --------------------------------------------------------
    # Fixed-grid threshold analysis
    # --------------------------------------------------------
    threshold_records = []

    for target in target_levels_pct:
        for grid_ef in grid_levels:
            minimum_efficiency = None
            achieved_reduction = np.nan

            for efficiency in (efficiency_levels):
                reduction = float(df_surface.loc[grid_ef,efficiency])
                if reduction >= target:
                    minimum_efficiency = abs(float(efficiency))
                    achieved_reduction = reduction

                    break

            threshold_records.append(
                {
                    "target_national_reduction_pct":
                        target,
                    "grid_ef_reduction_pct":
                        abs(float(grid_ef)),
                    "minimum_efficiency_reduction_pct":
                        minimum_efficiency,
                    "achieved_national_reduction_pct":
                        achieved_reduction
                }
            )

    df_threshold_long = pd.DataFrame(threshold_records)
    df_threshold_by_grid = (
        df_threshold_long
        .pivot(
            index=
                "target_national_reduction_pct",
            columns=
                "grid_ef_reduction_pct",
            values=
                "minimum_efficiency_reduction_pct"
        )
    )

    df_threshold_by_grid = (
        df_threshold_by_grid
        .reindex(
            index=target_levels_pct,
            columns=sorted(
                [abs(float(x)) for x in grid_levels]
            )
        )
    )

    # --------------------------------------------------------
    # Minimum combined percentage-point magnitude
    # --------------------------------------------------------
    minimum_records = []
    for target in target_levels_pct:
        feasible = []
        for grid_ef in grid_levels:
            for efficiency in (efficiency_levels):
                reduction = float(
                    df_surface.loc[grid_ef, efficiency]
                )
                if reduction >= target:
                    combined_magnitude = (
                        abs(float(efficiency))
                        +
                        abs(float(grid_ef))
                    )
                    feasible.append(
                        {
                            "target":
                                target,
                            "efficiency":
                                abs(float(efficiency)),
                            "grid_ef":
                                abs(float(grid_ef)),
                            "achieved":
                                reduction,
                            "combined_magnitude":
                                combined_magnitude
                        }
                    )

        if not feasible:
            minimum_records.append(
                {
                    "target_national_reduction_pct":
                        target,
                    "minimum_combined_percentage_points":
                        np.nan,
                    "efficiency_reduction_pct":
                        np.nan,
                    "grid_ef_reduction_pct":
                        np.nan,
                    "achieved_national_reduction_pct":
                        np.nan
                }
            )
            continue

        minimum_magnitude = min(
            item["combined_magnitude"]
            for item in feasible
        )

        for item in feasible:
            if np.isclose(
                    item["combined_magnitude"],
                    minimum_magnitude,
                    rtol=1e-10,
                    atol=1e-10
                ):
                minimum_records.append(
                    {
                        "target_national_reduction_pct":
                            target,
                        "minimum_combined_percentage_points":
                            minimum_magnitude,
                        "efficiency_reduction_pct":
                            item["efficiency"],
                        "grid_ef_reduction_pct":
                            item["grid_ef"],
                        "achieved_national_reduction_pct":
                            item["achieved"]
                    }
                )

    df_minimum_combinations = pd.DataFrame(minimum_records)

    return (
        df_threshold_by_grid,
        df_minimum_combinations
    )

def save_electricity_response_surface_heatmap_v21(
        df_surface,
        filepath,
        target_sector_code="149",
        efficiency_target_scope="SECTOR_ENERGY"
    ):
    """
    Save the national CO2 reduction response surface
    as a publication-oriented heatmap.
    """

    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 6))

    image = ax.imshow(df_surface.values,aspect="auto")
    ax.set_xticks(np.arange(len(df_surface.columns)))
    ax.set_yticks(np.arange(len(df_surface.index)))
    ax.set_xticklabels(
        [f"{value:g}%" for value in df_surface.columns]
    )
    ax.set_yticklabels(
        [f"{value:g}%" for value in df_surface.index]
    )
    ax.set_xlabel("Electricity efficiency improvement (%)")
    ax.set_ylabel("Electricity emission-factor reduction (%)")

    if efficiency_target_scope == "ENERGY":
        efficiency_scope_label = ("All sectors")
    else:
        efficiency_scope_label = (f"Sector {target_sector_code}")

    ax.set_title(
        "Electricity Efficiency × Grid Emission-Factor "
        "Response Surface\n"
       #f"Target sector: {target_sector_code}"
        f"Efficiency scope: {efficiency_scope_label}"
    )

    # --------------------------------------------------------
    # Cell values
    # --------------------------------------------------------
    for i in range(len(df_surface.index)):
        for j in range(len(df_surface.columns)):
            value = df_surface.iloc[i,j]
            ax.text(
                j, i, f"{value:.2f}",
                ha="center", va="center"
            )

    colorbar = fig.colorbar(image,ax=ax)
    colorbar.set_label("National CO2 reduction (%)")
    fig.tight_layout()
    fig.savefig(filepath, dpi=300, bbox_inches="tight")

    plt.close(fig)

def run_electricity_policy_response_surface_all_sectors_v21(
        model,
        efficiency_levels_pct=None,
        grid_ef_levels_pct=None,
        verbose=True
    ):
    """
    Surface B:
    Electricity efficiency:
        all 185 sectors
    Electricity emission-factor reduction:
        all sectors
    Uses the same factor levels as Surface A.
    """

    return (
        run_electricity_policy_response_surface_v21(
            model=model,
            target_sector_code=None,
            efficiency_levels_pct=
                efficiency_levels_pct,
            grid_ef_levels_pct=
                grid_ef_levels_pct,
            efficiency_target_scope=
                "ENERGY",
            verbose=verbose
        )
    )


# ============================================================
# Supply-chain contribution analysis
# ============================================================

def calculate_target_supply_chain_contribution_v21(
        model,
        df_sector,
        baseline_result,
        scenario_result,
        target_sector_code
    ):
    """
    Calculate sectoral supply-chain contributions to one
    target sector.
    For target sector j:
        C_i = b_i * L_ij * y_j

    In the current EEIO implementation, y_j is represented
    by model.X_domOut[j], which is the scaling vector used
    by the existing emission calculation.

    The function returns contributions for all 185 sectors.
    Units:
        CO2 contribution = ktCO2
    """

    # --------------------------------------------------------
    # Normalize target sector
    # --------------------------------------------------------
    target_sector_code = normalize_sector_code_v21(target_sector_code)

    # --------------------------------------------------------
    # Locate target sector column
    # --------------------------------------------------------
    sector_column_map = get_sector_column_map_v21(model)
    if target_sector_code not in sector_column_map:
        raise ValueError(
            f"Unknown target sector: {target_sector_code}"
        )

    target_column = int(
        sector_column_map[target_sector_code]
    )

    # --------------------------------------------------------
    # Target activity/output scale
    #
    # Current model uses X_domOut as the scaling vector.
    # --------------------------------------------------------
    target_scale = float(model.X_domOut[target_column])

    # --------------------------------------------------------
    # Leontief requirements for target sector
    #
    # L[:, j] gives the total requirement from every
    # supplying sector i for the target sector j.
    # --------------------------------------------------------
    upstream_output = model.L[:, target_column] * target_scale

    # --------------------------------------------------------
    # Baseline direct emission intensity
    # --------------------------------------------------------
    baseline_B = np.asarray(model.B,dtype=float).reshape(-1)

    # --------------------------------------------------------
    # Scenario direct emission intensity
    # --------------------------------------------------------
    scenario_B = np.asarray(scenario_result["B"],dtype=float).reshape(-1)
    if len(baseline_B) != model.n_sectors:
        raise ValueError(
            "Baseline B dimension does not match model sector count."
        )
    if len(scenario_B) != model.n_sectors:
        raise ValueError(
            "Scenario B dimension does not match model sector count."
        )

    # --------------------------------------------------------
    # Contribution by upstream sector
    # --------------------------------------------------------
    baseline_contribution_tco2 = baseline_B * upstream_output
    scenario_contribution_tco2 = scenario_B * upstream_output
    reduction_tco2 = (
        baseline_contribution_tco2 - scenario_contribution_tco2
    )

    # --------------------------------------------------------
    # Model target totals for validation
    # --------------------------------------------------------
    baseline_target_total_tco2 = float(
        np.asarray(baseline_result["totalEmission"]).reshape(-1)[target_column]
    )
    scenario_target_total_tco2 = float(
        np.asarray(scenario_result["totalEmission"]).reshape(-1)[target_column]
    )

    # --------------------------------------------------------
    # Contribution sums must reproduce target-sector totals
    # --------------------------------------------------------
    calculated_baseline_total = np.sum(baseline_contribution_tco2)
    calculated_scenario_total = np.sum(scenario_contribution_tco2)

    if not np.isclose(
            calculated_baseline_total, baseline_target_total_tco2,
            rtol=1e-9, atol=1e-6
        ):

        raise RuntimeError(
            "Baseline contribution sum does not "
            "match target-sector emission total: "
            f"contribution={calculated_baseline_total}, "
            f"model={baseline_target_total_tco2}"
        )

    if not np.isclose(
            calculated_scenario_total, scenario_target_total_tco2,
            rtol=1e-9, atol=1e-6
        ):

        raise RuntimeError(
            "Scenario contribution sum does not "
            "match target-sector emission total: "
            f"contribution={calculated_scenario_total}, "
            f"model={scenario_target_total_tco2}"
        )

    # --------------------------------------------------------
    # Sector metadata
    # --------------------------------------------------------
    metadata = df_sector.copy()
    metadata["_sector_code_norm"] = (
        metadata["sector_code"].map(normalize_sector_code_v21)
    )

    if (metadata["_sector_code_norm"].duplicated().any()):
        raise ValueError("Duplicate sector codes found in df_sector.")

    metadata = metadata.set_index("_sector_code_norm")

    # --------------------------------------------------------
    # Build records in matrix-column order
    # --------------------------------------------------------
    column_to_sector = {
        int(column): code
        for code, column in sector_column_map.items()
    }

    records = []
    for column in range(model.n_sectors):
        sector_code = column_to_sector[column]

        if sector_code in metadata.index:
            sector_name = str(
                metadata.loc[sector_code, "sector_name"]
            )
            if "sector_agg" in metadata.columns:
                sector_group = metadata.loc[sector_code, "sector_agg"]
            else:
                sector_group = "Unclassified"
        else:
            sector_name = f"Sector {sector_code}"
            sector_group = "Unclassified"

        records.append(
            {
                "sector_code":
                    sector_code,
                "sector_name":
                    sector_name,
                "sector_group":
                    sector_group,
                "upstream_output":
                    upstream_output[column],
                "baseline_contribution_ktco2":
                    baseline_contribution_tco2[column] / 1_000,
                "scenario_contribution_ktco2":
                    scenario_contribution_tco2[column] / 1_000,
                "reduction_ktco2":
                    reduction_tco2[column] / 1_000
            }
        )

    df_contribution = pd.DataFrame(records)

    # --------------------------------------------------------
    # Baseline contribution share
    # --------------------------------------------------------
    if (baseline_target_total_tco2 != 0):
        df_contribution["baseline_share_pct"] = (
            df_contribution["baseline_contribution_ktco2"] /
            (baseline_target_total_tco2 / 1_000) * 100
        )
    else:
        df_contribution["baseline_share_pct"] = np.nan

    # --------------------------------------------------------
    # Scenario contribution share
    # --------------------------------------------------------
    if (scenario_target_total_tco2 != 0):
        df_contribution["scenario_share_pct"] = (
            df_contribution["scenario_contribution_ktco2"] /
            (scenario_target_total_tco2/1_000) * 100
        )
    else:
        df_contribution["scenario_share_pct"] = np.nan

    # --------------------------------------------------------
    # Total target-sector reduction
    # --------------------------------------------------------
    total_reduction_ktco2 = (
        (baseline_target_total_tco2 - scenario_target_total_tco2)/1_000
    )

    # --------------------------------------------------------
    # Reduction share
    # --------------------------------------------------------
    if total_reduction_ktco2 != 0:
        df_contribution["reduction_share_pct"] = (
            df_contribution["reduction_ktco2"] / total_reduction_ktco2 * 100
        )
    else:
        df_contribution["reduction_share_pct"] = np.nan

    # --------------------------------------------------------
    # Pareto contribution
    #
    # Positive reductions are used for the Pareto curve.
    # Negative values, if any, are retained in the main
    # table but excluded from cumulative positive contribution.
    # --------------------------------------------------------
    df_contribution["pareto_reduction_ktco2"] = np.maximum(
        df_contribution["reduction_ktco2"], 0
    )

    positive_total = (
        df_contribution["pareto_reduction_ktco2"].sum()
    )

    # --------------------------------------------------------
    # Sort by reduction
    # --------------------------------------------------------
    df_contribution = (
        df_contribution
        .sort_values(
            by=[
                "reduction_ktco2",
                "baseline_contribution_ktco2"
            ],
            ascending=[
                False,
                False
            ]
        )
        .reset_index(drop=True)
    )

    # --------------------------------------------------------
    # Pareto rank
    # --------------------------------------------------------
    df_contribution.insert(
        0,
        "rank",
        np.arange(1,len(df_contribution) + 1)
    )

    # --------------------------------------------------------
    # Pareto share and cumulative share
    # --------------------------------------------------------
    if positive_total > 0:
        df_contribution["pareto_share_pct"] = (
            df_contribution["pareto_reduction_ktco2"]
            / positive_total * 100
        )

        df_contribution["pareto_cumulative_pct"] = (
            df_contribution["pareto_share_pct"].cumsum()
        )
    else:
        df_contribution["pareto_share_pct"] = np.nan
        df_contribution["pareto_cumulative_pct"] = np.nan

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------
    top5_share = np.nan
    if (positive_total > 0 and len(df_contribution) >= 5):
        top5_share = float(
            df_contribution.loc[4,"pareto_cumulative_pct"]
        )

    top10_share = np.nan
    if (positive_total > 0 and len(df_contribution) >= 10):
        top10_share = float(
            df_contribution.loc[9,"pareto_cumulative_pct"]
        )

    reaches_80 = (
        df_contribution[df_contribution["pareto_cumulative_pct"] >= 80]
    )
    sectors_to_80pct = (
        int(reaches_80.iloc[0]["rank"])
        if len(reaches_80) > 0
        else None
    )

    summary = {
        "target_sector_code":
            target_sector_code,
        "baseline_target_emission_ktco2":
            baseline_target_total_tco2 / 1_000,
        "scenario_target_emission_ktco2":
            scenario_target_total_tco2 / 1_000,
        "target_sector_reduction_ktco2":
            total_reduction_ktco2,
        "positive_reduction_total_ktco2":
            positive_total,
        "top5_cumulative_share_pct":
            top5_share,
        "top10_cumulative_share_pct":
            top10_share,
        "sectors_to_reach_80pct":
            sectors_to_80pct
    }

    return (
        df_contribution,
        summary
    )

def run_target_supply_chain_contribution_analysis_v21(
        model,
        df_sector,
        target_sector_code="149",
        efficiency_change_pct=0,
        grid_ef_change_pct=-20
    ):
    """
    Run one temporary scenario and calculate the
    supply-chain contribution to the selected target sector.

    Default experiment:
        Target:
            Sector 149
        Scenario:
            Electricity emission factor -20%
            All sectors
    """

    baseline_result = model.calculate_baseline()
    parameters = (
        build_electricity_response_parameters_v21(
            efficiency_change_pct=efficiency_change_pct,
            grid_ef_change_pct=grid_ef_change_pct,
            target_sector_code=target_sector_code
        )
    )

    scenario = (
        build_batch_scenario_from_parameters_v21(
            parameters,
            (
                "Contribution analysis "
                f"EE{efficiency_change_pct:+g}% "
                f"EF{grid_ef_change_pct:+g}%"
            )
        )
    )

    processor = ScenarioProcessor(model)
    inputs = processor.apply(model, scenario)

    if (0):
        electricity_row = int(ENERGY_MAPPING[ELECTRICITY_ENERGY_CODE_V21]["finCons_row"])
        baseline_electricity = model.mat_finCons[electricity_row,:]
        scenario_electricity = inputs.mat_finCons[electricity_row,:]
        difference = scenario_electricity - baseline_electricity
        unchanged = np.where(
            np.isclose(difference,0.0,rtol=1e-10,atol=1e-10)
        )[0]

        print()
        print("=== 2016 UNCHANGED ELECTRICITY CELLS ===")

        for column in unchanged:
            sector_code = model.sector_column_map
            reverse_map = {
                int(col): code
                for code, col
                in model.sector_column_map.items()
            }
            code = reverse_map[int(column)]

            row = (df_sector[
                    df_sector["sector_code"].astype(str).str.zfill(3)
                    == str(code).zfill(3)
                ]
            )

            if len(row) > 0:
                name = row.iloc[0]["sector_name"]
            else:
                name = "Unknown"

            print(code, name,
                "baseline =",baseline_electricity[column],
                "scenario =",scenario_electricity[column]
            )

            #==alt
            diff_matrix = inputs.mat_finCons - model.mat_finCons
            changed_columns = np.where(
                np.any(~np.isclose(diff_matrix, 0.0, rtol=1e-10, atol=1e-10),
                    axis=0)
            )[0]
            print("Number of changed columns:",len(changed_columns))
            print("Number of unchanged columns:",
                model.n_sectors-len(changed_columns))

    scenario_result = model.calculate_scenario_from_inputs(inputs)

    return (
        calculate_target_supply_chain_contribution_v21(
            model=model,
            df_sector=df_sector,
            baseline_result= baseline_result,
            scenario_result= scenario_result,
            target_sector_code= target_sector_code
        )
    )

# ============================================================
# 1. Baseline contribution Top-N
# ============================================================

def prepare_baseline_contribution_top_v21(
        df_contribution,
        top_n=10
    ):
    """
    Rank sectors by their baseline contribution to the
    selected target sector.

    Returns the Top-N sectors with:
        baseline contribution
        baseline share
        cumulative baseline share
    """
    required_columns = [
        "sector_code",
        "sector_name",
        "baseline_contribution_ktco2",
        "baseline_share_pct"
    ]

    for column in required_columns:
        if column not in df_contribution.columns:
            raise ValueError(
                f"Missing required column: {column}"
            )

    df_top = (
        df_contribution[required_columns].copy()
        .sort_values(
            by=["baseline_contribution_ktco2",
                "sector_code"],
            ascending=[False,True]
        )
        .reset_index(drop=True)
    )

    # --------------------------------------------------------
    # Recalculate baseline share from the full decomposition
    # --------------------------------------------------------
    baseline_total = (
        df_contribution["baseline_contribution_ktco2"].sum()
    )

    if baseline_total == 0:
        df_top["baseline_share_pct"] = np.nan
        df_top["baseline_cumulative_share_pct"] = np.nan
    else:
        df_top["baseline_share_pct"] = (
            df_top["baseline_contribution_ktco2"]/
            baseline_total*100
        )
        df_top["baseline_cumulative_share_pct"] = (
            df_top["baseline_share_pct"].cumsum()
        )

    # --------------------------------------------------------
    # Rank
    # --------------------------------------------------------
    df_top.insert(
        0,
        "baseline_rank",
        np.arange(1,len(df_top) + 1)
    )

    return (
        df_top.head(top_n).copy()
    )


# ============================================================
# 2. Pareto preparation
# ============================================================
def prepare_supply_chain_pareto_v21(
        df_contribution
    ):
    """
    Prepare the complete sector-level reduction data
    for Pareto analysis.

    Positive reduction contributions are ranked from
    largest to smallest.

    Negative reduction values are retained in the original
    contribution table but are excluded from the positive
    Pareto accumulation.
    """

    required_columns = [
        "sector_code",
        "sector_name",
        "reduction_ktco2"
    ]

    for column in required_columns:
        if column not in df_contribution.columns:
            raise ValueError(
                f"Missing required column: {column}"
            )

    df_pareto = (
        df_contribution[required_columns]
        .copy()
    )

    # --------------------------------------------------------
    # Positive contribution only
    # --------------------------------------------------------
    df_pareto["pareto_reduction_ktco2"] = (
        df_pareto["reduction_ktco2"]
        .clip(lower=0)
    )

    # --------------------------------------------------------
    # Rank
    # --------------------------------------------------------
    df_pareto = (
        df_pareto
        .sort_values(
            by=["pareto_reduction_ktco2","sector_code"],
            ascending=[False,True]
        )
        .reset_index(drop=True)
    )

    df_pareto.insert(
        0,
        "pareto_rank",
        np.arange(1, len(df_pareto) + 1)
    )

    positive_total = (
        df_pareto["pareto_reduction_ktco2"].sum()
    )

    if positive_total > 0:
        df_pareto["pareto_share_pct"] = (
            df_pareto["pareto_reduction_ktco2"]
            /
            positive_total * 100
        )

        df_pareto["pareto_cumulative_pct"] = (
            df_pareto["pareto_share_pct"].cumsum()
            )
    else:
        df_pareto["pareto_share_pct"] = np.nan
        df_pareto["pareto_cumulative_pct"] = np.nan

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------
    top5_share = np.nan
    top10_share = np.nan
    rank_80 = None

    if positive_total > 0:
        if len(df_pareto) >= 5:
            top5_share = float(df_pareto.loc[4,"pareto_cumulative_pct"])

        if len(df_pareto) >= 10:
            top10_share = float(df_pareto.loc[9,"pareto_cumulative_pct"])

        reached_80 = df_pareto[df_pareto["pareto_cumulative_pct"] >= 80]
        if len(reached_80) > 0:
            rank_80 = int(reached_80.iloc[0]["pareto_rank"])

    summary = {
        "positive_reduction_total_ktco2":
            positive_total,
        "top5_cumulative_share_pct":
            top5_share,
        "top10_cumulative_share_pct":
            top10_share,
        "sectors_to_reach_80pct":
            rank_80
    }

    return (
        df_pareto,
        summary
    )

# ============================================================
# Pareto figure
# ============================================================

def build_supply_chain_pareto_figure_v21(
        df_pareto,
        target_sector_code="149",
        target_sector_name=None,
        plot_n=40
    ):
    """
    Build a Pareto chart combining:

        bars  = sectoral CO2 reduction
        line  = cumulative contribution share

    The first plot_n sectors are displayed, while the
    underlying df_pareto retains all sectors.
    """

    if len(df_pareto) == 0:
        raise ValueError(
            "Pareto dataframe is empty."
        )

    df_plot = df_pareto.head(plot_n).copy()
    if target_sector_name is None:
        target_sector_name = f"Sector {target_sector_code}"

    # --------------------------------------------------------
    # Figure with secondary Y axis
    # --------------------------------------------------------
    fig = make_subplots(
        specs=[[{"secondary_y": True}]]
    )

    # --------------------------------------------------------
    # Hover information
    # --------------------------------------------------------
    customdata = np.column_stack(
        [
            df_plot["sector_code"].astype(str),
            df_plot["sector_name"].astype(str),
            df_plot["pareto_share_pct"].values,
            df_plot["pareto_cumulative_pct"].values
        ]
    )

    # --------------------------------------------------------
    # Reduction bars
    # --------------------------------------------------------
    fig.add_trace(
        go.Bar(
            x=df_plot["pareto_rank"],
            y=df_plot["pareto_reduction_ktco2"],
            name="Reduction",
            customdata=customdata,
            hovertemplate=(
                "<b>%{customdata[1]}</b><br>"
                "Sector code: %{customdata[0]}<br>"
                "Reduction: %{y:,.2f} ktCO2<br>"
                "Contribution: %{customdata[2]:.2f}%<br>"
                "Cumulative: %{customdata[3]:.2f}%"
                "<extra></extra>"
            )
        ),
        secondary_y=False
    )

    # --------------------------------------------------------
    # Cumulative line
    # --------------------------------------------------------
    fig.add_trace(
        go.Scatter(
            x=df_plot["pareto_rank"],
            y=df_plot["pareto_cumulative_pct"],
            mode="lines+markers",
            name="Cumulative contribution",
            hovertemplate=(
                "Rank: %{x}<br>"
                "Cumulative contribution: "
                "%{y:.2f}%"
                "<extra></extra>"
            )
        ),
        secondary_y=True
    )

    # --------------------------------------------------------
    # 80% reference line
    # --------------------------------------------------------
    fig.add_trace(
        go.Scatter(
            x=[1,int(df_plot["pareto_rank"].max())],
            y=[80,80],
            mode="lines",
            name="80% threshold",
            line=dict(dash="dash"),
            hoverinfo="skip",
            showlegend=False
        ),
        secondary_y=True
    )

    # --------------------------------------------------------
    # Layout
    # --------------------------------------------------------
    fig.update_layout(
        title=f"Pareto Contribution Analysis — {target_sector_name}",
        height=650,
        width=1000,
        hovermode="x unified",
        bargap=0.15,
        margin=dict(l=80,r=80,t=90,b=80)
    )

    fig.update_xaxes(title_text="Sector rank",dtick=5)
    fig.update_yaxes(title_text="CO2 reduction (ktCO2)",secondary_y=False)
    fig.update_yaxes(
        title_text="Cumulative contribution (%)",
        range=[0,100],
        secondary_y=True
    )

    return fig

# ============================================================
# 3. Aggregate contribution to sector groups
# ============================================================

def aggregate_supply_chain_contribution_v21(
        df_contribution,
        expected_group_count=14
    ):
    """
    Aggregate 185 sector-level supply-chain contributions
    into the existing sector groups.
    """

    required_columns = [
        "sector_group",
        "baseline_contribution_ktco2",
        "scenario_contribution_ktco2",
        "reduction_ktco2"
    ]

    for column in required_columns:
        if column not in df_contribution.columns:
            raise ValueError(
                "Missing required column: "
                f"{column}"
            )

    df = df_contribution.copy()

    # --------------------------------------------------------
    # Handle missing group labels
    # --------------------------------------------------------
    df["sector_group"] = (
        df["sector_group"]
        .fillna("Unclassified")
        .astype(str)
    )

    # --------------------------------------------------------
    # Aggregate
    # --------------------------------------------------------
    df_group = (df
        .groupby("sector_group",as_index=False)
        .agg(
            baseline_contribution_ktco2=("baseline_contribution_ktco2","sum"),
            scenario_contribution_ktco2=("scenario_contribution_ktco2","sum"),
            reduction_ktco2=("reduction_ktco2","sum")
        )
    )

    # --------------------------------------------------------
    # Validate number of groups
    # --------------------------------------------------------
    actual_group_count = (len(df_group))

    if (expected_group_count is not None
        and
        actual_group_count != expected_group_count
        ):
        raise RuntimeError(
            "Unexpected number of sector groups: "
            f"{actual_group_count}; "
            f"expected {expected_group_count}."
        )

    # --------------------------------------------------------
    # Total baseline
    # --------------------------------------------------------
    baseline_total = (
        df_group["baseline_contribution_ktco2"].sum()
    )

    if baseline_total != 0:
        df_group["baseline_share_pct"] = (
            df_group["baseline_contribution_ktco2"]
            /baseline_total*100
        )
    else:
        df_group["baseline_share_pct"] = np.nan

    # --------------------------------------------------------
    # Total scenario
    # --------------------------------------------------------
    scenario_total = (
        df_group["scenario_contribution_ktco2"].sum()
    )

    if scenario_total != 0:
        df_group["scenario_share_pct"] = (
            df_group["scenario_contribution_ktco2"]
            /scenario_total*100
        )

    else:
        df_group["scenario_share_pct"] = np.nan

    # --------------------------------------------------------
    # Reduction share
    # --------------------------------------------------------
    total_reduction = (
        df_group["reduction_ktco2"].sum()
    )

    if total_reduction != 0:
        df_group["reduction_share_pct"] = (
            df_group["reduction_ktco2"]
            /total_reduction*100
        )

        df_group["reduction_cumulative_pct"] = (
            df_group["reduction_share_pct"]
            .sort_values(ascending=False).cumsum()
        )

    else:
        df_group["reduction_share_pct"] = np.nan
        df_group["reduction_cumulative_pct"] = np.nan

    # --------------------------------------------------------
    # Sort
    # --------------------------------------------------------
    df_group = (
        df_group
        .sort_values(
            by=["reduction_ktco2","sector_group"],
            ascending=[False,True]
        )
        .reset_index(drop=True)
    )

    # Recalculate cumulative after final sorting
    if total_reduction != 0:
        df_group["reduction_cumulative_pct"] = (
            df_group["reduction_share_pct"].cumsum()
        )

    return df_group

# ============================================================
# Sankey figure
# ============================================================
def build_supply_chain_contribution_sankey_v21(
        df_group,
        target_sector_code="149",
        target_sector_name=None,
        group_name_map=None
    ):
    """
    Build a Sankey diagram showing contribution of the
    aggregated sector groups to the selected target sector.

    Flow value:
        reduction_ktco2

    Source:
        sector group

    Target:
        selected target sector
    """

    if target_sector_name is None:
        target_sector_name = f"Sector {target_sector_code}"

    if len(df_group) == 0:
        raise ValueError(
            "Sector-group dataframe is empty."
        )

    # --------------------------------------------------------
    # Sankey cannot represent negative flows.
    # --------------------------------------------------------
    tolerance = 1e-12
    invalid = df_group["reduction_ktco2"] <= tolerance
    if invalid.any():
        invalid_groups = (
            df_group.loc[invalid,"sector_group"]
            .tolist()
        )

        raise ValueError(
            "Sankey requires positive reduction "
            "values. Problematic groups: "
            f"{invalid_groups}"
        )

    # --------------------------------------------------------
    # Sort groups by contribution
    # --------------------------------------------------------
    df_plot = (
        df_group
        .sort_values(by="reduction_ktco2",ascending=False)
        .reset_index(drop=True)
    )

    #group_labels = df_plot["sector_group"].astype(str).tolist()

    group_codes = (
        df_plot["sector_group"].astype(str)
        .map(lambda value: value.zfill(2))
        .tolist()
    )
    if group_name_map is None:
        group_name_map = {}
    group_names = [
        group_name_map.get(code,"Unknown group")
        for code in group_codes
    ]
    group_labels = [
        format_sector_group_label_v21(
            code, group_name_map, max_width=32
        )
        for code in group_codes
    ]

    target_label = f"{target_sector_code} - {target_sector_name}"
    labels = group_labels + [target_label]
    target_index = (len(group_labels))

    # --------------------------------------------------------
    # Links
    # --------------------------------------------------------
    source = list(range(len(group_labels)))
    target = [target_index for _ in source]
    values = df_plot["reduction_ktco2"].astype(float).tolist()

    # --------------------------------------------------------
    # Hover information
    # --------------------------------------------------------
    link_customdata = [
        [
            str(row["sector_group"]).zfill(2),
            group_name_map.get(
                str(row["sector_group"]).zfill(2),
                "Unknown group"
            ),
            float(row["reduction_ktco2"]),
            float(row["reduction_share_pct"])
        ]
        for _, row
        in df_plot.iterrows()
    ]

    node_customdata = [
        [
            str(row["sector_group"]).zfill(2),
            group_name_map.get(
                str(row["sector_group"]).zfill(2),"Unknown group"),
            float(row["reduction_ktco2"]),
            float(row["reduction_share_pct"])
        ]
        for _, row
        in df_plot.iterrows()
    ]

    node_customdata.append(
        [
            target_sector_code,
            target_sector_name,
            float(df_plot["reduction_ktco2"].sum()),
            100.0
        ]
    )

    # --------------------------------------------------------
    # Figure
    # --------------------------------------------------------
    fig = go.Figure(
        data=[
            go.Sankey(
                arrangement="snap",
                valueformat=".2f",
                valuesuffix=" ktCO2",
                node=dict(
                    pad=16,
                    thickness=18,
                    label=labels,
                    customdata=node_customdata,
                    hovertemplate=(
                        "<b>%{customdata[0]}</b> — "
                        "%{customdata[1]}<br>"
                        "Reduction: "
                        "%{customdata[2]:,.2f} ktCO2<br>"
                        "Share: "
                        "%{customdata[3]:.2f}%"
                        "<extra></extra>"
                    )
                ),
                link=dict(
                    source=source,
                    target=target,
                    value=values,
                    customdata=link_customdata,
                    hovertemplate=(
                        "<b>%{customdata[0]}</b> — "
                        "%{customdata[1]}<br>"
                        "→ "
                        f"<b>{target_label}</b><br>"
                        "Reduction: "
                        "%{customdata[2]:,.2f} ktCO2<br>"
                        "Share: "
                        "%{customdata[3]:.2f}%"
                        "<extra></extra>"
                    )
                )
            )
        ]
    )

    # --------------------------------------------------------
    # Layout
    # --------------------------------------------------------
    fig.update_layout(
        title=(
            f"Supply-Chain Emission Reduction Contribution — {target_label}"
        ),
        width=1200,
        height=850,
        font=dict(size=12),
        margin=dict(l=260,r=40,t=80,b=30)
    )

    return fig

def get_sector_group_name_map_v21(db):
    """
    Return the official aggregate-sector names
    from the existing sector_agg master table.

    Returns
    -------
    dict
        Example:
        {
            "01": "....",
            "02": "....",
            ...
        }
    """

    cursor = db.connection.cursor(
        MySQLdb.cursors.DictCursor
    )
    cursor.execute(
        """
        SELECT id, name FROM sector_agg ORDER BY id
        """
    )
    rows = cursor.fetchall()
    cursor.close()

    return {
        str(row["id"]).zfill(2): str(row["name"]).strip()
        for row in rows
    }

def format_sector_group_label_v21(
        sector_group,
        group_name_map,
        max_width=32
    ):
    """
    Create a compact visible Sankey label.

    The full official name remains available through
    hover information.
    """
    code = str(sector_group).zfill(2)
    full_name = group_name_map.get(code,"Unknown group")
    short_name = (
        textwrap.shorten(full_name,width=max_width,placeholder="...")
    )

    return f"{code} — {short_name}"

# ============================================================
# Year-to-year robustness analysis
# ============================================================

def run_electricity_demand_year_robustness_v21(
        db,
        years=(2016, 2020),
        demand_change_pct=-10,
        verbose=True
    ):
    """
    Compare the same all-sector electricity-demand intervention
    across different EEIO model years.

    Scenario:
        energy_demand_change
        Electricity
        ENERGY
        -10%

    The EEIO model itself changes with the year, including:
        - IO structure
        - final energy data
        - conversion factors, if year-dependent
        - emission factors, if year-dependent

    No scenario definitions or scenario-history records
    are created.
    """

    results = []
    # --------------------------------------------------------
    # Fixed scenario definition
    # --------------------------------------------------------
    parameters = (
        build_electricity_response_parameters_v21(
            efficiency_change_pct=
                demand_change_pct,
            grid_ef_change_pct=
                0,
            target_sector_code=
                None,
            efficiency_target_scope=
                "ENERGY"
        )
    )

    # --------------------------------------------------------
    # Run each model year
    # --------------------------------------------------------
    for year in years:
        fname_io = f"io_ind_{year}.csv"
        cache_key = (year, fname_io)

        if verbose:
            print()
            print("=" * 60)
            print(f"YEAR ROBUSTNESS TEST: {year}")
            print(f"Scenario: Electricity demand {demand_change_pct}% (all sectors)")

        # ----------------------------------------------------
        # Load/build year-specific EEIO model
        # ----------------------------------------------------
        model = get_eeio_model(
            cache_key=cache_key,
            model_builder=
                lambda year=year,
                       fname_io=fname_io:
                    build_model_year(
                        db=db,
                        fname_io=fname_io,
                        year=year
                    )
        )

        # ----------------------------------------------------
        # Basic structural validation
        # ----------------------------------------------------
        if model.n_sectors != 185:
            raise RuntimeError(
                f"Unexpected sector count for "
                f"{year}: {model.n_sectors}; "
                f"expected 185."
            )

        # ----------------------------------------------------
        # Baseline
        # ----------------------------------------------------
        baseline_result = model.calculate_baseline()

        # ----------------------------------------------------
        # Run the same parameterized scenario
        # ----------------------------------------------------
        scenario_result = (
            run_parameterized_policy_case_v21(
                model=model,
                parameters=
                    parameters,
                scenario_name=
                    (f"Year robustness - Electricity demand {demand_change_pct}%"),
                baseline_result=baseline_result
            )
        )

        # ----------------------------------------------------
        # Electricity physical validation
        # ----------------------------------------------------
        electricity_row = int(
            ENERGY_MAPPING[ELECTRICITY_ENERGY_CODE_V21]["finCons_row"]
        )
        baseline_electricity_mwh = float(
            np.sum(model.mat_finCons[electricity_row, :])
        )
        baseline_electricity_tj = (
            baseline_electricity_mwh * 0.0036
        )
        electricity_reduction_tj = (
            scenario_result["target_electricity_reduction_tj"]
        )
        scenario_electricity_tj = (
            baseline_electricity_tj - electricity_reduction_tj
        )
        physical_change_pct = (
            (scenario_electricity_tj - baseline_electricity_tj)
            / baseline_electricity_tj * 100.0
            if baseline_electricity_tj != 0
            else np.nan
        )

        # ----------------------------------------------------
        # Record
        # ----------------------------------------------------

        row = {
            "year": year,
            "number_of_sectors": model.n_sectors,
            "parameter_code": "energy_demand_change",
            "energy_type_code": ELECTRICITY_ENERGY_CODE_V21,
            "target_scope": "ENERGY",
            "change_pct": demand_change_pct,
            "baseline_electricity_tj":
                baseline_electricity_tj,
            "scenario_electricity_tj":
                scenario_electricity_tj,
            "electricity_reduction_tj":
                electricity_reduction_tj,
            "physical_change_check_pct":
                physical_change_pct,
            "baseline_emission_mtco2":
                scenario_result["baseline_total_emission_mtco2"],
            "scenario_emission_mtco2":
                scenario_result["scenario_total_emission_mtco2"],
            "emission_reduction_mtco2":
                scenario_result["emission_reduction_mtco2"],
            "emission_reduction_ktco2":
                scenario_result["emission_reduction_ktco2"],

            # Positive reduction convention
            "reduction_pct":
                scenario_result["national_reduction_pct"],

            # Signed version corresponding to
            # (scenario - baseline) / baseline
            "relative_change_pct":
                -scenario_result["national_reduction_pct"],
            "scope1_reduction_ktco2":
                scenario_result["scope1_reduction_ktco2"],
            "scope2_reduction_ktco2":
                scenario_result["scope2_reduction_ktco2"],
            "scope3_reduction_ktco2":
                scenario_result["scope3_reduction_ktco2"],
            "changed_cells":
                scenario_result["changed_cells"]
        }

        results.append(row)

        # ----------------------------------------------------
        # Console validation
        # ----------------------------------------------------

        if verbose:
            print(f"Baseline emissions: {row['baseline_emission_mtco2']:.6f} MtCO2"
            )
            print(f"Scenario emissions: {row['scenario_emission_mtco2']:.6f} MtCO2"
            )
            print(f"Emission reduction: {row['emission_reduction_mtco2']:.6f} MtCO2"
            )
            print(f"Relative reduction: {row['reduction_pct']:.6f}%"
            )
            print(f"Electricity reduction: {row['electricity_reduction_tj']:.6f} TJ"
            )
            print(f"Physical change check: {row['physical_change_check_pct']:.6f}%"
            )
            print(f"Changed cells: {row['changed_cells']}"
            )

    # --------------------------------------------------------
    # Result dataframe
    # --------------------------------------------------------
    df_robustness = (
        pd.DataFrame(results)
        .sort_values("year")
        .reset_index(drop=True)
    )

    # --------------------------------------------------------
    # Require requested years
    # --------------------------------------------------------
    if len(df_robustness) != len(years):
        raise RuntimeError(
            "Number of robustness results does not match requested years."
        )

    # --------------------------------------------------------
    # Year comparison
    # --------------------------------------------------------
    year_values = df_robustness["year"].tolist()
    if (2016 in year_values and 2020 in year_values):
        row_2016 = df_robustness[df_robustness["year"] == 2016].iloc[0]
        row_2020 = df_robustness[df_robustness["year"] == 2020].iloc[0]
        R2016 = float(row_2016["reduction_pct"])
        R2020 = float(row_2020["reduction_pct"])

        comparison = {
            "R_2016_pct":R2016,
            "R_2020_pct":R2020,
            "difference_2020_minus_2016_pp": R2020 - R2016,
            "effect_ratio_2020_to_2016":
                (R2020 / R2016 if R2016 != 0 else np.nan
                ),
            "effect_change_2020_vs_2016_pct":
                ((R2020 - R2016) / R2016 * 100.0
                    if R2016 != 0 else np.nan
                ),
            "absolute_reduction_difference_mtco2":
                (float(row_2020["emission_reduction_mtco2"])
                    - float(row_2016["emission_reduction_mtco2"])
                ),
            "baseline_emission_difference_mtco2":
                (float(row_2020["baseline_emission_mtco2"])
                    - float(row_2016["baseline_emission_mtco2"])
                )
        }

    else:
        comparison = None

    return (df_robustness, comparison)


# ============================================================
# Deterministic parameter sensitivity
# ============================================================

def run_single_sector_electricity_sensitivity_v21(
        model,
        sector_code="149",
        change_levels_pct=None,
        baseline_result=None
    ):
    """
    Deterministic sensitivity analysis for one sector.

    The same electricity-demand intervention is applied to
    the selected sector at several deterministic levels.

    Default:
        -5%, -10%, -15%, -20%, -30%, -40%

    No database scenario/history records are created.
    """

    if change_levels_pct is None:
        change_levels_pct = [
            -5,-10,-15,-20,-30,-40
        ]

    # --------------------------------------------------------
    # Validate sector code
    # --------------------------------------------------------
    sector_code = normalize_sector_code_v21(sector_code)

    # --------------------------------------------------------
    # Baseline once
    # --------------------------------------------------------
    if baseline_result is None:
        baseline_result = model.calculate_baseline()
    results = []

    # --------------------------------------------------------
    # Run sensitivity levels
    # --------------------------------------------------------
    for change_pct in change_levels_pct:
        change_pct = float(change_pct)
        if change_pct >= 0:
            raise ValueError(
                "This sensitivity function expects "
                "electricity-demand reductions, "
                "so change_pct must be negative."
            )

        change_fraction = (change_pct / 100.0)
        result = (
            run_single_sector_electricity_batch_v21(
                model=model,
                sector_code=sector_code,
                change=change_fraction,
                baseline_result=baseline_result
            )
        )

        # ----------------------------------------------------
        # Scenario effectiveness ratio
        # ----------------------------------------------------
        reduction_pct = float(result["economy_wide_reduction_pct"])
        intervention_magnitude_pct = abs(change_pct)
        ser = reduction_pct / intervention_magnitude_pct

        # ----------------------------------------------------
        # Discrete elasticity check
        # ----------------------------------------------------
        baseline_emission = float(
            result["baseline_total_emission_mtco2"]
        )
        scenario_emission = float(
            result["scenario_total_emission_mtco2"]
        )
        baseline_electricity = float(
            result["baseline_electricity_tj"]
        )
        scenario_electricity = float(
            result["scenario_electricity_tj"]
        )
        emission_relative_change = (
            (scenario_emission - baseline_emission
            ) / baseline_emission
            if baseline_emission != 0
            else np.nan
        )
        electricity_relative_change = (
            (scenario_electricity - baseline_electricity)
            / baseline_electricity
            if baseline_electricity != 0
            else np.nan
        )

        response_elasticity = (
            emission_relative_change / electricity_relative_change
            if electricity_relative_change != 0
            else np.nan
        )

        # ----------------------------------------------------
        # Add sensitivity information
        # ----------------------------------------------------
        row = result.copy()
        row["change_pct"] = change_pct
        row["intervention_magnitude_pct"] = intervention_magnitude_pct
        row["ser"] = ser
        row["response_elasticity"] = response_elasticity
        row["ser_elasticity_difference"] = ser - response_elasticity

        results.append(row)

    # --------------------------------------------------------
    # DataFrame
    # --------------------------------------------------------
    df_sensitivity = (
        pd.DataFrame(results)
        .sort_values(by="intervention_magnitude_pct")
        .reset_index(drop=True)
    )

    return df_sensitivity


# ============================================================
# Sector ranking stability
# ============================================================

def run_sector_electricity_ranking_sensitivity_v21(
        model,
        df_sector,
        change_levels_pct=None,
        baseline_result=None,
        verbose=True
    ):
    """
    Evaluate sector ranking stability under different
    electricity-demand reduction magnitudes.

    Default:
        -10%, -20%, -30%

    Every level is applied independently to each of
    the 185 sectors.

    Returns:
        df_rank_long
        df_score_matrix
        df_rank_matrix
        df_rank_correlation
        df_rank_stability
    """

    if change_levels_pct is None:
        change_levels_pct = [
            -10, -20, -30
        ]

    change_levels_pct = [
        float(value)
        for value in change_levels_pct
    ]

    # --------------------------------------------------------
    # Baseline
    # --------------------------------------------------------
    if baseline_result is None:
        baseline_result = (
            model.calculate_baseline()
        )

    # --------------------------------------------------------
    # Sector metadata
    # --------------------------------------------------------
    sectors = []
    seen_codes = set()

    for _, row in (df_sector.iterrows()):
        sector_code = normalize_sector_code_v21(row["sector_code"])

        if sector_code in seen_codes:
            continue
        seen_codes.add(sector_code)
        sectors.append(
            {
                "sector_code": sector_code,
                "sector_name": str(row["sector_name"])
            }
        )

    if len(sectors) != model.n_sectors:
        raise RuntimeError(
            "Sector metadata count does not "
            "match model sector count: "
            f"{len(sectors)} vs "
            f"{model.n_sectors}"
        )

    # --------------------------------------------------------
    # Run all sensitivity levels
    # --------------------------------------------------------
    all_results = []
    total_runs =  len(change_levels_pct) * len(sectors)

    run_number = 0
    for change_pct in change_levels_pct:

        change_fraction = change_pct / 100.0
        level_results = []

        for sector in sectors:
            run_number += 1
            if verbose:
                print(
                    f"[{run_number}/{total_runs}] "
                    f"Change={change_pct:g}% | "
                    f"{sector['sector_code']} - "
                    f"{sector['sector_name']}"
                )

            result = (
                run_single_sector_electricity_batch_v21(
                    model=model,
                    sector_code=sector["sector_code"],
                    change=change_fraction,
                    baseline_result= baseline_result
                )
            )

            row = result.copy()
            row["change_pct"] = change_pct
            row["sector_name"] = (
                sector["sector_name"]
            )

            level_results.append(row)
            all_results.append(row)

    # --------------------------------------------------------
    # Long-format result
    # --------------------------------------------------------
    df_rank_long = pd.DataFrame(all_results)

    # --------------------------------------------------------
    # Rank within each intervention level
    # --------------------------------------------------------
    df_rank_long["rank"] = np.nan

    for change_pct in change_levels_pct:
        mask = (
            df_rank_long["change_pct"] == change_pct
        )

        level = (
            df_rank_long.loc[mask]
            .copy()
            .sort_values(
                by=["economy_wide_reduction_ktco2",
                    "sector_code"],
                ascending=[False,True]
            )
        )

        df_rank_long.loc[level.index,"rank"] = np.arange(1,len(level) + 1)

    df_rank_long["rank"] = (
        df_rank_long["rank"]
        .astype(int)
    )

    # --------------------------------------------------------
    # Score matrix
    #
    # Rows = sectors
    # Columns = intervention levels
    # Values = national CO2 reduction
    # --------------------------------------------------------
    df_score_matrix = (
        df_rank_long
        .pivot(
            index="sector_code",
            columns="change_pct",
            values="economy_wide_reduction_ktco2"
        )
    )

    df_score_matrix = (
        df_score_matrix[
            sorted(df_score_matrix.columns, reverse=True)
        ]
    )

    # --------------------------------------------------------
    # Rank matrix
    # --------------------------------------------------------
    df_rank_matrix = (
        df_rank_long
        .pivot(
            index="sector_code",
            columns="change_pct",
            values="rank"
        )
    )
    df_rank_matrix = df_rank_matrix[df_score_matrix.columns].copy()

    # --------------------------------------------------------
    # Add sector names
    # --------------------------------------------------------
    sector_name_map = {
        sector["sector_code"]: sector["sector_name"]
        for sector in sectors
    }

    df_score_matrix.insert(0,"sector_name", [
            sector_name_map.get(code,"Unknown")
            for code in df_score_matrix.index
        ]
    )

    df_rank_matrix.insert(0,"sector_name", [
            sector_name_map.get(code, "Unknown")
            for code in df_rank_matrix.index
        ]
    )

    # --------------------------------------------------------
    # Spearman correlation matrix
    #
    # Pandas calculates Spearman directly from the
    # sector-level score columns.
    # --------------------------------------------------------
    score_columns = [
        column for column
        in df_score_matrix.columns
        if column != "sector_name"
    ]

    df_rank_correlation = (
        df_score_matrix[score_columns]
        .corr(method="spearman")
    )

    # --------------------------------------------------------
    # Pairwise stability diagnostics
    # --------------------------------------------------------
    stability_records = []

    for i in range(len(change_levels_pct)):
        for j in range(i + 1,len(change_levels_pct)):
            level_a = change_levels_pct[i]
            level_b = change_levels_pct[j]

            column_a = level_a
            column_b = level_b
            rho = float(df_rank_correlation.loc[column_a,column_b])

            # ----------------------------------------------
            # Rank difference
            # ----------------------------------------------
            rank_a = df_rank_matrix[column_a]
            rank_b = df_rank_matrix[column_b]

            rank_difference = rank_b - rank_a
            absolute_rank_difference = rank_difference.abs()

            # ----------------------------------------------
            # Top-10 overlap
            # ----------------------------------------------
            top10_a = set(
                df_rank_long[
                    df_rank_long["change_pct"] == level_a
                ]
                .nsmallest(10,"rank")["sector_code"]
            )
            top10_b = set(
                df_rank_long[
                    df_rank_long["change_pct"] == level_b
                ]
                .nsmallest(10, "rank")["sector_code"]
            )
            top10_overlap_count = len(top10_a & top10_b)
            top10_overlap_pct = top10_overlap_count / 10.0 * 100.0

            # ----------------------------------------------
            # Top-5 overlap
            # ----------------------------------------------
            top5_a = set(
                df_rank_long[
                    df_rank_long["change_pct"] == level_a
                ]
                .nsmallest(5,"rank")["sector_code"]
            )

            top5_b = set(
                df_rank_long[
                    df_rank_long["change_pct"] == level_b
                ]
                .nsmallest(5,"rank")["sector_code"]
            )

            top5_overlap_count = len(top5_a & top5_b)
            top5_overlap_pct = top5_overlap_count / 5.0 * 100.0

            # ----------------------------------------------
            # Number of rank changes
            # ----------------------------------------------
            rank_changes_count = int((absolute_rank_difference > 0).sum())
            stability_records.append(
                {
                    "level_a_pct":
                        level_a,
                    "level_b_pct":
                        level_b,
                    "spearman_rho":
                        rho,
                    "top5_overlap_count":
                        top5_overlap_count,
                    "top5_overlap_pct":
                        top5_overlap_pct,
                    "top10_overlap_count":
                        top10_overlap_count,
                    "top10_overlap_pct":
                        top10_overlap_pct,
                    "mean_absolute_rank_change":
                        float(absolute_rank_difference.mean()),
                    "max_absolute_rank_change":
                        int(absolute_rank_difference.max()),
                    "rank_changes_count":
                        rank_changes_count
                }
            )

    df_rank_stability = pd.DataFrame(stability_records)

    return (
        df_rank_long,
        df_score_matrix,
        df_rank_matrix,
        df_rank_correlation,
        df_rank_stability
    )

# ============================================================
# Request #7: read-only transport Fuel allocation and unit audit
# ============================================================

def diagnose_transport_fuel_allocation_v21(
        model,
        db,
        df_sector,
        target_sector_code=ROAD_TRANSPORT_SECTOR_CODE_V21,
        top_n=12
    ):
    """
    Trace the zero-Fuel problem without changing the EEIO model.

    The existing get_mat_finEnerCons() derives:
        Coal        from A[36, :]
        Fuel        from A[37, :]
        Natural Gas from A[38, :]
        Electricity from A[144, :]

    A zero A[37, target] implies a zero modeled Fuel allocation
    to the target sector, regardless of the physical-unit factor.

    This diagnostic only SELECTs MySQL data and prints results.
    """
    target_sector_code = normalize_sector_code_v21(
        target_sector_code
    )
    sector_map = get_sector_column_map_v21(model)
    if target_sector_code not in sector_map:
        raise ValueError(
            f"Sector {target_sector_code} not found in model"
        )

    col = int(sector_map[target_sector_code])
    n = int(model.n_sectors)
    if model.A.shape != (n, n) or model.mat_finCons.shape != (4, n):
        raise ValueError("Unexpected model matrix dimensions")

    names = {}
    codes_by_col = {}
    for _, record in df_sector.iterrows():
        code = normalize_sector_code_v21(record["sector_code"])
        if code in sector_map:
            column = int(sector_map[code])
            names[column] = str(record["sector_name"])
            codes_by_col[column] = code

    def describe(column):
        return (
            f'{codes_by_col.get(column, "UNKNOWN")} | '
            f'{names.get(column, "UNKNOWN")}'
        )

    print("\n=== REQUEST #7: TRANSPORT FUEL ALLOCATION AUDIT ===")
    print("Year:", model.year)
    print("Matrix shapes: A", model.A.shape,
          ", mat_finCons", model.mat_finCons.shape)
    print("Target sector:", describe(col), "| column:", col)

    print("\n--- Source IO rows used for FEC allocation ---")
    supply_rows = {
        "Coal": 36,
        "Fuel": 37,
        "Natural Gas": 38,
        "Electricity": 144,
    }
    for energy, row in supply_rows.items():
        print(
            f"{energy}: A-row index={row}; source sector="
            f"{describe(row)}; A[row,target]={model.A[row, col]:.12g}; "
            f"A-row sum={float(np.sum(model.A[row,:])):.12g}"
        )

    print("\n--- Target sector: all four modeled energy carriers ---")
    energy_names = ["Coal", "Fuel", "Natural Gas", "Electricity"]
    for row, energy in enumerate(energy_names):
        print(
            f"{energy}: mat_finCons[{row},{col}] = "
            f"{float(model.mat_finCons[row, col]):.12g}"
        )

    fuel_raw = np.asarray(model.mat_finCons[1, :], dtype=float)
    fuel_alloc = np.asarray(model.A[37, :], dtype=float)
    print("\n--- Fuel allocation across the full 185-sector model ---")
    print("Fuel model units: kL (from conversion_factor)")
    print("Fuel baseline total (kL):", float(np.sum(fuel_raw)))
    print("Fuel baseline total (TJ, using 38.65 GJ/kL):",
          float(np.sum(fuel_raw)) * FUEL_MAT_FINCONS_TO_TJ_V21)
    print("Sectors with modeled Fuel > 0:",
          int(np.count_nonzero(fuel_raw > 0.0)))
    print("Sectors with A[37,:] > 0:",
          int(np.count_nonzero(fuel_alloc > 0.0)))

    if np.isclose(fuel_raw[col], 0.0, rtol=0.0, atol=1e-12):
        print("DIAGNOSTIC FLAG: Sector", target_sector_code,
              "has zero Fuel baseline.")
        if np.isclose(fuel_alloc[col], 0.0, rtol=0.0, atol=1e-12):
            print("EXPLANATION: the corresponding A[37,target] "
                  "allocation coefficient is also zero.")
        print("The transport electrification S0-S4 experiment "
              "must remain disabled.")

    print("\n--- Largest Fuel recipients ---")
    for column in np.argsort(-fuel_raw)[:top_n]:
        column = int(column)
        print(f"{describe(column)} | Fuel_kL={fuel_raw[column]:,.6f} "
              f"| A[37,col]={fuel_alloc[column]:.12g}")

    print("\n--- Transport-related sector records ---")
    keywords = ("transport", "road", "rail", "courier", "postal", "taxi")
    match_columns = [
        column for column in range(n)
        if any(word in names.get(column, "").lower() for word in keywords)
    ]
    for column in match_columns:
        print(f"{describe(column)} | Fuel_kL={fuel_raw[column]:,.6f} "
              f"| Electricity_MWh={model.mat_finCons[3,column]:,.6f} "
              f"| A[37,col]={fuel_alloc[column]:.12g}")
    if not match_columns:
        print("No transport-related names matched; inspect sector_lst manually.")

    # Confirm national FEC and conversion-factor records from the
    # source tables. All database statements below are SELECT only.
    year = int(model.year)
    q_fec = f"""
        SELECT f.year, e.energy_type_name, f.fec_val
        FROM fec_byyear AS f
        JOIN energy_lst AS e
          ON f.energy_type = e.energy_type_code
        WHERE f.year = {year}
        ORDER BY e.energy_type_name
    """
    df_fec = tbl_to_df(db, q_fec)
    print("\n--- MySQL national FEC source data ---")
    print(df_fec.to_string(index=False) if not df_fec.empty
          else "No matching FEC rows")

    df_factor = tbl_to_df(
        db,
        "SELECT energy_type, unit, multiplier_factor, note "
        "FROM conversion_factor ORDER BY energy_type"
    )
    print("\n--- MySQL physical-unit conversion factors ---")
    print(df_factor.to_string(index=False) if not df_factor.empty
          else "No matching conversion factors")

    df_co2 = tbl_to_df(
        db,
        "SELECT energy_type, heat_content_hhv, "
        "emission_factor, multiplier FROM co2_factor "
        "ORDER BY energy_type"
    )
    print("\n--- MySQL CO2 factors (source units require verification) ---")
    print(df_co2.to_string(index=False) if not df_co2.empty
          else "No matching CO2 factors")

    # Optional numerical unit check. This does not modify the
    # model or make corrections to the EEIO engine.
    print("\n--- Provisional Fuel CO2 factor unit reconciliation ---")
    ef = np.asarray(model.CO2_EF, dtype=float)
    if ef.shape[0] >= 2 and np.isfinite(ef[1,0]) and np.isfinite(ef[1,1]):
        heat_gj_per_kl = float(ef[1,0])
        ef_t_per_tj = float(ef[1,1])
        # 1 GJ * (t/TJ) = 1 kg, so this is kg CO2/kL.
        expected_kg_per_kl = heat_gj_per_kl * ef_t_per_tj
        # get_mat_finConsCO2() currently returns the unscaled
        # product for Fuel and subsequently divides by 1e6 for
        # t CO2 (as if the product were grams).
        engine_kg_per_kl = (
            heat_gj_per_kl * ef_t_per_tj / 1000.0
        )
        print("Expected kgCO2/kL if HHV=GJ/kL and EF=tCO2/TJ:",
              expected_kg_per_kl)
        print("Implemented implied kgCO2/kL under those assumptions:",
              engine_kg_per_kl)
        print("Ratio (expected / implemented):",
              expected_kg_per_kl / engine_kg_per_kl
              if engine_kg_per_kl != 0 else np.nan)
        print("This indicates a POSSIBLE 1000-fold emissions-unit "
              "issue; verify the official original factor units "
              "before modifying eeio_engine.py.")
    else:
        print("Fuel CO2 factors missing or not finite")

    print("\n--- Provisional Natural Gas CO2 unit reconciliation ---")
    if (ef.shape[0] >= 3 and np.all(np.isfinite(ef[2, :3]))):
        # Conditional source-unit interpretation:
        # multiplier = m3/MMBTU, HHV = MJ/m3, EF = gCO2/MJ.
        # Equivalent EF numeric units: tCO2/TJ.
        expected_g_per_mmbtu = float(
            ef[2, 2] * ef[2, 0] * ef[2, 1]
        )
        engine_g_per_mmbtu = expected_g_per_mmbtu / 1000.0
        print("Expected kgCO2/MMBTU if multiplier=m3/MMBTU, "
              "HHV=MJ/m3, EF=t/TJ:",
              expected_g_per_mmbtu / 1000.0)
        print("Implemented implied kgCO2/MMBTU "
              "under those assumptions:",
              engine_g_per_mmbtu / 1000.0)
        print("Possible natural-gas unit mismatch: 1000-fold; "
              "verify source units before changing the engine.")
    else:
        print("Natural Gas CO2 factors missing or not finite")
    print("=== END REQUEST #7 ALLOCATION AUDIT ===\n")

# ============================================================
# Transport Electrification Analysis
# ============================================================

def calculate_transport_electrification_shift_v21(
        model,
        target_sector_code=ROAD_TRANSPORT_SECTOR_CODE_V21,
        fuel_displacement_pct=20.0,
        electricity_to_fuel_ratio=
            EV_ELECTRICITY_TO_FUEL_ENERGY_RATIO_V21
    ):
    """
    Calculate the physical Fuel -> Electricity shift required
    for a stylized transport-electrification counterfactual.

    The intervention is defined as a percentage of the modeled
    Fuel demand in one road-transport sector. The displaced fuel
    energy is converted to additional electricity using the
    specified technology-based energy ratio.

    IMPORTANT
    ---------
    The current EEIO code explicitly treats electricity values in
    mat_finCons as MWh. The MySQL conversion_factor identifies
    Fuel in kL. Under the working interpretation of HHV=38.65
    GJ/kL, FUEL_MAT_FINCONS_TO_TJ_V21 is 0.03865 TJ/kL.
    Validate HHV source units and resolve the Fuel allocation to
    Sector 158 before using any EV case as a manuscript result.

    Parameters
    ----------
    fuel_displacement_pct : float
        Percentage of the selected sector's modeled Fuel demand
        displaced by electrification.
    electricity_to_fuel_ratio : float
        Electricity energy required per unit of displaced fuel energy.

    Returns
    -------
    dict
        Physical shift assumptions and the resulting electricity
        increase required by the scenario.
    """

    target_sector_code = normalize_sector_code_v21(target_sector_code)
    sector_column_map = get_sector_column_map_v21(model)

    if target_sector_code not in sector_column_map:
        raise ValueError(
            f"Unknown target sector: {target_sector_code}"
        )

    fuel_displacement_pct = float(fuel_displacement_pct)
    electricity_to_fuel_ratio = float(electricity_to_fuel_ratio)

    if fuel_displacement_pct < 0.0 or fuel_displacement_pct > 100.0:
        raise ValueError(
            "fuel_displacement_pct must be between 0 and 100."
        )

    if electricity_to_fuel_ratio <= 0.0:
        raise ValueError(
            "electricity_to_fuel_ratio must be greater than zero."
        )

    sector_column = int(
        sector_column_map[target_sector_code]
    )

    fuel_row = int(
        ENERGY_MAPPING[FUEL_ENERGY_CODE_V21]["finCons_row"]
    )
    electricity_row = int(
        ENERGY_MAPPING[ELECTRICITY_ENERGY_CODE_V21]["finCons_row"]
    )

    baseline_fuel_model = float(
        model.mat_finCons[fuel_row, sector_column]
    )
    baseline_electricity_mwh = float(
        model.mat_finCons[electricity_row, sector_column]
    )

    if baseline_fuel_model < 0.0:
        raise ValueError(
            f"Negative baseline Fuel value for sector "
            f"{target_sector_code}: {baseline_fuel_model}"
        )

    if baseline_electricity_mwh < 0.0:
        raise ValueError(
            f"Negative baseline Electricity value for sector "
            f"{target_sector_code}: {baseline_electricity_mwh}"
        )

    displacement_fraction = fuel_displacement_pct / 100.0

    displaced_fuel_model = (
        baseline_fuel_model * displacement_fraction
    )
    scenario_fuel_model = (
        baseline_fuel_model - displaced_fuel_model
    )

    # Convert the Fuel row from kL to TJ using HHV=38.65 GJ/kL.
    # The HHV units must be verified against source metadata.
    baseline_fuel_tj = (
        baseline_fuel_model * FUEL_MAT_FINCONS_TO_TJ_V21
    )
    displaced_fuel_tj = (
        displaced_fuel_model * FUEL_MAT_FINCONS_TO_TJ_V21
    )
    scenario_fuel_tj = (
        scenario_fuel_model * FUEL_MAT_FINCONS_TO_TJ_V21
    )

    # The technology ratio is defined on an energy basis.
    additional_electricity_tj = (
        displaced_fuel_tj * electricity_to_fuel_ratio
    )
    additional_electricity_mwh = (
        additional_electricity_tj / MWH_TO_TJ_V21
    )

    scenario_electricity_mwh = (
        baseline_electricity_mwh
        + additional_electricity_mwh
    )
    baseline_electricity_tj = (
        baseline_electricity_mwh * MWH_TO_TJ_V21
    )
    scenario_electricity_tj = (
        scenario_electricity_mwh * MWH_TO_TJ_V21
    )

    if baseline_electricity_mwh > 0.0:
        electricity_increase_pct = (
            additional_electricity_mwh
            / baseline_electricity_mwh
            * 100.0
        )
    elif additional_electricity_mwh > 0.0:
        electricity_increase_pct = np.inf
    else:
        electricity_increase_pct = 0.0

    net_energy_change_tj = (
        additional_electricity_tj
        - displaced_fuel_tj
    )

    return {
        "target_sector_code":
            target_sector_code,
        "fuel_displacement_pct":
            fuel_displacement_pct,
        "electricity_to_fuel_ratio":
            electricity_to_fuel_ratio,
        "fuel_mat_finCons_to_tj_factor":
            FUEL_MAT_FINCONS_TO_TJ_V21,
        "baseline_fuel_model":
            baseline_fuel_model,
        "displaced_fuel_model":
            displaced_fuel_model,
        "scenario_fuel_model":
            scenario_fuel_model,
        "baseline_fuel_tj":
            baseline_fuel_tj,
        "displaced_fuel_tj":
            displaced_fuel_tj,
        "scenario_fuel_tj":
            scenario_fuel_tj,
        "baseline_electricity_mwh":
            baseline_electricity_mwh,
        "additional_electricity_mwh":
            additional_electricity_mwh,
        "scenario_electricity_mwh":
            scenario_electricity_mwh,
        "baseline_electricity_tj":
            baseline_electricity_tj,
        "additional_electricity_tj":
            additional_electricity_tj,
        "scenario_electricity_tj":
            scenario_electricity_tj,
        "electricity_increase_pct":
            electricity_increase_pct,
        "net_energy_change_tj":
            net_energy_change_tj
    }


def build_transport_electrification_parameters_v21(
        model,
        target_sector_code=ROAD_TRANSPORT_SECTOR_CODE_V21,
        fuel_displacement_pct=20.0,
        grid_ef_change_pct=0.0,
        electricity_to_fuel_ratio=
            EV_ELECTRICITY_TO_FUEL_ENERGY_RATIO_V21
    ):
    """
    Build the flexible v2.1 parameters for one transport-
    electrification scenario.

    The scenario contains:
        1. Fuel demand reduction in the selected road-transport sector.
        2. Electricity demand increase in the same sector.
        3. Optional electricity emission-factor reduction economy-wide.

    The first two interventions deliberately use the same
    parameter_code (energy_demand_change) but different energy
    carriers, demonstrating parameter composability.
    """

    target_sector_code = normalize_sector_code_v21(target_sector_code)

    shift = calculate_transport_electrification_shift_v21(
        model=model,
        target_sector_code=target_sector_code,
        fuel_displacement_pct=fuel_displacement_pct,
        electricity_to_fuel_ratio=electricity_to_fuel_ratio
    )

    parameters = []

    # --------------------------------------------------------
    # Fuel displacement
    # --------------------------------------------------------
    if float(fuel_displacement_pct) != 0.0:
        parameters.append(
            ScenarioParameter(
                parameter_id=None,
                parameter_code="energy_demand_change",
                value=-float(fuel_displacement_pct) / 100.0,
                unit="%",
                energy_type_code=FUEL_ENERGY_CODE_V21,
                sector_code=target_sector_code,
                target_scope="SECTOR_ENERGY"
            )
        )

    # --------------------------------------------------------
    # Corresponding electricity demand increase
    # --------------------------------------------------------
    if not np.isclose(
            shift["additional_electricity_mwh"],
            0.0,
            rtol=1e-12,
            atol=1e-12
        ):
        parameters.append(
            ScenarioParameter(
                parameter_id=None,
                parameter_code="energy_demand_change",
                value=(
                    shift["electricity_increase_pct"] / 100.0
                ),
                unit="%",
                energy_type_code=ELECTRICITY_ENERGY_CODE_V21,
                sector_code=target_sector_code,
                target_scope="SECTOR_ENERGY"
            )
        )

    # --------------------------------------------------------
    # Optional grid emission-factor reduction
    # --------------------------------------------------------
    grid_ef_change_pct = float(grid_ef_change_pct)
    if grid_ef_change_pct <= -100.0:
        raise ValueError(
            "grid_ef_change_pct must be greater than -100%."
        )

    if not np.isclose(
            grid_ef_change_pct,
            0.0,
            rtol=1e-12,
            atol=1e-12
        ):
        parameters.append(
            ScenarioParameter(
                parameter_id=None,
                parameter_code="emission_factor_change",
                value=grid_ef_change_pct / 100.0,
                unit="%",
                energy_type_code=ELECTRICITY_ENERGY_CODE_V21,
                sector_code=None,
                target_scope="ENERGY"
            )
        )

    return parameters, shift


def run_transport_electrification_case_v21(
        model,
        target_sector_code=ROAD_TRANSPORT_SECTOR_CODE_V21,
        fuel_displacement_pct=20.0,
        grid_ef_change_pct=0.0,
        electricity_to_fuel_ratio=
            EV_ELECTRICITY_TO_FUEL_ENERGY_RATIO_V21,
        baseline_result=None
    ):
    """
    Run one stylized transport-electrification case through the
    existing v2.1 ScenarioProcessor and EEIO calculation engine.

    No database scenario definition or scenario-history record is created.
    """

    if baseline_result is None:
        baseline_result = model.calculate_baseline()

    (
        parameters,
        shift
    ) = build_transport_electrification_parameters_v21(
        model=model,
        target_sector_code=target_sector_code,
        fuel_displacement_pct=fuel_displacement_pct,
        grid_ef_change_pct=grid_ef_change_pct,
        electricity_to_fuel_ratio=electricity_to_fuel_ratio
    )

    scenario = build_batch_scenario_from_parameters_v21(
        parameters=parameters,
        scenario_name="Transport electrification"
    )

    processor = ScenarioProcessor(model)
    inputs = processor.apply(model, scenario)

    scenario_result = model.calculate_scenario_from_inputs(inputs)

    baseline_total = float(
        np.sum(baseline_result["totalEmission"])
    )
    scenario_total = float(
        np.sum(scenario_result["totalEmission"])
    )
    emission_reduction = (
        baseline_total - scenario_total
    )

    national_reduction_pct = (
        emission_reduction / baseline_total * 100.0
        if baseline_total != 0.0
        else np.nan
    )

    baseline_scope1 = float(
        np.sum(baseline_result["emissionScope1"])
    )
    scenario_scope1 = float(
        np.sum(scenario_result["emissionScope1"])
    )
    baseline_scope2 = float(
        np.sum(baseline_result["emissionScope2"])
    )
    scenario_scope2 = float(
        np.sum(scenario_result["emissionScope2"])
    )
    baseline_scope3 = float(
        np.sum(baseline_result["emissionScope3"])
    )
    scenario_scope3 = float(
        np.sum(scenario_result["emissionScope3"])
    )

    # --------------------------------------------------------
    # Matrix-level validation
    # --------------------------------------------------------
    baseline_finCons = model.mat_finCons
    scenario_finCons = inputs.mat_finCons
    delta_finCons = scenario_finCons - baseline_finCons

    changed_positions = np.argwhere(
        ~np.isclose(
            delta_finCons,
            0.0,
            rtol=1e-10,
            atol=1e-10
        )
    )
    changed_cells = int(len(changed_positions))

    target_sector_code = normalize_sector_code_v21(
        target_sector_code
    )
    sector_column = int(
        get_sector_column_map_v21(model)[target_sector_code]
    )
    fuel_row = int(
        ENERGY_MAPPING[FUEL_ENERGY_CODE_V21]["finCons_row"]
    )
    electricity_row = int(
        ENERGY_MAPPING[ELECTRICITY_ENERGY_CODE_V21]["finCons_row"]
    )

    expected_fuel = (
        baseline_finCons[fuel_row, sector_column]
        * (1.0 - float(fuel_displacement_pct) / 100.0)
    )
    expected_electricity = (
        baseline_finCons[electricity_row, sector_column]
        + shift["additional_electricity_mwh"]
    )

    fuel_target_check = np.isclose(
        scenario_finCons[fuel_row, sector_column],
        expected_fuel,
        rtol=1e-10,
        atol=1e-10
    )
    electricity_target_check = np.isclose(
        scenario_finCons[electricity_row, sector_column],
        expected_electricity,
        rtol=1e-10,
        atol=1e-10
    )

    expected_changed_cells = 0
    if not np.isclose(
            float(fuel_displacement_pct),
            0.0,
            rtol=1e-12,
            atol=1e-12
        ):
        if not np.isclose(
                baseline_finCons[fuel_row, sector_column],
                0.0,
                rtol=1e-12,
                atol=1e-12
            ):
            expected_changed_cells += 1

    if not np.isclose(
            shift["additional_electricity_mwh"],
            0.0,
            rtol=1e-12,
            atol=1e-12
        ):
        expected_changed_cells += 1

    matrix_validation_passed = (
        changed_cells == expected_changed_cells
        and bool(fuel_target_check)
        and bool(electricity_target_check)
    )

    return {
        "scenario_name":
            "Transport electrification",
        "target_sector_code":
            target_sector_code,
        "fuel_displacement_pct":
            float(fuel_displacement_pct),
        "electricity_to_fuel_ratio":
            float(electricity_to_fuel_ratio),
        "grid_ef_change_pct":
            float(grid_ef_change_pct),
        "baseline_fuel_tj":
            shift["baseline_fuel_tj"],
        "displaced_fuel_tj":
            shift["displaced_fuel_tj"],
        "scenario_fuel_tj":
            shift["scenario_fuel_tj"],
        "baseline_electricity_tj":
            shift["baseline_electricity_tj"],
        "additional_electricity_tj":
            shift["additional_electricity_tj"],
        "scenario_electricity_tj":
            shift["scenario_electricity_tj"],
        "electricity_increase_pct":
            shift["electricity_increase_pct"],
        "net_energy_change_tj":
            shift["net_energy_change_tj"],
        "baseline_total_emission_mtco2":
            baseline_total / 1_000_000.0,
        "scenario_total_emission_mtco2":
            scenario_total / 1_000_000.0,
        "emission_reduction_mtco2":
            emission_reduction / 1_000_000.0,
        "national_reduction_pct":
            national_reduction_pct,
        "scope1_reduction_ktco2":
            (baseline_scope1 - scenario_scope1) / 1_000.0,
        "scope2_reduction_ktco2":
            (baseline_scope2 - scenario_scope2) / 1_000.0,
        "scope3_reduction_ktco2":
            (baseline_scope3 - scenario_scope3) / 1_000.0,
        "changed_cells":
            changed_cells,
        "expected_changed_cells":
            expected_changed_cells,
        "fuel_target_check":
            bool(fuel_target_check),
        "electricity_target_check":
            bool(electricity_target_check),
        "matrix_validation_passed":
            matrix_validation_passed,
        "parameter_count":
            len(parameters)
    }


# ============================================================
# Standalone execution
# ============================================================
if __name__ == "__main__":

    from flask import Flask
    from dotenv import load_dotenv

    load_dotenv()

    YEAR = 2020
    CHANGE = -0.10

    str_change = str(int(CHANGE*100))

    app = Flask(__name__)
    db = db_init(app)

    with app.app_context():
        #model = build_batch_model_v21(db, 2016)
        model = build_batch_model_v21(db, 2020)

        q_str = "SELECT sector_agg, sector_code, sector_name FROM sector_lst WHERE sector_label='DET'"
        df_sector = tbl_to_df(db, q_str)

        # 0. Request #7 staged diagnostic
        # --------------------------------
        # Run this before enabling the full S0-S4 EV experiment.
        if RUN_TRANSPORT_ELECTRIFICATION_DIAGNOSTIC_V21:
            baseline_result = model.calculate_baseline()
            shift = calculate_transport_electrification_shift_v21(
                model=model,
                target_sector_code=ROAD_TRANSPORT_SECTOR_CODE_V21,
                fuel_displacement_pct=20.0,
                electricity_to_fuel_ratio=
                    EV_ELECTRICITY_TO_FUEL_ENERGY_RATIO_V21
            )

            print()
            print("=== TRANSPORT ELECTRIFICATION SHIFT ===")
            print("Model year:", model.year)
            print("Target sector:", ROAD_TRANSPORT_SECTOR_CODE_V21)
            print("Fuel displacement (%):", shift["fuel_displacement_pct"])
            print("Electricity / fuel energy ratio:", shift["electricity_to_fuel_ratio"])
            print("Fuel mat_finCons -> TJ factor:", shift["fuel_mat_finCons_to_tj_factor"])
            print()

            for key, value in shift.items():
                if key not in {
                    "target_sector_code",
                    "fuel_displacement_pct",
                    "electricity_to_fuel_ratio",
                    "fuel_mat_finCons_to_tj_factor"
                }:
                    print(f"{key}: {value}")

            print()
            print("Baseline national emission (MtCO2):",
                  np.sum(baseline_result["totalEmission"]) / 1_000_000.0)
            print()
            print("Fuel row now interpreted as kL and converted "
                  "to TJ using the supplied 38.65 GJ/kL factor.")
            print("The source units and Fuel allocation require audit "
                  "before S0-S4.")
            diagnose_transport_fuel_allocation_v21(
                model=model,
                db=db,
                df_sector=df_sector,
                target_sector_code=ROAD_TRANSPORT_SECTOR_CODE_V21,
                top_n=12
            )
            sys.exit(0)

        if (0):
            result = (
                run_single_sector_electricity_batch_v21(
                    model=model,
                    sector_code="149",
                    change=-0.20
                )
            )
            print(result)

        # 1. Sectoral intervention screening
        # ----------------------------------
        if (0):
            electricity_row = int(
                ENERGY_MAPPING[
                    ELECTRICITY_ENERGY_CODE_V21
                ]["finCons_row"]
            )

            print()
            print("=== ELECTRICITY ALLOCATION DIAGNOSTIC ===")
            electricity_values = (
                model.mat_finCons[
                    electricity_row,
                    :
                ]
            )
            print(
                "Electricity row:",electricity_row
            )
            print(
                "Total model electricity:",np.sum(electricity_values)
            )
            print(
                "Maximum electricity:",np.max(electricity_values)
            )
            max_column = int(np.argmax(electricity_values))
            print("Maximum column:",max_column)
            print("Sector 145 electricity:",electricity_values[144])
            print("Sector 149 electricity:",electricity_values[148])
            print(
                "Sector 145 share of model electricity:",
                (
                    electricity_values[144]
                    /
                    np.sum(electricity_values)
                )
            )
            print()

            electricity_A_row = (
                model.A[144,:]
            )
            print()
            print("=== ELECTRICITY A-ROW DIAGNOSTIC ===")
            print(
                "A row sum:",
                np.sum(electricity_A_row)
            )
            print(
                "A row minimum:",np.min(electricity_A_row)
            )
            print(
                "A row maximum:",np.max(electricity_A_row)
            )
            print(
                "A[144,144]:",electricity_A_row[144]
            )
            print(
                "Normalized share sector 145:",
                (
                    electricity_A_row[144]
                    /
                    np.sum(electricity_A_row)
                )
            )
            print()

        if (0):
            df_result = (
                run_batch_for_year_v21(
                    db=db,
                    year=YEAR,
                    change=CHANGE,
                    verbose=True
                )
            )

            output_csv = (
                f"output/batch/sector_electricity_efficiency_{YEAR}_{str_change}pct.csv"
            )
            save_batch_results_csv_v21(df_result,output_csv)

            print()
            print("Batch completed successfully.")
            print(f"Year: {YEAR}")
            print(f"Change: {CHANGE * 100:g}%")
            print(f"Output: {output_csv}")
            print()
            print("Top 10 sectors:")
            print(df_result.head(10).to_string(index=False))

            print(f"\nSectors (len(df_result)) : {len(df_result)}")
            print('df_result["changed_cells"] == 1).sum()', (df_result["changed_cells"] == 1).sum())

        # 2. Combined-policy interaction
        # ------------------------------
        if (0):
            # ============================================================
            # Controlled S1-S3 policy interaction test
            # ============================================================
            df_policy, interaction = (
                run_combined_policy_interaction_v21(
                    model=model,
                    target_sector_code="149",
                    demand_change=-0.20,
                    emission_factor_change=-0.20
                )
            )

            print()
            print("=== COMBINED POLICY INTERACTION ===")
            print(df_policy.to_string(index=False))
            print()
            print("=== INTERACTION ANALYSIS ===")

            for key, value in interaction.items():
                print(f"{key}: {value}")

        #3 # ============================================================
        # Electricity policy response surface
        # ============================================================
        if (0):
            (df_rs_long,df_rs_surface,df_rs_emission_surface) = (
                run_electricity_policy_response_surface_v21(
                    model=model,
                    target_sector_code="149",
                    efficiency_levels_pct=[
                        0,-10,-20,-30,-40
                    ],
                    grid_ef_levels_pct=[
                        0,-10,-20,-30,-40,-50
                    ],
                    verbose=True
                )
            )

            print()
            print("=== NATIONAL CO2 REDUCTION RESPONSE SURFACE (%) ===")
            print(df_rs_surface.to_string())
            (df_rs_interaction,df_rs_incremental,df_rs_retained,df_rs_marginal) = (
                analyze_electricity_response_surface_v21(df_rs_surface)
            )

            print()
            print("=== INTERACTION SURFACE (percentage points) ===")
            print(df_rs_interaction.to_string())

            print()
            print("=== RETAINED EFFICIENCY BENEFIT (%) ===")
            print(df_rs_retained.to_string())

            print()
            print("=== MARGINAL EFFICIENCY BENEFIT ===")
            print(df_rs_marginal.to_string(index=False))

            save_electricity_response_surface_heatmap_v21(
                df_rs_surface,
                "output/batch/electricity_policy_response_surface_2020.png",
                target_sector_code="149"
            )
        if (0):
            (df_rs_b_long, df_rs_b_surface, df_rs_b_emission_surface) = (
                run_electricity_policy_response_surface_all_sectors_v21(
                    model=model,
                    efficiency_levels_pct=[
                        0, -10, -20, -30, -40
                    ],
                    grid_ef_levels_pct=[
                        0, -10, -20, -30, -40, -50
                    ],
                    verbose=True
                )
            )
            if (0):
                print()
                print("=== SURFACE B: ALL-SECTOR CO2 REDUCTION (%) ===")
                print(df_rs_b_surface.to_string())

                save_electricity_response_surface_heatmap_v21(
                    df_rs_b_surface,
                    "output/batch/"
                    "electricity_policy_response_surface_all_sectors_2020.png",
                    target_sector_code=None,
                    efficiency_target_scope="ENERGY"
                )
                df_rs_b_long.to_csv(
                    "output/batch/"
                    "electricity_policy_response_surface_all_sectors_2020_long.csv",
                    index=False
                )
                df_rs_b_surface.to_csv(
                    "output/batch/"
                    "electricity_policy_response_surface_all_sectors_2020_pct.csv"
                )
            if (0):
                (df_rs_interaction,df_rs_incremental,df_rs_retained,df_rs_marginal) = (
                    analyze_electricity_response_surface_v21(
                        df_rs_b_surface
                    )
                )
                (df_rs_threshold,df_rs_minimum) = (
                    analyze_response_surface_thresholds_v21(
                        df_rs_b_surface,
                        target_levels_pct=[10,20,30,40,50]
                    )
                )
                print()
                print("=== 1. INTERACTION SURFACE (percentage points) ===")
                print(df_rs_interaction.to_string())

                print()
                print("=== 2. RETAINED EFFICIENCY BENEFIT (%) ===")
                print(df_rs_retained.to_string())

                print()
                print("=== 3. MARGINAL EFFICIENCY BENEFIT "
                    "(national reduction percentage points "
                    "per additional 10% efficiency) ==="
                )
                print(df_rs_marginal.to_string(index=False))

                print()
                print("=== 4A. MINIMUM EFFICIENCY REDUCTION "
                    "REQUIRED AT EACH GRID EF LEVEL (%) ==="
                )
                print(df_rs_threshold.to_string())

                print()
                print("=== 4B. MINIMUM COMBINED PERCENTAGE-POINT MAGNITUDE ===")
                print(df_rs_minimum.to_string(index=False))

        # 4. Contribution / hotspot analysis
        if (1):
            df_contribution, contribution_summary = (
                run_target_supply_chain_contribution_analysis_v21(
                    model=model,
                    df_sector=df_sector,
                    target_sector_code="149",
                    efficiency_change_pct=0,
                    grid_ef_change_pct=-20
                )
            )
            if (0):
                print()
                print("=== SUPPLY-CHAIN CONTRIBUTION SUMMARY ===")
                for key, value in (contribution_summary.items()):
                    print(f"{key}: {value}")

                print()
                print("=== TOP 10 SUPPLY-CHAIN CONTRIBUTING SECTORS ===")
                print(
                    df_contribution[
                        [
                            "rank",
                            "sector_code",
                            "sector_name",
                            "baseline_contribution_ktco2",
                            "scenario_contribution_ktco2",
                            "reduction_ktco2",
                            "reduction_share_pct",
                            "pareto_cumulative_pct"
                        ]
                    ]
                    .head(10).to_string(index=False)
                )
            if (0):
                df_contribution, contribution_summary = (
                    run_target_supply_chain_contribution_analysis_v21(
                        model=model,
                        df_sector=df_sector,
                        target_sector_code="149",
                        efficiency_change_pct=0,
                        grid_ef_change_pct=-20
                    )
                )
                # ============================================================
                # SUPPLY-CHAIN CONTRIBUTION REPORTING
                # ============================================================
                OUTPUT_BATCH_DIR = "output/batch"
                os.makedirs(OUTPUT_BATCH_DIR,exist_ok=True)

                TARGET_SECTOR_CODE = "149"
                TARGET_SECTOR_NAME = "Residential and non-residential buildings"

                # ------------------------------------------------------------
                # 1. Baseline Top 10
                # ------------------------------------------------------------
                df_baseline_top10 = (
                    prepare_baseline_contribution_top_v21(
                        df_contribution,
                        top_n=10
                    )
                )
                print()
                print("=== TOP 10 BASELINE CONTRIBUTIONS ===")
                print(df_baseline_top10.to_string(index=False))

                df_baseline_top10.to_csv(
                    os.path.join(
                        OUTPUT_BATCH_DIR,"contribution_baseline_top10_2020_sector149.csv"
                    ),
                    index=False
                )

                # ------------------------------------------------------------
                # 2. Pareto
                # ------------------------------------------------------------
                (df_pareto,pareto_summary) = (
                    prepare_supply_chain_pareto_v21(df_contribution)
                )
                print()
                print("=== PARETO SUMMARY ===")

                for key, value in (pareto_summary.items()):
                    print(f"{key}: {value}")

                df_pareto.to_csv(
                    os.path.join(
                        OUTPUT_BATCH_DIR,"contribution_pareto_2020_sector149.csv"
                    ),
                    index=False
                )

                fig_pareto = (
                    build_supply_chain_pareto_figure_v21(
                        df_pareto,
                        target_sector_code=TARGET_SECTOR_CODE,
                        target_sector_name=TARGET_SECTOR_NAME,
                        plot_n=40
                    )
                )

                fig_pareto.write_html(
                    os.path.join(OUTPUT_BATCH_DIR,
                        "contribution_pareto_2020_sector149.html"
                    ),
                    include_plotlyjs=True,
                    auto_open=False
                )
                fig_pareto.write_image(
                    os.path.join(OUTPUT_BATCH_DIR,
                        "contribution_pareto_2020_sector149.png"
                    ),
                    format="png",width=1000,height=650,scale=3
                )
                fig_pareto.write_image(
                    os.path.join(OUTPUT_BATCH_DIR,
                        "contribution_pareto_2020_sector149.svg"
                    ),
                    format="svg",width=1000,height=650
                )

                # ------------------------------------------------------------
                # 3. 14-group aggregation
                # ------------------------------------------------------------
                df_group = (aggregate_supply_chain_contribution_v21(
                        df_contribution,expected_group_count=14
                    )
                )

                print()
                print("=== CONTRIBUTION BY SECTOR GROUP ===")
                print(df_group.to_string(index=False))

                df_group.to_csv(
                    os.path.join(OUTPUT_BATCH_DIR,
                        "contribution_by_group_2020_sector149.csv"
                    ),
                    index=False
                )

                # ------------------------------------------------------------
                # 4. Sankey
                # ------------------------------------------------------------
                group_name_map = get_sector_group_name_map_v21(db)
                fig_sankey = (
                    build_supply_chain_contribution_sankey_v21(
                        df_group,
                        target_sector_code=TARGET_SECTOR_CODE,
                        target_sector_name=TARGET_SECTOR_NAME,
                        group_name_map=group_name_map
                    )
                )

                fig_sankey.write_html(
                    os.path.join(OUTPUT_BATCH_DIR,
                        "contribution_sankey_2020_sector149.html"
                    ),
                    include_plotlyjs=True,
                    auto_open=False
                )
                fig_sankey.write_image(
                    os.path.join(OUTPUT_BATCH_DIR,
                        "contribution_sankey_2020_sector149.png"
                    ),
                    format="png",width=1100,height=750,scale=3
                )
                fig_sankey.write_image(
                    os.path.join(OUTPUT_BATCH_DIR,
                        "contribution_sankey_2020_sector149.svg"
                    ),
                    format="svg",width=1100,height=750
                )

                print()
                print("=== REPORTING OUTPUTS COMPLETED ===")
                print("Baseline Top 10:")
                print(os.path.join(OUTPUT_BATCH_DIR,
                        "contribution_baseline_top10_2020_sector149.csv"
                    )
                )
                print("Pareto:")
                print(os.path.join(OUTPUT_BATCH_DIR,
                        "contribution_pareto_2020_sector149.html"
                    )
                )
                print("Sector groups:")
                print(os.path.join(OUTPUT_BATCH_DIR,
                        "contribution_by_group_2020_sector149.csv"
                    )
                )
                print("Sankey:")
                print(os.path.join(OUTPUT_BATCH_DIR,
                        "contribution_sankey_2020_sector149.html"
                    )
                )
        #5
        if (0):
            (df_year_robustness, year_comparison) = (
                run_electricity_demand_year_robustness_v21(
                    db=db,
                    years=[2016, 2020],
                    demand_change_pct=-10,
                    verbose=True
                )
            )
            sys.exit()

            print()
            print("=== YEAR ROBUSTNESS RESULTS ===")
            print(df_year_robustness.to_string(index=False))
            print()
            print("=== 2016 vs 2020 COMPARISON ===")

            for key, value in (year_comparison.items()):
                print(f"{key}: {value}")

            os.makedirs("output/batch", exist_ok=True)
            df_year_robustness.to_csv(
                "output/batch/electricity_demand_robustness_2016_2020_-10pct.csv",
                index=False
            )
            pd.DataFrame([year_comparison]).to_csv(
                "output/batch/electricity_demand_robustness_comparison_2016_2020_-10pct.csv",
                index=False
            )
        #6
        df_sensitivity_149 = (
            run_single_sector_electricity_sensitivity_v21(
                model=model,
                sector_code="149",
                change_levels_pct=[
                    -5,-10,-15,-20,-30,-40
                ]
            )
        )
        #print("df_sensitivity_149")
        #print(df_sensitivity_149.columns.to_list())

        print()
        print("=== DETERMINISTIC ELECTRICITY ""SENSITIVITY: SECTOR 149 ===")
        print(
            df_sensitivity_149[
                [
                    "change_pct",
                    "baseline_electricity_tj",
                    "scenario_electricity_tj",
                    "target_electricity_reduction_tj",
                    "economy_wide_reduction_mtco2",
                    "economy_wide_reduction_pct",
                    "ser",
                    "response_elasticity",
                    "emission_leverage_ktco2_per_tj",
                    "changed_cells"
                ]
            ]
            .to_string(index=False)
        )

        (df_rank_long, df_rank_score_matrix, df_rank_matrix,
         df_rank_correlation, df_rank_stability) = (
            run_sector_electricity_ranking_sensitivity_v21(
                model=model,
                df_sector=df_sector,
                change_levels_pct=[-10,-20,-30],
                verbose=True
            )
        )
        print()
        print("=== SPEARMAN RANK CORRELATION ===")
        print(df_rank_correlation.to_string())
        print()
        print("=== RANK STABILITY ===")
        print(df_rank_stability.to_string(index=False))

        for change_pct in [-10,-20,-30]:
            print()
            print(f"=== TOP 10 SECTORS: "
                f"{change_pct}% ELECTRICITY REDUCTION ===")
            print(
                df_rank_long[df_rank_long["change_pct"]==change_pct]
                .sort_values("rank")[
                    [
                        "rank",
                        "sector_code",
                        "sector_name",
                        "economy_wide_reduction_mtco2",
                        "economy_wide_reduction_pct"
                    ]
                ]
                .head(10)
                .to_string(index=False)
            )