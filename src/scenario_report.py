from util_db import tbl_to_df
import MySQLdb
import numpy as np
import pandas as pd


def analyze_target_impacts(
        parameters,
        baseline_finCons,
        scenario_finCons,
        energy_mapping,
        sector_column_map,
        energy_lookup=None,
        sector_lookup=None
    ):
    """
    Quantify the direct physical energy change at each scenario target.
    This describes the intervention itself, not its EEIO emission effects.
    """

    impacts = []

    for p in parameters:
        if p.parameter_code != "energy_demand_change":
            continue

        energy_row = (
            energy_mapping[
                p.energy_type_code
            ]["finCons_row"]
        )

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

        # Economy-wide energy target
        if p.target_scope == "ENERGY":
            baseline_value = (
                baseline_finCons[
                    energy_row,
                    :
                ].sum()
            )
            scenario_value = (
                scenario_finCons[
                    energy_row,
                    :
                ].sum()
            )

        # Sector-specific energy target
        elif p.target_scope == "SECTOR_ENERGY":
            sector_column = (
                sector_column_map[
                    p.sector_code
                ]
            )
            baseline_value = (
                baseline_finCons[
                    energy_row,
                    sector_column
                ]
            )
            scenario_value = (
                scenario_finCons[
                    energy_row,
                    sector_column
                ]
            )
            item["sector_code"] = (
                p.sector_code
            )
            if sector_lookup:
                item["sector_name"] = (
                    sector_lookup[
                        p.sector_code
                    ]
                )

        else:
            continue

        item["energy_name"] = None
        if energy_lookup:
            item["energy_name"] = (
                energy_lookup[
                    p.energy_type_code
                ]
            )
        item["baseline_energy"] = (
            float(baseline_value)
        )
        item["scenario_energy"] = (
            float(scenario_value)
        )
        item["energy_difference"] = (
            float(scenario_value - baseline_value)
        )
        item["energy_reduction"] = (
            float(baseline_value - scenario_value)
        )

        if np.isclose(baseline_value, 0.0):
            item["percentage_change"] = None
        else:
            item["percentage_change"] = (
                float((scenario_value - baseline_value) / baseline_value * 100)
            )
        item["energy_unit"] = "TJ"

        impacts.append(item)

    return impacts

def save_target_impacts_v21(
        db,
        scenario_history_id,
        target_impacts
    ):

    if not target_impacts:
        return

    q_str = """
    INSERT INTO scenario_target_impact_history
    (
        scenario_history_id,
        parameter_code,
        energy_type_code,
        energy_name,
        sector_code,
        sector_name,
        target_scope,
        change_value,
        change_percent,
        baseline_energy,
        scenario_energy,
        energy_difference,
        energy_reduction,
        percentage_change,
        energy_unit
    )
    VALUES
    (
        %s,%s,%s,%s,%s,%s,%s,
        %s,%s,%s,%s,%s,%s,%s,%s
    )
    """
    cursor = db.connection.cursor()
    #cursor = db.connection.cursor(MySQLdb.cursors.DictCursor)

    print("--target_impacts", target_impacts, type(target_impacts))

    for item in target_impacts:
        cursor.execute(
            q_str,
            (
                scenario_history_id,
                item.get("parameter_code"),
                item.get("energy_type_code"),
                item.get("energy_name"),
                item.get("sector_code"),
                item.get("sector_name"),
                item.get("target_scope"),
                item.get("change"),
                item.get("change_percent"),
                item.get("baseline_energy"),
                item.get("scenario_energy"),
                item.get("energy_difference"),
                item.get("energy_reduction"),
                item.get("percentage_change"),
                item.get("energy_unit")
            )
        )

    db.connection.commit()
    cursor.close()


def get_saved_target_impacts_v21(
        db,
        scenario_history_id
    ):

    q_str = f"""
    SELECT
        parameter_code,
        energy_type_code,
        energy_name,
        sector_code,
        sector_name,
        target_scope,
        change_value,
        change_percent,
        baseline_energy,
        scenario_energy,
        energy_difference,
        energy_reduction,
        percentage_change,
        energy_unit
    FROM scenario_target_impact_history
    WHERE scenario_history_id =
          '{scenario_history_id}'
    ORDER BY target_impact_id
    """
    #print("-- inside get_saved_target_impacts_v21::q_str", q_str)

    df = tbl_to_df(
        db,
        q_str
    )
    if len(df) == 0:
        return None

    impacts = []
    for _, row in df.iterrows():
        percentage_change = (
            None
            if pd.isna(row["percentage_change"])
            else float(row["percentage_change"])
        )

        impacts.append(
            {
                "parameter_code":
                    row["parameter_code"],
                "energy_type_code":
                    row["energy_type_code"],
                "energy_name":
                    row["energy_name"],
                "sector_code":
                    row["sector_code"],
                "sector_name":
                    row["sector_name"],
                "target_scope":
                    row["target_scope"],
                "change":
                    float(
                        row["change_value"]
                    ),
                "change_percent":
                    float(
                        row["change_percent"]
                    ),
                "baseline_energy":
                    float(
                        row["baseline_energy"]
                    ),
                "scenario_energy":
                    float(
                        row["scenario_energy"]
                    ),
                "energy_difference":
                    float(
                        row["energy_difference"]
                    ),
                "energy_reduction":
                    float(
                        row["energy_reduction"]
                    ),
                "percentage_change":
                    percentage_change,
                "energy_unit":
                    row["energy_unit"]
            }
        )

    return impacts

