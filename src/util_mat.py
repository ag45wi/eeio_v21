import os
import pandas as pd
import numpy as np
import sys
from dotenv import load_dotenv
from flask import Flask
import shutil

from util_db import tbl_to_df, db_init, execute_query
from eeio_engine import (
    calc_matrix_A,
    get_mat_finEnerCons,
    get_mat_finCons,
    get_mat_finConsCO2,
    build_eeio_model,
    get_eeio_model,
    get_cached_eeio_model_keys,
    compare_baseline_scenario,
    summarize_comparison,
    analyze_sector_impacts,
    rank_sector_reductions,
    rank_sector_increases,
    calculate_reduction_contribution,
    aggregate_sector_impacts,
    count_sector_changes,
    build_scenario_report,
    run_scenario,
    build_sector_column_map
)
from util_scenario import save_complete_scenario_report

def copy_file_todata(in_fname):
    cwd = os.getcwd()

    if (in_fname != ""):
        print("in_fname", in_fname)

    src_dir=f'{cwd}/static/uploads'
    dest_dir=f'{cwd}/static/data'

    files = os.listdir(src_dir)
    for file in files:
        file_path_src = os.path.join(src_dir, file)
        file_path_dest = os.path.join(dest_dir, file)

        #https://www.freecodecamp.org/news/python-copy-file-copying-files-to-another-directory/
        #If the destination file already exists, it gets replaced
        shutil.copyfile(file_path_src, file_path_dest)
        print(f"Copied: {file_path_src} -> {file_path_dest}")


def get_io_aggregate(df_io, df_agg_label, totEm, scope1, scope2, scope3):
    #https://www.geeksforgeeks.org/pandas-groupby-and-sum/
    #https://stackoverflow.com/questions/37697195/how-to-merge-two-data-frames-based-on-particular-column-in-pandas-python
    #https://www.geeksforgeeks.org/adding-new-column-to-existing-dataframe-in-pandas/

    cnf_sector=df_agg_label.shape[0]
    print("cnf_sector", cnf_sector)

    #df_agg_io=df_io[["Sector_Code",	"Sector_Name", "Sector_CodeName"]]
    df_agg_io=df_io[["Sector_Code"]]
    #df_agg_io=df_io[["Sector_Code"]]
    df_agg_io=df_agg_io.head(cnf_sector) #get the first 185 sectors

    df_agg_io["total_Emission"] = totEm
    df_agg_io["scope1_Emission"] = scope1
    df_agg_io["scope2_Emission"] = scope2
    df_agg_io["scope3_Emission"] = scope3

    print("column df_agg_io, df_agg_label", df_agg_io.columns, df_agg_label.columns)
    df_agg_io=pd.merge(df_agg_io, df_agg_label, on='Sector_Code')
    print(df_agg_io.head(5))

    #df_agg_io=df_agg_io.drop(columns=['Sector_CodeName', 'Sector_Number'])
    df_agg_io=df_agg_io.drop(columns=['Sector_Code', 'Sector_Name'])
    print(df_agg_io.head(5))

    #df1 = df_agg_io.groupby("Aggregated sectors")["total_Emission", "scope1_Emission", "scope2_Emission", "scope3_Emission"].sum()
    df_agg_sectors= df_agg_io.groupby("Aggregated sectors").sum()
    print(df_agg_sectors.head(5))

    return df_agg_sectors

def get_aggregate_each (df_io, df_emission, df_agg_label):
    #cwd=os.getcwd()
    #df_emission = pd.read_csv(f"{cwd}/static/buf/result_emission.csv")
    #df_io = pd.read_csv(f"{cwd}/static/data/io_ind_2016.csv")

    cnf_sector=df_emission.shape[0]
    df_sector=df_io[["Sector_Code"]]
    df_sector=df_sector.head(cnf_sector)

    df_emission["Sector_Code"]=df_sector.values.ravel()

    #file_path_AGG = f'{os.getcwd()}/static/data/aggregated_sectors.csv'
    #file_path_AGG = f'{os.getcwd()}/static/data/agg_sectors_det.csv'
    #df_agg_label= pd.read_csv(file_path_AGG)

    #df_emission=pd.merge(df_emission, df_agg_label, on='Sector_CodeName')
    df_emission=pd.merge(df_emission, df_agg_label, on='Sector_Code')

    return df_emission

