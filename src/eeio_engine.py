# eeio_engine.py
# Internal development comment removed for public distribution.

import numpy as np
from dataclasses import dataclass
from threading import Lock
import logging
import MySQLdb

from util_db import tbl_to_df
from scenario_engine import (
    ScenarioLoader, ScenarioValidator, ScenarioProcessor
)
from config_scenario import (
    SCENARIO_VERSION_V20, SCENARIO_VERSION_V21,
    ENERGY_MAPPING,
    V21_VALIDATION_DIAGNOSTICS, ENERGY_ROW_INDEX_V21_TEST
)
from scenario_report import (
    analyze_target_impacts,
    analyze_target_emission_impacts
)

logging.basicConfig(
    level=logging.INFO
)
logger = logging.getLogger(__name__)

EEIO_MODEL_CACHE = {}
EEIO_MODEL_CACHE_LOCK = Lock()


# ============================================================
# Existing low-level numerical functions
# ============================================================

def calc_matrix_A(X_io, X_total):
    mat_A = np.divide(X_io, X_total)
    return mat_A


def get_mat_finEnerCons(mat_A, X_FEC_yr):
    """
    Allocate national Final Energy Consumption (FEC)
    to sectors using the corresponding energy-sector
    rows in matrix A.

    Current row convention:
        36  = Coal
        37  = Fuel
        38  = Natural Gas
        144 = Electricity
    """

    rows = np.array([36, 37, 38, 144])

    X1 = mat_A[rows, :]

    total_1_axis = np.sum(X1, axis=1)

    divider = total_1_axis.reshape(-1, 1)
    X1_div = np.divide(X1, divider)

    mult = X_FEC_yr.reshape(-1, 1)
    X1_mult = np.multiply(X1_div, mult)

    return X1_mult


def get_mat_finCons(finEnerCons, convFactor):
    div1 = convFactor.reshape(-1, 1)

    X1_div = np.divide(finEnerCons, div1)
    X1_div = np.multiply(X1_div, 1000)

    return X1_div


def get_mat_finConsCO2(finCons, CO2_EF):
    """
    Calculate direct CO2 emissions from final energy consumption.

    Rows are assumed to be ordered as:
        0 = Coal
        1 = Fuel
        2 = Natural Gas
        3 = Electricity

    CO2_EF columns currently follow:
        heat_content_hhv
        emission_factor
        multiplier
    """

    # Coal
    row1 = np.multiply(finCons[0, :], 1000)
    row1 = np.multiply(row1, CO2_EF[0, 0])
    row1 = np.multiply(row1, CO2_EF[0, 1])

    # Fuel
    row2 = np.multiply(finCons[1, :], CO2_EF[1, 0])
    row2 = np.multiply(row2, CO2_EF[1, 1])

    # Natural gas
    row3 = np.multiply(finCons[2, :], CO2_EF[2, 2])
    row3 = np.multiply(row3, CO2_EF[2, 0])
    row3 = np.divide(row3, 1000)
    row3 = np.multiply(row3, CO2_EF[2, 1])

    # Electricity
    row4 = np.multiply(finCons[3, :], CO2_EF[3, 0])
    row4 = np.multiply(row4, 1000000)

    mat_co2 = np.vstack(
        (
            row1,
            row2,
            row3,
            row4
        )
    )

    return mat_co2

