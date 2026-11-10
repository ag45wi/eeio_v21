import MySQLdb
import pandas as pd

from util_db import tbl_to_df
from eeio_engine import calculate_impact_statistics, aggregate_sector_impacts, ScenarioReport

def save_scenario_report(
        db,
        report
    ):
    """
    Save ScenarioReport into MySQL.

    Returns:
        scenario_id
    """

    total = report.summary["totalEmission"]
    scope1 = report.summary["emissionScope1"]
    scope2 = report.summary["emissionScope2"]
    scope3 = report.summary["emissionScope3"]

    q_str = """
    INSERT INTO scenario_history
    (
        year,
        scenario_code,
        scenario_name,
        number_of_sectors,
        fuel_demand_change,
        electricity_demand_change,
        electricity_emission_factor_change,
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
        %s,%s,%s,
        %s,%s,%s,%s,
        %s,%s,%s,%s,
        %s,%s,%s,%s,%s,%s
    )
    """
    params = (
        report.metadata["year"],
        report.metadata["scenario_code"],
        report.metadata["scenario_name"],
        report.metadata["number_of_sectors"],
        report.metadata["parameters"]["fuel_demand_change"],
        report.metadata["parameters"]["electricity_demand_change"],
        report.metadata["parameters"]["electricity_emission_factor_change"],
        total["baseline_total"],
        total["scenario_total"],
        total["difference_total"],
        total["percentage_change"],
        scope1["baseline_total"], scope1["scenario_total"],
        scope2["baseline_total"], scope2["scenario_total"],
        scope3["baseline_total"], scope3["scenario_total"]
    )

    cursor = db.connection.cursor()
    cursor.execute(
        q_str,
        params
    )
    db.connection.commit()

    scenario_id = (
        cursor.lastrowid
    )

    cursor.close()

    return scenario_id