def write_to_db(db, year, df_emissionIntensity, df_emission, df_agg_sectors, df_agg_sectors_each):
    q_str = "SELECT sector_agg, sector_code, sector_name FROM sector_lst WHERE sector_label='DET'"
    df_sector = tbl_to_df(db, q_str)
    print("df columns", df_sector.columns)
    print("df_sector", df_sector.head(5))
    cnt_sector=df_sector.shape[0]
    sector_agg=df_sector["sector_agg"].values.tolist()
    sector_code=df_sector["sector_code"].values.tolist()
    sector_name=df_sector["sector_name"].values.tolist()
    year_lst=[year]*cnt_sector

    #emissionIntensity==========================================================================
    #df_emissionIntensity.to_sql('emissionIntensity', con=db, if_exists='replace', index=False)

    # Internal development comment removed for public distribution.
    #on float data types

    print(f"df_emissionIntensity.shape {df_emissionIntensity.shape}, cnt_sector {cnt_sector}")
    if (df_emissionIntensity.shape[0] != cnt_sector):
        print("Inequal length of df_emissionIntensity and sector_lst")
        sys.exit()

    df_emissionIntensity["year"]=year_lst
    df_emissionIntensity["sector_agg"]=sector_agg
    df_emissionIntensity["sector_code"]=sector_code
    df_emissionIntensity["sector_name"]=sector_name

    df_emissionIntensity.to_csv(f"{os.getcwd()}/static/buf/{year}/tmp/df_emissionIntensity_{year}.csv", index=False)

    data_to_upsert = list(df_emissionIntensity[['year', 'sector_agg', 'sector_code', 'sector_name', 'emissionIntensityScope1', 'emissionIntensityScope2', 'emissionIntensityScope3', 'totalEmissionIntensity']].itertuples(index=False, name=None))

    sql_str = """
        INSERT INTO result_emission_intensity (year, sector_agg, sector_code, sector_name, emissionIntensityScope1, emissionIntensityScope2, emissionIntensityScope3, totalEmissionIntensity)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            emissionIntensityScope1 = VALUES(emissionIntensityScope1),
            emissionIntensityScope2 = VALUES(emissionIntensityScope2),
            emissionIntensityScope3 = VALUES(emissionIntensityScope3),
            totalEmissionIntensity = VALUES(totalEmissionIntensity);
        """

    execute_query(db, sql_str, isBatch=True, data_to_insert=data_to_upsert)

    tbl_name="result_emission_intensity"
    df_cnt = tbl_to_df(db, f"select count(*) as cnt from {tbl_name} where year='{year}'")
    print(f"Count of {tbl_name} {year}: {df_cnt['cnt'].values[0]}")

    #emission==========================================================================
    print(f"df_emission.shape {df_emission.shape}, cnt_sector {cnt_sector}")
    if (df_emission.shape[0] != cnt_sector):
        print("Inequal length of df_emission and sector_lst")
        sys.exit()

    df_emission["year"]=year_lst
    df_emission["sector_agg"]=sector_agg
    df_emission["sector_code"]=sector_code
    df_emission["sector_name"]=sector_name

    data_to_upsert = list(df_emission[['year', 'sector_agg', 'sector_code', 'sector_name', 'emissionScope1', 'emissionScope2', 'emissionScope3', 'totalEmission']].itertuples(index=False, name=None))

    sql_str = """
        INSERT INTO result_emission (year, sector_agg, sector_code, sector_name, emissionScope1, emissionScope2, emissionScope3, totalEmission)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            emissionScope1 = VALUES(emissionScope1),
            emissionScope2 = VALUES(emissionScope2),
            emissionScope3 = VALUES(emissionScope3),
            totalEmission = VALUES(totalEmission);
        """

    execute_query(db, sql_str, isBatch=True, data_to_insert=data_to_upsert)

    tbl_name="result_emission"
    df_cnt = tbl_to_df(db, f"select count(*) as cnt from {tbl_name} where year='{year}'")
    print(f"Count of {tbl_name} {year}: {df_cnt['cnt'].values[0]}")

    if (0):
        #agg_sectors==========================================================================
        q_str = "SELECT * FROM sector_agg ORDER by id"
        df_sector_agg = tbl_to_df(db, q_str)
        cnt_sector_agg=df_sector_agg.shape[0]
        print(f"df_agg_sectors.shape {df_agg_sectors.shape}, cnt_sector_agg {cnt_sector_agg}")
        if (df_agg_sectors.shape[0] != cnt_sector_agg):
            print("Inequal length of df_agg_sectors and sector_agg")
            sys.exit()

        year_lst_agg=[year]*cnt_sector_agg
        df_agg_sectors["year"]=year_lst_agg

        df_agg_sectors = df_agg_sectors.reset_index()
        df_agg_sectors.rename(columns={'index': 'Aggregated sectors'}, inplace=True)
        print("df_agg_sectors.columns", df_agg_sectors.columns.values)

        data_to_upsert = list(df_agg_sectors[['year', 'Aggregated sectors', 'scope1_Emission', 'scope2_Emission', 'scope3_Emission', 'total_Emission']].itertuples(index=False, name=None))

        #print("data_to_upsert", data_to_upsert)

        sql_str = """
            INSERT INTO result_sector_agg (year, sector_agg, emissionScope1, emissionScope2, emissionScope3, totalEmission)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                emissionScope1 = VALUES(emissionScope1),
                emissionScope2 = VALUES(emissionScope2),
                emissionScope3 = VALUES(emissionScope3),
                totalEmission = VALUES(totalEmission);
            """

        execute_query(db, sql_str, isBatch=True, data_to_insert=data_to_upsert)

        tbl_name="result_sector_agg"
        df_cnt = tbl_to_df(db, f"select count(*) as cnt from {tbl_name} where year='{year}'")
        print(f"Count of {tbl_name} {year}: {df_cnt['cnt'].values[0]}")

        #agg_sectors_each==========================================================================

        print(f"df_agg_sectors_each.shape {df_agg_sectors_each.shape}, cnt_sector {cnt_sector}")
        if (df_agg_sectors_each.shape[0] != cnt_sector):
            print("Inequal length of df_agg_sectors_each and sector_lst")
            sys.exit()

        df_agg_sectors_each["year"]=year_lst

        data_to_upsert = list(df_agg_sectors_each[['year', 'Aggregated sectors', 'Sector_Code', 'Sector_Name', 'emissionScope1', 'emissionScope2', 'emissionScope3', 'totalEmission']].itertuples(index=False, name=None))

        sql_str = """
            INSERT INTO result_sector_each (year, sector_agg, sector_code, sector_name, emissionScope1, emissionScope2, emissionScope3, totalEmission)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                emissionScope1 = VALUES(emissionScope1),
                emissionScope2 = VALUES(emissionScope2),
                emissionScope3 = VALUES(emissionScope3),
                totalEmission = VALUES(totalEmission);
            """

        execute_query(db, sql_str, isBatch=True, data_to_insert=data_to_upsert)

        tbl_name="result_sector_each"
        df_cnt = tbl_to_df(db, f"select count(*) as cnt from {tbl_name} where year='{year}'")
        print(f"Count of {tbl_name} {year}: {df_cnt['cnt'].values[0]}")

