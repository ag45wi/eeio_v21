import MySQLdb
import json
import hashlib
import numpy as np

from util_db import tbl_to_df
from eeio_engine import (
    calculate_impact_statistics,
    aggregate_sector_impacts,
    ScenarioReport,
    run_scenario_v21,
    generate_scenario_code_v21,
    get_energy_lookup,
    get_sector_lookup
)
from scenario_engine import (
    ScenarioLoader,
    ScenarioNotRunnableError
)
from scenario_report import (
    save_target_impacts_v21,
    get_saved_target_impacts_v21,
    save_target_emission_impacts_v21,
    get_saved_target_emission_impacts_v21
)
from config_scenario import (
    SCENARIO_VERSION_V21,
    SUPPORTED_TARGET_SCOPES,
    SUPPORTED_PARAMETERS,
    PARAMETER_RULES_V21,
    SCENARIO_TYPES_V21,
    CUSTOM_SCENARIO_TYPE_CODE_V21,
    PARAMETER_MNEMONICS, ENERGY_MNEMONICS, TARGET_SCOPE_MNEMONICS
)

def save_scenario_history_v21(
        db,
        report
    ):

    total = report.summary["totalEmission"]
    scope1 = report.summary["emissionScope1"]
    scope2 = report.summary["emissionScope2"]
    scope3 = report.summary["emissionScope3"]

    q_str = """
    INSERT INTO scenario_history_v21
    (
        scenario_id,
        year,
        scenario_code,
        scenario_name,
        scenario_version,
        scenario_source,
        number_of_sectors,
        baseline_emission,
        scenario_emission,
        emission_difference,
        percentage_change,
        baseline_scope1,
        scenario_scope1,
        baseline_scope2,
        scenario_scope2,
        baseline_scope3,
        scenario_scope3
    )
    VALUES
    (
        %s,%s,%s,%s,%s,%s,%s,
        %s,%s,%s,%s,
        %s,%s,
        %s,%s,
        %s,%s
    )
    """
    params = (
        report.metadata.get(
            "scenario_id"
        ),
        report.metadata["year"],
        report.metadata[
            "scenario_code"
        ],
        report.metadata[
            "scenario_name"
        ],
        report.metadata.get(
            "scenario_version", SCENARIO_VERSION_V21
        ),
        report.metadata.get(
            "scenario_source",
            "database"
        ),
        report.metadata[
            "number_of_sectors"
        ],
        float(
            total["baseline_total"]
        ),
        float(
            total["scenario_total"]
        ),
        float(
            total["difference_total"]
        ),
        float(
            total["percentage_change"]
        ),
        float(
            scope1["baseline_total"]
        ),
        float(
            scope1["scenario_total"]
        ),
        float(
            scope2["baseline_total"]
        ),
        float(
            scope2["scenario_total"]
        ),
        float(
            scope3["baseline_total"]
        ),
        float(
            scope3["scenario_total"]
        )
    )
    #print("--save_scenario_history_v21:q_str--", q_str)

    cursor = db.connection.cursor()
    cursor.execute(
        q_str,
        params
    )

    db.connection.commit()

    scenario_history_id = (
        cursor.lastrowid
    )
    cursor.close()

    return scenario_history_id

def save_parameter_history_v21(
        db,
        scenario_history_id,
        parameters
    ):
    q_str = """
    INSERT INTO scenario_parameter_history
    (
        scenario_history_id,
        parameter_code,
        parameter_value,
        unit,
        energy_type_code,
        sector_code,
        target_scope
    )
    VALUES
    (
        %s,%s,%s,%s,%s,%s,%s
    )
    """

    cursor = db.connection.cursor()

    for p in parameters:
        cursor.execute(
            q_str,
            (
                scenario_history_id,
                p.parameter_code,
                float(
                    p.value
                ),
                p.unit,
                p.energy_type_code,
                p.sector_code,
                p.target_scope
            )
        )
    db.connection.commit()
    cursor.close()

def save_type_history_v21(
        db,
        scenario_history_id,
        scenario_types
    ):
    #print ("--Inside save_type_history_v21--")
    q_str = """
    INSERT INTO scenario_type_history
    (
        scenario_history_id,
        type_code,
        type_name,
        is_primary,
        scenario_type_id
    )
    VALUES
    (
        %s,%s,%s,%s,%s
    )
    """

    cursor = db.connection.cursor()

    for t in scenario_types:
        cursor.execute(
            q_str,
            (
                scenario_history_id,
                t["type_code"],
                t["type_name"],
                bool(
                    t["is_primary"]
                ),
                t["scenario_type_id"]
            )
        )

    #print(" --q_str-- ", q_str)
    db.connection.commit()
    cursor.close()

def save_sector_results_v21(
        db,
        scenario_history_id,
        sector_results
    ):

    q_str = """
    INSERT INTO scenario_sector_result
    (
        scenario_history_v21_id,
        sector_code,
        sector_name,
        sector_agg,
        baseline,
        scenario,
        difference,
        reduction,
        percentage_change
    )
    VALUES
    (
        %s,%s,%s,%s,
        %s,%s,%s,%s,%s
    )
    """
    cursor = db.connection.cursor()

    for _, row in sector_results.iterrows():
        cursor.execute(
            q_str,
            (
                scenario_history_id,
                row["sector_code"],
                row["sector_name"],
                row["sector_agg"],
                float(
                    row["baseline"]
                ),
                float(
                    row["scenario"]
                ),
                float(
                    row["difference"]
                ),
                float(
                    row["reduction"]
                ),
                float(
                    row["percentage_change"]
                )
            )
        )
    db.connection.commit()
    cursor.close()

def save_complete_scenario_report_v21(
        db,
        report
    ):
    scenario_history_id = (
        save_scenario_history_v21(
            db,
            report
        )
    )
    save_parameter_history_v21(
        db,
        scenario_history_id,
        report.metadata["parameters"]
    )

    save_type_history_v21(
        db,
        scenario_history_id,
        report.metadata["scenario_types"]
    )

    save_sector_results_v21(
        db,
        scenario_history_id,
        report.sector_results
    )

    save_target_impacts_v21(
        db,
        scenario_history_id,
        report.target_impacts
    )

    save_target_emission_impacts_v21(
        db,
        scenario_history_id,
        report.target_emission_impacts
    )

    return scenario_history_id

