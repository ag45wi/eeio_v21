import pandas as pd
import os
from flask import Flask, send_file
from dotenv import load_dotenv
import plotly.graph_objects as go
from textwrap import wrap
from urllib.parse import unquote
import sys
import json
from plotly.subplots import make_subplots
import io
import time
from datetime import datetime, timedelta

from util_db import tbl_to_df, db_init

cwd = os.getcwd()

def get_year(db):
    try:
        q_str = "SELECT distinct year FROM result_emission"
        df = tbl_to_df(db, q_str)
        lst = df[['year']].values.ravel().tolist()
    except:
        print (f"Cannot read result_emission table:: {q_str}")
        return []

    return lst

def get_dct_agg(db):

    #fpath = cwd+f"/static/data/{year}/agg_sectors_lst.csv"
    #if (not os.path.exists(fpath)):
    #    return []

    try:
        q_str = "SELECT id, name FROM sector_agg ORDER by id"
        df = tbl_to_df(db, q_str)

        # Trim trailing whitespace from 'sector_name' column
        df['name'] = df['name'].str.strip()

        df.set_index('id', inplace=True)
        sector_dict = df['name'].to_dict()
        #lst = df_agg[['name']].values.ravel().tolist()
    except:
        print (f"Cannot read sector_agg table:: {q_str}")
        sector_dict={}

    #df_lagg = pd.read_csv(fpath)
    #print("df_lagg", df_lagg.head(5)
    #lst = df_lagg['Aggregated sectors'].tolist()
    #lst.insert(0, "--All_Sectors--")

    #print("lst", lst)

    return sector_dict

def get_dct_sector(db):

    try:
        q_str = "SELECT sector_code, sector_name FROM sector_lst WHERE sector_label='DET' ORDER by sector_code"
        df = tbl_to_df(db, q_str)

        # Trim trailing whitespace from 'sector_name' column
        df['sector_name'] = df['sector_name'].str.strip()

        df.set_index('sector_code', inplace=True)
        sector_dict = df['sector_name'].to_dict()
        #lst = df[['name']].values.ravel().tolist()
    except:
        print (f"Cannot read sector_agg table:: {q_str}")
        sector_dict={}

    #df_lagg = pd.read_csv(fpath)
    #print("df_lagg", df_lagg.head(5)
    #lst = df_lagg['Aggregated sectors'].tolist()
    #lst.insert(0, "--All_Sectors--")

    #print("lst", lst)

    return sector_dict

def get_dct_sectorByAgg(db):
    #eg., dct={'01':['037','038','039'], '02': ['001', '002'], '03': ['031','032']}

    try:
        q_str = "SELECT sector_agg, sector_code, sector_name FROM sector_lst WHERE sector_label='DET' ORDER by sector_code"
        df = tbl_to_df(db, q_str)

        #q_str = "SELECT id, name FROM sector_agg ORDER by id"
        #df_agg = tbl_to_df(db, q_str)

        #print("df_sector", df)
        #print("df_agg", df_agg)

        grouped = df.groupby('sector_agg')
        #print("Grouped object:", grouped)  # Shows DataFrameGroupBy object

        sector_lists = grouped['sector_code'].apply(list)
        #print("Series with lists:", sector_lists)  # Shows the Series before converting to dict

        dct = sector_lists.to_dict()
        #print("Final dictionary:", dct)  # Shows the desired output

    except:
        print (f"Cannot read sector_agg table:: {q_str}")
        dct={}

    return dct