# ============================================================
# EEIO model
# ============================================================
@dataclass
class EEIOModel:
    """
    Reusable EEIO model for one baseline IO system/year.

    The object contains matrices that do not need to be
    reconstructed for every scenario calculation.
    """

    # Core IO matrices
    A: np.ndarray
    L: np.ndarray

    # Baseline emission intensity matrices
    B: np.ndarray
    B_El: np.ndarray


    # Output vectors
    X_total: np.ndarray
    X_domOut: np.ndarray

    # Scenario-related baseline data
    # ---------------------------------
    # Rows:
    #   0 = Coal
    #   1 = Fuel
    #   2 = Natural Gas
    #   3 = Electricity
    #
    # Columns:
    #   sectors
    mat_finCons: np.ndarray

    # CO2 emission factors
    # rows correspond to energy types
    CO2_EF: np.ndarray

    year: int = None

    @property
    def n_sectors(self):
        return self.A.shape[0]

    def calculate_baseline(self):
        """
        Calculate baseline emission intensities and emissions
        using the matrices stored in this EEIOModel.

        Returns
        -------
        dict
            Same structure currently returned by
            calc_eeio_baseline().
        """

        # -----------------------------------------------------
        # Identity matrices
        # -----------------------------------------------------
        mat_I = np.identity(
            self.n_sectors
        )

        # Keep F explicitly for compatibility with
        # the existing calculation.
        mat_F = np.identity(
            self.n_sectors
        )

        # -----------------------------------------------------
        # Scope 1
        # -----------------------------------------------------
        mat_BF = np.matmul(
            self.B,
            mat_F
        )

        # -----------------------------------------------------
        # Total
        # -----------------------------------------------------
        mat_InvF = np.matmul(
            self.L,
            mat_F
        )

        mat_BInvF = np.matmul(
            self.B,
            mat_InvF
        )

        # -----------------------------------------------------
        # Scope 2
        # -----------------------------------------------------
        mat_AF = np.matmul(
            self.A,
            mat_F
        )

        mat_BAF = np.matmul(
            self.B_El,
            mat_AF
        )

        # -----------------------------------------------------
        # Emission intensity
        # -----------------------------------------------------
        totalEmissionIntensity = mat_BInvF

        emissionIntensityScope1 = mat_BF

        emissionIntensityScope2 = mat_BAF

        emissionIntensityScope3 = np.subtract(
            mat_BInvF,
            np.add(
                mat_BF,
                mat_BAF
            )
        )

        # -----------------------------------------------------
        # Emissions
        # -----------------------------------------------------
        totalEmission = (
            totalEmissionIntensity
            * self.X_domOut
        )

        scope1_emission_inT = (
            emissionIntensityScope1
            * self.X_domOut
        )

        scope2_emission_inT = (
            emissionIntensityScope2
            * self.X_domOut
        )

        scope3_emission_inT = (
            totalEmission
            - (
                scope1_emission_inT
                + scope2_emission_inT
            )
        )

        # -----------------------------------------------------
        # Return same result structure as Step 2
        # -----------------------------------------------------
        return {
            # reusable matrices
            "A": self.A,
            "B": self.B,
            "B_El": self.B_El,
            "I": mat_I,
            "F": mat_F,
            "IminusA": np.subtract(
                mat_I,
                self.A
            ),
            "L": self.L,

            # intermediate matrices
            "BF": mat_BF,
            "InvF": mat_InvF,
            "BInvF": mat_BInvF,
            "AF": mat_AF,
            "BAF": mat_BAF,

            # emission intensity
            "emissionIntensityScope1":
                emissionIntensityScope1,

            "emissionIntensityScope2":
                emissionIntensityScope2,

            "emissionIntensityScope3":
                emissionIntensityScope3,

            "totalEmissionIntensity":
                totalEmissionIntensity,

            # emissions
            "emissionScope1":
                scope1_emission_inT,

            "emissionScope2":
                scope2_emission_inT,

            "emissionScope3":
                scope3_emission_inT,

            "totalEmission":
                totalEmission
        }

    def calculate_scenario(
            self,
            fuel_demand_change=0.0,
            electricity_demand_change=0.0,
            electricity_emission_factor_change=0.0
        ):
        """
        Calculate EEIO scenario emissions.

        Scenario version 1:
        - modifies sectoral final energy consumption
        - modifies electricity emission factor
        - keeps A and L unchanged

        Parameters
        ----------
        fuel_demand_change :
            Relative change in fuel consumption.
            Example:
                -0.20 = 20% reduction

        electricity_demand_change :
            Relative change in electricity consumption.
            Example:
                +0.15 = 15% increase

        electricity_emission_factor_change :
            Relative change in electricity CO2 intensity.
            Example:
                -0.30 = 30% reduction
        """

        # --------------------------------------------------
        # Validation
        # --------------------------------------------------

        for name, value in {
            "fuel_demand_change":
                fuel_demand_change,
            "electricity_demand_change":
                electricity_demand_change,
            "electricity_emission_factor_change":
                electricity_emission_factor_change
        }.items():
            if value <= -1.0:
                raise ValueError(
                    f"{name} cannot be <= -100%"
                )

        scenario_finCons = (
            self.mat_finCons.copy()
        )

        '''
        current ordering is:
        row 0 = Coal
        row 1 = Fuel
        row 2 = Natural Gas
        row 3 = Electricity
        '''
        # Fuel
        scenario_finCons[1, :] *= (
            1.0 + fuel_demand_change
        )

        # Electricity
        scenario_finCons[3, :] *= (
            1.0 + electricity_demand_change
        )

        scenario_CO2_EF = (
            self.CO2_EF.copy()
        )

        '''
        factor table:
        column 0 = heat_content_hhv
        column 1 = emission_factor
        column 2 = multiplier
        '''
        scenario_CO2_EF[3, 0] *= (
            1.0
            +
            electricity_emission_factor_change
        )

        scenario_finConsCO2 = get_mat_finConsCO2(
            scenario_finCons,
            scenario_CO2_EF
        )

        total_finConsCO2_inG = np.sum(
            scenario_finConsCO2,
            axis=0
        )

        total_finConsCO2_inT = np.divide(
            total_finConsCO2_inG,
            1000000
        )

        scenario_B = np.divide(
            total_finConsCO2_inT,
            self.X_total
        )

        #Electricity:
        total_finConsCO2_El_inT = np.divide(
            scenario_finConsCO2[3, :],
            1000000
        )

        scenario_B_El = np.divide(
            total_finConsCO2_El_inT,
            self.X_total
        )

        '''
        Repeat the baseline calculation, but replace:
        self.B
        self.B_El
        with:
        scenario_B
        scenario_B_El
        '''
        cnt_sector = self.n_sectors
        mat_F = np.identity(
            cnt_sector
        )

        #Scope 1
        scenario_BF = np.matmul(
            scenario_B,
            mat_F
        )

        #Total
        scenario_InvF = np.matmul(
            self.L,
            mat_F
        )
        scenario_BInvF = np.matmul(
            scenario_B,
            scenario_InvF
        )

        #Scope 2
        scenario_AF = np.matmul(
            self.A,
            mat_F
        )
        scenario_BAF = np.matmul(
            scenario_B_El,
            scenario_AF
        )

        #Calculate scenario emissions
        #Same as baseline
        scenario_totalEmissionIntensity = (
            scenario_BInvF
        )
        scenario_emissionIntensityScope1 = (
            scenario_BF
        )
        scenario_emissionIntensityScope2 = (
            scenario_BAF
        )
        scenario_emissionIntensityScope3 = np.subtract(
            scenario_BInvF,
            np.add(
                scenario_BF,
                scenario_BAF
            )
        )

        scenario_totalEmission = (
            scenario_totalEmissionIntensity
            *
            self.X_domOut
        )
        scenario_scope1_emission = (
            scenario_emissionIntensityScope1
            *
            self.X_domOut
        )
        scenario_scope2_emission = (
            scenario_emissionIntensityScope2
            *
            self.X_domOut
        )
        scenario_scope3_emission = (
            scenario_totalEmission
            -
            (
                scenario_scope1_emission
                +
                scenario_scope2_emission
            )
        )

        return {
            "scenario_parameters": {
                "fuel_demand_change":
                    fuel_demand_change,
                "electricity_demand_change":
                    electricity_demand_change,
                "electricity_emission_factor_change":
                    electricity_emission_factor_change
            },
            "mat_finCons":
                scenario_finCons,
            "mat_finConsCO2":
                scenario_finConsCO2,
            "emissionScope1":
                scenario_scope1_emission,
            "emissionScope2":
                scenario_scope2_emission,
            "emissionScope3":
                scenario_scope3_emission,
            "totalEmission":
                scenario_totalEmission,
            "totalEmissionIntensity":
                scenario_totalEmissionIntensity,
            "B": scenario_B
        }

    def calculate_scenario_from_inputs(
            self,
            scenario_inputs
        ):

        scenario_finCons = (
            scenario_inputs.mat_finCons
        )
        scenario_CO2_EF = (
            scenario_inputs.CO2_EF
        )
        # calculate direct CO2
        scenario_finConsCO2 = get_mat_finConsCO2(
            scenario_finCons,
            scenario_CO2_EF
        )
        # calculate direct emission intensity
        total_finConsCO2_inG = np.sum(
            scenario_finConsCO2,
            axis=0
        )
        total_finConsCO2_inT = np.divide(
            total_finConsCO2_inG,
            1000000
        )
        scenario_B = np.divide(
            total_finConsCO2_inT,
            self.X_total
        )

        total_finConsCO2_El_inT = np.divide(
            scenario_finConsCO2[3, :],
            1000000
        )

        scenario_B_El = np.divide(
            total_finConsCO2_El_inT,
            self.X_total
        )

        cnt_sector = self.n_sectors
        mat_F = np.identity(
            cnt_sector
        )
        #Scope 1
        scenario_BF = np.matmul(
            scenario_B,
            mat_F
        )
        #Total
        scenario_InvF = np.matmul(
            self.L,
            mat_F
        )
        scenario_BInvF = np.matmul(
            scenario_B,
            scenario_InvF
        )
        #Scope 2
        scenario_AF = np.matmul(
            self.A,
            mat_F
        )
        scenario_BAF = np.matmul(
            scenario_B_El,
            scenario_AF
        )

        #Calculate scenario emissions
        scenario_totalEmissionIntensity = (
            scenario_BInvF
        )
        scenario_emissionIntensityScope1 = (
            scenario_BF
        )
        scenario_emissionIntensityScope2 = (
            scenario_BAF
        )
        scenario_emissionIntensityScope3 = np.subtract(
            scenario_BInvF,
            np.add(
                scenario_BF,
                scenario_BAF
            )
        )
        scenario_totalEmission = (
            scenario_totalEmissionIntensity
            *
            self.X_domOut
        )
        scenario_scope1_emission = (
            scenario_emissionIntensityScope1
            *
            self.X_domOut
        )
        scenario_scope2_emission = (
            scenario_emissionIntensityScope2
            *
            self.X_domOut
        )
        scenario_scope3_emission = (
            scenario_totalEmission
            -
            (scenario_scope1_emission + scenario_scope2_emission)
        )
        return {
            "scenario_parameters":
                scenario_inputs.parameters,
            "mat_finCons":
                scenario_finCons,
            "mat_finConsCO2":
                scenario_finConsCO2,
            "emissionScope1":
                scenario_scope1_emission,
            "emissionScope2":
                scenario_scope2_emission,
            "emissionScope3":
                scenario_scope3_emission,
            "totalEmission":
                scenario_totalEmission,
            "totalEmissionIntensity":
                scenario_totalEmissionIntensity,
            "B":
                scenario_B
        }