def get_saved_scenario_v21(
        db,
        scenario_history_id
    ):
    q_str = f"""
    SELECT *
    FROM scenario_history_v21
    WHERE scenario_history_id =
          '{scenario_history_id}'
    """
    df = tbl_to_df(
        db,
        q_str
    )
    if len(df) == 0:
        #print("--get_saved_scenario_v21::q_str", q_str)
        raise ValueError(
            "EEIO 2.1 scenario history not found"
        )

    return df.iloc[0].to_dict()

def get_saved_parameter_history_v21(
        db,
        scenario_history_id
    ):

    q_str = f"""
    SELECT
        parameter_code,
        parameter_value,
        unit,
        energy_type_code,
        sector_code,
        target_scope
    FROM scenario_parameter_history
    WHERE scenario_history_id =
          '{scenario_history_id}'
    ORDER BY parameter_history_id
    """

    df = tbl_to_df(
        db,
        q_str
    )

    return df

def serialize_saved_parameters_v21(
        df_parameter
    ):
    parameters = []
    for _, row in df_parameter.iterrows():
        parameters.append(
            {
            "parameter_code":
                row["parameter_code"],
            "value":
                float(
                    row["parameter_value"]
                ),
            "unit":
                row["unit"],
            "energy_type_code":
                row["energy_type_code"],
            "sector_code":
                row["sector_code"],
            "target_scope":
                row["target_scope"]

            }
        )

    return parameters

def get_saved_type_history_v21(
        db,
        scenario_history_id
    ):

    q_str = f"""
    SELECT
        type_code,
        type_name,
        is_primary,
        scenario_type_id
    FROM scenario_type_history
    WHERE scenario_history_id =
          '{scenario_history_id}'
    ORDER BY type_history_id
    """
    df = tbl_to_df(db, q_str)

    #print("-- get_saved_type_history_v21::df", df.head(5))
    return (
        df.to_dict(
            orient="records"
        )
    )

def get_scenario_definition_byid(
        db,
        scenario_id
    ):
    print("--inside get_scenario_definition_byid::scenario_id", scenario_id)
    q_str = f"""
    SELECT
        scenario_id,
        scenario_name,
        description,
        scenario_signature
    FROM scenario_definition
    WHERE scenario_id =
          '{scenario_id}'
    """
    df = tbl_to_df(db, q_str)
    row = df.to_dict(orient="records")[0]
    print("--get_scenario_definition_byid::row", row)

    return (row)

def get_saved_sector_results_v21(
        db,
        scenario_history_id
    ):

    q_str = f"""
    SELECT
        sector_code,
        sector_name,
        sector_agg,
        baseline,
        scenario,
        difference,
        reduction,
        percentage_change
    FROM scenario_sector_result
    WHERE scenario_history_v21_id =
          '{scenario_history_id}'
    ORDER BY sector_code
    """

    return tbl_to_df(db, q_str)

def load_scenario_report_v21(
        db, scenario_history_id
    ):
    #print("-- inside load_scenario_report_v21")
    scenario = get_saved_scenario_v21(db, scenario_history_id)
    scenario_def = get_scenario_definition_byid(db, scenario["scenario_id"])
    #print("scenario_def", scenario_def)

    df_parameters = (
        get_saved_parameter_history_v21(db, scenario_history_id)
    )
    parameters = (
        serialize_saved_parameters_v21(df_parameters)
    )
    scenario_types = (
        get_saved_type_history_v21(db, scenario_history_id)
    )
    sector_results = (
        get_saved_sector_results_v21(db, scenario_history_id)
    )
    aggregate_results = (
        aggregate_sector_impacts(sector_results)
    )
    impact_statistics = (
        calculate_impact_statistics(sector_results)
    )

    summary = {
        "emissionScope1":{
            "baseline_total":
                scenario["baseline_scope1"],
            "scenario_total":
                scenario["scenario_scope1"],
            "difference_total":
                scenario["scenario_scope1"]
                -
                scenario["baseline_scope1"],
            "percentage_change":
                None if scenario["baseline_scope1"] == 0 else
                (scenario["scenario_scope1"] - scenario["baseline_scope1"])
                /
                scenario["baseline_scope1"] * 100
            },
        "emissionScope2":{
            "baseline_total":
                scenario["baseline_scope2"],
            "scenario_total":
                scenario["scenario_scope2"],
            "difference_total":
                scenario["scenario_scope2"] - scenario["baseline_scope2"],
            "percentage_change":
                None if scenario["baseline_scope2"] == 0 else
                (scenario["scenario_scope2"] - scenario["baseline_scope2"])
                /
                scenario["baseline_scope2"] * 100
            },
        "emissionScope3":{
            "baseline_total":
                scenario["baseline_scope3"],
            "scenario_total":
                scenario["scenario_scope3"],
            "difference_total":
                scenario["scenario_scope3"] - scenario["baseline_scope3"],
            "percentage_change":
                None if scenario["baseline_scope3"] == 0 else
                (scenario["scenario_scope3"] - scenario["baseline_scope3"])
                /
                scenario["baseline_scope3"] * 100
            },
        "totalEmission": {
            "baseline_total":
                scenario["baseline_emission"],
            "scenario_total":
                scenario["scenario_emission"],
            "difference_total":
                scenario["emission_difference"],
            "percentage_change":
                scenario["percentage_change"]
        }
    }

    metadata = {
        "year":
            scenario["year"],
        "model_type":
            "EEIO",
        "scenario_version":
            scenario["scenario_version"],
        "scenario_source":
            scenario["scenario_source"],
        "scenario_id":
            scenario["scenario_id"],
        "scenario_name":
            scenario["scenario_name"],
        "scenario_code":
            scenario["scenario_code"],
        "scenario_types":
            scenario_types,
        "parameters":
            parameters,
        "number_of_sectors":
            scenario["number_of_sectors"],
        "scenario_description": scenario_def["description"],
        "scenario_signature": scenario_def["scenario_signature"]
    }

    energy_lookup = get_energy_lookup(db)
    sector_lookup = get_sector_lookup(db)
    target_summary = build_target_summary_v21(
        metadata["parameters"],
        energy_lookup=energy_lookup,
        sector_lookup=sector_lookup
    )

    #print("--util_scenario_v21.py -> load_scenario_report_v21 ::target_summary", target_summary)

    metadata["target_summary"] = target_summary
    target_impacts = (
        get_saved_target_impacts_v21(db, scenario_history_id)
    )
    target_emission_impacts = (
        get_saved_target_emission_impacts_v21(db, scenario_history_id)
    )

    # --------------------------------------------------
    # Backward compatibility for older ENERGY scenarios
    # --------------------------------------------------
    if (
        target_emission_impacts is None
        and target_impacts
        ):
        target_emission_impacts = []
        for item in target_impacts:
            if item.get("target_scope") != "ENERGY":
                continue

            total_reduction = float(
                sector_results["reduction"].sum()
            )
            target_emission_impacts.append(
                {
                    "parameter_code":
                        item.get("parameter_code"),
                    "energy_type_code":
                        item.get("energy_type_code"),
                    "sector_code":
                        None,
                    "sector_name":
                        "All sectors",
                    "target_scope":
                        "ENERGY",
                    "target_sector_reduction":
                        total_reduction,
                    "other_sector_reduction":
                        0.0,
                    "total_reduction":
                        total_reduction,
                    "target_share_pct":
                        100.0
                        if total_reduction != 0
                        else 0.0,
                    "other_share_pct":
                        0.0
                }
            )


    #print("-- inside load_scenario_report_v21")
    #print("-- loaded target_impacts:", target_impacts)

    return ScenarioReport(
        metadata=metadata,
        summary=summary,
        comparison=None,
        sector_results=sector_results,
        aggregate_results=aggregate_results,
        impact_statistics=impact_statistics,
        target_impacts=target_impacts,
        target_emission_impacts=target_emission_impacts
    )