def generate_multichart(db, in_sector_lst, year):
    #in_agg is a list of sector_agg

    #print("inside generate_multichart", in_sector_lst, type(in_sector_lst))

    #cwd = os.getcwd()

    q_str = f"""
    SELECT energy_type, fec_val, energy_lst.energy_type_code, energy_lst.energy_type_name
    FROM fec_byyear
    LEFT JOIN energy_lst ON fec_byyear.energy_type = energy_lst.energy_type_code
    WHERE year='{year}' ORDER BY energy_type"""

    df_fec = tbl_to_df(db, q_str)
    #df_fec.set_index('energy_type', inplace=True)
    #df_fec = df_fec.transpose()
    #print("df_fec", df_fec.head(5))
    fec_type=df_fec["energy_type_name"].values.tolist()

    fec_val=df_fec["fec_val"].values.tolist()
    print("fec_type", fec_type, "\nfec_val", fec_val)

    #sys.exit()

    formatted_sector_codes = ", ".join(f"'{code}'" for code in in_sector_lst)

    q_str = f"SELECT sector_code, sector_name, emissionIntensityScope1, emissionIntensityScope2, emissionIntensityScope3 FROM result_emission_intensity WHERE year='{year}' and sector_code in ({formatted_sector_codes})"
    df_EI = tbl_to_df(db, q_str)
    #print("df_EI", df_EI.head(5))
    emissionIntensity_type=["emissionIntensityScope1", "emissionIntensityScope2", "emissionIntensityScope3"]
    emissionIntensity_val=[df_EI["emissionIntensityScope1"].values.sum(),
                           df_EI["emissionIntensityScope2"].values.sum(),
                           df_EI["emissionIntensityScope3"].values.sum()]
    emissionIntensity_val = [float(item) for item in emissionIntensity_val]
    print("emissionIntensity_type", emissionIntensity_type, "\nemissionIntensity_val", emissionIntensity_val)

    q_str = f"SELECT sector_code, sector_name, emissionScope1, emissionScope2, emissionScope3 FROM result_emission WHERE year='{year}' and sector_code in ({formatted_sector_codes})"
    #print("q_str", q_str)
    df_emission = tbl_to_df(db, q_str)
    print("df_emission", df_emission.head(5))
    emission_type=["emissionScope1", "emissionScope2", "emissionScope3"]
    emission_val=[df_emission["emissionScope1"].values.sum(),
                  df_emission["emissionScope2"].values.sum(),
                  df_emission["emissionScope3"].values.sum()]
    emission_val = [float(item) for item in emission_val]
    print("emission_type", emission_type, "\nemission_val", emission_val)


    # Create subplots: 1 row, 3 columns
    fig = make_subplots(rows=1, cols=3,
                        subplot_titles=(f"Final Energy Consumption",
                                        f"Emission Intensity Scopes",
                                        f"Emission Scopes"),
                        horizontal_spacing=0.1)

    # Add the first bar chart
    fig.add_trace(
        go.Bar(
            x=fec_type,
            y=fec_val,
            name='FEC Value',
            marker_color=["#DBDBDB", "#C9B194", "#A08963", "#706D54"], # Custom colors, #https://colorhunt.co/palettes/gradient
            marker_line_color="#706D54",
            marker_line_width=1.0
        ),
        row=1, col=1
    )

    # Add the second bar chart
    fig.add_trace(
        go.Bar(
            x=["Scope1", "Scope2", "Scope3"],
            y=emissionIntensity_val,
            name='Emission Intensity Value',
            marker_color=['#9EBC8A', '#73946B', '#537D5D'], # Custom colors
            marker_line_color="#537D5D",
            marker_line_width=1.0
        ),
        row=1, col=2
    )

    # Add the 3rd bar chart
    fig.add_trace(
        go.Bar(
            x=["Scope1", "Scope2", "Scope3"], #emission_type,
            y=emission_val,
            name='Emission Value',
            marker_color=['#3ABEF9', '#3572EF', '#050C9C'], # Custom colors
            marker_line_color="#050C9C",
            marker_line_width=1.0
        ),
        row=1, col=3
    )

    # Update layout for titles and axis labels
    fig.update_layout(
        #title_text="Energy and Emission Data Visualizations",
        #title_font_size=20,
        height=350, # Set a fixed height for better side-by-side viewing
        showlegend=False, # Hide legend if not necessary
        # Adjust margins to prevent titles from overlapping
        margin=dict(l=50, r=50, t=50, b=50),

    )

    # --- Reduce subplot title font size here ---
    fig.update_annotations(font_size=14) # Default is often 16, try 14 or 12

    # Update x-axis and y-axis titles for the first subplot
    fig.update_xaxes(title_text="Energy Type", row=1, col=1)
    fig.update_yaxes(title_text="FEC Value (in Thousand BOE)", row=1, col=1, title_standoff=0, title_font_size=12)

    # Update x-axis and y-axis titles for the 2nd subplot
    fig.update_xaxes(title_text=f"Emission Intensity Type<br>Total: {sum(emissionIntensity_val):,.6f} TCO<sub>2</sub>/Mil. IDR", row=1, col=2)
    fig.update_yaxes(title_text="Emission Intensity (in TCO<sub>2</sub>/Mil. IDR)", row=1, col=2, title_standoff=0, title_font_size=12)

    # Update x-axis and y-axis titles for the 3rd subplot
    fig.update_xaxes(title_text=f"Emission Type<br>Total: {sum(emission_val):,.2f} TCO<sub>2</sub>", row=1, col=3)
    fig.update_yaxes(title_text="Emission Value (in TCO<sub>2</sub>)", row=1, col=3, title_standoff=0, title_font_size=12)


    # Convert the figure to JSON for embedding in HTML
    plotly_json = fig.to_json()

    return plotly_json


