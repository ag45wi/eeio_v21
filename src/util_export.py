import pandas as pd
from io import StringIO
import re
from io import BytesIO

def scenario_summary_to_csv(report):
    total = (
        report.summary["totalEmission"]
    )

    df = pd.DataFrame(
        {
            "Metric":
            [
                "Year",
                "Scenario",
                "Baseline emission (tCO2)",
                "Scenario emission (tCO2)",
                "Difference (tCO2)",
                "Percentage change (%)"
            ],
            "Value":
            [
                report.metadata["year"],

                report.metadata[
                    "scenario_name"
                ],
                total[
                    "baseline_total"
                ],
                total[
                    "scenario_total"
                ],
                total[
                    "difference_total"
                ],
                total[
                    "percentage_change"
                ]
            ]
        }
    )

    return df.to_csv(
        index=False
    )

def scope_comparison_to_csv(report):
    rows = []
    for key, value in (
        report.summary.items()
        ):
        rows.append(
            {
                "Component":
                    key,
                "Baseline (tCO2)":
                    value[
                        "baseline_total"
                    ],
                "Scenario (tCO2)":
                    value[
                        "scenario_total"
                    ],
                "Difference (tCO2)":
                    value[
                        "difference_total"
                    ],
                "Change (%)":
                    value[
                        "percentage_change"
                    ]
            }
        )
    df = pd.DataFrame(rows)

    return df.to_csv(
        index=False
    )

def sector_impact_to_csv(report):
    df = (
        report.sector_results
        .copy()
    )

    return df.to_csv(
        index=False
    )

def make_scenario_filename(
        report,
        suffix
    ):
    """
    Create a safe filename based on
    scenario year and name.
    """
    year = (
        report.metadata["year"]
    )

    scenario_name = (
        report.metadata["scenario_name"]
    )

    # Convert to abbreviation-like string
    scenario_tag = (
        scenario_name
        .lower()
        .replace("illustrative", "")
        .strip()
    )

    scenario_tag = re.sub(
        r'[^a-z0-9]+',
        '_',
        scenario_tag
    )

    return (
        f"{year}_"
        f"{scenario_tag}_"
        f"{suffix}.csv"
    )


def scenario_target_to_csv(report):
    import pandas as pd
    import io

    rows = (
        report.metadata.get("target_summary", [])
        or []
    )

    if len(rows) == 0:
        return ""

    df = pd.DataFrame(
        rows
    )

    export_columns = [
        "parameter_code",
        "energy_name",
        "sector_name",
        "target_scope",
        "change_percent"
    ]

    # Keep only available columns
    export_columns = [
        c
        for c in export_columns
        if c in df.columns
    ]

    df = df[export_columns]
    df.rename(
        columns={
            "parameter_code":
                "Parameter",
            "energy_name":
                "Energy",
            "sector_name":
                "Sector",
            "target_scope":
                "Target Scope",
            "change_percent":
                "Change (%)"
        },
        inplace=True
    )

    output = io.StringIO()
    df.to_csv(output, index=False)

    return output.getvalue()

def target_energy_impact_to_csv(report):
    import pandas as pd
    import io

    rows = (report.target_impacts or [])
    if len(rows) == 0:
        return ""

    df = pd.DataFrame(rows)

    export_columns = [
        "energy_name",
        "sector_name",
        "baseline_energy",
        "scenario_energy",
        "energy_reduction",
        "energy_unit",
        "percentage_change"
    ]

    export_columns = [
        c
        for c in export_columns
        if c in df.columns
    ]

    df = df[export_columns]
    df.rename(
        columns={
            "energy_name":
                "Energy",
            "sector_name":
                "Sector",
            "baseline_energy":
                "Baseline Energy",
            "scenario_energy":
                "Scenario Energy",
            "energy_reduction":
                "Energy Reduction",
            "energy_unit":
                "Unit",
            "percentage_change":
                "Change (%)"
        },
        inplace=True
    )

    output = io.StringIO()
    df.to_csv(output, index=False)

    return output.getvalue()

def target_emission_impact_to_csv(report):

    import pandas as pd
    import io

    rows = report.target_emission_impacts or []
    if len(rows) == 0:
        return ""

    df = pd.DataFrame(rows)

    export_columns = [
        "sector_name",
        "target_sector_reduction",
        "other_sector_reduction",
        "total_reduction",
        "target_share_pct",
        "other_share_pct"
    ]

    export_columns = [
        c
        for c in export_columns
        if c in df.columns
    ]

    df = df[export_columns]
    df.rename(
        columns={
            "sector_name":
                "Target Sector",
            "target_sector_reduction":
                "Target Sector Reduction (tCO2)",
            "other_sector_reduction":
                "Other Sector Reduction (tCO2)",
            "total_reduction":
                "Total Reduction (tCO2)",
            "target_share_pct":
                "Target Share (%)",
            "other_share_pct":
                "Other Share (%)"
        },
        inplace=True
    )

    output = io.StringIO()
    df.to_csv(
        output,
        index=False
    )

    return output.getvalue()


