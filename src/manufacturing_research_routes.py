"""Feature-flagged Flask routes for the READ-ONLY M0-M8 manufacturing pilot.

This file does not register anything until explicitly called by app.py.
It never uses scenario_history, ScenarioLoader, build_model_year, or any
INSERT/UPDATE/DELETE/commit, and does not mutate EEIO_MODEL_CACHE.

Secured by existing Flask logged-in session AND an explicit allowed-user list.
DO NOT expose on a public host until the app authentication and staging
configuration have been independently reviewed.
"""
from __future__ import annotations

import csv
import io
import os
from pathlib import Path

from flask import Blueprint, Response, abort, jsonify, render_template_string, request, session

from manufacturing_factor_catalog import FACTOR_SETS
from manufacturing_readonly_loader import read_model_readonly
from manufacturing_research_core import TARGET_SECTORS, calculate_research_grid

_TEMPLATE = '''
<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>EEIO v2.1 / Manufacturing Research Only</title>
<style>
body{font-family:system-ui,sans-serif;margin:2rem auto;max-width:1120px;line-height:1.5;color:#17212d}
small,footer{color:#596478}table{border-collapse:collapse;width:100%;font-size:0.92rem}
th,td{border-bottom:1px solid #dfe5eb;padding:.6rem;text-align:right}
th:first-child,td:first-child{text-align:left}.note{border-left:3px solid #8895a9;padding:.5rem 1rem;background:#f7f9fa}
select,button{font:inherit;padding:.35rem;margin-right:1rem}
</style></head><body>
<h1>Manufacturing M0–M8 — Research Staging</h1>
<p class="note"><strong>Not a national 2020 emissions inventory.</strong> Model year: 2020 TOTAL IO.
Grid EF baseline is a historical JAMALI proxy or hypothetical sensitivity. No scenario history is saved.</p>
<form method="get" action="{{ request_path }}">
<label>Industry <select name="sector">
{% for code,name in sectors.items() %}
<option value="{{code}}" {% if code==selected_sector %}selected{% endif %}>{{name}} ({{code}})</option>
{% endfor %}</select></label>
<label>EF baseline <select name="factor_set">
{% for code,f in sets.items() %}
<option value="{{code}}" {% if code==selected_factor %}selected{% endif %}>{{code}} · {{f.baseline_tco2_per_mwh}} tCO₂/MWh</option>
{% endfor %}</select></label>
<button type="submit">Run read-only research</button></form>
<p><strong>Factor:</strong> {{ result.factor_set.baseline_tco2_per_mwh }} tCO₂/MWh —
{{result.factor_set.note}}</p>
<p><strong>Industry:</strong> {{ result.sector_name }} ({{result.sector_code}}).
<strong>Input SHA256:</strong> <code>{{result.provenance.io_sha256[:16]}}…</code></p>
<p><a href="{{ request_path }}?sector={{selected_sector}}&factor_set={{selected_factor}}&format=csv">CSV results</a>
· <a href="{{ request_path }}?sector={{selected_sector}}&factor_set={{selected_factor}}&format=json">JSON + provenance</a></p>
<table><thead><tr><th>Case</th><th>Efficiency ↓</th><th>Grid EF ↓</th>
<th>Target direct elec. ↓ (ktCO₂)</th><th>Whole-economy EEIO elec. Δ (MtCO₂)</th></tr></thead><tbody>
{% for r in display_rows %}<tr>
<td>{{r.case}}</td><td>{{'%.0f'|format(r.electricity_efficiency_reduction_pct)}}%</td>
<td>{{'%.0f'|format(r.grid_ef_reduction_pct)}}%</td>
<td>{{'%.3f'|format(r.target_direct_kt)}}</td>
<td>{{'%.3f'|format(r.whole_economy_mt)}}</td></tr>{% endfor %}
</tbody></table>
<h2>Factorial contrasts</h2><p>Incremental EEIO electricity improvement from 20% industrial
 efficiency when the grid EF is already 40% lower:
<strong>{{'%.6f'|format(result.contrasts.incremental_industry_efficiency_at_40pct_grid_reduction_tco2/1000000)}} MtCO₂</strong>.</p>
<p>Model-defined EEIO overlap (M8−M6−M2+M0):
<strong>{{'%.6f'|format(result.contrasts.whole_economy_factorial_overlap_tco2/1000000)}} MtCO₂</strong>.
A negative overlap denotes non-additivity, not increased physical emissions.</p>
<footer><p>{{result.scope_warning}}. {{result.baseline_electricity_units_status}}.
Absolute baseline and fossil-factor units have not been certified.
Do not sum target direct and full-model reductions.</p></footer>
</body></html>
'''

def register_manufacturing_research_routes(app, db) -> bool:
    """Explicit opt-in; default is disabled. No existing app route changes."""
    if os.getenv('EEIO_ENABLE_MANUFACTURING_RESEARCH', '0') != '1':
        return False
    raw_allowed = os.getenv('EEIO_MFG_ALLOWED_USERS', '')
    allowed = {part.strip() for part in raw_allowed.split(',') if part.strip()}
    if not allowed:
        raise ValueError('EEIO_MFG_ALLOWED_USERS must be set when research route is enabled')
    blueprint = Blueprint('manufacturing_research_v21', __name__)

    @blueprint.get('/research/manufacturing')
    def manufacturing_research_page():
        if not session.get('loggedin') or str(session.get('userid', '')) not in allowed:
            abort(403)
        sector = request.args.get('sector', '114')
        factor_set = request.args.get('factor_set', 'LEGACY_JAMALI_2016')
        fmt = request.args.get('format', 'html')
        if sector not in TARGET_SECTORS or factor_set not in FACTOR_SETS or fmt not in {'html','json','csv'}:
            abort(400)
        io_file = (Path(app.root_path) / 'static' / 'data' / '2020' /
                   'io_ind_2020_total_BPS_verified.csv')
        try:
            model, provenance = read_model_readonly(db, io_file, year=2020)
            result = calculate_research_grid(
                model, sector=sector, factor_set_code=factor_set,
                source_metadata=provenance,
            )
        except (ValueError, FileNotFoundError, AssertionError, FloatingPointError) as exc:
            # Fail closed instead of serving stale report history or falling back
            # to an unreviewed IO, factor set, or sector mapping.
            app.logger.warning('Manufacturing research input gate failed: %s', exc)
            return Response('Manufacturing research baseline verification failed; see server log.',
                            status=422, mimetype='text/plain')
        if fmt == 'json':
            response = jsonify(result)
        elif fmt == 'csv':
            buffer = io.StringIO(newline='')
            writer = csv.DictWriter(buffer, fieldnames=list(result['scenarios'][0]))
            writer.writeheader()
            writer.writerows(result['scenarios'])
            response = Response(buffer.getvalue(), mimetype='text/csv', headers={
                'Content-Disposition': 'attachment; filename="manufacturing_research_m0_m8.csv"',
            })
        else:
            display_rows = [dict(r,
                target_direct_kt=r['target_direct_electricity_reduction_tco2_CONDITIONAL']/1000,
                whole_economy_mt=r['whole_economy_eeio_electricity_delta_tco2_CONDITIONAL']/1e6)
                            for r in result['scenarios']]
            response = Response(render_template_string(
                _TEMPLATE, result=result, request_path=request.path,
                display_rows=display_rows, sectors=TARGET_SECTORS, sets=FACTOR_SETS,
                selected_sector=sector, selected_factor=factor_set,
            ), mimetype='text/html')
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Research-Scope'] = 'illustrative-electricity-only'
        return response

    app.register_blueprint(blueprint)
    return True