def generate_multichart_years(db, in_sector_lst, years):
    #in_sector_lst, years are lists

    print("inside generate_multichart_years", in_sector_lst, type(in_sector_lst), years)

    formatted_years = ", ".join(f"'{year}'" for year in years)

    q_str = f"""
    SELECT year, energy_type, fec_val, energy_lst.energy_type_code, energy_lst.energy_type_name
    FROM fec_byyear
    LEFT JOIN energy_lst ON fec_byyear.energy_type = energy_lst.energy_type_code
    WHERE year in ({formatted_years}) ORDER BY year and energy_type"""
    print("query:", q_str)

    df_fec = tbl_to_df(db, q_str)
    print("df_fec", df_fec.shape)

    color_palette_marker = ["#03FD57", "#FD4040", "#2BC1FD", "#FDDA40", "#F83AFF"] # Add more if you have more years
    year_lst=get_year(db)
    #print("type(year_lst[0])", type(year_lst[0]), year_lst) #year item's type is integer
    color_palette_byyear={year_lst[i]: color_palette_marker[i% len(color_palette_marker)] for i in range(len(year_lst))}
    print("color_palette_byyear", color_palette_byyear)

    df=df_fec.copy()
    fec_type_all = df['energy_type_name'].unique().tolist()
    color_palette = ["#F5EAEA", "#EBB7B7", "#A85656", "#830808"] # Add more if you have more years

    yearly_data = [];
    for i, year in enumerate(years): # Use enumerate to get an index for color selection
        year_str = str(year) # Convert year to string for 'name' if preferred
        #print("year", year, type(year)) #year is integer
        #sys.exit()
        yearly_data.append({
            'year': int(year),
            'values': df[df['year'] == year]['fec_val'].tolist(),
            #'colors': [color_palette[ix % len(color_palette)] for ix in range(len(fec_type_all))], # Cycle through colors if more years than colors % len(color_palette)], # Cycle through colors if more years than colors
            'colors': color_palette,
            #'marker_line_color': color_palette_marker[i % len(color_palette_marker)],
            'marker_line_color': color_palette_byyear[year],
            'name': year_str
        })

    print("yearly_data", yearly_data)

    #emissionIntensity------------------------------------------------------------------------------
    formatted_sector_codes = ", ".join(f"'{code}'" for code in in_sector_lst)

    q_str = f"SELECT year, sector_code, sector_name, emissionIntensityScope1, emissionIntensityScope2, emissionIntensityScope3 FROM result_emission_intensity WHERE year in ({formatted_years}) and sector_code in ({formatted_sector_codes})"
    df_EI = tbl_to_df(db, q_str)
    print("df_EI", df_EI.head(5))

    emissionIntensity_type=["emissionIntensityScope1", "emissionIntensityScope2", "emissionIntensityScope3"]
    emissionIntensity_val=[df_EI["emissionIntensityScope1"].values.sum(),
                           df_EI["emissionIntensityScope2"].values.sum(),
                           df_EI["emissionIntensityScope3"].values.sum()]
    emissionIntensity_val = [float(item) for item in emissionIntensity_val]
    print("emissionIntensity_type", emissionIntensity_type, "\nemissionIntensity_val", emissionIntensity_val)

    df=df_EI.copy()
    emission_x_label=["Scope1", "Scope2", "Scope3"]
    color_palette = ["#D6E4CD", '#9EBC8A', '#537D5D'] # Add more if you have more years

    yearly_data_EI = [];
    for i, year in enumerate(years): # Use enumerate to get an index for color selection
        year_str = str(year) # Convert year to string for 'name' if preferred

        yearly_data_EI.append({
            'year': int(year),
            #'values': df[df['year'] == year]['fec_val'].tolist(),
            'values': [float(df[df['year'] == year]["emissionIntensityScope1"].values.sum()),
                       float(df[df['year'] == year]["emissionIntensityScope2"].values.sum()),
                       float(df[df['year'] == year]["emissionIntensityScope3"].values.sum())],
            'colors': color_palette,
            #'marker_line_color': color_palette_marker[i % len(color_palette_marker)],
            'marker_line_color': color_palette_byyear[year],
            'name': year_str
        })

    print("yearly_data_EI", yearly_data_EI)
    #sys.exit()

    #emission------------------------------------------------------------------------------
    q_str = f"SELECT year, sector_code, sector_name, emissionScope1, emissionScope2, emissionScope3 FROM result_emission WHERE year in ({formatted_years}) and sector_code in ({formatted_sector_codes})"
    #print("q_str", q_str)
    df_emission = tbl_to_df(db, q_str)
    print("df_emission", df_emission.head(5))
    emission_type=["emissionScope1", "emissionScope2", "emissionScope3"]
    emission_val=[df_emission["emissionScope1"].values.sum(),
                  df_emission["emissionScope2"].values.sum(),
                  df_emission["emissionScope3"].values.sum()]
    emission_val = [float(item) for item in emission_val]
    print("emission_type", emission_type, "\nemission_val", emission_val)

    df=df_emission.copy()
    emission_x_label=["Scope1", "Scope2", "Scope3"]
    color_palette = ["#B0E5FD", "#4783FC", '#050C9C'] # Add more if you have more years

    yearly_data_emission = [];
    for i, year in enumerate(years): # Use enumerate to get an index for color selection
        year_str = str(year) # Convert year to string for 'name' if preferred

        yearly_data_emission.append({
            'year': int(year),
            'values': [float(df[df['year'] == year]["emissionScope1"].values.sum()),
                       float(df[df['year'] == year]["emissionScope2"].values.sum()),
                       float(df[df['year'] == year]["emissionScope3"].values.sum())],
            'colors': color_palette,
            #'marker_line_color': color_palette_marker[i % len(color_palette_marker)],
            'marker_line_color': color_palette_byyear[year],
            'name': year_str
        })

    print("yearly_data_emission", yearly_data_emission)
    #sys.exit()

    # Create subplots==========================================================================
    fig = make_subplots(rows=1, cols=3,
                    subplot_titles=(f"Final Energy Consumption",
                                    f"Emission Intensity Scopes",
                                    f"Emission Scopes"),
                    horizontal_spacing=0.1)

    for data_entry in yearly_data:
        fig.add_trace(
            go.Bar(
                x=fec_type_all,  # X-axis remains the energy types
                y=data_entry['values'], # Y-values for the current year
                name=data_entry['name'], # Name for the legend (e.g., '2016', '2020')
                marker_color=data_entry['colors'], # Color for the current year's bars
                marker_line_color=data_entry['marker_line_color'],
                marker_line_width=2.0,
                showlegend=True
            ),
            row=1, col=1
        )

    for data_entry in yearly_data_EI:
        fig.add_trace(
            go.Bar(
                x=emission_x_label,
                y=data_entry['values'], # Y-values for the current year
                name=data_entry['name'], # Name for the legend (e.g., '2016', '2020')
                marker_color=data_entry['colors'], # Color for the current year's bars
                marker_line_color=data_entry['marker_line_color'],
                marker_line_width=1.5,
                showlegend=True
            ),
            row=1, col=2
        )

    for data_entry in yearly_data_emission:
        fig.add_trace(
            go.Bar(
                x=emission_x_label,
                y=data_entry['values'], # Y-values for the current year
                name=data_entry['name'], # Name for the legend (e.g., '2016', '2020')
                marker_color=data_entry['colors'], # Color for the current year's bars
                marker_line_color=data_entry['marker_line_color'],
                marker_line_width=1.5,
                showlegend=True
            ),
            row=1, col=3
        )

    # Update layout for titles and axis labels
    fig.update_layout(
        #title_text="Energy and Emission Data Visualizations",
        #title_font_size=20,
        height=350, # Set a fixed height for better side-by-side viewing
        showlegend=True, # Hide legend if not necessary
        # Adjust margins to prevent titles from overlapping
        margin=dict(l=50, r=50, t=50, b=50),

    )

    # --- Reduce subplot title font size here ---
    fig.update_annotations(font_size=14) # Default is often 16, try 14 or 12

    # Update x-axis and y-axis titles for the first subplot
    fig.update_xaxes(title_text="Energy Type", row=1, col=1)
    fig.update_yaxes(title_text="FEC Value (in Thousand BOE)", row=1, col=1, title_standoff=0, title_font_size=12)

    # Update x-axis and y-axis titles for the 2nd subplot
    fig.update_xaxes(title_text=f"Emission Intensity Type<br>Total: {sum(emissionIntensity_val):,.6f} TCO<sub>2</sub>/Mil. IDR", row=1, col=2)
    fig.update_yaxes(title_text="Emission Intensity (in TCO<sub>2</sub>/Mil. IDR)", row=1, col=2, title_standoff=0, title_font_size=12)

    # Update x-axis and y-axis titles for the 3rd subplot
    fig.update_xaxes(title_text=f"Emission Type<br>Total: {sum(emission_val):,.2f} TCO<sub>2</sub>", row=1, col=3)
    fig.update_yaxes(title_text="Emission Value (in TCO<sub>2</sub>)", row=1, col=3, title_standoff=0, title_font_size=12)
    #sys.exit()


    # Convert the figure to JSON for embedding in HTML
    plotly_json = fig.to_json()

    return plotly_json