def build_target_summary_v21(
        parameters,
        energy_lookup=None,
        sector_lookup=None
    ):
    target_summary = []

    def get_value(p, key):
        if isinstance(p, dict):
            return p.get(key)

        return getattr(p, key, None)

    for p in parameters:
        parameter_code = get_value(p, "parameter_code")
        energy_type_code = get_value(p, "energy_type_code")
        sector_code = get_value(p, "sector_code")
        target_scope = get_value(p, "target_scope")
        value = get_value(p, "value")
        item = {
            "parameter_code":
                parameter_code,
            "energy_type_code":
                energy_type_code,
            "target_scope":
                target_scope,
            "change":
                value,
            "change_percent":
                value * 100
        }

        item["energy_name"] = "N/A"
        item["sector_name"] = "N/A"

        if energy_lookup is not None:
            item["energy_name"] = energy_lookup.get(energy_type_code, "N/A")

        if sector_code is not None:
            item["sector_code"] = sector_code
            if sector_lookup is not None:
                item["sector_name"] = sector_lookup.get(sector_code, "N/A")

        target_summary.append(item)

    return target_summary


def run_and_save_scenario_v21(
        db,
        model,
        scenario_id,
        df_sector
    ):
    report = run_scenario_v21(
        db,
        model,
        scenario_id,
        df_sector
    )
    history_id = save_complete_scenario_report_v21(db, report)

    return report, history_id

def find_existing_scenario_history_v21(
        db,
        scenario_code
    ):

    q_str = f"""
    SELECT
        scenario_history_id
    FROM scenario_history_v21
    WHERE scenario_code =
          '{scenario_code}'
    ORDER BY created_time DESC
    LIMIT 1
    """

    df = tbl_to_df(db, q_str)
    if len(df) == 0:
        return None

    return int(
        df.iloc[0]["scenario_history_id"]
    )

def run_or_load_scenario_v21(
        db,
        model,
        scenario_id,
        df_sector
    ):

    #print("--scenario_id", scenario_id)
    # 1. Load scenario definition
    #scenario = ScenarioLoader.load(scenario_id) #err, need object
    loader = ScenarioLoader(db)
    scenario = loader.load(scenario_id)

    # 2. Generate code
    scenario_code = (
        generate_scenario_code_v21(
            model.year, scenario_id, scenario.parameters
        )
    )

    # 3. Check history
    history_id = find_existing_scenario_history_v21(db, scenario_code)
    if history_id:
        print("--history_id found", history_id)

        #print("--inside run_or_load_scenario_v21 :: TEMPORARY TEST ONLY")
        #print("--loaded report.target_impacts:", report.target_impacts)
        if (1):
            # TEMPORARY TEST ONLY
            print("--TEMPORARY run_scenario_v21()")
            test_report = run_scenario_v21(
                db,
                model,
                scenario_id,
                df_sector
            )
            #print("--direct calculation target_impacts:",test_report.target_impacts)
            if (0):

                print("--TEMPORARY REPAIR OF EXISTING HISTORY 38")
                # -----------------------------------------
                # TEMPORARY REPAIR OF EXISTING HISTORY 38
                # -----------------------------------------
                save_target_impacts_v21(
                    db,
                    history_id,
                    test_report.target_impacts
                )
                print(
                    "--target impacts saved for history:",
                    history_id
                )
            history_id = save_complete_scenario_report_v21(db, report)
        else:
            report = load_scenario_report_v21(db, history_id)

        return {
            "report": report,
            "history_id": history_id,
            "status": "loaded"
        }

    # 4. Otherwise calculate
    print("--history_id not found", history_id, " -- about to run_scenario_v21()")

    report = run_scenario_v21(
        db,
        model,
        scenario_id,
        df_sector
    )
    if (0):
        print("--inside run_or_load_scenario_v21")
        print("--calculated report.target_impacts:",report.target_impacts)
        print("--calculated report.target_emission_impacts:",report.target_emission_impacts)

    history_id = (
        save_complete_scenario_report_v21(db, report)
    )

    return {
        "report": report,
        "history_id": history_id,
        "status": "calculated"
    }

'''
def get_canonical_scenario_list_v21(db):
    """
    Return canonical EEIO v2.1 scenario definitions.

    Only definitions with scenario_signature are exposed
    to the v2.1 UI. Older unsigned/debug definitions remain
    in the database but are hidden from normal selection.
    """

    cursor = db.connection.cursor(
        MySQLdb.cursors.DictCursor
    )

    q_str = """
    SELECT
        scenario_id,
        scenario_name,
        scenario_year,
        description,
        scenario_signature
    FROM scenario_definition
    WHERE scenario_signature IS NOT NULL
    ORDER BY
        scenario_year DESC,
        scenario_name ASC,
        scenario_id ASC
    """

    cursor.execute(q_str)
    scenarios = cursor.fetchall()

    cursor.close()

    return scenarios
'''