@dataclass
class ScenarioReport:
    metadata: dict
    summary: dict
    comparison: dict
    sector_results: object = None
    aggregate_results: object = None
    impact_statistics: dict = None

    # Direct physical energy change at the scenario target
    target_impacts: list = None

    # Emission reduction split between target sector
    # and the rest of the economy
    target_emission_impacts: list = None

# ============================================================
# Model builder
# ============================================================
def build_eeio_model(
    mat_A,
    mat_finConsCO2,
    X_total,
    X_domOut,
    mat_finCons=None,
    CO2_EF=None,
    year=None
    ):
    """
    Build a reusable EEIOModel from baseline matrices/data.

    Parameters
    ----------
    mat_A : np.ndarray
        Technical coefficient matrix A.

    mat_finConsCO2 : np.ndarray
        CO2 emissions by energy type and sector.

    X_total : np.ndarray
        Total output vector.

    X_domOut : np.ndarray
        Domestic output vector.

    year : int, optional
        IO/base year represented by this model.

    Returns
    -------
    EEIOModel
    """

    cnt_sector = mat_A.shape[0]

    # ---------------------------------------------------------
    # Basic dimensional checks
    # ---------------------------------------------------------
    if mat_A.shape != (
        cnt_sector,
        cnt_sector
    ):
        raise ValueError(
            "mat_A must be a square matrix."
        )

    if len(X_total) != cnt_sector:
        raise ValueError(
            "X_total length does not match mat_A."
        )

    if len(X_domOut) != cnt_sector:
        raise ValueError(
            "X_domOut length does not match mat_A."
        )

    if mat_finConsCO2.shape[1] != cnt_sector:
        raise ValueError(
            "mat_finConsCO2 sector dimension "
            "does not match mat_A."
        )

    # ---------------------------------------------------------
    # Direct CO2 emission per sector
    # ---------------------------------------------------------
    total_finConsCO2_inG = np.sum(
        mat_finConsCO2,
        axis=0
    )

    total_finConsCO2_inT = np.divide(
        total_finConsCO2_inG,
        1000000
    )

    total_finConsCO2_inT_perMillion = (
        np.divide(
            total_finConsCO2_inT,
            X_total
        )
    )

    # ---------------------------------------------------------
    # Electricity component
    #
    # Preserve current convention:
    # row 3 = Electricity
    # ---------------------------------------------------------
    total_finConsCO2_El_inT = np.divide(
        mat_finConsCO2[3, :],
        1000000
    )

    total_finConsCO2_El_inT_perMillion = (
        np.divide(
            total_finConsCO2_El_inT,
            X_total
        )
    )

    # ---------------------------------------------------------
    # B matrices
    # ---------------------------------------------------------
    mat_B = (
        total_finConsCO2_inT_perMillion
    )

    mat_B_El = (
        total_finConsCO2_El_inT_perMillion
    )

    # ---------------------------------------------------------
    # Leontief inverse
    # L = (I - A)^-1
    # ---------------------------------------------------------
    mat_I = np.identity(
        cnt_sector
    )

    mat_IminusA = np.subtract(
        mat_I,
        mat_A
    )

    mat_L = np.linalg.inv(
        mat_IminusA
    )

    # ---------------------------------------------------------
    # Construct reusable model
    # ---------------------------------------------------------
    return EEIOModel(
        A=mat_A,
        L=mat_L,
        B=mat_B,
        B_El=mat_B_El,
        X_total=X_total,
        X_domOut=X_domOut,
        mat_finCons=mat_finCons,
        CO2_EF=CO2_EF,
        year=year
    )


