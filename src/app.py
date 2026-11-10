from flask import Flask, render_template, Response, request, redirect, url_for, session, send_file, jsonify, send_from_directory, abort
from flask_mail import Mail, Message
import os
import pandas as pd
import numpy as np
import re
from dotenv import load_dotenv
import sys
import traceback
from time import perf_counter
from statistics import median

app = Flask(__name__)

from util_app import get_year, get_dct_agg, get_dct_sector, get_dct_sectorByAgg,  generate_multichart, generate_multichart_years, delete_files_in_directory, format_df, get_df_input, save_file, clean_list_items, generate_csv
from util_mat import calc_mat, build_model_year, get_sector
from util_db import db_init, get_hash_password, login_prompt, login_register, login_changePassword, get_table_user, verify_user, verify_email_byToken, delete_user
from eeio_engine import get_eeio_model, get_cached_eeio_model_keys, run_scenario
from util_export import (
    scenario_summary_to_csv,
    scope_comparison_to_csv,
    sector_impact_to_csv,
    scenario_report_to_excel,
    make_scenario_filename,
    scenario_target_to_csv,
    target_energy_impact_to_csv,
    target_emission_impact_to_csv
)

from util_scenario import (
    save_complete_scenario_report,
    get_scenario_history,
    load_scenario_report,
    prepare_scenario_display,
    compare_scenario_reports,
    scenario_exists,
    get_existing_scenario_id,
    get_scenario_list,
    get_scenario_definition,
    format_scenario_options,
    insert_scenario_definition,
    validate_scenario_definition,
    scenario_definition_exists
)
from util_scenario_v21 import (
    get_canonical_scenario_list_v21,
    run_or_load_scenario_v21,
    prepare_scenario_display_v21,
    get_scenario_history_v21,
    load_scenario_report_v21,
    get_scenario_history_list_v21,
    compare_scenario_reports_v21,
    create_scenario_v21,
    get_scenario_definition_v21,
    get_available_parameter_rules_v21,
    validate_scenario_definition_v21,
    get_active_scenario_types_v21,
    benchmark_scenario_v21,
    get_parameter_options_by_scenario_type,
    get_implemented_parameter_options_v21
)
from scenario_engine import (
    ScenarioLoader,
    ScenarioNotRunnableError
)
from config_scenario import (
    PARAMETER_RULES_V21,
    ENERGY_OPTIONS,
    SCENARIO_TYPES_V21,
    V21_VALIDATION_DIAGNOSTICS,
    CUSTOM_SCENARIO_TYPE_CODE_V21,
    PERIOD_YEAR
)

app.secret_key = os.environ["APP_KEY"]

app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ["MAIL_USERNAME"]
app.config['MAIL_PASSWORD'] = os.environ["MAIL_PASSWORD"]  # Use an app-specific password

load_dotenv()
db = db_init(app)

from manufacturing_research_routes import register_manufacturing_research_routes
register_manufacturing_research_routes(app, db)

mail = Mail(app)

DOWNLOAD_FOLDER = os.path.join(app.root_path, 'static', 'buf', 'download')
#os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

#YEAR_LST=[] #get_year(db) #[2016]
YEAR=0 #YEAR_LST[0]
EEIO_FILES = ["IO", "FinalEnergyConsumption", "ConversionFactor", "DirectCO2EmmisionFactor"]

@app.route('/nav_home', methods=['GET', 'POST'])
#@app.route('/', methods=['GET', 'POST'])
def nav_home(): # This is the function name to pass to url_for()
    global YEAR
    print("inside nav_home")

    year_lst=get_year(db)
    #print("year_lst", year_lst)
    dct_year = {
       str(year): [year] for year in year_lst
    }
    dct_year['ALL'] = year_lst.copy()
    print("dct_year", dct_year)
    YEAR=list(dct_year.items())[0]

    dct_sector=get_dct_sector(db) #code: name
    dct_agg=get_dct_agg(db) #code: name
    dct_sector_byAgg=get_dct_sectorByAgg(db) #code_agg: list of sector codes
    #print("dct_sector_byAgg-0", len(dct_sector_byAgg), list(dct_sector_byAgg.items())[:5])

    dct_agg["ALL"]="ALL"
    #all_values = sorted({code for codes in dct_sector_byAgg.values() for code in codes})
    all_values=list(dct_sector.keys())
    #print("all_values", len(all_values), all_values)

    dct_sector_byAgg['ALL'] = all_values
    print("dct_agg", dct_agg)
    #print("dct_sector", dct_sector)
    #print("dct_sector_byAgg", dct_sector_byAgg)

    return render_template("nav_display.html", dct_sector=dct_sector, dct_year=dct_year, dct_sector_aggregates=dct_sector_byAgg, dct_agg=dct_agg)