def get_canonical_scenario_list_v21(db):
    """
    Return canonical EEIO v2.1 scenario definitions.

    Only definitions with scenario_signature are exposed
    to the v2.1 UI. Older unsigned/debug definitions remain
    in the database but are hidden from normal selection.
    """

    cursor = db.connection.cursor(
        MySQLdb.cursors.DictCursor
    )

    q_str = """
    SELECT
        scenario_id,
        scenario_name,
        scenario_year,
        description,
        scenario_signature
    FROM scenario_definition
    WHERE scenario_signature IS NOT NULL
    ORDER BY
        scenario_id DESC
    """
    cursor.execute(q_str)
    scenarios = cursor.fetchall()

    cursor.close()

    # --------------------------------------------------
    # Add human-readable parameter summary
    # --------------------------------------------------
    loader = ScenarioLoader(db)
    for scenario in scenarios:

        try:
            loaded_scenario = loader.load(
                scenario["scenario_id"]
            )
            scenario["parameter_summary"] = (
                format_parameter_summary(
                    loaded_scenario.parameters
                )
            )
            scenario["is_runnable"] = True
            scenario["validation_error"] = None

        except ScenarioNotRunnableError as exc:
            scenario["parameter_summary"] = (
                "Invalid / incomplete definition"
            )
            scenario["is_runnable"] = False
            scenario["validation_error"] = str(exc)

    return scenarios

def prepare_scenario_display_v21(report):
    """
    Prepare ScenarioReport v2.1 for the existing Flask/Jinja UI.

    This is the UI adapter between the generic v2.1 report structure
    and scenario_display.html.

    Step 18A keeps the existing result layout as much as possible.
    Richer v2.1 target reporting will be added in Step 18B.
    """

    # -------------------------------------------------
    # Overall emission summary
    # -------------------------------------------------
    total = report.summary["totalEmission"]

    difference_total = float(
        total["difference_total"]
    )

    # Model convention:
    # difference = scenario - baseline
    #
    # UI convention:
    # reduction = baseline - scenario
    reduction_total = -difference_total

    scenario_summary = {
        "scenario_code":
            report.metadata.get("scenario_code"),
        "scenario_name":
            report.metadata.get("scenario_name"),
        "year":
            report.metadata.get("year"),
        "baseline_total":
            total["baseline_total"],
        "scenario_total":
            total["scenario_total"],
        "difference_total":
            total["difference_total"],
        "reduction_total":
            reduction_total,
        "percentage_change":
            total["percentage_change"],

        # UI-ready units
        "baseline_mtco2":
            float(total["baseline_total"]) / 1_000_000,
        "scenario_mtco2":
            float(total["scenario_total"]) / 1_000_000,
        "reduction_mtco2":
            reduction_total / 1_000_000,
        "reduction_ktco2":
            reduction_total / 1_000
    }

    # -------------------------------------------------
    # Scope summary
    #
    # report.summary already contains the Scope
    # decomposition calculated by the v2.1 engine.
    # -------------------------------------------------
    scope_summary = {}

    scope_labels = {
        "emissionScope1": "Scope 1",
        "emissionScope2": "Scope 2",
        "emissionScope3": "Scope 3"
    }

    for key, value in report.summary.items():
        if key == "totalEmission":
            continue

        if not isinstance(value, dict):
            continue

        if (
            "baseline_total" in value
            and
            "scenario_total" in value
            ):
            display_key = scope_labels.get(key, key)
            scope_summary[display_key] = value

    # -------------------------------------------------
    # Impact statistics
    # -------------------------------------------------
    #impact_statistics = report.impact_statistics
    impact_statistics = dict(
        report.impact_statistics
        or {}
    )
    top_reduction_sectors = []

    raw_top_sectors = (
        impact_statistics.get("top_reduction_sectors", [])
        or []
    )

    total_reduction = scenario_summary["reduction_total"]

    for item in raw_top_sectors:
        row = dict(item)

        reduction = float(
            row.get("reduction", 0.0)
        )
        row["reduction_mtco2"] = (
            reduction / 1_000_000
        )
        row["reduction_ktco2"] = (
            reduction / 1_000
        )
        if (row.get("contribution_pct") is None):
            row["contribution_pct"] = (
                reduction
                / total_reduction
                * 100
                if total_reduction != 0
                else 0.0
            )
        top_reduction_sectors.append(
            row
        )

    impact_statistics[
        "top_reduction_sectors"
    ] = top_reduction_sectors

    # -------------------------------------------------
    # Existing Scope chart format
    # -------------------------------------------------
    scope_chart_data = {}
    has_scope_change = False

    for key, value in scope_summary.items():
        if key not in [
            "emissionScope1",
            "emissionScope2",
            "emissionScope3",
            "totalEmission"
            ]:
            continue

        baseline = (value["baseline_total"])
        scenario = (value["scenario_total"])
        scope_chart_data[key] = {
            "baseline":
                baseline / 1_000_000,
            "scenario":
                scenario / 1_000_000
        }
        if not np.isclose(
            baseline,
            scenario,
            rtol=1e-12,
            atol=1e-9
            ):
            has_scope_change = True

    if not has_scope_change:
        scope_chart_data = {}
    print("--scope_chart_data", scope_chart_data)


    # -------------------------------------------------
    # Existing Top Sector chart format
    # -------------------------------------------------
    top_sector_chart_data = []

    for item in (
        impact_statistics.get("top_reduction_sectors", [])
        ):
        top_sector_chart_data.append({
            "sector":
                item["sector"],
            "sector_code":
                item.get("sector_code"),
            "reduction_ktco2":
                item["reduction_ktco2"],
            "contribution_pct":
                item["contribution_pct"]
        })

    # -------------------------------------------------
    # Scenario target summary
    # -------------------------------------------------
    target_summary = []

    raw_target_summary = (
        report.metadata.get("target_summary", [])
        or []
    )

    parameter_labels = {
        "energy_demand_change":
            "Energy demand change",
        "emission_factor_change":
            "Emission factor change"
    }

    for item in raw_target_summary:
        row = dict(item)
        parameter_code = row.get(
            "parameter_code"
        )
        row["parameter_label"] = (
            parameter_labels.get(
                parameter_code,
                parameter_code.replace(
                    "_",
                    " "
                ).title()
                if parameter_code
                else "-"
            )
        )
        # Clean DB text
        if isinstance(row.get("energy_name"), str):
            row["energy_name"] = row["energy_name"].strip()
        if isinstance(row.get("sector_name"), str):
            row["sector_name"] = row["sector_name"].strip()

        # ENERGY means the target is not restricted
        # to one specific economic sector.
        if not row.get("sector_name"):
            row["sector_name"] = "All sectors"

        scope_labels = {
            "ENERGY": "All sectors",
            "SECTOR_ENERGY": "Specific sector"
        }
        row["scope_label"] = (
            scope_labels.get(
                row.get("target_scope"),
                row.get("target_scope")
            )
        )
        target_summary.append(row)

    # -------------------------------------------------
    # Physical target impacts
    # -------------------------------------------------
    target_impacts = []

    for item in (
        report.target_impacts or []
       ):
        row = dict(item)
        parameter_code = row.get(
            "parameter_code"
        )
        row["parameter_label"] = (
            parameter_labels.get(
                parameter_code,
                parameter_code.replace(
                    "_",
                    " "
                ).title()
                if parameter_code
                else "-"
            )
        )
        if isinstance(row.get("energy_name"), str):
            row["energy_name"] = row["energy_name"].strip()
        if isinstance(row.get("sector_name"), str):
            row["sector_name"] = row["sector_name"].strip()

        if not row.get("sector_name"):
            row["sector_name"] = "All sectors"

        target_impacts.append(row)

    # -------------------------------------------------
    # Target-sector vs rest-of-economy
    # emission impacts
    # -------------------------------------------------
    target_emission_impacts = []

    for item in (report.target_emission_impacts or []):
        row = dict(item)
        if isinstance(row.get("sector_name"), str):
            row["sector_name"] = row["sector_name"].strip()

        # Convert tCO2 -> ktCO2 for UI readability
        row["target_sector_reduction_ktco2"] = (
            row["target_sector_reduction"]
            / 1000
        )
        row["other_sector_reduction_ktco2"] = (
            row["other_sector_reduction"]
            / 1000
        )
        row["total_reduction_ktco2"] = (
            row["total_reduction"]
            / 1000
        )
        target_emission_impacts.append(row)

    #print("--target_summary--", target_summary)

    # -------------------------------------------------
    # Target-sector vs rest-of-economy chart
    #
    # Only render automatically when there is exactly
    # one target-emission decomposition.
    # -------------------------------------------------
    """
    target_emission_chart_data = None

    if len(target_emission_impacts) == 1:
        item = target_emission_impacts[0]
        target_emission_chart_data = {
            "labels": [
                "Target sector",
                "Other sectors"
            ],
            "values": [
                item["target_sector_reduction_ktco2"],
                item["other_sector_reduction_ktco2"]
            ],
            "shares": [
                item["target_share_pct"],
                item["other_share_pct"]
            ],
            "sector_name":
                item["sector_name"]
        }
    """

    target_emission_chart_data = None
    if len(target_emission_impacts) == 1:
        item = target_emission_impacts[0]
        target_reduction = float(
            item["target_sector_reduction_ktco2"]
        )
        other_reduction = float(
            item["other_sector_reduction_ktco2"]
        )

        # Show the chart only when there is
        # a meaningful emission impact.
        has_emission_impact = (
            not np.isclose(
                target_reduction,
                0.0,
                rtol=1e-12,
                atol=1e-9
            )
            or
            not np.isclose(
                other_reduction,
                0.0,
                rtol=1e-12,
                atol=1e-9
            )
        )

        if has_emission_impact:
            target_emission_chart_data = {
                "labels": [
                    "Target sector",
                    "Other sectors"
                ],
                "values": [
                    target_reduction,
                    other_reduction
                ],
                "shares": [
                    item["target_share_pct"],
                    item["other_share_pct"]
                ],
                "sector_name":
                    item["sector_name"]
            }

    return {
        "scenario_summary":
            scenario_summary,
        "target_summary":
            target_summary,
        "target_impacts":
            target_impacts,
        "target_emission_impacts":
            target_emission_impacts,
        "target_emission_chart_data":
            target_emission_chart_data,
        "scope_summary":
            scope_summary,
        "impact_statistics":
            impact_statistics,
        "scope_chart_data":
            scope_chart_data,
        "top_sector_chart_data":
            top_sector_chart_data,

    }