#https://www.tutorialspoint.com/how-to-delete-all-files-in-a-directory-with-python
def delete_files_in_directory(directory_path):
    print("inside delete_files_in_directory")
    files = os.listdir(directory_path)
    for file in files:
        file_path = os.path.join(directory_path, file)
        if os.path.isfile(file_path):
            os.remove(file_path)

# Function to format integers and floats
def format_column(column):
    if column.dtype in ['int64', 'int32']:
        # Format integers with thousand separator
        return column.apply(lambda x: f"{x:,}")
    elif column.dtype in ['float64', 'float32']:
        # Format floats with thousand separator and 2 decimal places
        return column.apply(lambda x: f"{x:,.2f}")
    else:
        # Return unchanged for other types
        return column

def format_df(df):
    #print(df.head(5))
    formatted_df = df.copy()  # Keep original DataFrame intact
    for col in formatted_df.columns:
        #print(f"col '{col}'", type(formatted_df[col]), formatted_df[col])
        formatted_df[col] = format_column(formatted_df[col])
    return formatted_df

def get_df_input(fnames, year):
    folder_path = f'{cwd}/static/data/{year}'

    fname_io=f"{fnames[0]}.csv"
    fname_fec=f"{fnames[1]}.csv"
    fname_conv=f"{fnames[2]}.csv"
    fname_co2=f"{fnames[3]}.csv"

    file_path=f"{folder_path}/{fname_io}"
    df_io = pd.read_csv(file_path, index_col=False)
    df_io.columns = df_io.columns.str[:20] #beware of truncating the column names !, may have the same trucated file names if too short

    file_path=f"{folder_path}/{fname_fec}"
    df_fec = pd.read_csv(file_path, index_col=False)

    file_path=f"{folder_path}/{fname_conv}"
    df_conv = pd.read_csv(file_path, index_col=False)

    file_path=f"{folder_path}/{fname_co2}"
    df_co2 = pd.read_csv(file_path, index_col=False)

    df_io_fmt=format_df(df_io);
    df_fec_fmt=format_df(df_fec);
    df_conv_fmt=format_df(df_conv);
    df_co2_fmt=format_df(df_co2)

    return df_io_fmt, df_fec_fmt, df_conv_fmt, df_co2_fmt