@app.route('/get_chart_data/<in_sector_str>/<in_year_str>')
def get_chart_data(in_sector_str, in_year_str):
    #https://plotly.com/python/creating-and-updating-figures/
    #pip install kaleido
    print(f"inside get_chart_data:: {in_sector_str}, {type(in_sector_str)}, {in_year_str}")

    sector_lst=in_sector_str.split(",")
    year_lst=in_year_str.split(",")
    year_lst=[int(y) for y in year_lst]
    #print("sector_lst", sector_lst)

    #chart_json_content = generate_multichart(db, sector_lst, year=int(in_year))
    chart_json_content = generate_multichart_years(db, sector_lst, years=year_lst)

    return Response(chart_json_content, mimetype='application/json')


@app.route('/download_data', methods=['POST'])
def download_data_route():
    """
    API endpoint to generate and serve a CSV file for download, using a POST request
    to avoid long URLs.
    """
    print("inside /download_data")

    # Get the JSON data from the request body
    data = request.json
    if (not data) or ('sectors' not in data) or ('years' not in data) or ('dataType' not in data):
        print("Invalid request data")
        return jsonify({"error": "Invalid request data"}), 400

    sectors = data['sectors']
    years = data['years']
    report_type = data['dataType']

    print("sectors", sectors)
    print("years", years)
    print("report_type", report_type)

    try:
        fname_dl = generate_csv(db, sectors, years, report_type=report_type, dl_folder=DOWNLOAD_FOLDER)
        # Construct the URL for the client to download from
        # You'll need a new Flask route to serve these temporary files
        download_url = f"/get_download/{fname_dl}"
        return {"download_url": download_url}, 200 # Return JSON with the URL
    except Exception as e:
        print(f"Error generating CSV: {e}")
        return {"error": "Failed to generate CSV for download."}, 500

@app.route('/get_download/<filename>', methods=['GET'])
def serve_generated_csv(filename):
# Internal development comment removed for public distribution.
    # Basic security: Prevent directory traversal attacks
    # Ensure the filename is safe and within the allowed directory
    print("inside /get_download/ ->serve_generated_csv")
    print("os.path.basename(filename), filename", os.path.basename(filename), filename)

    if not os.path.basename(filename) == filename:
        abort(404) # Or 403, depending on your security preference

    try:
        # Flask's send_from_directory is secure and recommended
        response = send_from_directory(
            DOWNLOAD_FOLDER,
            filename,
            as_attachment=True,
            download_name=filename, # You can set a more user-friendly name if desired
            mimetype='text/csv'
        )
        return response
    except FileNotFoundError:
        abort(404) # File not found if it was cleaned up already or never created
    except Exception as e:
        print(f"Error serving file {filename}: {e}")

        traceback.print_exc()
        abort(500)

##Upload files-----------------------------------------------

# Set the upload folder
#https://stackoverflow.com/questions/51733419/flask-file-upload-is-not-saving-the-file
#https://flask.palletsprojects.com/en/2.3.x/patterns/fileuploads/

cwd=os.getcwd()
#UPLOAD_FOLDER = './static/uploads'
UPLOAD_FOLDER = cwd+'/static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Ensure the upload folder exists
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# Set allowed file extensions
ALLOWED_EXTENSIONS = {'csv'}