def get_scenario_history_v21(db):

    cursor = db.connection.cursor(MySQLdb.cursors.DictCursor)

    q_str = """
    SELECT
        sh.scenario_history_id,
        sh.scenario_id,
        sh.scenario_code,
        sh.created_time,
        sh.year,
        sh.scenario_name,
        sh.baseline_emission,
        sh.scenario_emission,
        sh.percentage_change,
        sh.emission_difference
    FROM scenario_history_v21 sh
    ORDER BY
        sh.created_time DESC
    """

    cursor.execute(q_str)
    rows = cursor.fetchall()
    cursor.close()

    return rows

def get_scenario_history_list_v21(db):

    cursor = db.connection.cursor(MySQLdb.cursors.DictCursor)

    q_str = """
    SELECT
        scenario_history_id,
        scenario_id,
        scenario_code,
        scenario_name,
        year,
        created_time,
        baseline_emission,
        scenario_emission,
        percentage_change
    FROM scenario_history_v21
    ORDER BY
        created_time DESC
    """

    cursor.execute(q_str)
    rows = cursor.fetchall()
    cursor.close()

    return rows

def compare_scenario_reports_v21(
        report_a,
        report_b
    ):
    from util_scenario import compare_scenario_reports

    comparison = (
        compare_scenario_reports(
            report_a,
            report_b
        )
    )

    comparison["scenario_a_target"] = (
        report_a.metadata.get("target_summary", [])
    )
    comparison["scenario_b_target"] = (
        report_b.metadata.get("target_summary", [])
    )
    comparison["scenario_a_energy_impact"] = (
        report_a.target_impacts
    )
    comparison["scenario_b_energy_impact"] = (
        report_b.target_impacts
    )

    return comparison