def save_file(file, fname, year):
    folder_path = f'{cwd}/static/data/{year}'
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)

    file_path=f"{folder_path}/{fname}"
    if (file):
        df= pd.read_csv(file)
        df.to_csv(file_path, index=False)
        msg=f"File {file.filename} has been saved to {file_path}"
        return 1, msg

    msg=f"File {file.filename} has not been saved"
    return 0, msg

def clean_list_items(item_list):
    # Internal development comment removed for public distribution.
    """
    Cleans up a list by replacing '%20' with spaces in each string item.

    Args:
        item_list: A list of strings, some of which may contain '%20'.

    Returns:
        A new list with '%20' replaced by spaces in all string items.
    """
    cleaned_list = []
    for item in item_list:
        if isinstance(item, str):  # Ensure we only process string items
            cleaned_list.append(item.replace('%20', ' '))
        else:
            cleaned_list.append(item)  # Keep non-string items as they are
    return cleaned_list


def clean_old_csv_files(dl_folder):
    """
    Deletes CSV files in DOWNLOAD_FOLDER that are older than CLEANUP_THRESHOLD_SECONDS.
    """
    now = time.time() # Get current time in seconds since epoch
    cleanup_threshold_seconds = 600 # 10 minutes * 60 seconds/minutes

    for filename in os.listdir(dl_folder):
        if filename.endswith(".csv"): # Only process CSV files
            file_path = os.path.join(dl_folder, filename)
            try:
                # Get the last modification time of the file
                file_mtime = os.path.getmtime(file_path)
                file_age_seconds = now - file_mtime

                #print("file_path, file_age_seconds, cleanup_threshold_seconds", file_path, file_age_seconds, cleanup_threshold_seconds)

                if file_age_seconds > cleanup_threshold_seconds:
                    os.remove(file_path)
                    print(f"Cleaned up old CSV file: {filename} (age: {file_age_seconds:.0f} seconds)")
            except OSError as e:
                # Handle cases where file might be deleted by another process
                # or permissions issues
                print(f"Error cleaning up file {filename}: {e}")
            except Exception as e:
                print(f"Unexpected error during cleanup of {filename}: {e}")