# Check if the file extension is allowed
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/nav_matrix', methods=['GET', 'POST'])
def nav_matrix():

    msg_lst=[]
    if request.method == 'POST' and 'calc_matrix' in request.form:
        #delete_files_in_directory(app.config['UPLOAD_FOLDER'])

        file_io = request.files['f_io']
        file_fec = request.files['f_fec']
        file_conv = request.files['f_conv']
        file_co2 = request.files['f_co2']

        found=True
        if (not file_io and not file_fec and not file_conv and not file_co2):
            msg_lst=["Please upload one of the input files"]
            found=False
            #print("got here1")
        else:
            #print("got here2")
            if (file_io):
                rv, msg1=save_file(file_io, f"{EEIO_FILES[0]}.csv", YEAR)
                msg_lst.append(msg1)
            if (file_fec):
                rv, msg1=save_file(file_fec, f"{EEIO_FILES[1]}.csv", YEAR)
                msg_lst.append(msg1)
            if (file_conv):
                rv, msg1=save_file(file_conv, f"{EEIO_FILES[2]}.csv", YEAR)
                msg_lst.append(msg1)
            if (file_co2):
                rv, msg1=save_file(file_co2, f"{EEIO_FILES[3]}.csv", YEAR)
                msg_lst.append(msg1)

        if (found):
            #df_agg_sectors, df_agg_sectors_each=calc_mat(fname_io, fname_fec, fname_conv, fname_co2, YEAR)
            #rv, msg1, df_agg_sectors, df_agg_sectors_each=calc_mat(EEIO_FILES, YEAR)
            #rv, msg1, df_agg_sectors, __=calc_mat(EEIO_FILES, YEAR)
            rv, msg1, df_agg_sectors, __=calc_mat(db, EEIO_FILES, YEAR)
            msg_lst.append(msg1)

            if (rv):
                df_agg_sectors.reset_index(inplace=True)
                print("df_agg_sectors", df_agg_sectors.head(5))

                df_fmt=format_df(df_agg_sectors)
                html_tbl=df_fmt.to_html(classes='data', header="true")
            else:
                html_tbl=""
            #https://stackoverflow.com/questions/52644035/how-to-show-a-pandas-dataframe-into-a-existing-flask-html-table
            #return render_template("nav_matrix.html", df_tables=[df_agg_sectors.to_html(classes='data', header="true")])
            return render_template("nav_matrix.html", msg_lst=msg_lst, html_tbl=html_tbl)

    return render_template("nav_matrix.html", msg_lst=msg_lst)


#Menu display input data-------------------------------------------------
@app.route('/nav_viewData')
def nav_viewData():
    df_io, df_fec, df_conv, df_co2=get_df_input(EEIO_FILES, YEAR)

    html_io=df_io.to_html(classes='data', header="true")
    html_fec=df_fec.to_html(classes='data', header="true")
    html_conv=df_conv.to_html(classes='data', header="true")
    html_co2=df_co2.to_html(classes='data', header="true")

    return render_template("nav_viewData.html",html_io=html_io, html_fec=html_fec, html_conv=html_conv, html_co2=html_co2)


@app.route('/nav_inputCategory', methods=['GET', 'POST'])
def nav_inputCategory():
    if request.method == 'POST' and 'submit' in request.form:
        file_cat_lst = request.files['f_cat_lst']
        file_cat_det = request.files['f_cat_det']

        msg_lst=[]
        if (not file_cat_lst and not file_cat_det):
            msg_lst=["Please upload at least one of the category input files"]
        if (file_cat_lst):
            #rv, msg1=save_file(file_cat_lst, "agg_sectors_lst.csv", YEAR)
            __, msg1=save_file(file_cat_lst, "agg_sectors_lst.csv", YEAR)
            msg_lst.append(msg1)
        if (file_cat_det):
            #rv, msg1=save_file(file_cat_det, "agg_sectors_det.csv", YEAR)
            __, msg1=save_file(file_cat_det, "agg_sectors_det.csv", YEAR)
            msg_lst.append(msg1)
    else:
        msg_lst=[]

    return render_template("nav_inputCategory.html", msg_lst=msg_lst)

# DB----------------------------------------------------------------------------
@app.route('/login')
def login():
    return redirect(url_for('nav_login', mode='prompt'))