def create_scenario_v21(
        db,
        scenario,
        return_status=False
    ):
    # --------------------------------------------------
    # v2.1:
    # Scenario type is classification metadata only.
    # Authoring always uses CUSTOM.
    # 2006-10-03
    # --------------------------------------------------

    scenario = dict(scenario) #scenario definition
    scenario["scenario_types"] = [
        {
            "type_code": CUSTOM_SCENARIO_TYPE_CODE_V21,
            "is_primary": True
        }
    ]

    # ---------------------------------------------
    # Validate scenario definition
    # ---------------------------------------------
    validate_scenario_definition_v21(db, scenario)

    # ---------------------------------------------
    # Build normalized scenario signature
    # ---------------------------------------------
    scenario_signature = (
        build_scenario_definition_signature(scenario)
    )

    # ---------------------------------------------
    # Check whether identical definition exists
    # ---------------------------------------------
    existing_scenario_id = (
        find_existing_scenario_definition_v21(db, scenario_signature)
    )

    # ---------------------------------------------
    # Existing scenario definition
    # ---------------------------------------------
    if existing_scenario_id is not None:
        if return_status:
            return {
                "scenario_id":
                    existing_scenario_id,
                "created":
                    False
            }

        return existing_scenario_id

    # ---------------------------------------------
    # Create new scenario definition
    # ---------------------------------------------
    scenario_id = (
        insert_scenario_definition_v21(
            db,
            scenario,
            scenario_signature
        )
    )

    # ---------------------------------------------
    # Insert scenario types
    # ---------------------------------------------
    insert_scenario_types_v21(
        db,
        scenario_id,
        scenario["scenario_types"]
    )

    # ---------------------------------------------
    # Insert parameters and targets
    # ---------------------------------------------
    for parameter in scenario["parameters"]:
        parameter_id = (
            insert_parameter_v21(
                db,
                scenario_id,
                parameter
            )
        )
        insert_target_v21(
            db,
            parameter_id,
            parameter
        )
    db.connection.commit()

    # ---------------------------------------------
    # Return result
    # ---------------------------------------------
    if return_status:
        return {
            "scenario_id":
                scenario_id,
            "created":
                True
        }

    return scenario_id

def insert_parameter_v21(
        db,
        scenario_id,
        parameter
    ):

    q_str = """
    INSERT INTO scenario_parameter
    (
        scenario_id,
        parameter_code,
        parameter_value,
        unit
    )
    VALUES
    (%s,%s,%s,%s)
    """
    cursor = db.connection.cursor()

    cursor.execute(
        q_str,
        (
            scenario_id,
            parameter["parameter_code"],
            parameter["value"],
            parameter["unit"]
        )
    )

    db.connection.commit()
    parameter_id = cursor.lastrowid
    cursor.close()

    return parameter_id

def insert_target_v21(
        db,
        parameter_id,
        parameter
    ):

    q_str = """
    INSERT INTO scenario_target
    (
        parameter_id,
        energy_type_code,
        sector_code,
        target_scope
    )
    VALUES
    (%s,%s,%s,%s)
    """

    cursor = db.connection.cursor()

    cursor.execute(
        q_str,
        (
            parameter_id,
            parameter["energy_type_code"],
            parameter.get(
                "sector_code"
            ),
            parameter["target_scope"]
        )
    )
    db.connection.commit()

    cursor.close()

def validate_scenario_definition_v21(db, scenario):
    # -----------------------------------------
    # Required fields
    # -----------------------------------------
    req_fields = {
        "scenario_name": "Scenario Name",
        #"scenario_year": "Scenario Year",
        "description": "Description",
        "scenario_types": "Scenario Types"
        }
    for f in req_fields:
        if not scenario.get(f):
            raise ValueError(
                f"Please enter a {req_fields[f]}."
            )


    parameters = scenario.get("parameters", [])
    if len(parameters) == 0:
        raise ValueError(
            "Please add at least one Scenario Parameter."
        )

    # -----------------------------
    # Parameter-level validation
    # -----------------------------
    #for p in parameters:
    #    validate_parameter_v21(p)

    """
    scenario_types =  scenario.get("scenario_types", [])
    for st in scenario_types:
        validate_scenario_type_v21(db, st["type_code"])
    """
    for idx, p in enumerate(parameters, start=1):
        try:
            #validate_parameter_v21(p, scenario_type_codes=scenario_type_codes)
            validate_parameter_v21(p)
        except ValueError as exc:
            raise ValueError(
                f"Parameter row {idx}: {exc}"
            )

    seen_targets = set()
    for idx, p in enumerate(parameters, start=1):
        parameter_code = p.get("parameter_code")
        energy_type_code = p.get("energy_type_code")

        if energy_type_code is not None:
            energy_type_code = (str(energy_type_code).strip())

        target_scope = p.get("target_scope")
        sector_code = p.get("sector_code")

        if target_scope == "ENERGY":
            sector_code = None

        elif sector_code is not None:
            sector_code = (str(sector_code).strip())

        duplicate_key = (
            parameter_code,
            energy_type_code,
            target_scope,
            sector_code
        )
        is_duplicate = (
            duplicate_key in seen_targets
        )
        #print("--duplicate check row:",idx)
        #print("--duplicate key:",duplicate_key)
        #print("--seen targets:",seen_targets)

        if is_duplicate:
            print(
                "-- DUPLICATE DETECTED --"
            )
            sector_display = (
                "All sectors"
                if target_scope == "ENERGY"
                else sector_code
            )
            return (
                False,
                f"Duplicate target in "
                f"parameter row {idx}: "
                f"{parameter_code}, "
                f"energy={energy_type_code}, "
                f"scope={target_scope}, "
                f"sector={sector_display}."
            )
        seen_targets.add(
            duplicate_key
        )

    is_valid = True
    message = None

    return is_valid, message

def validate_scenario_type_v21(db,
        scenario_type
    ):

    active_types = get_active_scenario_types_v21(db)
    allowed_types = [
        st["code"]
        for st in active_types
    ]

    if scenario_type not in allowed_types:
        raise ValueError(
            f"Unsupported scenario type: {scenario_type}"
        )


def validate_parameter_v21(p, scenario_type_codes=None):
    parameter_code = p.get("parameter_code")
    target_scope = p.get("target_scope")
    energy_type_code = p.get("energy_type_code")
    #sector_code = p.get("sector_code") #dropdown has been handled by HTML-v5
    value = p.get("value")

    if parameter_code not in PARAMETER_RULES_V21:
        raise ValueError(
            f"Unsupported parameter code: "
            f"{parameter_code}"
        )

    rule = PARAMETER_RULES_V21[parameter_code]
    if not rule.get("implemented", False):
        raise ValueError(
            f"Parameter '{parameter_code}' "
            "is not yet implemented."
        )

    if target_scope not in rule["allowed_scopes"]:
        raise ValueError(
            f"{parameter_code} does not support "
            f"target scope {target_scope}"
        )

    if energy_type_code not in rule["allowed_energy_types"]:
        raise ValueError(
            f"{parameter_code} does not support "
            f"energy type {energy_type_code}"
        )

    if value is None:
        raise ValueError(f"Please enter a value for {parameter_code}")
    elif value <= -1.0:
        raise ValueError(f"{parameter_code} cannot be <= -100%")
    elif value >= 1.0:
        raise ValueError(f"{parameter_code} cannot be >= 100%")

    return True