def generate_csv(db, sector_lst, years, report_type="FEC", dl_folder=None):
    print("generate_csv", report_type)

    if dl_folder is None:
        print("dl_folder is None")
        return

    clean_old_csv_files(dl_folder)

    formatted_years = ", ".join(f"'{year}'" for year in years)

    if (report_type=="FEC"):
        q_str = f"""SELECT year, energy_type, fec_val,
        energy_lst.energy_type_code, energy_lst.energy_type_name
        FROM fec_byyear
        LEFT JOIN energy_lst ON fec_byyear.energy_type = energy_lst.energy_type_code
        WHERE year in ({formatted_years})
        """
        print("query:", q_str)
        df=tbl_to_df(db, q_str)
        df=df[['year', 'energy_type_code', 'energy_type_name', 'fec_val']].copy()

    elif (report_type=="emissionIntensity"):
        formatted_sector_codes = ", ".join(f"'{code}'" for code in sector_lst)

        q_str = f"""SELECT year, sector_agg, sector_code, sector_name, emissionIntensityScope1, emissionIntensityScope2, emissionIntensityScope3,
        sector_agg.id, sector_agg.name as sector_agg_name
        FROM result_emission_intensity
        LEFT JOIN sector_agg ON result_emission_intensity.sector_agg = sector_agg.id
        WHERE year in ({formatted_years}) and sector_code in ({formatted_sector_codes})
        """
        print("query:", q_str)
        df=tbl_to_df(db, q_str)
        print("df columns", df.columns.to_list())
        df=df[['year', 'sector_agg', 'sector_agg_name', 'sector_code', 'sector_name', 'emissionIntensityScope1', 'emissionIntensityScope2', 'emissionIntensityScope3']].copy()
        df['emissionIntensityTotal'] = df[['emissionIntensityScope1', 'emissionIntensityScope2', 'emissionIntensityScope3']].sum(axis=1)

    elif (report_type=="emission"):
        formatted_sector_codes = ", ".join(f"'{code}'" for code in sector_lst)

        q_str = f"""SELECT year, sector_agg, sector_code, sector_name, emissionScope1, emissionScope2, emissionScope3,
        sector_agg.id, sector_agg.name as sector_agg_name
        FROM result_emission
        LEFT JOIN sector_agg ON result_emission.sector_agg = sector_agg.id
        WHERE year in ({formatted_years}) and sector_code in ({formatted_sector_codes})
        """
        print("query:", q_str)
        df=tbl_to_df(db, q_str)
        print("df columns", df.columns.to_list())
        df=df[['year', 'sector_agg', 'sector_agg_name', 'sector_code', 'sector_name', 'emissionScope1', 'emissionScope2', 'emissionScope3']].copy()
        df['emissionTotal'] = df[['emissionScope1', 'emissionScope2', 'emissionScope3']].sum(axis=1)

    print("df", df.head(5))

    if (len(years)>1): fmt_year=f"{years[0]}_{years[-1]}"
    else: fmt_year=f"{years[0]}"

    fname_noext=f"report_{report_type}__{fmt_year}"
    sector_tag="sector_cnt"+str(len(sector_lst))+"_code"+sector_lst[0]+"_"+sector_lst[-1]
    if (report_type=="FEC"):
        fname=f"{fname_noext}.csv"
    elif (report_type=="emissionIntensity" or report_type=="emission"):
        fname=f"{fname_noext}__{sector_tag}.csv"
    else:
        print("Undefined report_type", report_type)
        fname=f"{fname_noext}__default.csv"
    print("fname", fname)

    dl_path=f"{dl_folder}/{fname}"
    print(f"DL Path: {dl_path}")
    df.to_csv(dl_path, index=False)

    return fname

