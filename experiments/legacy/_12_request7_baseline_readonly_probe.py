"""Request 7 read-only baseline gate for the unmodified EEIO v2.1 application.

Modes:
  static: examine actual monetary IO product cells, no DB connection.
  live: SELECT three MySQL input tables, call original eeio_engine allocation
        functions in memory; does not call build_model_year or any history writer.

The probe NEVER modifies source modules, IO matrices, cached EEIO models or
MySQL records. It writes CSV/JSON only in its explicitly selected new outdir.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

TARGET_PRODUCTS = ("037", "038", "039", "095", "145")
ORIGINAL_PROXY_ROWS = {"Coal": 36, "Fuel": 37, "Natural Gas": 38, "Electricity": 144}
FEC_ORDER = ("Coal", "Fuel", "Natural Gas", "Electricity")
STAGE3L_DOMESTIC_IO_SHA256 = "8843e1300e091c3150f150cbb92976ed419c336f5a4d0198987296044d6b45ca"


def numeric(value):
    result = float(value)
    if not np.isfinite(result):
        raise ValueError("Non-finite numeric input")
    return result

def optional_numeric(value):
    if pd.isna(value):
        return None
    return numeric(value)


def read_io(path):
    df = pd.read_csv(path, dtype={"Sector_Code": str}, thousands=",")
    if "Sector_Code" not in df.columns:
        raise ValueError("IO file must contain Sector_Code")
    return df


def inspect_io(df, target_sector="158"):
    target_sector = str(target_sector)
    if target_sector not in df.columns:
        raise ValueError("Target sector column missing: " + target_sector)
    if "7000" not in df.columns:
        raise ValueError("IO file has no 7000 domestic output column")
    products = df["Sector_Code"].head(185).tolist()
    if products != [f"{n:03d}" for n in range(1, 186)]:
        raise ValueError("First 185 IO product rows are not in code order")
    rows = []
    for product in TARGET_PRODUCTS:
        product_index = products.index(product)
        io_row = df.iloc[product_index]
        rows.append({
            "product_code": product,
            "zero_based_row": int(product_index),
            "is_existing_proxy": product_index in ORIGINAL_PROXY_ROWS.values(),
            "io_purchase_sector": target_sector,
            "intermediate_purchase_million_rp": numeric(io_row[target_sector]),
        })
    fuel_proxy_code = products[ORIGINAL_PROXY_ROWS["Fuel"]]
    fuel_proxy = next(r for r in rows if r["product_code"] == fuel_proxy_code)
    refined = next(r for r in rows if r["product_code"] == "095")
    electricity = next(r for r in rows if r["product_code"] == "145")
    return {
        "target_sector": target_sector,
        "io_shape": [int(df.shape[0]), int(df.shape[1])],
        "proxy_product_codes": {name: products[pos] for name, pos in ORIGINAL_PROXY_ROWS.items()},
        "monetary_transactions": rows,
        "fuel_proxy_zero": bool(fuel_proxy["intermediate_purchase_million_rp"] == 0),
        "refined_petroleum_positive": bool(refined["intermediate_purchase_million_rp"] > 0),
        "electricity_positive": bool(electricity["intermediate_purchase_million_rp"] > 0),
        "diagnosis": "The existing fuel allocation uses product " + fuel_proxy_code
                     + ", not refined petroleum product 095. Monetary inputs are NOT physical fuel or electricity.",
    }


def ordered_series(df, index_col, value_col, order):
    if df[index_col].duplicated().any():
        raise ValueError("Duplicate input records for " + index_col)
    result = df.set_index(index_col).reindex(order)[value_col]
    if result.isna().any():
        raise ValueError("Missing source records for " + str(list(result[result.isna()].index)))
    a = result.to_numpy(dtype=float)
    if not np.isfinite(a).all():
        raise ValueError("Non-finite values for " + value_col)
    return a


def inspect_live_inputs(df_io, df_sector, df_fec, df_conv, df_co2,
                        calc_matrix_A, get_mat_finEnerCons, get_mat_finCons,
                        get_mat_finConsCO2, target_sector="158"):
    """Reproduce original util_mat's allocation chain without its buffer writes."""
    source_codes = df_io["Sector_Code"].head(185).astype(str).tolist()
    db_codes = df_sector["sector_code"].astype(str).tolist()
    if len(db_codes) != 185 or source_codes != db_codes:
        raise ValueError(
            "BLOCKING: sector_lst SELECT row order differs from first 185 IO rows. "
            "Original util_mat assumes the same product-row order; do not remap silently."
        )
    if str(target_sector) not in db_codes:
        raise ValueError("Target sector absent in sector_lst")
    idx = db_codes.index(str(target_sector))
    matrix = df_io[db_codes].to_numpy(dtype=float)
    X_io = matrix[:185, :]
    X_total = matrix[-1, :]
    if not np.isfinite(X_total).all() or np.any(X_total <= 0):
        raise ValueError("Nonpositive/invalid 7000-equivalent IO total in last row")
    A = calc_matrix_A(X_io, X_total)
    if not np.isfinite(A).all():
        raise ValueError("Non-finite matrix A")

    fec = ordered_series(df_fec, "energy_type_name", "fec_val", FEC_ORDER)
    conv = ordered_series(df_conv, "energy_type_lower", "multiplier_factor",
                          [x.lower() for x in FEC_ORDER])
    if np.any(conv <= 0):
        raise ValueError("Nonpositive conversion factor")
    if df_co2["energy_type"].duplicated().any():
        raise ValueError("Duplicate CO2 energy_type")
    co2_factors = (df_co2.set_index("energy_type")
                   .reindex(FEC_ORDER)[["heat_content_hhv", "emission_factor", "multiplier"]]
                   .to_numpy(dtype=float))
    #print("--df_co2", df_co2)
    #print("--co2_factors", co2_factors)
    required_mask = np.array([
        [True, True, False],   # Coal
        [True, True, False],   # Fuel
        [True, True, True],    # Natural Gas
        [True, False, False]   # Electricity
    ])
    if co2_factors.shape != (4, 3):
        raise ValueError(
            f"Unexpected CO2 factor shape: {co2_factors.shape}"
        )
    if not np.isfinite(co2_factors[required_mask]).all():
        raise ValueError(
            "Missing or invalid CO2 factor in a required position"
        )

    # Calls the actual unchanged eeio_engine functions. No model cache or builder.
    fin_ener = get_mat_finEnerCons(A, fec)
    fin_cons = get_mat_finCons(fin_ener, conv)
    mat_co2 = get_mat_finConsCO2(fin_cons, co2_factors)
    if not all(np.isfinite(a).all() for a in (fin_ener, fin_cons, mat_co2)):
        raise ValueError("Non-finite output from legacy energy conversion")

    rows = []
    for i, name in enumerate(FEC_ORDER):
        rows.append({
            "energy_type": name,
            "legacy_proxy_product_code": source_codes[ORIGINAL_PROXY_ROWS[name]],
            "baseline_fec_national_native": numeric(fec[i]),
            "baseline_fec_allocated_to_sector_native": numeric(fin_ener[i, idx]),
            "conversion_factor_from_db": numeric(conv[i]),
            "baseline_finCons_sector_native_UNVERIFIED_UNIT": numeric(fin_cons[i, idx]),
            "model_finConsCO2_sector_raw_UNVERIFIED": numeric(mat_co2[i, idx]),
            "co2_factor_col0": optional_numeric(co2_factors[i, 0]),
            "co2_factor_col1": optional_numeric(co2_factors[i, 1]),
            "co2_factor_col2": optional_numeric(co2_factors[i, 2]),
        })

    return {
        "target_sector": str(target_sector),
        "sector_column_zero_based": idx,
        "sector_order_validated": True,
        "carrier_rows": rows,
        "fuel_native_baseline_zero": bool(np.isclose(fin_cons[1, idx], 0.0, atol=1e-14)),
        "electricity_native_baseline_zero": bool(np.isclose(fin_cons[3, idx], 0.0, atol=1e-14)),
        "scientific_gate": (
            "STOP: Legacy fuel sector baseline is zero. A percentage decrease cannot displace gasoline. "
            "Use a separate HEESI transport satellite and independently verified mapping to EEIO."
            if np.isclose(fin_cons[1, idx], 0.0, atol=1e-14)
            else "NONZERO fuel baseline, but attribution and physical units must still be verified."
        ),
        "unit_warning": "The four mat_finCons rows use carrier-specific conversion factors. "
                        "Do not label them all TJ or equate sector 158 with national gasoline.",
        "double_count_warning": "Do not add HEESI tailpipe+grid emissions to existing EEIO totals.",
    }