def build_scenario_definition_signature(scenario):
    """
    Build a canonical SHA-256 signature for the computational definition of
    a v2.1 scenario.
    Scenario type is intentionally excluded because it is classification metadata.
    2026-10-03
    """
    normalized_parameters = []

    for p in scenario.get("parameters", []):
        value = p.get("value")

        if value is not None:
            try:
                value = round(float(value), 12)
            except (TypeError, ValueError):
                value = str(ValueError).strip()

        normalized_parameters.append(
            {
                "parameter_code":
                    p.get("parameter_code"),
                "value":
                    value,
                "unit":
                    p.get("unit"),
                "energy_type_code":
                    p.get("energy_type_code"),
                "sector_code":
                    p.get("sector_code"),
                "target_scope":
                    p.get("target_scope")
            }
        )

    # Parameter order must not affect
    # scenario identity.
    normalized_parameters.sort(
        key=lambda p: (
            "" if p["parameter_code"] is None
            else str(p["parameter_code"]),

            "" if p["energy_type_code"] is None
            else str(p["energy_type_code"]),

            "" if p["target_scope"] is None
            else str(p["target_scope"]),

            "" if p["sector_code"] is None
            else str(p["sector_code"]),

            "" if p["unit"] is None
            else str(p["unit"]),

            "" if p["value"] is None
            else str(p["value"])
        )
    )

    signature_data = {
        #"scenario_year": int(scenario["scenario_year"]),
        "parameters": normalized_parameters
    }

    signature_text = json.dumps(
        signature_data,
        sort_keys=True,
        separators=(",", ":")
    )

    return hashlib.sha256(
        signature_text.encode("utf-8")
    ).hexdigest()

def find_existing_scenario_definition_v21(
        db,
        scenario_signature
    ):

    q_str = f"""
    SELECT
        scenario_id
    FROM scenario_definition
    WHERE scenario_signature =
          '{scenario_signature}'
    LIMIT 1
    """

    df = tbl_to_df(db, q_str)
    if len(df) == 0:
        return None

    return int(
        df.iloc[0]["scenario_id"]
    )

def insert_scenario_types_v21(
        db,
        scenario_id,
        scenario_types
    ):

    q_str = """
    INSERT INTO scenario_type_mapping
    (
        scenario_id,
        scenario_type_id,
        is_primary
    )
    VALUES
    (%s,%s,%s)
    """
    cursor = db.connection.cursor()

    for t in scenario_types:
        type_id = get_scenario_type_id(
            db,
            t["type_code"]
        )
        cursor.execute(
            q_str,
            (
                scenario_id,
                type_id,
                t.get(
                    "is_primary",
                    False
                )
            )
        )

    db.connection.commit()
    cursor.close()

def insert_scenario_definition_v21(
        db,
        scenario,
        scenario_signature
    ):

    q_str = """
    INSERT INTO scenario_definition
    (
        scenario_name,
        description,
        scenario_year,
        scenario_signature
    )
    VALUES
    (
        %s,%s,%s,%s
    )
    """
    cursor = db.connection.cursor()
    cursor.execute(
        q_str,
        (
            scenario["scenario_name"],
            scenario.get("description"),
            scenario.get("scenario_year"),
            scenario_signature
        )
    )

    #db.connection.commit()
    scenario_id = cursor.lastrowid
    cursor.close()

    return scenario_id


def get_scenario_type_id(
        db,
        type_code
    ):

    q_str = f"""
    SELECT
        scenario_type_id
    FROM scenario_type
    WHERE type_code =
    '{type_code}'
    """

    df = tbl_to_df(
        db,
        q_str
    )

    if len(df)==0:
        raise ValueError(
            f"Unknown scenario type {type_code}"
        )

    return int(
        df.iloc[0]
        ["scenario_type_id"]
    )

def get_scenario_definition_v21(
        db,
        scenario_id
    ):

    cursor = db.connection.cursor(
        MySQLdb.cursors.DictCursor
    )

    # ---------------------------------
    # Main definition
    # ---------------------------------
    q_str = """
    SELECT
        scenario_id,
        scenario_name,
        scenario_year,
        description,
        scenario_signature
    FROM scenario_definition
    WHERE scenario_id=%s
    """

    cursor.execute(
        q_str,
        (scenario_id,)
    )

    scenario = cursor.fetchone()

    if scenario is None:
        cursor.close()
        return None

    # ---------------------------------
    # Parameters
    # ---------------------------------

    q_str = """
    SELECT
        sp.parameter_code,
        sp.parameter_value,
        sp.unit,
        st.energy_type_code,
        st.sector_code,
        st.target_scope
    FROM scenario_parameter sp
    LEFT JOIN scenario_target st
        ON sp.parameter_id =
           st.parameter_id
    WHERE sp.scenario_id=%s
    ORDER BY
        sp.parameter_id
    """

    cursor.execute(
        q_str,
        (scenario_id,)
    )

    scenario["parameters"] = (
        cursor.fetchall()
    )

    cursor.close()

    return scenario

def get_available_parameter_rules_v21():
    return [
        {
            "parameter_code": key,
            "implemented": value["implemented"],
            "label": value["label"]
        }
        for key, value
        in PARAMETER_RULES_V21.items()
        if value["implemented"]
    ]


def get_active_scenario_types_v21(db):

    q_str = """
        SELECT
            scenario_type_id,
            type_code,
            type_name,
            description
        FROM scenario_type
        WHERE is_active = 1
        ORDER BY scenario_type_id
    """

    cursor = db.connection.cursor(
        MySQLdb.cursors.DictCursor
    )

    cursor.execute(
        q_str
    )

    rows = cursor.fetchall() #tuple of dictionary
    cursor.close()

    scenario_types_list = [
        {
            "code": item["type_code"],
            "label": item["type_name"],
            "implemented": True
        }
        for item in rows
    ]

    return scenario_types_list