def get_eeio_model(
        cache_key,
        model_builder
    ):
    """
    Get an EEIOModel from the in-memory cache.

    If the model does not yet exist, model_builder()
    is called once to create it and the resulting model
    is stored in the cache.
    """

    # First check without building
    if cache_key in EEIO_MODEL_CACHE:

        #print(f"Using cached EEIO model: {cache_key}")
        logger.info("Using cached EEIO model: %s", cache_key)

        return EEIO_MODEL_CACHE[cache_key]


    # Only one request should be allowed to build
    # a missing model at a time.
    with EEIO_MODEL_CACHE_LOCK:

        # Check again after acquiring the lock.
        # Another request may have built it already.
        if cache_key in EEIO_MODEL_CACHE:

            print(
                f"Using cached EEIO model: {cache_key}"
            )
            return EEIO_MODEL_CACHE[cache_key]

        #print(f"Building EEIO model: {cache_key}")
        logger.info(
            "Building EEIO model: %s", cache_key
        )

        model = model_builder()
        EEIO_MODEL_CACHE[cache_key] = model
        print(
            f"EEIO model cached: {cache_key}"
        )

        return model


def clear_eeio_model_cache():
    """
    Clear all cached EEIO models.
    """

    with EEIO_MODEL_CACHE_LOCK:

        EEIO_MODEL_CACHE.clear()

    print("EEIO model cache cleared.")


def get_cached_eeio_model_keys():
    """
    Return the currently cached model keys.
    """

    with EEIO_MODEL_CACHE_LOCK:

        return list(
            EEIO_MODEL_CACHE.keys()
        )


# ============================================================
# Backward-compatible Module 1 function
# ============================================================
def calc_eeio_baseline(
    mat_A,
    mat_finConsCO2,
    X_total,
    X_domOut,
    mat_finCons=None,
    CO2_EF=None,
    year=None
    ):
    """
    Backward-compatible baseline calculation.

    Builds an EEIOModel and returns the same dictionary
    structure used by the existing Module 1 workflow.
    """

    model = build_eeio_model(
        mat_A=mat_A,
        mat_finConsCO2=mat_finConsCO2,
        X_total=X_total,
        X_domOut=X_domOut,
        mat_finCons=mat_finCons,
        CO2_EF=CO2_EF,
        year=year
    )

    return model.calculate_baseline()


def calc_percentage_change(
        baseline,
        scenario
    ):
    """
    Calculate percentage change.

    Formula:
        (scenario - baseline) / baseline * 100

    Handles zero baseline values.
    """

    baseline = np.asarray(
        baseline,
        dtype=float
    )

    scenario = np.asarray(
        scenario,
        dtype=float
    )

    result = np.zeros_like(baseline)
    mask = baseline != 0

    result[mask] = (
        (
            scenario[mask] - baseline[mask]
        )
        /
        baseline[mask]
        *
        100
    )

    return result

def compare_baseline_scenario(
        baseline_result,
        scenario_result
    ):
    """
    Compare baseline EEIO result with scenario result.

    Returns absolute and percentage changes.

    Parameters
    ----------
    baseline_result :
        Output from model.calculate_baseline()

    scenario_result :
        Output from model.calculate_scenario()
    """

    comparison = {}

    emission_keys = [
        "emissionScope1",
        "emissionScope2",
        "emissionScope3",
        "totalEmission"
    ]

    for key in emission_keys:
        baseline = (
            baseline_result[key]
        )
        scenario = (
            scenario_result[key]
        )
        difference = (
            scenario - baseline
        )
        pct_change = calc_percentage_change(
            baseline,
            scenario
        )
        comparison[key] = {
            "baseline":
                baseline,
            "scenario":
                scenario,
            "difference":
                difference,
            "percentage_change":
                pct_change
        }

    return comparison

def summarize_comparison(
        comparison
    ):
    """
    Produce scalar summary from comparison results.
    """

    summary = {}
    for key, value in comparison.items():
        summary[key] = {
            "baseline_total":
                np.sum(
                    value["baseline"]
                ),
            "scenario_total":
                np.sum(
                    value["scenario"]
                ),
            "difference_total":
                np.sum(
                    value["difference"]
                ),
            "percentage_change":
                None if np.sum(value["baseline"]) == 0 else
                (
                    np.sum(
                        value["difference"]
                    )
                    /
                    np.sum(
                        value["baseline"]
                    )
                    *
                    100
                )
        }

    return summary

def analyze_sector_impacts(
        comparison,
        df_sector,
        metric="totalEmission"
    ):
    """
    Analyze sector-level emission impacts.

    Parameters
    ----------
    comparison :
        Output from compare_baseline_scenario()

    df_sector :
        Sector metadata dataframe containing:
        sector_code
        sector_name
        sector_agg

    metric :
        Emission metric to analyze.

    Returns
    -------
    DataFrame
        Sector impact ranking
    """

    import pandas as pd


    result = pd.DataFrame({

        "sector_code":
            df_sector["sector_code"].values,

        "sector_name":
            df_sector["sector_name"].values,

        "sector_agg":
            df_sector["sector_agg"].values,


        "baseline":
            comparison[metric]["baseline"],

        "scenario":
            comparison[metric]["scenario"],

        "difference":
            comparison[metric]["difference"],

        "percentage_change":
            comparison[metric]["percentage_change"]
    })

    # Sort by absolute reduction
    result["reduction"] = (
        -result["difference"]
    )

    return result