def read_live_db(year):
    # Deliberately follows the application's existing Flask+dotenv db_init setup.
    # This function issues SELECT queries only and does not call build_model_year.
    from dotenv import load_dotenv
    from flask import Flask
    from util_db import db_init, tbl_to_df
    from eeio_engine import (calc_matrix_A, get_mat_finEnerCons,
                             get_mat_finCons, get_mat_finConsCO2)
    load_dotenv()
    app = Flask("request7_baseline_readonly_probe")
    db = db_init(app)
    with app.app_context():
        sector = tbl_to_df(db, "SELECT sector_agg, sector_code, sector_name FROM sector_lst WHERE sector_label='DET'")
        fec = tbl_to_df(db, f"""SELECT year, energy_type, fec_val,
                       energy_lst.energy_type_code, energy_lst.energy_type_name
                       FROM fec_byyear LEFT JOIN energy_lst
                       ON fec_byyear.energy_type = energy_lst.energy_type_code
                       WHERE year='{int(year)}'""")
        conv = tbl_to_df(db, "SELECT energy_type, multiplier_factor FROM conversion_factor")
        co2 = tbl_to_df(db, "SELECT energy_type, heat_content_hhv, emission_factor, multiplier FROM co2_factor")
    conv["energy_type_lower"] = conv["energy_type"].str.lower()
    return sector, fec, conv, co2, (calc_matrix_A, get_mat_finEnerCons, get_mat_finCons, get_mat_finConsCO2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("static", "live"), default="static")
    parser.add_argument("--year", type=int, default=2020)
    parser.add_argument("--target-sector", default="158")
    parser.add_argument("--io", required=True, help="Existing 2020 domestic IO CSV; never modified")
    parser.add_argument("--outdir", required=True, help="New output directory; must be empty")
    args = parser.parse_args()
    path = Path(args.io).expanduser().resolve(strict=True)
    dest = Path(args.outdir).expanduser().resolve()
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit("Refusing to overwrite a nonempty output directory: " + str(dest))
    dest.mkdir(parents=True, exist_ok=True)
    df = read_io(path)
    report = {"purpose": "Request7 read-only gate, not emissions validation",
              "mode": args.mode, "year": args.year,
              "input_io_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
              "stage3l_domestic_reference_sha256": STAGE3L_DOMESTIC_IO_SHA256,
              "app_io_matches_research_domestic_snapshot": bool(
                  hashlib.sha256(path.read_bytes()).hexdigest() == STAGE3L_DOMESTIC_IO_SHA256),
              "io_filename_warning": "The Flask app uses io_ind_2020.csv, while Stage3L used io_ind_2020_domestic.csv. Check SHA256 before treating them as the same baseline.",
              "io": inspect_io(df, args.target_sector)}
    if args.mode == "live":
        sector, fec, conv, co2, calc = read_live_db(args.year)
        report["live_baseline"] = inspect_live_inputs(df, sector, fec, conv, co2,
                                                        *calc, target_sector=args.target_sector)
        pd.DataFrame(report["live_baseline"]["carrier_rows"]).to_csv(
            dest / "carrier_158_existing_model_UNVERIFIED_UNITS.csv", index=False)
    pd.DataFrame(report["io"]["monetary_transactions"]).to_csv(
        dest / "io_158_monetary_inputs.csv", index=False)
    with (dest / "request7_baseline_gate.json").open("w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print("Mode:", args.mode)
    print("IO file SHA256:", report["input_io_sha256"])
    print("Matches Stage3L domestic IO snapshot:", report["app_io_matches_research_domestic_snapshot"])
    print("Fuel proxy product:", report["io"]["proxy_product_codes"]["Fuel"])
    print("Fuel proxy zero for sector 158:", report["io"]["fuel_proxy_zero"])
    if args.mode == "live":
        print("Legacy model fuel baseline zero:", report["live_baseline"]["fuel_native_baseline_zero"])
        print("Legacy model native electricity baseline:", report["live_baseline"]["carrier_rows"][3]["baseline_finCons_sector_native_UNVERIFIED_UNIT"])
    print("Output:", dest)


if __name__ == "__main__":
    main()