def scenario_report_to_excel(
        report
    ):
    """
    Convert ScenarioReport into Excel workbook.

    Returns:
        BytesIO object
    """

    #print("--Inside util_export::scenario_report_to_excel")
    #print("report", report)

    output = BytesIO()

    with pd.ExcelWriter(
        output,
        engine="openpyxl"
    ) as writer:

        # ------------------------------------
        # Sheet 1: Summary
        # ------------------------------------

        total = (
            report.summary["totalEmission"]
        )
        df_summary = pd.DataFrame(
            {
            "Metric":
            [
                "Year",
                "Scenario",
                "Baseline emission (tCO2)",
                "Scenario emission (tCO2)",
                "Emission Change (tCO2)",
                "Percentage Change (%)"
            ],
            "Value":
            [
                report.metadata["year"],
                report.metadata["scenario_name"],
                total["baseline_total"],
                total["scenario_total"],
                total["difference_total"],
                total["percentage_change"]
            ]
            }
        )
        df_summary.to_excel(
            writer,
            sheet_name="1_Scenario_Summary",
            index=False
        )
        #print("--report.metadata", report.metadata)

        # ------------------------------------
        scenario_types = (
            report.metadata.get("scenario_types", [])
        )
        scenario_type_names = None
        scenario_type_display =""
        if scenario_types:
            scenario_type_names = [
                st.get("type_name")
                for st in scenario_types
            ]
            scenario_type_display = ", ".join(scenario_type_names)

        print("--scenario_report_to_excel::report.metadata", report.metadata)
        scenario_definition_df = pd.DataFrame(
            [
                {
                    "Item": "Scenario ID",
                    "Value": report.metadata.get("scenario_id")
                },
                {
                    "Item": "Scenario Name",
                    "Value": report.metadata.get("scenario_name")
                },
                {
                    "Item": "Scenario Code",
                    "Value": report.metadata.get("scenario_code")
                },
                {
                    "Item": "Scenario Type",
                    "Value": scenario_type_display
                },
                #{
                #    "Item": "Year",
                #    "Value": report.metadata.get("year")
                #},
                {
                    "Item": "Description",
                    "Value": report.metadata.get("scenario_description")
                },
                {
                    "Item": "Scenario Signature",
                    "Value": report.metadata.get("scenario_signature")
                }
            ]
        )
        scenario_definition_df.to_excel(
            writer,
            sheet_name="2_Scenario_Definition",
            index=False
        )

        scenario_target_df = pd.DataFrame(
            report.metadata.get("target_summary", [])
        )
        print("--scenario_target_df", scenario_target_df)
        scenario_target_df = scenario_target_df[
            [
                "parameter_code",
                "energy_name",
                "sector_name",
                "target_scope",
                "change_percent"
            ]
        ]
        scenario_target_df.to_excel(
            writer,
            sheet_name="3_Scenario_Target",
            index=False
        )

        #print("--report.target_impacts", report.target_impacts)
        energy_impact_df = pd.DataFrame(
            report.target_impacts
        )
        energy_impact_df = energy_impact_df[
            [
                "energy_name",
                "sector_name",
                "baseline_energy",
                "scenario_energy",
                "energy_reduction",
                "energy_unit",
                "percentage_change"
            ]
        ]
        energy_impact_df.to_excel(
            writer,
            sheet_name="4_Physical_Energy_Impact",
            index=False
        )

        emission_distribution_df = pd.DataFrame(
            report.target_emission_impacts
        )
        #print("inside util_export.py::scenario_report_to_excel")
        #print("--emission_distribution_df", emission_distribution_df, type(emission_distribution_df))
        #print("--report.target_emission_impacts", report.target_emission_impacts, type(report.target_emission_impacts))
        emission_distribution_df = emission_distribution_df[
            [
                "sector_name",
                "target_sector_reduction",
                "other_sector_reduction",
                "total_reduction",
                "target_share_pct",
                "other_share_pct"
            ]
        ]

        emission_distribution_df.to_excel(
            writer,
            sheet_name="5_Emission_Impact_Distribution",
            index=False
        )

        # ------------------------------------
        # Sheet 2: Scope Comparison
        # ------------------------------------
        scope_rows = []
        for key, value in (
            report.summary.items()
            ):
            scope_rows.append(
                {
                "Component":
                    key,
                "Baseline (tCO2)":
                    value[
                        "baseline_total"
                    ],
                "Scenario (tCO2)":
                    value[
                        "scenario_total"
                    ],
                "Difference (tCO2)":
                    value[
                        "difference_total"
                    ],
                "Change (%)":
                    value[
                        "percentage_change"
                    ]
                }
            )
        pd.DataFrame(
            scope_rows
        ).to_excel(
            writer,
            sheet_name="6_Scope_Emission",
            index=False
        )
        # => scope_df

        # ------------------------------------
        # Sheet 5: Aggregate Impact
        # ------------------------------------
        df_aggregate = (
            report.aggregate_results
            .reset_index()
        )
        df_aggregate.to_excel(
            writer,
            sheet_name="7_Aggregate_Impact",
            index=False
        )

        #sector_df
        # ------------------------------------
        # Sheet 4: Sector Impact
        # ------------------------------------
        report.sector_results.to_excel(
            writer,
            sheet_name="8_Sector_Impact",
            index=False
        )

        # ------------------------------------
        # Sheet 3: Top Sectors
        # ------------------------------------
        df_top_sector = pd.DataFrame(
            report
            .impact_statistics
            .get(
                "top_reduction_sectors",
                []
            )
        )
        df_top_sector.to_excel(
            writer,
            sheet_name="9_Top_Reduction_Sectors",
            index=False
        )

    for ws in writer.book.worksheets:
        ws.freeze_panes = "A2"
        for col in ws.columns:
            max_length = max(
                len(
                    str(cell.value)
                )
                if cell.value
                else 0
                for cell in col
            )
            ws.column_dimensions[
                col[0].column_letter
            ].width = (
                max_length + 2
            )

    #print("Excel report generated successfully")
    output.seek(0)

    return output