def rank_sector_reductions(
        sector_result,
        n=10
    ):
    """
    Return sectors with largest emission reductions.
    """

    return (
        sector_result
        .sort_values(
            by="difference",
            ascending=True
        )
        .head(n)
    )

def rank_sector_increases(
        sector_result,
        n=10
    ):
    """
    Return sectors with increasing emissions.
    """

    result = sector_result[
        sector_result["difference"] > 0
    ]

    return (
        result
        .sort_values(
            by="difference",
            ascending=False
        )
        .head(n)
    )

def calculate_reduction_contribution(
        sector_result
    ):
    total_reduction = (
        -sector_result["difference"]
        .sum()
    )

    sector_result["contribution_pct"] = (
        -sector_result["difference"]
        /
        total_reduction
        *
        100
    )

    return sector_result

def aggregate_sector_impacts(
        sector_result
    ):

    return (
        sector_result
        .groupby(
            "sector_agg"
        )
        [
            [
                "baseline",
                "scenario",
                "difference"
            ]
        ]
        .sum()
        .sort_values(
            by="difference"
        )
    )

def count_sector_changes(sector_result):
    return {
        "reduced":
            int(
                (sector_result["difference"] < 0)
                .sum()
            ),

        "increased":
            int(
                (sector_result["difference"] > 0)
                .sum()
            ),

        "unchanged":
            int(
                (sector_result["difference"] == 0)
                .sum()
            )
    }


def build_scenario_report(
        year,
        scenario_name,
        scenario_parameters,
        baseline_result,
        scenario_result,
        comparison,
        summary,
        number_of_sectors,
        scenario_id=None,
        scenario_version=SCENARIO_VERSION_V20,
        scenario_source="manual",
        scenario_types=None,
        scenario_code=None,
        sector_results=None,
        aggregate_results=None,
        impact_statistics=None,
        energy_lookup=None,
        sector_lookup=None,
        target_impacts=None,
        target_emission_impacts=None,
        scenario_signature=None
    ):

    target_summary = None
    if scenario_parameters is not None:
        target_summary = (
            build_target_summary(
                scenario_parameters,
                energy_lookup,
                sector_lookup
            )
        )

    #scenario_signature = get_scenario_signature(db, scenario_id)

    return ScenarioReport(
        metadata={
            "year":
                year,
            "model_type":
                "EEIO",
            "scenario_version":
                scenario_version,
            "scenario_source":
                scenario_source,
            "scenario_id":
                scenario_id,
            "scenario_name":
                scenario_name,
            "scenario_types":
                scenario_types,
            "parameters":
                scenario_parameters, #machine-readable scenario definition
            "number_of_sectors":
                number_of_sectors,
            "scenario_code":
                scenario_code,
            "target_summary":
                target_summary,   #human-readable explanation
            "scenario_signature":
                scenario_signature
        },
        summary=summary,
        comparison=comparison,
        sector_results=sector_results,
        aggregate_results=aggregate_results,
        impact_statistics=impact_statistics,
        target_impacts=target_impacts,
        target_emission_impacts=target_emission_impacts
    )

def validate_scenario_parameters(
        parameters
    ):
    required = [
        "fuel_demand_change",
        "electricity_demand_change",
        "electricity_emission_factor_change"
    ]

    for key in required:
        if key not in parameters:
            raise ValueError(
                f"Missing scenario parameter: {key}"
            )

    return True

# Note: v2.0; go to run_scenario_v21 for v2.1
def run_scenario(
        model,
        scenario_name,
        scenario_parameters,
        df_sector
    ):
    """
    Execute complete EEIO scenario workflow.

    Parameters
    ----------
    model :
        EEIOModel object

    scenario_name :
        Human-readable scenario name

    scenario_parameters :
        Dictionary containing scenario parameters

        Example:
        {
            "fuel_demand_change": -0.20,
            "electricity_demand_change": 0.15,
            "electricity_emission_factor_change": -0.30
        }

    df_sector :
        Sector metadata dataframe

    Returns
    -------
    ScenarioReport
    """
    # --------------------------------------------------
    # 1. Baseline
    # --------------------------------------------------
    baseline_result = (
        model.calculate_baseline()
    )

    # --------------------------------------------------
    # 2. Scenario calculation
    # --------------------------------------------------
    scenario_result = (
        model.calculate_scenario(
            **scenario_parameters
        )
    )

    # --------------------------------------------------
    # 3. Compare baseline vs scenario
    # --------------------------------------------------
    comparison = (
        compare_baseline_scenario(
            baseline_result,
            scenario_result
        )
    )

    # --------------------------------------------------
    # 4. Summary
    # --------------------------------------------------
    summary = (
        summarize_comparison(
            comparison
        )
    )

    # --------------------------------------------------
    # 5. Sector impact
    # --------------------------------------------------
    sector_results = (
        analyze_sector_impacts(
            comparison,
            df_sector
        )
    )

    # --------------------------------------------------
    # 6. Aggregate impact
    # --------------------------------------------------
    aggregate_results = (
        aggregate_sector_impacts(
            sector_results
        )
    )

    impact_statistics = (
        calculate_impact_statistics(
            sector_results
        )
    )

    # --------------------------------------------------
    # 7. Build report
    # --------------------------------------------------

    report = (
        build_scenario_report(
            year=model.year,
            scenario_name=scenario_name,
            scenario_parameters=scenario_parameters,
            baseline_result=baseline_result,
            scenario_result=scenario_result,
            comparison=comparison,
            summary=summary,
            number_of_sectors=model.n_sectors,
            sector_results=sector_results,
            aggregate_results=aggregate_results,
            impact_statistics=impact_statistics
        )
    )

    validate_scenario_parameters(
        scenario_parameters
    )

    return report