def get_sector(db):
    q_str = "SELECT sector_agg, sector_code, sector_name FROM sector_lst WHERE sector_label='DET'"
    df_sector = tbl_to_df(db, q_str)

    return df_sector


def build_model_year(db, fname_io, year):
    """
    Prepare and build reusable EEIOModel.

    This function extracts the model preparation
    part from calc_mat().

    It does not write to database.
    It does not create reports.
    """
    cwd = os.getcwd()
    folder_path = f'{cwd}/static/data/{year}'

    # -------------------------------------------------
    # 1. Read IO table
    # -------------------------------------------------
    file_path = f"{folder_path}/{fname_io}"
    if not os.path.exists(file_path):
        raise FileNotFoundError(file_path)

    df_io = pd.read_csv(
        file_path,
        thousands=","
    )

    # -------------------------------------------------
    # 2. Sector definition
    # -------------------------------------------------
    q_str = """
        SELECT sector_agg,
               sector_code,
               sector_name
        FROM sector_lst
        WHERE sector_label='DET'
    """

    df_sector = tbl_to_df(db, q_str)
    cnt_sector = df_sector.shape[0]
    sector_code = (
        df_sector["sector_code"]
        .values
        .tolist()
    )

    # -------------------------------------------------
    # 3. Matrix A
    # -------------------------------------------------
    X = df_io[sector_code].values
    X_io = X[:cnt_sector, :]
    X_total = X[-1, :]

    if (0):
        print("X_total", X_total)
        print("X_io last", X_io[-1,:])
        print("sector_code", sector_code)
        print("X shape", X.shape, "X_io shape", X_io.shape)

    mat_A = calc_matrix_A(
        X_io,
        X_total
    )

    # -------------------------------------------------
    # 4. FEC
    # -------------------------------------------------
    fec_lst = [
        "Coal",
        "Fuel",
        "Natural Gas",
        "Electricity"
    ]

    q_str = f"""
        SELECT year,
               energy_type,
               fec_val,
               energy_lst.energy_type_code,
               energy_lst.energy_type_name
        FROM fec_byyear
        LEFT JOIN energy_lst
        ON fec_byyear.energy_type =
           energy_lst.energy_type_code
        WHERE year='{year}'
    """

    df_fec = tbl_to_df(
        db,
        q_str
    )

    df_fec_reordered = (
        df_fec
        .set_index('energy_type_name')
        .reindex(fec_lst)
    )

    X_fec_subset = (
        df_fec_reordered['fec_val']
        .values
    )

    mat_finEnerCons = get_mat_finEnerCons(
        mat_A,
        X_fec_subset
    )

    # -------------------------------------------------
    # 5. Conversion factor
    # -------------------------------------------------

    q_str = """
        SELECT energy_type,
               multiplier_factor
        FROM conversion_factor
    """

    df_conv = tbl_to_df(
        db,
        q_str
    )

    df_conv['energy_type_lower'] = (
        df_conv['energy_type']
        .str.lower()
    )

    fec_lst_lower = [
        item.lower()
        for item in fec_lst
    ]

    df_reordered = (
        df_conv
        .set_index('energy_type_lower')
        .reindex(fec_lst_lower)
    )

    X_conv_subset = (
        df_reordered['multiplier_factor']
        .values
    )

    mat_finCons = get_mat_finCons(
        mat_finEnerCons,
        X_conv_subset
    )

    # -------------------------------------------------
    # 6. CO2 factor
    # -------------------------------------------------
    q_str = """
        SELECT energy_type,
               heat_content_hhv,
               emission_factor,
               multiplier
        FROM co2_factor
    """

    df_co2 = tbl_to_df(
        db,
        q_str
    )

    df_co2_reordered = (
        df_co2
        .set_index('energy_type')
        .reindex(fec_lst)
    )

    X_co2_subset = (
        df_co2_reordered[
            [
                'heat_content_hhv',
                'emission_factor',
                'multiplier'
            ]
        ]
        .values
    )

    mat_finConsCO2 = get_mat_finConsCO2(
        mat_finCons,
        X_co2_subset
    )

    # -------------------------------------------------
    # 7. Domestic output
    # -------------------------------------------------
    col_domOut = "7000"

    X_domOut = (
        df_io[[col_domOut]]
        .values
    )

    X_domOut = (
        X_domOut[:cnt_sector]
        .ravel()
    )

    # -------------------------------------------------
    # 8. Build reusable model
    # -------------------------------------------------
    model = build_eeio_model(
        mat_A=mat_A,
        mat_finConsCO2=mat_finConsCO2,
        X_total=X_total,
        X_domOut=X_domOut,
        mat_finCons=mat_finCons,
        CO2_EF=X_co2_subset,
        year=year
    )

    # Save to buffer
    pd.DataFrame(
        mat_finEnerCons,
        index=fec_lst
    ).to_csv(
        f"static/buf/{year}/tmp/df_finEnerCons.csv"
    )
    pd.DataFrame(
        mat_finCons
    ).to_csv(
        f"static/buf/{year}/tmp/mat_finCons.csv"
    )
    pd.DataFrame(
        mat_finConsCO2,
        index=fec_lst
    ).to_csv(
        f"static/buf/{year}/tmp/df_finConsCO2.csv"
    )
    if (0):
        print("fec_lst", fec_lst)
        print("X_co2_subset", X_co2_subset)
        print("model.mat_finCons.shape", model.mat_finCons.shape)
        print("model.mat_finCons[:,:5]", model.mat_finCons[:,:5])

    model.sector_column_map = (
        build_sector_column_map(
            df_sector
        )
    )
    return model