def analyze_target_emission_impacts(
        parameters,
        sector_results
    ):

    """
Analyze the emission consequence of a scenario intervention.

    For SECTOR_ENERGY targets, the total sector-level emission
    reduction is decomposed into:
    1. target-sector impact, and
    2. rest-of-economy impact.

    For ENERGY targets, the intervention applies across all sectors.
    Therefore, target_sector_reduction represents the reduction
    within the target scope (all sectors), while
    other_sector_reduction is zero.

    This is separate from the model's Scope 1/2/3 decomposition.
    """

    impacts = []

    for p in parameters:

        # --------------------------------------------------
        # ENERGY scope
        # --------------------------------------------------
        if p.target_scope == "ENERGY":

            # For ENERGY scope, "target_sector_reduction"
            # means target-scope reduction across all sectors.

            total_reduction = float(
                sector_results["reduction"].sum()
            )
            impacts.append(
                {
                    "parameter_code":
                        p.parameter_code,
                    "energy_type_code":
                        p.energy_type_code,
                    "sector_code":
                        None,
                    "sector_name":
                        "All sectors",
                    "target_scope":
                        p.target_scope,
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
            continue


        if p.target_scope != "SECTOR_ENERGY":
            continue

        if p.sector_code is None:
            continue

        target_rows = sector_results[
            sector_results["sector_code"]
            == str(p.sector_code)
        ]

        if len(target_rows) == 0:
            raise ValueError(
                f"Sector result not found for "
                f"sector_code={p.sector_code}"
            )

        target_row = target_rows.iloc[0]
        target_sector_reduction = float(
            target_row["reduction"]
        )
        total_reduction = float(
            sector_results["reduction"].sum()
        )
        other_sector_reduction = (
            total_reduction
            -
            target_sector_reduction
        )
        target_share_pct = (
            target_sector_reduction
            /
            total_reduction
            *
            100
            if total_reduction != 0
            else 0.0
        )
        other_share_pct = (
            other_sector_reduction
            /
            total_reduction
            *
            100
            if total_reduction != 0
            else 0.0
        )
        impacts.append(
            {
                "parameter_code":
                    p.parameter_code,
                "energy_type_code":
                    p.energy_type_code,
                "sector_code":
                    p.sector_code,
                "sector_name":
                    target_row["sector_name"],
                "target_scope":
                    p.target_scope,
                "target_sector_reduction":
                    target_sector_reduction,
                "other_sector_reduction":
                    other_sector_reduction,
                "total_reduction":
                    total_reduction,
                "target_share_pct":
                    target_share_pct,
                "other_share_pct":
                    other_share_pct
            }
        )

    return impacts

def save_target_emission_impacts_v21(
        db,
        scenario_history_id,
        target_emission_impacts
    ):

    if not target_emission_impacts:
        return

    q_str = """
    INSERT INTO scenario_target_emission_impact_history
    (
        scenario_history_id,
        parameter_code,
        energy_type_code,
        sector_code,
        sector_name,
        target_scope,
        target_sector_reduction,
        other_sector_reduction,
        total_reduction,
        target_share_pct,
        other_share_pct
    )
    VALUES
    (
        %s,%s,%s,%s,%s,%s,
        %s,%s,%s,%s,%s
    )
    """
    cursor = db.connection.cursor()
    for item in target_emission_impacts:
        cursor.execute(
            q_str,
            (
                scenario_history_id,
                item.get("parameter_code"),
                item.get("energy_type_code"),
                item.get("sector_code"),
                item.get("sector_name"),
                item.get("target_scope"),
                float(item.get("target_sector_reduction",  0.0)),
                float(item.get("other_sector_reduction", 0.0)),
                float(item.get("total_reduction", 0.0)),
                float(item.get("target_share_pct", 0.0)),
                float(item.get("other_share_pct", 0.0))
            )
        )

    db.connection.commit()

    cursor.close()

def get_saved_target_emission_impacts_v21(
        db,
        scenario_history_id
    ):

    q_str = f"""
    SELECT
        parameter_code,
        energy_type_code,
        sector_code,
        sector_name,
        target_scope,
        target_sector_reduction,
        other_sector_reduction,
        total_reduction,
        target_share_pct,
        other_share_pct
    FROM scenario_target_emission_impact_history
    WHERE scenario_history_id =
          '{scenario_history_id}'
    ORDER BY target_emission_impact_id
    """

    df = tbl_to_df(
        db,
        q_str
    )
    if len(df) == 0:
        return None

    impacts = []
    for _, row in df.iterrows():
        impacts.append(
            {
                "parameter_code":
                    row["parameter_code"],
                "energy_type_code":
                    row["energy_type_code"],
                "sector_code":
                    row["sector_code"],
                "sector_name":
                    row["sector_name"],
                "target_scope":
                    row["target_scope"],
                "target_sector_reduction":
                    float(row["target_sector_reduction"]),
                "other_sector_reduction":
                    float(row["other_sector_reduction"]),
                "total_reduction":
                    float(row["total_reduction"]),
                "target_share_pct":
                    float(row["target_share_pct"]),
                "other_share_pct":
                    float(row["other_share_pct"])
            }
        )

    return impacts