def serialize_scenario_parameters(parameters):

    if parameters is None:
        return None

    serialized = []
    for p in parameters:
        # Already serialized
        if isinstance(p, dict):
            serialized.append(p.copy())
            continue

        # ScenarioParameter object
        serialized.append(
            {
                "parameter_id": p.parameter_id,
                "parameter_code": p.parameter_code,
                "value": p.value,
                "unit": p.unit,
                "energy_type_code": p.energy_type_code,
                "sector_code": p.sector_code,
                "target_scope": p.target_scope
            }
        )

    return serialized

def run_scenario_v21(
        db,
        model,
        scenario_id,
        df_sector
    ):

    # 1. Load scenario definition
    loader = ScenarioLoader(db)
    scenario = loader.load(scenario_id)

    scenario_name = scenario.scenario_name
    #scenario_parameters = serialize_scenario_parameters(scenario.parameters)
    scenario_parameters = scenario.parameters

    # 2. Validate
    ScenarioValidator.validate(scenario)

    # 3. Apply scenario assumptions
    processor = ScenarioProcessor(model)
    inputs = processor.apply(model, scenario)

    if (1):
        # --------------------------------------------------
        # TEMPORARY DIAGNOSTIC: mat_finCons
        # --------------------------------------------------
        print("--inside run_scenario_v21 :: TEMPORARY TEST ONLY")
        baseline_finCons = model.mat_finCons.copy()

        print("\n=== TARGET CELL DEBUG ===")
        for p in scenario.parameters:
            if p.target_scope != "SECTOR_ENERGY":
                continue
            energy_code = str(p.energy_type_code).strip()
            sector_code = str(p.sector_code).strip()
            mapping = ENERGY_MAPPING[energy_code]
            energy_row = (mapping["finCons_row"])
            sector_column = (model.sector_column_map[sector_code])
            baseline_value = (
                baseline_finCons[energy_row,sector_column]
            )
            scenario_value = (
                inputs.mat_finCons[energy_row,sector_column]
            )
            print("parameter_code:",p.parameter_code)
            print("energy:",energy_code,p.energy_type_code)
            print("sector:",sector_code)
            print("energy_row:",energy_row)
            print("sector_column:",sector_column)
            print("change:",p.value)
            print("baseline:",baseline_value)
            print("scenario:",scenario_value)
            print("expected:",baseline_value *(1.0 + p.value))
            print("----------------------------")


        scenario_finCons = inputs.mat_finCons

        delta_finCons = scenario_finCons - baseline_finCons
        changed_mask = ~np.isclose(
            delta_finCons,
            0.0,
            rtol=1e-12,
            atol=1e-12
        )
        changed_positions = np.argwhere(changed_mask)

        print("--mat_finCons shape:", baseline_finCons.shape)
        print("--number of changed cells:", len(changed_positions))
        print("--total absolute mat_finCons change:", np.sum(np.abs(delta_finCons)))

        for row_idx, col_idx in changed_positions:
            print(
                "--changed cell:", "row=", row_idx,
                "col=", col_idx,
                "baseline=", baseline_finCons[row_idx, col_idx],
                "scenario=", scenario_finCons[row_idx, col_idx],
                "difference=", delta_finCons[row_idx, col_idx]
            )

        #Target-cell check
        target_col = model.sector_column_map["149"]
        print("--target Electricity/149 baseline:", baseline_finCons[3, target_col])
        print("--target Electricity/149 scenario:", scenario_finCons[3, target_col])
        print("--target Electricity/149 difference:", delta_finCons[3, target_col])

    # 4. Calculate EEIO result
    scenario_result = model.calculate_scenario_from_inputs(inputs)

    # 5. Continue with existing reporting pipeline
    # --------------------------------------------------
    # 1. Baseline
    baseline_result = (model.calculate_baseline())

    # -----------------------------------------
    # TEMPORARY V2.1 VALIDATION
    # -----------------------------------------
    if V21_VALIDATION_DIAGNOSTICS:
        diagnose_scenario_inputs_v21(
            model=model,
            inputs=inputs,
            scenario=scenario,
            baseline_result=baseline_result,
            scenario_result=scenario_result
        )

    # 3. Compare baseline vs scenario
    comparison = (
        compare_baseline_scenario(baseline_result, scenario_result)
    )

    # 4. Summary
    summary = summarize_comparison(comparison)

    # 5. Sector impact
    # --------------------------------------------------
    sector_results = analyze_sector_impacts(comparison, df_sector)

    # --------------------------------------------------
    # 6. Aggregate impact
    # --------------------------------------------------
    aggregate_results = aggregate_sector_impacts(sector_results)
    impact_statistics = calculate_impact_statistics(sector_results)

    energy_lookup = get_energy_lookup(db)
    sector_lookup = get_sector_lookup(db)

    baseline_finCons = model.mat_finCons.copy()
    target_impacts = analyze_target_impacts(
        scenario.parameters,
        baseline_finCons,
        inputs.mat_finCons,
        ENERGY_MAPPING,
        model.sector_column_map,
        energy_lookup,
        sector_lookup
    )
    #print("-- target_impacts", target_impacts)

    target_emission_impacts = analyze_target_emission_impacts(
        scenario.parameters, sector_results
    )
    scenario_signature = get_scenario_signature(db, scenario_id)

    # --------------------------------------------------
    # 7. Build report
    # --------------------------------------------------
    report = (
        build_scenario_report(
            year=model.year,
            scenario_name=scenario_name,
            scenario_parameters=scenario_parameters,
            scenario_id=scenario_id,
            scenario_version=SCENARIO_VERSION_V21,
            scenario_source="database",
            scenario_types=scenario.scenario_types,
            scenario_code=
                generate_scenario_code_v21(
                    model.year,
                    scenario.scenario_id,
                    scenario.parameters
                ),
            baseline_result=baseline_result,
            scenario_result=scenario_result,
            comparison=comparison,
            summary=summary,
            number_of_sectors=model.n_sectors,
            sector_results=sector_results,
            aggregate_results=aggregate_results,
            impact_statistics=impact_statistics,
            energy_lookup=energy_lookup,
            sector_lookup=sector_lookup,
            target_impacts=target_impacts,
            target_emission_impacts=target_emission_impacts,
            scenario_signature=scenario_signature
        )
    )

    return report