def calc_mat(db, fname_io, year):
    print ("Inside util_app::calc_mat")

    cwd = os.getcwd()
    folder_path = f'{cwd}/static/data/{year}'
    folder_path_buf = f'{cwd}/static/buf/{year}'

    if not os.path.exists(folder_path_buf):
        os.makedirs(folder_path_buf)
    if not os.path.exists(f"{folder_path_buf}/tmp"):
        os.makedirs(f"{folder_path_buf}/tmp")

    #Original IO table
    #-----------------------------------------------------------------------------------------
    #file_path = 'data/io_ind_2016.xlsx'
    file_path=f"{folder_path}/{fname_io}"
    print("file_path", file_path)
    if not os.path.exists(file_path):
        return 0, f"Cannot find {file_path}", pd.DataFrame(), pd.DataFrame()

    # IO loading
    #-----------------------------------------------------------------------------------------
    df_io = pd.read_csv(file_path, thousands=",")
    #print(df_io.head(5))
    print("df_io shape", df_io.shape)

    # sector metadata from MySQL
    #-----------------------------------------------------------------------------------------
    q_str = "SELECT sector_agg, sector_code, sector_name FROM sector_lst WHERE sector_label='DET'"
    df_sector = tbl_to_df(db, q_str)

    cache_key = (year, fname_io)

    model = get_eeio_model(
        cache_key=cache_key,
        model_builder=lambda: build_model_year(
            db=db,
            fname_io=fname_io,
            year=year
        )
    )

    eeio_result = model.calculate_baseline()

    # Back to presentation/persistence preparation
    # ---------------------------------------------------------
    emissionIntensityScope1 = eeio_result["emissionIntensityScope1"]
    emissionIntensityScope2 = eeio_result["emissionIntensityScope2"]
    emissionIntensityScope3 = eeio_result["emissionIntensityScope3"]
    totalEmissionIntensity = eeio_result["totalEmissionIntensity"]
    scope1_emission_inT = eeio_result["emissionScope1"]
    scope2_emission_inT = eeio_result["emissionScope2"]
    scope3_emission_inT = eeio_result["emissionScope3"]
    totalEmission = eeio_result["totalEmission"]


    df_emissionIntensity = pd.DataFrame(data=[emissionIntensityScope1, emissionIntensityScope2, emissionIntensityScope3, totalEmissionIntensity]).T
    df_emissionIntensity.columns = ["emissionIntensityScope1", "emissionIntensityScope2", "emissionIntensityScope3", "totalEmissionIntensity"]

    df_emission = pd.DataFrame(data=[scope1_emission_inT, scope2_emission_inT, scope3_emission_inT, totalEmission]).T
    df_emission.columns = ["emissionScope1", "emissionScope2", "emissionScope3", "totalEmission"]


    df_Sector=df_sector.copy()
    df_Sector = df_Sector.rename(columns={'sector_agg': 'Aggregated sectors', 'sector_code': 'Sector_Code', 'sector_name': 'Sector_Name'})

    print("df_io before merge", df_io.shape, df_io.head(5))
    print("df_Sector", df_Sector.shape, df_Sector.head(5))

    df_io = pd.merge(df_io, df_Sector[['Aggregated sectors', 'Sector_Code', 'Sector_Name']], on='Sector_Code')
    #df_io['Sector_CodeName'] = df_io['Sector_Code'].astype(str) + '/' + df_io['Sector_Name'].astype(str)
    print("df_io after merge", df_io.shape, df_io.head(5))
    #sys.exit()
    df_agg_sectors=get_io_aggregate(df_io, df_Sector, totalEmission, scope1_emission_inT, scope2_emission_inT, scope3_emission_inT)
    print("df_agg_sectors", df_agg_sectors.head(5))
    df_agg_sectors.to_csv(f"{folder_path_buf}/tmp/df_agg_sectors.csv", index=True)
    #sys.exit()

    df_agg_sectors_each=get_aggregate_each(df_io, df_emission, df_Sector)

    if (0):
        print ("Writing dataframe to csv...")

        mat_B = eeio_result["B"]
        mat_B_El = eeio_result["B_El"]
        mat_I = eeio_result["I"]
        mat_F = eeio_result["F"]
        mat_BF = eeio_result["BF"]
        mat_IminusA = eeio_result["IminusA"]
        mat_Inv = eeio_result["L"]
        mat_InvF = eeio_result["InvF"]
        mat_BInvF = eeio_result["BInvF"]
        mat_AF = eeio_result["AF"]
        mat_BAF = eeio_result["BAF"]

        pd.DataFrame(X_io).to_csv(f"{folder_path_buf}/X_io.csv", index=False)
        pd.DataFrame(X_total).to_csv(f"{folder_path_buf}/X_total.csv", index=False)
        pd.DataFrame(X_domOut).to_csv(f"{folder_path_buf}/X_domOut.csv", index=False)
        pd.DataFrame(totalEmission).to_csv(f"{folder_path_buf}/totalEmission.csv", index=False)
        pd.DataFrame(scope1_emission_inT).to_csv(f"{folder_path_buf}/scope1_emission_inT.csv", index=False)
        pd.DataFrame(scope2_emission_inT).to_csv(f"{folder_path_buf}/scope2_emission_inT.csv", index=False)
        pd.DataFrame(scope3_emission_inT).to_csv(f"{folder_path_buf}/scope3_emission_inT.csv", index=False)
        pd.DataFrame(mat_A).to_csv(f"{folder_path_buf}/mat_A.csv", index=False)
        pd.DataFrame(mat_B).to_csv(f"{folder_path_buf}/mat_B.csv", index=False)
        pd.DataFrame(mat_B_El).to_csv(f"{folder_path_buf}/mat_B_El.csv", index=False)
        pd.DataFrame(mat_I).to_csv(f"{folder_path_buf}/mat_I.csv", index=False)
        pd.DataFrame(mat_F).to_csv(f"{folder_path_buf}/mat_F.csv", index=False)
        pd.DataFrame(mat_BF).to_csv(f"{folder_path_buf}/mat_BF.csv", index=False)
        pd.DataFrame(mat_IminusA).to_csv(f"{folder_path_buf}/mat_IminusA.csv", index=False)
        pd.DataFrame(mat_Inv).to_csv(f"{folder_path_buf}/mat_Inv.csv", index=False)
        pd.DataFrame(mat_InvF).to_csv(f"{folder_path_buf}/mat_InvF.csv", index=False)
        pd.DataFrame(mat_BInvF).to_csv(f"{folder_path_buf}/mat_BInvF.csv", index=False)
        pd.DataFrame(mat_AF).to_csv(f"{folder_path_buf}/mat_AF.csv", index=False)
        pd.DataFrame(mat_BAF).to_csv(f"{folder_path_buf}/mat_BAF.csv", index=False)

        df_emissionIntensity.to_csv(f"{folder_path_buf}/result_emissionIntensity.csv", index=False)
        df_emission.to_csv(f"{folder_path_buf}/result_emission.csv", index=False)
        df_agg_sectors.to_csv(f"{folder_path_buf}/result_agg_sectors.csv", index=True)
        df_agg_sectors_each.to_csv(f"{folder_path_buf}/result_agg_sectors_each.csv",index=False)

    if (1):
        print ("Writing dataframe to database...")
        write_to_db(db, year, df_emissionIntensity, df_emission, df_agg_sectors, df_agg_sectors_each)

    return 1, "Matrices are successfully computed", df_agg_sectors, df_agg_sectors_each