@app.route('/nav_login/<mode>', methods=['GET', 'POST'])
def nav_login(mode):
    #return f"Hello nav_login {mode}"
    message=''; status=''
    if (mode == "prompt"):
        if request.method == 'POST' and 'userid' in request.form and 'password' in request.form:
            userid = request.form['userid']
            password = get_hash_password(request.form['password'])

            message, status = login_prompt(db, userid, password)
            if (status=="success"):
                return redirect(url_for('nav_home'))

        return render_template('nav_login.html', message=message, mode=mode, status=status)

    elif (mode=="register"):
        message=''; status=''
        if request.method == 'POST' and 'password' in request.form and 'password' in request.form and 'email' in request.form:
            userID = request.form['userid']
            userName = request.form['name']
            password = get_hash_password(request.form['password'])
            email = request.form['email']

            message, status = login_register(db, mail, userID, userName, password, email)
        elif request.method == 'POST':
            #print("fill in request form")
            message = 'Please fill out the form !'
        return render_template('nav_login.html', message=message, mode=mode, status=status)

    elif (mode=="change_password"):
        message = ''; status = ''
        #print("inside change_password")
        if request.method == 'POST' and session['loggedin']:
            password_old = get_hash_password(request.form['password_old'])
            password_new = get_hash_password(request.form['password_new'])

            message, status = login_changePassword(db, password_old, password_new)

        return render_template('nav_login.html', message=message, mode=mode, status=status)

    elif (mode=="verify_user"):
        userid=request.args.get('userid')
        if (userid):
            print("userid", userid)
            message, status=verify_user(db, userid)

        df_user=get_table_user(db)
        tbl_user = df_user.to_dict(orient='records')

        return render_template('nav_login.html', mode=mode, tbl_user=tbl_user, message=message)

    elif (mode=="delete_user"):
        userid=request.args.get('userid')
        if (userid):
            print("userid", userid)
            message, status=delete_user(db, userid)

        df_user=get_table_user(db)
        tbl_user = df_user.to_dict(orient='records')

        return render_template('nav_login.html', mode=mode, tbl_user=tbl_user, message=message)

    elif (mode=="logout"):
        session.pop('loggedin', None)
        session.pop('userid', None)
        session.pop('email', None)
        return redirect(url_for('nav_login', mode='prompt'))

#LOGIN-MAIL---------------------------------------------------------------------------------------
@app.route("/verify_email/<token>")
def verify_email(token):
    message, status = verify_email_byToken(db, token)
    mode="register"
    return render_template('nav_login.html', message=message, mode=mode, status=status)