def diagnose_scenario_inputs_v21(
        model,
        inputs,
        scenario,
        baseline_result=None,
        scenario_result=None
    ):
    """
    Temporary validation diagnostics for EEIO v2.1.
    This function does not modify the model or database.
    """
    baseline = np.asarray(
        model.mat_finCons,
        dtype=float
    )
    scenario_mat = np.asarray(
        inputs.mat_finCons,
        dtype=float
    )
    delta = scenario_mat - baseline
    close_to_zero = np.isclose(
        delta,
        0.0,
        rtol=1e-10,
        atol=1e-10
    )

    changed_mask = ~close_to_zero
    changed_cells = int(np.count_nonzero(changed_mask))
    changed_rows = (
        np.where(np.any(changed_mask,axis=1))[0].tolist()
    )

    print()
    print("=" * 60)
    print("V2.1 SCENARIO INPUT VALIDATION")
    print("=" * 60)

    print("--scenario parameters:", scenario.parameters)
    print("--mat_finCons shape:", baseline.shape)
    print("--changed cells:", changed_cells)
    print("--changed rows:", changed_rows)
    print("--total absolute mat_finCons change:", np.sum(np.abs(delta)))

    # --------------------------------------------------
    # Test 1:
    # ENERGY + energy_demand_change
    # --------------------------------------------------
    print ("--scenario parameters:", scenario.parameters)
    demand_parameters = [
        p
        for p in scenario.parameters
        if p.parameter_code == "energy_demand_change"
    ]

    if demand_parameters:
        for p in demand_parameters:
            energy_code = p.energy_type_code
            target_scope = p.target_scope
            change = float(p.value)
            if (target_scope=="ENERGY"):
                row = (ENERGY_ROW_INDEX_V21_TEST[energy_code])
                expected_row = (
                    baseline[row, :] * (1.0 + change)
                )
                target_ok = np.allclose(
                    scenario_mat[row, :], expected_row, rtol=1e-10, atol=1e-10
                )
                other_rows = [
                    r
                    for r in range(baseline.shape[0])
                    if r != row
                ]
                other_rows_ok = np.allclose(
                    scenario_mat[other_rows,:],
                    baseline[other_rows,:],
                    rtol=1e-10,
                    atol=1e-10
                )
                print()
                print("--TEST 1: ENERGY demand change")
                print("--energy type:",energy_code)
                print("--change:",change * 100,"%")
                print("--target row:",row)
                print("--target row follows expected change:",target_ok)
                print("--other energy rows unchanged:",other_rows_ok)
                print("--TEST 1 RESULT:","PASS"
                    if (target_ok and other_rows_ok)
                    else "FAIL"
                )

    # --------------------------------------------------
    # Test 2:
    # emission_factor_change only
    # --------------------------------------------------
    ef_parameters = [
        p
        for p in scenario.parameters
        if p.parameter_code == "emission_factor_change"
    ]
    if (ef_parameters and not demand_parameters):
        mat_unchanged = np.allclose(
            scenario_mat,
            baseline,
            rtol=1e-10,
            atol=1e-10
        )
        print()
        print("--TEST 2: emission factor change only")
        print("--mat_finCons unchanged:", mat_unchanged)
        emission_changed = None
        if (baseline_result is not None and scenario_result is not None):
            baseline_total = float(np.sum(baseline_result["totalEmission"]))
            scenario_total = float(np.sum(scenario_result["totalEmission"]))
            baseline_scope2 = float(np.sum(baseline_result["emissionScope2"]))
            scenario_scope2 = float(np.sum(scenario_result["emissionScope2"]))
            emission_changed = (not np.isclose(scenario_total,baseline_total, rtol=1e-10,atol=1e-10))
            scope2_changed = (not np.isclose(scenario_scope2,baseline_scope2,rtol=1e-10,atol=1e-10))
            print("--baseline total emission:",baseline_total)
            print("--scenario total emission:",scenario_total)
            print("--baseline Scope 2:",baseline_scope2)
            print("--scenario Scope 2:",scenario_scope2)
            print("--total emission changed:",emission_changed)
            print("--Scope 2 changed:",scope2_changed)
        else:
            scope2_changed = None

        if (emission_changed is None):
            test2_ok = mat_unchanged
        else:
            test2_ok = (mat_unchanged and emission_changed and scope2_changed)

        print("--TEST 2 RESULT:", "PASS" if test2_ok
            else "FAIL"
        )

    print("=" * 60)
    print()

def calculate_impact_statistics(
        sector_results,
        top_n=5
    ):
    """
    Calculate high-level sector impact statistics.

    Parameters
    ----------
    sector_results :
        DataFrame from analyze_sector_impacts()
    top_n :
        Number of sectors shown in top reduction list.
    Returns
    -------
    dict
    """
    total_sectors = len(
        sector_results
    )
    reduced = sector_results[
        sector_results["difference"] < 0
    ]
    increased = sector_results[
        sector_results["difference"] > 0
    ]
    unchanged = sector_results[
        sector_results["difference"] == 0
    ]

    # --------------------------------------------------
    # Identify sectors with an actual reduction
    # --------------------------------------------------
    reduced_for_ranking = (
        sector_results[
            sector_results["reduction"] > 0
        ]
        .copy()
    )

    # --------------------------------------------------
    # Total reduction
    # --------------------------------------------------
    total_reduction = (
        reduced_for_ranking["reduction"]
        .sum()
    )

    if np.isclose(
        total_reduction,
        0.0,
        rtol=1e-12,
        atol=1e-9
        ):
        largest_reduction_sector = None
        largest_reduction_value = 0.0
        top_reduction_sectors = []
    else:
        ranked_reduction = (
            reduced_for_ranking
            .sort_values(
                by="reduction",
                ascending=False
            )
        )
        # Largest reduction
        largest_reduction = (
            ranked_reduction.iloc[0]
        )
        largest_reduction_sector = (
            largest_reduction[
                "sector_name"
            ]
        )
        largest_reduction_value = (
            largest_reduction[
                "reduction"
            ]
        )

        # ----------------------------------------------
        # Top reduction sectors
        # ----------------------------------------------
        top_reduction_sectors = []
        for _, row in (
            ranked_reduction
            .head(top_n)
            .iterrows()
            ):
            reduction_value = float(
                row["reduction"]
            )
            contribution_pct = (
                reduction_value
                /
                total_reduction
                *
                100
            )
            top_reduction_sectors.append(
                {
                    "sector":
                        row["sector_name"],
                    "sector_code":
                        row["sector_code"],
                    "reduction":
                        reduction_value,
                    "reduction_mtco2":
                        reduction_value
                        /
                        1_000_000,
                    "contribution_pct":
                        contribution_pct
                }
            )

    return {
        "total_sectors":
            total_sectors,
        "sectors_reduced":
            len(reduced),
        "sectors_increased":
            len(increased),
        "sectors_unchanged":
            len(unchanged),
        "largest_reduction_sector":
            largest_reduction_sector,
        "largest_reduction_value":
            largest_reduction_value,
        "top_reduction_sectors":
            top_reduction_sectors
    }