def benchmark_scenario_v21(
        db,
        model,
        scenario_id,
        df_sector,
        build_repeats=3,
        calc_repeats=5,
        load_repeats=5
    ):
    from util_mat import build_model_year
    from time import perf_counter
    from statistics import median

    print()
    print("=" * 60)
    print("V2.1 PERFORMANCE BENCHMARK")
    print("=" * 60)

    # --------------------------------------------------
    # A. Model construction
    # --------------------------------------------------

    build_times = []
    fname_io = (
        f"io_ind_{model.year}.csv"
    )

    for _ in range(
        build_repeats
    ):

        t0 = perf_counter()
        _model_test = (
            build_model_year(
                db=db,
                fname_io=fname_io,
                year=model.year
            )
        )
        elapsed = (
            perf_counter()
            -
            t0
        )
        build_times.append(
            elapsed
        )

    # --------------------------------------------------
    # B. Scenario calculation
    #    Uses already-built model
    # --------------------------------------------------

    calc_times = []
    for _ in range(
        calc_repeats
    ):

        t0 = perf_counter()
        report = (
            run_scenario_v21(
                db,
                model,
                scenario_id,
                df_sector
            )
        )
        elapsed = (
            perf_counter()
            -
            t0
        )
        calc_times.append(
            elapsed
        )

    # --------------------------------------------------
    # C. Existing-history load
    # --------------------------------------------------

    load_times = []

    for _ in range(
        load_repeats
    ):

        t0 = perf_counter()
        result = (
            run_or_load_scenario_v21(
                db,
                model,
                scenario_id,
                df_sector
            )
        )
        elapsed = (
            perf_counter()
            -
            t0
        )
        load_times.append(
            elapsed
        )

    # --------------------------------------------------
    # Results
    # --------------------------------------------------
    build_median = median(
        build_times
    )
    calc_median = median(
        calc_times
    )
    load_median = median(
        load_times
    )

    print()
    print(
        "--model build times:",
        build_times
    )
    print(
        "--scenario calculation times:",
        calc_times
    )
    print(
        "--existing-history load times:",
        load_times
    )

    print()
    print(
        "--median model build:",
        f"{build_median:.4f} s"
    )
    print(
        "--median scenario calculation:",
        f"{calc_median:.4f} s"
    )
    print(
        "--median existing-history load:",
        f"{load_median:.4f} s"
    )
    print(
        "--approximate first-run calculation:",
        f"{build_median + calc_median:.4f} s"
    )

    if load_median > 0:
        print(
            "--approximate first-run/load ratio:",
            f"{(build_median + calc_median) / load_median:.2f}x"
        )

    print(
        "=" * 60
    )
    print()

def get_parameter_options_by_scenario_type():

    result = {}
    for parameter_code, rule in PARAMETER_RULES_V21.items():
        if not rule.get("implemented"):
            continue

        for scenario_type in rule.get(
                "allowed_scenario_types",
                []
            ):
            result.setdefault(
                scenario_type,
                []
            ).append(
                {
                    "code":
                        parameter_code,
                    "label":
                        rule.get(
                            "label",
                            parameter_code
                        )
                }
            )

    return result

def get_implemented_parameter_options_v21():
    """
    Return all currently implemented parameter codes
    for the v2.1 authoring UI.
    Scenario type is intentionally not used here.
    """

    options = []
    for code, rule in PARAMETER_RULES_V21.items():
        if not rule.get("implemented", False):
            continue

        options.append(
            {
                "code": code,
                "name": rule.get(
                    "label",
                    code
                )
            }
        )

    options.sort(
        key=lambda x:
            x["name"].lower()
    )

    return options

# ============================================================
# Scenario Definition Display Helpers
# ============================================================

def format_parameter_item(
        parameter,
        energy_lookup=None,
        sector_lookup=None
    ):
    """
    Convert one scenario parameter into a compact
    human-readable summary string.
    """

    parameter_code = (
        parameter.get("parameter_code")
    )

    energy_code = (
        parameter.get("energy_type_code")
    )

    target_scope = (
        parameter.get("target_scope")
    )

    sector_code = (
        parameter.get("sector_code")
    )

    parameter_label = (
        PARAMETER_MNEMONICS.get(
            parameter_code,
            parameter_code
        )
    )

    energy_label = (
        ENERGY_MNEMONICS.get(
            energy_code,
            energy_code
        )
    )

    if target_scope == "ENERGY":

        target_label = (
            TARGET_SCOPE_MNEMONICS[
                "ENERGY"
            ]
        )

    elif target_scope == "SECTOR_ENERGY":

        target_label = (
            f"{TARGET_SCOPE_MNEMONICS['SECTOR_ENERGY']}"
            f"{sector_code}"
        )

    else:

        target_label = target_scope

    value = float(
        parameter.get("value", 0)
    ) * 100

    return (
        f"{parameter_label} · "
        f"{energy_label} · "
        f"{target_label} · "
        f"{value:g}%"
    )


# ============================================================
# Scenario Definition Display Helpers
# ============================================================
def format_parameter_item(
        parameter
    ):
    """
    Convert one ScenarioParameter object into
    a compact human-readable summary.
    """

    parameter_code = (
        parameter.parameter_code
    )
    energy_code = (
        parameter.energy_type_code
    )
    target_scope = (
        parameter.target_scope
    )
    sector_code = (
        parameter.sector_code
    )

    # ---------------------------------------------
    # Parameter mnemonic
    # ---------------------------------------------
    parameter_label = (
        PARAMETER_MNEMONICS.get(
            parameter_code,
            parameter_code
        )
    )

    # ---------------------------------------------
    # Energy mnemonic
    # ---------------------------------------------
    if energy_code is not None:
        energy_code = str(
            energy_code
        ).zfill(2)
    energy_label = (
        ENERGY_MNEMONICS.get(
            energy_code,
            energy_code
        )
    )

    # ---------------------------------------------
    # Target
    # ---------------------------------------------
    if target_scope == "ENERGY":
        target_label = (
            TARGET_SCOPE_MNEMONICS.get("ENERGY", "All")
        )

    elif target_scope == "SECTOR_ENERGY":
        if sector_code:
            sector_code = str(
                sector_code
            ).zfill(3)
            target_label = (
                f"{TARGET_SCOPE_MNEMONICS.get('SECTOR_ENERGY', 'S')}{sector_code}"
            )
        else:
            target_label = "S?"
    else:
        target_label = target_scope

    # ---------------------------------------------
    # Change value
    # ---------------------------------------------
    value = parameter.value
    if value is None:
        value_label = "?"
    else:
        value_label = (
            f"{float(value) * 100:g}%"
        )

    # ---------------------------------------------
    # Final compact representation
    # ---------------------------------------------
    return (
        f"{parameter_label} · "
        f"{energy_label} · "
        f"{target_label} · "
        f"{value_label}"
    )


def format_parameter_summary(
        parameters
    ):
    """
    Convert all scenario parameters into a compact
    human-readable scenario summary.
    """
    if not parameters:
        return ""

    return "; ".join(
        format_parameter_item(
            parameter
        )
        for parameter in parameters
    )