if __name__ == '__main__':
    if (1):

        EEIO_FILES = ["IO", "FinalEnergyConsumption", "ConversionFactor", "DirectCO2EmmisionFactor"]
        YEAR=2020
        fname_io=f"io_ind_{YEAR}.csv"

        app = Flask(__name__)
        load_dotenv()
        db = db_init(app)
        with app.app_context():
            #rv, msg1, df_agg_sectors, df_agg_sectors_each=calc_mat(db, fname_io, YEAR)

            if (1):
                model = build_model_year(db, fname_io, YEAR)
                print(model.year, model.A.shape, model.L.shape)

                sys.exit()

            if (0):
                model1 = get_eeio_model(
                    cache_key=(2020, "io_ind_2020.csv"),
                    model_builder=lambda: build_model_year(
                        db=db,
                        fname_io="io_ind_2020.csv",
                        year=2020
                    )
                )

                model2 = get_eeio_model(
                    cache_key=(2020, "io_ind_2020.csv"),
                    model_builder=lambda: build_model_year(
                        db=db,
                        fname_io="io_ind_2020.csv",
                        year=2020
                    )
                )

                print("model1:", id(model1))
                print("model2:", id(model2))

                print(
                    "same object:",
                    model1 is model2
                )

                print(
                    "cache:",
                    get_cached_eeio_model_keys()
                )
            if (0):
                model2016 = get_eeio_model(
                    cache_key=(2016, "io_ind_2016.csv"),
                    model_builder=lambda: build_model_year(
                        db=db,
                        fname_io="io_ind_2016.csv",
                        year=2016
                    )
                )

                model2020 = get_eeio_model(
                    cache_key=(2020, "io_ind_2020.csv"),
                    model_builder=lambda: build_model_year(
                        db=db,
                        fname_io="io_ind_2020.csv",
                        year=2020
                    )
                )
                print(
                    get_cached_eeio_model_keys()
                )

                print(model2016.year)
                print(model2016.A.shape)
                print(model2016.L.shape)

                print(model2020.year)
                print(model2020.A.shape)
                print(model2020.L.shape)

            if (1):
                '''
                model = get_eeio_model(
                            cache_key=(2016, "io_ind_2016.csv"),
                            model_builder=lambda: build_model_year(
                                db=db,
                                fname_io="io_ind_2016.csv",
                                year=2016
                            )
                        )
                '''
                model = get_eeio_model(
                            cache_key=(2020, "io_ind_2020.csv"),
                            model_builder=lambda: build_model_year(
                                db=db,
                                fname_io="io_ind_2020.csv",
                                year=2020
                            )
                        )
                df_sector=get_sector(db)

                if (0):
                    print("Test 0: zero-change scenario")
                    #scenario = model.calculate_scenario()
                    baseline = model.calculate_baseline()
                    #print(f'scenario["totalEmission"] = {scenario["totalEmission"]}, baseline["totalEmission"] = {baseline["totalEmission"]}')
                    #print(f'sum of scenario["totalEmission"] = {np.sum(scenario["totalEmission"])}')
                    #print(f'sum of baseline["totalEmission"] = {np.sum(baseline["totalEmission"])}')

                if (0):
                    print("Test 1: Fuel reduction only")
                    scenario = model.calculate_scenario(
                        fuel_demand_change=-0.20
                    )
                    #print(f'scenario["totalEmission"] = {scenario["totalEmission"][0:3]}')
                    print(f'sum of scenario["totalEmission"] = {np.sum(scenario["totalEmission"])}')

                    print("Test 2: Electricity increase only")
                    scenario = model.calculate_scenario(
                        electricity_demand_change=0.15
                    )
                    #print(f'scenario["totalEmission"] = {scenario["totalEmission"][0:3]}')
                    print(f'sum of scenario["totalEmission"] = {np.sum(scenario["totalEmission"])}')
                if (0):
                    print("Test 3: EV/electrification example")
                    scenario = model.calculate_scenario(
                        fuel_demand_change=-0.20,
                        electricity_demand_change=0.15,
                        electricity_emission_factor_change=-0.30
                    )
                    #print(f'scenario["totalEmission"] = {scenario["totalEmission"][0:3]}')
                    print(f'sum of scenario["totalEmission"] = {np.sum(scenario["totalEmission"])}')

                    print("Test 4: based on the result of Test 3, compare sum of baseline and scenario")
                    for key in [
                        "emissionScope1",
                        "emissionScope2",
                        "emissionScope3",
                        "totalEmission"
                    ]:
                        print(
                            key,
                            np.sum(
                                baseline[key]
                            ),
                            np.sum(
                                scenario[key]
                            )
                        )
                if (0):
                    print("Diagnostic A — Scenario energy matrix")
                    print(
                        "Fuel baseline:",
                        np.sum(model.mat_finCons[1,:])
                    )
                    print(
                        "Fuel scenario:",
                        np.sum(scenario["mat_finCons"][1,:])
                    )
                    print(
                        "Electricity baseline:",
                        np.sum(model.mat_finCons[3,:])
                    )
                    print(
                        "Electricity scenario:",
                        np.sum(scenario["mat_finCons"][3,:])
                    )
                    print("Diagnostic B — Scenario B")
                    print(
                        "Baseline B:",
                        np.sum(model.B)
                    )
                    print(
                        "Scenario B:",
                        np.sum(scenario["B"])
                    )
                if (0):
                    baseline = model.calculate_baseline()
                    scenario = model.calculate_scenario(
                        fuel_demand_change=-0.20,
                        electricity_demand_change=0.15,
                        electricity_emission_factor_change=-0.30
                    )
                    comparison = compare_baseline_scenario(
                        baseline,
                        scenario
                    )
                    if (0):
                        total = comparison["totalEmission"]
                        #print(total)
                        for key in total.keys():
                            #print(f'{key}: {total[key]}')
                            print(f'{key}: {total[key][:4]}')

                        print("\nSummary of comparison:")
                        summary = summarize_comparison(
                            comparison
                        )
                        for key in summary.keys():
                            #print(f'{key}: {summary[key]}')
                            print(f'{key}')
                            for subkey in summary[key].keys():
                                print(f'\t{subkey}: {summary[key][subkey]}')
                    if (0):
                        sector_result = analyze_sector_impacts(
                            comparison,
                            df_sector
                        )
                        print("sector_result head")
                        print(sector_result.head())

                        top_reduction = rank_sector_reductions(
                            sector_result,
                            n=10
                        )
                        print("top_reduction head")
                        print(top_reduction.head())

                        top_increase = rank_sector_increases(
                            sector_result,
                            n=10
                        )
                        print("top_increase head")
                        print(top_increase.head())
                        if (0):
                            print(
                                "Maximum difference:",
                                sector_result["difference"].max()
                            )

                            print(
                                "Minimum difference:",
                                sector_result["difference"].min()
                            )
                        if (1):
                            cnt_sector_chg=count_sector_changes(sector_result)
                            print("CNT_SECTOR_CHANGES:")
                            for key in cnt_sector_chg.keys():
                                print(f'{key}: {cnt_sector_chg[key]}')

                        sector_result=calculate_reduction_contribution(
                            sector_result
                        )
                        print("sector_result head")
                        print(sector_result.head())

                        agg_result = aggregate_sector_impacts(
                            sector_result
                        )
                        print("agg_result head")
                        print(agg_result.head())

                        print('sector_result["difference"].sum()', sector_result["difference"].sum())
                        print('comparison["totalEmission"]["difference"].sum()', comparison["totalEmission"]["difference"].sum())
            if (0):
                baseline = model.calculate_baseline()
                scenario_parameters = {
                    "fuel_demand_change":
                        -0.20,
                    "electricity_demand_change":
                        0.15,
                    "electricity_emission_factor_change":
                        -0.30
                }
                scenario = model.calculate_scenario(
                    **scenario_parameters
                )
                comparison = compare_baseline_scenario(
                    baseline,
                    scenario
                )
                summary = summarize_comparison(
                    comparison
                )
                sector_result = analyze_sector_impacts(
                    comparison,
                    df_sector
                )
                agg_result = aggregate_sector_impacts(
                    sector_result
                )
                report = build_scenario_report(
                    year=2020,
                    scenario_name=
                        "Illustrative Electrification",
                    scenario_parameters=
                        scenario_parameters,
                    baseline_result=
                        baseline,
                    scenario_result=
                        scenario,
                    comparison=
                        comparison,
                    summary=
                        summary,
                    sector_results=
                        sector_result,
                    aggregate_results=
                        agg_result
                )
                print("\n>>report metadata:")
                for key in report.metadata.keys():
                    print(f'{key}: {report.metadata[key]}')

                print("\n>>report summary:")
                for key in report.summary.keys():
                    #print(f'{key}: {report.summary[key]}')
                    print(f'{key}:')
                    for subkey in report.summary[key].keys():
                        print(f'\t{subkey}: {report.summary[key][subkey]}')

                print("\n>>report comparison:")
                for key in report.comparison.keys():
                    #print(f'{key}: {report.comparison[key]}')
                    print(f'{key}:')
                    for subkey in report.comparison[key].keys():
                        print(f'\t{subkey}: {report.comparison[key][subkey][:4]}...')

                print("\n>>report sector_results:")
                for key in report.sector_results.keys():
                    print(f'{key}: {report.sector_results[key]}')

                print("\n>>report aggregate_results:")
                for key in report.aggregate_results.keys():
                    print(f'{key}: {report.aggregate_results[key]}')

            if (0):
                scenario_parameters = {
                    "fuel_demand_change": -0.20,
                    "electricity_demand_change": 0.15,
                    "electricity_emission_factor_change": -0.30
                }
                report = run_scenario(
                    model=model,
                    scenario_name=
                        "Illustrative Electrification",
                    scenario_parameters=
                        scenario_parameters,
                    df_sector=df_sector
                )
                print("\n>>report.metadata:")
                #print(report.metadata)
                for key in report.metadata.keys():
                    print(f'{key}: {report.metadata[key]}')

                print("\n>>report summary:")
                for key in report.summary.keys():
                    print(f'{key}:')
                    for subkey in report.summary[key].keys():
                        print(f'\t{subkey}: {report.summary[key][subkey]}')

                report.sector_results.head()
                print("\n>>report sector_results:")
                for key in report.sector_results.keys():
                    print(f'{key}: {report.sector_results[key]}')

            if (1):
                scenario_parameters = {
                    "fuel_demand_change": -0.20,
                    "electricity_demand_change": 0.15,
                    "electricity_emission_factor_change": -0.30
                }
                report = run_scenario(
                    model,
                    "Illustrative Electrification",
                    scenario_parameters,
                    df_sector
                )
                if (0):
                    print("\n>>report.metadata:")
                    #print(report.metadata)
                    for key in report.metadata.keys():
                        print(f'{key}: {report.metadata[key]}')

                    print("\n>>report.impact_statistics:")
                    for key in report.impact_statistics.keys():
                        if (key == "top_reduction_sectors"):
                            print(f'{key}:')
                            for item in report.impact_statistics['top_reduction_sectors']:  # Loop over list items
                                for key, value in item.items():         # Loop over inner key-value pairs
                                    print(f"\t{key}: {value}")
                        else:
                            print(f'{key}: {report.impact_statistics[key]}')
                if (1):
                    scenario_id = save_complete_scenario_report(
                        db,
                        report
                    )
                    print("scenario_id", scenario_id)
                    print("scenario_id", type(scenario_id))