def convert_numpy_types(obj):
    if isinstance(obj, np.generic):
        return obj.item()

    if isinstance(obj, dict):
        return {
            k: convert_numpy_types(v)
            for k, v in obj.items()
        }

    if isinstance(obj, list):
        return [
            convert_numpy_types(v)
            for v in obj
        ]

    return obj

def report_summary_json(
        report
    ):
    """
    Convert ScenarioReport summary
    into JSON-friendly format.
    """
    metadata = report.metadata.copy()
    if "parameters" in metadata:
        metadata["parameters"] = (
            serialize_scenario_parameters(
                metadata["parameters"]
            )
        )
    json_src = {
        "metadata": metadata,
        "summary": report.summary,
        "impact_statistics": report.impact_statistics,
        "target_impacts": report.target_impacts,
        "target_emission_impacts": report.target_emission_impacts
    }
    json_ready = convert_numpy_types(json_src)

    return json_ready

def generate_scenario_code(
        year,
        parameters
    ):
    """
    Generate compact scenario identifier.
    """

    fuel = int(
        parameters[
            "fuel_demand_change"
        ]
        *
        100
    )
    elec = int(
        parameters[
            "electricity_demand_change"
        ]
        *
        100
    )
    ef = int(
        parameters[
            "electricity_emission_factor_change"
        ]
        *
        100
    )

    return (
        f"{year}_"
        f"F{fuel:+d}_"
        f"E{elec:+d}_"
        f"EF{ef:+d}"
    )

def generate_scenario_code_v21(
        year,
        scenario_id,
        parameters
    ):
    code_parts = []
    #print("--parameters", parameters)
    for p in parameters:
        #print("--p", p)
        if p.parameter_code == "energy_demand_change":
            prefix = "ED"
        elif p.parameter_code == "emission_factor_change":
            prefix = "EF"
        else:
            prefix = "P"
        value = int(
            p.value * 100
        )
        target = (
            p.energy_type_code
            if p.energy_type_code
            else "ALL"
        )
        code_parts.append(
            f"{prefix}{target}{value:+d}"
        )

    return (
        f"{year}_"
        f"S{scenario_id:03d}_"
        +
        "_".join(code_parts)
    )

def build_sector_column_map(
        df_sector
    ):

    sector_map = {}
    for idx, row in df_sector.iterrows():
        sector_map[
            row["sector_code"]
        ] = idx

    return sector_map

def build_target_summary(
        parameters,
        energy_lookup=None,
        sector_lookup=None
    ):

    target_summary = []
    for p in parameters:
        item = {
            "parameter_code":
                p.parameter_code,
            "energy_type_code":
                p.energy_type_code,
            "target_scope":
                p.target_scope,
            "change":
                p.value,
            "change_percent":
                p.value * 100
        }

        item["energy_name"] = "N/A"
        item["sector_name"] = "N/A"

        if energy_lookup:
            item["energy_name"] = (
                energy_lookup.get(
                    p.energy_type_code, "N/A"
                )
            )

        if p.sector_code is not None:
            item["sector_code"] = (
                p.sector_code
            )
            if sector_lookup:
                item["sector_name"] = (
                    sector_lookup.get(
                        p.sector_code, "N/A"
                    )
                )
        target_summary.append(item)
    #print("--target_summary", target_summary)

    return target_summary

def get_energy_lookup(db):

    q_str = """
    SELECT
        energy_type_code,
        energy_type_name
    FROM energy_lst
    """

    df = tbl_to_df(
        db,
        q_str
    )

    energy_lookup = {}
    for _, row in df.iterrows():
        energy_lookup[
            str(row["energy_type_code"])
        ] = row["energy_type_name"]

    return energy_lookup

def get_sector_lookup(db):

    q_str = """
    SELECT
        sector_code,
        sector_name
    FROM sector_lst
    """
    df = tbl_to_df(
        db,
        q_str
    )

    sector_lookup = {}
    for _, row in df.iterrows():
        sector_lookup[
            str(row["sector_code"])
        ] = row["sector_name"]

    return sector_lookup

def get_scenario_signature(db, scenario_id):

    # Multi-line query with triple quotes
    query = """
        SELECT scenario_signature
        FROM scenario_definition
        WHERE scenario_id = %s
    """

    cursor = db.connection.cursor(MySQLdb.cursors.DictCursor) #db.connection.cursor()
    # Pass the variable as a tuple in the connector's execute method
    cursor.execute(query, (scenario_id,))

    result = cursor.fetchone()

    # Assign the value to the variable
    if result:
        # result is a tuple, e.g. ('my_signature_value',)
        scenario_signature = result['scenario_signature']
    else:
        scenario_signature = None

    # Clean up
    cursor.close()

    return scenario_signature