def save_sector_results(
        db,
        scenario_id,
        sector_results
    ):
    """
    Save detailed sector impact.
    """

    q_str = """
    INSERT INTO scenario_sector_result
    (
        scenario_id,
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

    for _, row in (
        sector_results
        .iterrows()
        ):
        cursor.execute(
            q_str,
            (
                scenario_id,
                row["sector_code"],
                row["sector_name"],
                row["sector_agg"],
                row["baseline"],
                row["scenario"],
                row["difference"],
                row["reduction"],
                row["percentage_change"]
            )
        )

    db.connection.commit()

    cursor.close()

def scenario_exists(
        db,
        scenario_code
    ):
    """
    Check existing scenario.
    """

    q_str = """
    SELECT COUNT(*) AS cnt
    FROM scenario_history
    WHERE scenario_code=%s
    """
    cursor = (
        db.connection.cursor(
            MySQLdb.cursors.DictCursor
        )
    )
    cursor.execute(
        q_str,
        (scenario_code,)
    )

    result = cursor.fetchone()
    cursor.close()

    return result["cnt"] > 0

def save_complete_scenario_report__PREV(
        db,
        report
    ):
    """
    Save complete ScenarioReport.
    """
    scenario_id = (
        save_scenario_report(
            db,
            report
        )
    )

    save_sector_results(
        db,
        scenario_id,
        report.sector_results
    )

    return scenario_id

def save_complete_scenario_report(
        db,
        report
    ):
    """
    Save complete ScenarioReport.
    """
    scenario_id = (
        save_scenario_report(
            db,
            report
        )
    )

    save_sector_results(
        db,
        scenario_id,
        report.sector_results
    )

    return scenario_id


def get_scenario_history(
        db
    ):
    """
    Retrieve saved scenario list.
    """

    q_str = """
    SELECT
        scenario_id,
        created_time,
        year,
        scenario_code,
        scenario_name,
        baseline_emission,
        scenario_emission,
        emission_difference,
        percentage_change
    FROM scenario_history
    ORDER BY
        created_time DESC
    """

    cursor = (
        db.connection.cursor(
            MySQLdb.cursors.DictCursor
        )
    )

    cursor.execute(
        q_str
    )

    rows = cursor.fetchall()
    cursor.close()

    return rows

def get_saved_scenario(
        db,
        scenario_id
    ):
    """
    Load scenario metadata.
    """

    q_str = """
    SELECT *
    FROM scenario_history
    WHERE scenario_id=%s
    """

    cursor = (
        db.connection.cursor(
            MySQLdb.cursors.DictCursor
        )
    )

    cursor.execute(
        q_str,
        (scenario_id,)
    )

    result = cursor.fetchone()

    cursor.close()

    return result

def get_saved_sector_results(
        db,
        scenario_id
    ):
    """
    Load sector impact dataframe.
    """

    q_str = """
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
    WHERE scenario_id=%s
    """

    df = tbl_to_df(
        db,
        q_str.replace(
            "%s",
            f"'{scenario_id}'"
        )
    )

    return df

def load_scenario_report(
        db,
        scenario_id
    ):
    scenario = get_saved_scenario(
        db,
        scenario_id
    )
    #print ("--scenario--", scenario)

    sector_results = (
        get_saved_sector_results(
            db,
            scenario_id
        )
    )

    summary = {
        "emissionScope1": {
            "baseline_total":
                scenario["baseline_scope1"],
            "scenario_total":
                scenario["scenario_scope1"],
            "difference_total":
                (
                    scenario["scenario_scope1"]
                    -
                    scenario["baseline_scope1"]
                ),
            "percentage_change":
                (
                    (
                    scenario["scenario_scope1"]
                    -
                    scenario["baseline_scope1"]
                    )
                    /
                    scenario["baseline_scope1"]
                    *
                    100
                )
        },
        "emissionScope2": {
            "baseline_total":
                scenario["baseline_scope2"],
            "scenario_total":
                scenario["scenario_scope2"],
            "difference_total":
                (
                    scenario["scenario_scope2"]
                    -
                    scenario["baseline_scope2"]
                ),
            "percentage_change":
                (
                    (
                    scenario["scenario_scope2"]
                    -
                    scenario["baseline_scope2"]
                    )
                    /
                    scenario["baseline_scope2"]
                    *
                    100
                )
        },
        "emissionScope3": {
            "baseline_total":
                scenario["baseline_scope3"],
            "scenario_total":
                scenario["scenario_scope3"],
            "difference_total":
                (
                    scenario["scenario_scope3"]
                    -
                    scenario["baseline_scope3"]
                ),
            "percentage_change":
                (
                    (
                    scenario["scenario_scope3"]
                    -
                    scenario["baseline_scope3"]
                    )
                    /
                    scenario["baseline_scope3"]
                    *
                    100
                )
        },

        "totalEmission": {
            "baseline_total":
                scenario[
                    "baseline_emission"
                ],
            "scenario_total":
                scenario[
                    "scenario_emission"
                ],
            "difference_total":
                scenario[
                    "emission_difference"
                ],
            "percentage_change":
                scenario[
                    "percentage_change"
                ]
        }
    }

    comparison = None

    impact_statistics = (
        calculate_impact_statistics(
            sector_results
        )
    )

    aggregate_results = (
        aggregate_sector_impacts(
            sector_results
        )
    )

    report = ScenarioReport(
        metadata={
            "year":
                scenario["year"],
            "scenario_code":
                scenario["scenario_code"],
            "scenario_name":
                scenario["scenario_name"],
            "model_type":
                "EEIO",
            "scenario_version":
                "v1",
            "number_of_sectors":
                scenario["number_of_sectors"],
            "parameters":
            {
                "fuel_demand_change": scenario["fuel_demand_change"],
                "electricity_demand_change": scenario["electricity_demand_change"],
                "electricity_emission_factor_change": scenario["electricity_emission_factor_change"]
            }
        },

        summary=summary,
        comparison=comparison,
        sector_results=sector_results,
        aggregate_results=aggregate_results,
        impact_statistics=impact_statistics
    )

    return report

def prepare_scenario_display(report):
    """
    Convert ScenarioReport into
    HTML-friendly objects.
    """

    total_summary = (
        report.summary["totalEmission"]
    )

    scenario_summary = {
        "year":
            report.metadata["year"],
        "scenario_name":
            report.metadata["scenario_name"],
        "scenario_code":
            report.metadata["scenario_code"],
        "baseline_total":
            total_summary["baseline_total"],
        "scenario_total":
            total_summary["scenario_total"],
        "difference_total":
            total_summary["difference_total"],
        "percentage_change":
            total_summary["percentage_change"]
    }

    scope_summary = {}
    for key, value in (
        report.summary.items()
        ):
        scope_summary[key] = value

    impact_statistics = (
        report.impact_statistics
    )

    scope_chart_data = {}
    #print("--report.summary", report.summary)

    for key, value in report.summary.items():
        if key in [
            "emissionScope1",
            "emissionScope2",
            "emissionScope3",
            "totalEmission"
            ]:
            scope_chart_data[key] = {
                "baseline":
                    value["baseline_total"]
                    /
                    1_000_000,
                "scenario":
                    value["scenario_total"]
                    /
                    1_000_000
            }

    top_sector_chart_data = (
        report
        .impact_statistics
        [
            "top_reduction_sectors"
        ]
    )

    scenario_parameters = {
        "fuel_demand_change":
            report.metadata["parameters"]["fuel_demand_change"],
        "electricity_demand_change":
            report.metadata["parameters"]["electricity_demand_change"],
        "electricity_emission_factor_change":
            report.metadata["parameters"]["electricity_emission_factor_change"]
    }

    return {
        "scenario_summary":
            scenario_summary,
        "scope_summary":
            scope_summary,
        "impact_statistics":
            impact_statistics,
        "scope_chart_data":
            scope_chart_data,
        "top_sector_chart_data":
            top_sector_chart_data,
        "scenario_parameters":
            scenario_parameters
    }

def compare_scenario_reports(
        report_a,
        report_b
    ):
    """
    Compare two ScenarioReport objects.
    """
    comparison = {}
    metrics = [
        "emissionScope1",
        "emissionScope2",
        "emissionScope3",
        "totalEmission"
    ]

    chart_labels = []
    chart_a = []
    chart_b = []

    for metric in metrics:
        result_a = (report_a.summary[metric])
        result_b = (report_b.summary[metric])
        comparison[metric] = {
            "scenario_a": result_a["scenario_total"],
            "scenario_b": result_b["scenario_total"],
            #"difference": (result_b["scenario_total"] - result_a["scenario_total"])
            "difference": (result_a["scenario_total"] - result_b["scenario_total"])
        }

        chart_labels.append(metric)
        chart_a.append(result_a["scenario_total"] / 1_000_000)
        chart_b.append(result_b["scenario_total"] / 1_000_000)

    comparison["chart_data"] = {
        "labels": chart_labels,
        "scenario_a": chart_a,
        "scenario_b": chart_b
    }

    return comparison

def get_existing_scenario_id(
        db,
        scenario_code
    ):
    """
    Retrieve existing scenario_id
    based on scenario_code.
    """
    q_str = """
    SELECT
        scenario_id
    FROM scenario_history
    WHERE scenario_code=%s
    LIMIT 1
    """

    cursor = (
        db.connection.cursor(
            MySQLdb.cursors.DictCursor
        )
    )

    cursor.execute(
        q_str,
        (scenario_code,)
    )

    result = cursor.fetchone()
    cursor.close()

    if result:
        return result["scenario_id"]
    else:
        return None

def get_scenario_definitions(
        db
    ):
    """
    Retrieve available scenarios.
    """
    q_str = """
    SELECT
        scenario_name,
        category,
        description,
        fuel_demand_change,
        electricity_demand_change,
        electricity_emission_factor_change
    FROM scenario_definition
    ORDER BY scenario_name
    """

    return tbl_to_df(
        db,
        q_str
    )

def get_scenario_list(db):
    q_str = """
    SELECT
        scenario_name,
        category,
        description,
        fuel_demand_change,
        electricity_demand_change,
        electricity_emission_factor_change
    FROM scenario_definition
    ORDER BY scenario_name
    """

    return tbl_to_df(
        db,
        q_str
    )

def get_scenario_definition(
        db,
        scenario_name
    ):
    """
    Retrieve one scenario definition.
    """

    q_str = f"""
    SELECT *
    FROM scenario_definition
    WHERE scenario_name =
    '{scenario_name}'
    """
    df = tbl_to_df(db,
        q_str
    )

    if len(df)==0:
        raise ValueError(
            "Scenario not found"
        )

    #return df.iloc[0].to_dict()
    result = df.iloc[0].to_dict()

    scenario_definition = {
        "scenario_name": result["scenario_name"],
        "category": result["category"],
        "description": result["description"],
        "parameters":
        {
            "fuel_demand_change": result["fuel_demand_change"],
            "electricity_demand_change": result["electricity_demand_change"],
            "electricity_emission_factor_change": result["electricity_emission_factor_change"]
        }
    }

    return scenario_definition

def format_scenario_options(
        df_scenarios
    ):
    """
    Convert scenario dataframe into
    JavaScript-friendly dictionary.
    """

    return {
        row["scenario_name"]:
        {
            "fuel_demand_change":
                row[
                    "fuel_demand_change"
                ],
            "electricity_demand_change":
                row[
                    "electricity_demand_change"
                ],
            "electricity_emission_factor_change":
                row[
                    "electricity_emission_factor_change"
                ]
        }

        for _, row
        in df_scenarios.iterrows()
    }

def insert_scenario_definition(
        db,
        scenario_definition
    ):
    """
    Insert a new scenario definition.
    """

    q_str = """
    INSERT INTO scenario_definition
    (
        scenario_name,
        category,
        description,
        fuel_demand_change,
        electricity_demand_change,
        electricity_emission_factor_change
    )
    VALUES
    (
        %s,%s,%s,%s,%s,%s
    )
    """

    cursor = (
        db.connection.cursor()
    )

    cursor.execute(
        q_str,
        (
            scenario_definition[
                "scenario_name"
            ],
            scenario_definition[
                "category"
            ],
            scenario_definition[
                "description"
            ],
            scenario_definition[
                "fuel_demand_change"
            ],
            scenario_definition[
                "electricity_demand_change"
            ],
            scenario_definition[
                "electricity_emission_factor_change"
            ]
        )
    )

    db.connection.commit()
    scenario_id = (
        cursor.lastrowid
    )
    cursor.close()

    return scenario_id

def validate_scenario_definition(
        scenario
    ):
    """
    Validate scenario definition.

    Returns:
        (True, None)
        or
        (False, error_message)
    """

    required_fields = [
        "scenario_name",
        "category",
        "description",
        "fuel_demand_change",
        "electricity_demand_change",
        "electricity_emission_factor_change"
    ]

    # ---------------------------------
    # Check required fields
    # ---------------------------------
    for field in required_fields:
        if field not in scenario:
            return (
                False,
                f"Missing field: {field}"
            )

    # ---------------------------------
    # Check numeric values
    # ---------------------------------
    numeric_fields = [
        "fuel_demand_change",
        "electricity_demand_change",
        "electricity_emission_factor_change"
    ]

    for field in numeric_fields:
        if not isinstance(
            scenario[field],
            (int,float)
            ):
            return (
                False,
                f"{field} must be numeric"
            )

    # ---------------------------------
    # Check reasonable range
    # ---------------------------------

    for field in numeric_fields:
        value = scenario[field]

        if value < -1:
            return (
                False,
                f"{field} cannot be less than -100%"
            )
        if value > 5:
            return (
                False,
                f"{field} exceeds reasonable range"
            )

    return (True, None)

def scenario_definition_exists(
        db,
        scenario_name
    ):
    """
    Check duplicate scenario definition.
    """

    q_str = """
    SELECT COUNT(*) AS cnt
    FROM scenario_definition
    WHERE scenario_name=%s
    """
    cursor = (
        db.connection.cursor(
            MySQLdb.cursors.DictCursor
        )
    )
    cursor.execute(
        q_str,
        (scenario_name,)
    )
    result = cursor.fetchone()
    cursor.close()

    return result["cnt"] > 0