#DYNAMIC PAGES---------------------------------------------------------------------------------------
@app.route('/scenario', methods=['GET', 'POST'])
def scenario_page():

    duplicate_id = request.args.get(
        "duplicate_id"
    )

    selected_scenario_id = None
    history_id = None
    execution_status = None
    model_info = None
    scenario_description = None
    run_error = None
    report = None

    # -------------------------------------------------
    # Empty/default display state
    # -------------------------------------------------
    display_data = {
        "scenario_summary": None,
        "scope_summary": None,
        "impact_statistics": None,
        "scope_chart_data": None,
        "top_sector_chart_data": None
    }

    # -------------------------------------------------
    # Canonical v2.1 scenario definitions only
    # -------------------------------------------------
    available_scenarios = (
        get_canonical_scenario_list_v21(db)
    )

    # -------------------------------------------------
    # Run selected scenario
    # -------------------------------------------------
    if request.method == 'POST':
        selected_year = int(request.form["scenario_year"])
        selected_scenario_id = int(request.form["scenario_id"])

        # ---------------------------------------------
        # Load scenario definition
        scenario = ScenarioLoader(db).load(
            scenario_id=selected_scenario_id
        )

        # scenario_year is part of the v2.1 definition.
        # Therefore the UI does not independently choose
        # the model year.
        # Changed: 04-10-2026
        #year = int(scenario.scenario_year)
        scenario_description = (scenario.description)

        # ---------------------------------------------
        # Load EEIO model
        # ---------------------------------------------
        fname_io = f"io_ind_{selected_year}.csv"
        cache_key = (selected_year,fname_io)

        model = get_eeio_model(
            cache_key=cache_key,
            model_builder=lambda:
                build_model_year(
                    db=db, fname_io=fname_io, year=selected_year
                )
        )
        model_info = {
            "year": model.year,
            "sectors": model.n_sectors
        }

        # ---------------------------------------------
        # Sector metadata
        df_sector = get_sector(db)

        # ---------------------------------------------
        # EEIO v2.1 execution
        # Use the exact argument signature of our
        # existing run_or_load_scenario_v21().
        # ---------------------------------------------
        try:
            result = run_or_load_scenario_v21(
                db=db,
                model=model,
                scenario_id=selected_scenario_id,
                df_sector=df_sector
            )

        except ScenarioNotRunnableError as exc:
            # Expected, user-correctable scenario problem
            run_error = str(exc)

            app.logger.warning(
                "Scenario %s is not runnable: %s",
                selected_scenario_id,
                exc
            )
        else:
            report = result["report"]
            if (1):
                print("--execution_status:", result["status"])
                print("--history_id:", result["history_id"])
                print("--report.target_impacts:", report.target_impacts)

            history_id = result["history_id"]
            execution_status = result["status"]

            if (0):
                print("inside scenario_page()")
                print("--scenario report:", report)
                print("--scenario report target_impacts:", report.target_impacts)

            # ---------------------------------------------
            #for key in report.metadata.keys():
            #    print(" - key", key)

            # scenario_summary, target_summary, target_impacts, target_emission_impacts,
            # target_emission_chart_data, scope_summary, impact_statistics, scope_chart_data,
            # top_sector_chart_data
            display_data = (
                prepare_scenario_display_v21(report)
            )
            if (0):
                # Temporary diagnostics during migration
                print("--selected_scenario_id:", selected_scenario_id)
                print("--scenario_year:", year)
                print("--history_id:", history_id)
                print("--execution_status:", execution_status)

            # -------------------------------------------------
            # Existing history display left unchanged for 18A.
            # History migration will be handled separately.
            # -------------------------------------------------
            #recent_scenarios = get_scenario_history_v21(db)

    if (V21_VALIDATION_DIAGNOSTICS): #temporary test
        from util_db import tbl_to_df
        model = get_eeio_model(
            cache_key=(2020, "io_ind_2020.csv"),
            model_builder=lambda: build_model_year(
                db=db, fname_io="io_ind_2020.csv", year=2020
            )
        )
        q_str = "SELECT sector_agg, sector_code, sector_name FROM sector_lst WHERE sector_label='DET'"
        df_sector = tbl_to_df(db, q_str)
        benchmark_scenario_v21(
            db=db,
            model=model,
            scenario_id=40,
            df_sector=df_sector
        )

    period_year = PERIOD_YEAR #list

    return render_template(
        "scenario_display.html",
        duplicate_id=duplicate_id,
        model_info=model_info,
        page_mode="new",
        #recent_scenarios=recent_scenarios,
        available_scenarios=available_scenarios,
        selected_scenario_id=selected_scenario_id,
        scenario_description=scenario_description,
        history_id=history_id,
        execution_status=execution_status,
        run_error=run_error,
        period_year=period_year,
        **display_data
    )

@app.route(
    "/download_scenario_summary"
)
def download_scenario_summary():

    history_id = request.args.get("history_id", type=int)
    if history_id is None:
        return ("Missing history_id.", 400)

    report = load_scenario_report_v21(db, history_id)
    if report is None:
        return ("Scenario report not found.", 404)

    csv_data = scenario_summary_to_csv(report)
    filename = make_scenario_filename(report, "summary")

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )

@app.route(
    "/download_scope_comparison"
)
def download_scope_comparison():
    history_id = request.args.get("history_id", type=int)
    if history_id is None:
        return ("Missing history_id.", 400)

    report = load_scenario_report_v21(db, history_id)
    if report is None:
        return ("Scenario report not found.", 404)

    csv_data = scope_comparison_to_csv(report)
    filename = make_scenario_filename(report, "scope")

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )

@app.route(
    "/download_sector_impact"
)
def download_sector_impact():
    history_id = request.args.get("history_id", type=int)
    if history_id is None:
        return ("Missing history_id.", 400)

    report = load_scenario_report_v21(db, history_id)
    if report is None:
        return ("Scenario report not found.", 404)

    csv_data = sector_impact_to_csv(report)
    filename = make_scenario_filename(report, "sector_impact")

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )

@app.route(
    "/download_excel_report"
)
def download_excel_report():
    if (0):
        print("--Excel request path:",request.path)
        print("--Excel request full_path:",request.full_path)
        print("--Excel request args:",request.args)
        history_id = request.args.get("history_id",type=int)
        print("--Excel history_id:",history_id)

    history_id = request.args.get("history_id", type=int)
    if history_id is None:
        return ("Missing history_id.", 400)

    report = load_scenario_report_v21(db, history_id)
    if report is None:
        return ("Scenario report not found.", 404)

    if (0):
        print("--Excel history_id:", history_id)
        print("--Excel report:", report)
        print("--Excel report target_impacts:", report.target_impacts)

    excel_file = scenario_report_to_excel(report)
    filename = make_scenario_filename(report, "report")
    filename = filename.replace(".csv", ".xlsx")

    return Response(
        excel_file.getvalue(),
        mimetype=(
            "application/vnd.openxmlformats-"
            "officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition":
                f"attachment;filename={filename}"
        }
    )


@app.route(
    "/download_scenario_target"
)
def download_scenario_target():
    history_id = request.args.get("history_id", type=int)
    if history_id is None:
        return ("Missing history_id.", 400)

    report = load_scenario_report_v21(db, history_id)
    if report is None:
        return ("Scenario report not found.", 404)

    csv_data = scenario_target_to_csv(report)
    filename = make_scenario_filename(report, "scenario_target")

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={
            "Content-Disposition":
            f"attachment;filename={filename}"
        }
    )

@app.route(
    "/download_energy_impact"
)
def download_energy_impact():
    history_id = request.args.get("history_id", type=int)
    if history_id is None:
        return ("Missing history_id.", 400)

    report = load_scenario_report_v21(db, history_id)
    if report is None:
        return ("Scenario report not found.", 404)

    csv_data = target_energy_impact_to_csv(report)
    filename = make_scenario_filename(report, "energy_impact")

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={
            "Content-Disposition":
            f"attachment;filename={filename}"
        }
    )

@app.route(
    "/download_emission_impact"
)
def download_emission_impact():

    history_id = request.args.get("history_id", type=int)
    if history_id is None:
        return ("Missing history_id.", 400)

    report = load_scenario_report_v21(db, history_id)
    if report is None:
        return ("Scenario report not found.", 404)

    csv_data = target_emission_impact_to_csv(report)
    filename = make_scenario_filename(report, "emission_impact")

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={
            "Content-Disposition":
            f"attachment;filename={filename}"
        }
    )


@app.route(
    "/scenario_history"
)
def scenario_history():
    scenarios = (
        get_scenario_history_v21(
            db
        )
    )
    return render_template(
        "scenario_history.html",
        scenarios=scenarios
    )

@app.route(
    "/scenario_history/<int:scenario_history_id>"
)
def view_saved_scenario(
        scenario_history_id
    ):
    #scenario_id -> supposed to be named as scenario_history_id
    report = load_scenario_report_v21(db, scenario_history_id)
    #print("-- report.metadata", report.metadata)

    display_data = prepare_scenario_display_v21(report)

    return render_template(
        "scenario_display.html",
        model_info={
            "year":
                report.metadata["year"],
            "sectors":
                report.metadata["number_of_sectors"]
        },
        page_mode="saved",
        history_id = scenario_history_id,
        **display_data
    )

@app.route(
    "/scenario_compare",
    methods=["GET","POST"]
)
def scenario_compare():
    selected_a = None
    selected_b = None
    comparison_chart_data = {}
    comparison = None

    scenarios = get_scenario_history_list_v21(db)

    if request.method == "POST":
        selected_a = int(request.form["scenario_a"])
        selected_b = int(request.form["scenario_b"])

        #id_a = int(request.form["scenario_a"])
        #id_b = int(request.form["scenario_b"])

        report_a = (load_scenario_report_v21(db, selected_a))
        report_b = (load_scenario_report_v21(db, selected_b))

        comparison = compare_scenario_reports_v21(report_a, report_b)
        print("--comparison", comparison)

        comparison_chart_data = comparison["chart_data"]
        #print("--comparison_chart_data")
        #for key,value in comparison_chart_data.items():
        #    print("--key,value", key,value)

    return render_template(
        "scenario_compare.html",
        scenarios=scenarios,
        comparison=comparison,
        selected_a=selected_a,
        selected_b=selected_b,
        comparison_chart_data=comparison_chart_data,
    )