if __name__ == '__main__':

    if (0):
        ##app = Flask(__name__)
        ##load_dotenv()
        ##db = db_init(app)
        ##with app.app_context():
            '''
            lst_agg=get_lst_agg(db, 2016)
            print("lst_agg", lst_agg)

            lst_year=get_year(db)
            print("lst_year", lst_year)
            '''
            #rv=generate_multichart(db, ['001', '002'], 2016)
            ##generate_multichart_years(db, ['001', '002'], [2016, 2020, 2024])

            #get_dct_sector_byAgg(db)
            #generate_csv(db, ['001', '002'], 2016, report_type="emission_intensity")


    if (0):
        '''
        # Example DataFrame
        data = {
            "Integers": [1000, 2000, 3000],
            "Floats": [1234.567, 8901.234, 5678.9],
            "Strings": ["A", "B", "C"]
        }
        df = pd.DataFrame(data)
        print(df.head(5))

        # Apply formatting to each column based on its type
        formatted_df = df.copy()  # Keep original DataFrame intact
        for col in formatted_df.columns:
            print("col", col, type(formatted_df[col]), formatted_df[col])
            formatted_df[col] = format_column(formatted_df[col])

        print("Formatted DataFrame:")
        print(formatted_df)
        '''
    if (0):
        '''
        lst=get_lst_agg()
        print("lst", lst)

        #--------------------------
        fig=generate_chart_agg()
        #fig.show()
        img = io.BytesIO()
        fig.savefig(img, format='png')
        '''