#@app.route("/scenario_manage_v21", methods=["GET","POST"])
@app.route("/", methods=["GET","POST"])
def scenario_manage_v21():
    message = None
    error_message = None

    if request.method == "POST":
        try:
            parameter_codes = request.form.getlist("parameter_code[]")
            energy_type_codes = request.form.getlist("energy_type_code[]")
            target_scopes = request.form.getlist("target_scope[]")
            sector_codes = request.form.getlist("sector_code[]")
            values = request.form.getlist("value[]")

            n_parameters = len(parameter_codes)
            if n_parameters == 0:
                raise ValueError("At least one parameter is required.")
            if not (
                len(energy_type_codes) == n_parameters
                and
                len(target_scopes) == n_parameters
                and
                len(sector_codes) == n_parameters
                and
                len(values) == n_parameters
                ):
                raise ValueError(
                    "Scenario parameter rows are inconsistent."
                )
            parameters = []
            for i in range(n_parameters):
                target_scope = target_scopes[i]
                sector_code = sector_codes[i].strip()

                if target_scope == "ENERGY":
                    sector_code = None
                elif target_scope == "SECTOR_ENERGY":
                    if not sector_code:
                        raise ValueError(
                            "Sector code required for SECTOR_ENERGY."
                        )
                parameters.append(
                    {
                        "parameter_code": parameter_codes[i],
                        "value": float(values[i]) / 100,
                        "unit": "%",
                        "energy_type_code": energy_type_codes[i],
                        "sector_code": sector_code,
                        "target_scope": target_scope
                    }
                )

            scenario_definition = {
                "scenario_name": request.form["scenario_name"],
                #"scenario_year": int(request.form["scenario_year"]),
                "scenario_year": None,
                "description": request.form["description"],
                "scenario_types":
                [
                    {
                        #"type_code": request.form["scenario_type"],
                        "type_code": CUSTOM_SCENARIO_TYPE_CODE_V21,
                        "is_primary": True
                    }
                ],
                # Use the list we just built
                "parameters":
                    parameters
            }
            valid, error_message = validate_scenario_definition_v21(
                db, scenario_definition
            )

            if not valid:
                message = error_message
            else:
                result = create_scenario_v21(
                    db,
                    scenario_definition,
                    return_status=True
                )
                scenario_id = result["scenario_id"]

                if result["created"]:
                    message = (
                        "Scenario created successfully. "
                        f"Scenario ID={scenario_id}"
                    )
                else:
                    message = (
                        "Scenario definition already exists. "
                        "No new record was created. "
                        f"Existing Scenario ID={scenario_id}"
                    )

        except ValueError as exc:
            error_message = str(exc)
            print("--scenario validation error:", error_message)

    #parameters_available = (get_available_parameter_rules_v21())
    #print("--parameters_available", parameters_available)

    scenarios = get_canonical_scenario_list_v21(db)
    # --------------------------------------------------
    # Scenario Management UI:
    # Show newest definitions first.
    # scenario_id is used as the insertion-order proxy.
    # --------------------------------------------------

    scenarios = sorted(
        scenarios,
        key=lambda item:
            int(item.get("scenario_id", 0)),
        reverse=True
    )

    sector_list = get_sector(db)
    sector_options = (
        sector_list[["sector_code", "sector_name"]]
        .to_dict(orient="records")
    )
    parameter_rules = PARAMETER_RULES_V21
    energy_options = ENERGY_OPTIONS
    #period_year = PERIOD_YEAR #list

    #scenario_types = get_active_scenario_types_v21(db)
    #print("scenario_types", scenario_types, type(scenario_types))

    #scenario_parameter_options = get_parameter_options_by_scenario_type()
    parameter_options = get_implemented_parameter_options_v21()

    return render_template(
        "scenario_manage_v21.html",
        scenarios=scenarios,
        #parameters=parameters_available,
        parameter_options=parameter_options,
        sectors=sector_options,
        message=message,
        error_message=error_message,
        parameter_rules=parameter_rules,
        energy_options=energy_options,
        #period_year=period_year
    )

@app.route(
    "/scenario_manage_v21/<int:scenario_id>"
)
def view_scenario_definition_v21(
        scenario_id
    ):

    scenario = (
        get_scenario_definition_v21(
            db,
            scenario_id
        )
    )

    return render_template(
        "scenario_definition_detail_v21.html",
        scenario=scenario
    )


if __name__ == '__main__':
    app.run(debug=True)