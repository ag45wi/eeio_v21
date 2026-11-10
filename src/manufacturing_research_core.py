"""Read-only original v2.1 calculations for two illustrative manufacturing sectors.

Reuses unchanged eeio_engine and ScenarioProcessor; no ScenarioLoader/history,
file writes, DB writes, factor mutation, or changes to the source model.
Output is conditional 2020-model electricity delta; not a national inventory.
"""
from __future__ import annotations

import math
import hashlib
import json
from dataclasses import asdict
import numpy as np

from eeio_engine import get_mat_finConsCO2, build_eeio_model
from scenario_engine import ScenarioDefinition, ScenarioParameter, ScenarioProcessor, ScenarioValidator
from manufacturing_factor_catalog import get_factor_set

MODEL_YEAR = 2020
N_SECTORS = 185
TARGET_SECTORS = {'114': 'Iron and steel', '111': 'Glass and glassware'}
EFFICIENCY_LEVELS = (0.0, .1, .2)
GRID_LEVELS = (0.0, .2, .4)


def clone_with_research_factor(base_model, factor_set_code: str):
    """Create a separate model with a selected factor; leave base unchanged.

    Requires full model with 4x185 energy and 4x3 factor arrays.
    """
    factor = get_factor_set(factor_set_code)
    if base_model.year != factor.assigned_model_year:
        raise ValueError('Model year does not match selected factor assignment')
    if base_model.n_sectors != N_SECTORS:
        raise ValueError('Research case expects exactly 185 sectors')
    if base_model.mat_finCons.shape != (4, N_SECTORS) or base_model.CO2_EF.shape != (4, 3):
        raise ValueError('Unexpected matrix shape')
    if not np.isclose(base_model.CO2_EF[3, 0], .877, rtol=0, atol=1e-12):
        raise ValueError('Legacy baseline EF no longer equals 0.877; review factor catalog')
    if not np.all(np.isfinite(base_model.mat_finCons)):
        raise ValueError('Nonfinite consumption inputs')
    expected = {f'{k:03d}': k - 1 for k in range(1, 186)}
    if base_model.sector_column_map != expected:
        raise ValueError('Unexpected sector-column mapping; do not assume IO alignment')
    arr = base_model.CO2_EF.copy()
    arr[3, 0] = factor.baseline_tco2_per_mwh
    co2 = get_mat_finConsCO2(base_model.mat_finCons, arr)
    clone = build_eeio_model(
        mat_A=base_model.A.copy(), mat_finConsCO2=co2,
        X_total=base_model.X_total.copy(), X_domOut=base_model.X_domOut.copy(),
        mat_finCons=base_model.mat_finCons.copy(), CO2_EF=arr,
        year=MODEL_YEAR,
    )
    clone.sector_column_map = expected
    return clone


def _make_scenario(case, sector, efficiency_reduction, grid_reduction):
    return ScenarioDefinition(
        scenario_id=-(int(case[1:]) + 1),
        scenario_name=f'Research manufacturing {case}: sector {sector}',
        description='Illustrative electricity-efficiency x grid-EF experiment; not DB scenario',
        scenario_year=MODEL_YEAR,
        scenario_types=[],
        parameters=[
            ScenarioParameter(-101, 'energy_demand_change', -efficiency_reduction,
                              'fraction', '04', sector, 'SECTOR_ENERGY'),
            ScenarioParameter(-102, 'emission_factor_change', -grid_reduction,
                              'fraction', '04', None, 'ENERGY'),
        ],
    )


def calculate_research_grid(base_model, *, sector: str, factor_set_code: str,
                            source_metadata: dict):
    """Run 9 genuine v2.1 scenarios, verify an independent electric-only delta."""
    if sector not in TARGET_SECTORS:
        raise ValueError('Only explicitly reviewed 114 and 111 sectors supported')
    factor = get_factor_set(factor_set_code)
    model = clone_with_research_factor(base_model, factor_set_code)
    j = model.sector_column_map[sector]
    baseline_mwh = float(model.mat_finCons[3, j])
    if not np.isfinite(baseline_mwh) or baseline_mwh <= 0:
        raise ValueError('Target electricity baseline must be positive')
    ef0 = float(model.CO2_EF[3, 0])
    baseline = model.calculate_baseline()
    rows = []
    model_e = np.asarray(model.mat_finCons[3], dtype=float)
    for gi, grid_drop in enumerate(GRID_LEVELS):
        for ei, efficiency_drop in enumerate(EFFICIENCY_LEVELS):
            case = f'M{3 * gi + ei}'
            scenario = _make_scenario(case, sector, efficiency_drop, grid_drop)
            ScenarioValidator.validate(scenario)
            inputs = ScenarioProcessor(model).apply(model, scenario)
            result = model.calculate_scenario_from_inputs(inputs)
            scenario_mwh = float(inputs.mat_finCons[3, j])
            scenario_ef = float(inputs.CO2_EF[3, 0])
            if not math.isclose(scenario_mwh, baseline_mwh * (1 - efficiency_drop), abs_tol=1e-6):
                raise AssertionError('Expected target electricity demand change not reproduced')
            if not math.isclose(scenario_ef, ef0 * (1 - grid_drop), abs_tol=1e-12):
                raise AssertionError('Expected all-sector electricity EF change not reproduced')
            delta_cells = inputs.mat_finCons - model.mat_finCons
            unexpected = np.abs(delta_cells) > 1e-7
            allowed = np.zeros_like(unexpected, dtype=bool)
            allowed[3, j] = True
            if np.any(unexpected & ~allowed):
                raise AssertionError('Scenario changed energy cells outside requested industry')
            if not np.array_equal(inputs.CO2_EF[:3], model.CO2_EF[:3], equal_nan=True):
                raise AssertionError('Scenario changed fossil emission factors')

            target_baseline_t = baseline_mwh * ef0
            target_scenario_t = scenario_mwh * scenario_ef
            direct_target_reduction = target_baseline_t - target_scenario_t
            direct_e_all_t = float(np.sum(model_e * ef0 - inputs.mat_finCons[3] * scenario_ef))
            actual_total_change_t = float(
                np.sum(baseline['totalEmission']) - np.sum(result['totalEmission'])
            )
            # Independent linear-algebra reconstruction of the *change*,
            # not of absolute emissions or corporate Scopes 1/2/3.
            db_e = (model_e * ef0 - inputs.mat_finCons[3] * scenario_ef) / model.X_total
            downstream_footprint_delta_t = (db_e @ model.L) * model.X_domOut
            reconstructed = float(np.sum(downstream_footprint_delta_t))
            if not math.isclose(actual_total_change_t, reconstructed, rel_tol=1e-11, abs_tol=2e-5):
                raise AssertionError('Independent Leontief electricity change mismatch')
            rows.append({
                'case': case,
                'sector_code': sector,
                'sector_name': TARGET_SECTORS[sector],
                'electricity_efficiency_reduction_pct': efficiency_drop * 100,
                'grid_ef_reduction_pct': grid_drop * 100,
                'baseline_target_electricity_mwh_INFERRED': baseline_mwh,
                'scenario_target_electricity_mwh_INFERRED': scenario_mwh,
                'baseline_factor_tco2_per_mwh_CONDITIONAL': ef0,
                'scenario_factor_tco2_per_mwh_CONDITIONAL': scenario_ef,
                'target_direct_electricity_reduction_tco2_CONDITIONAL': direct_target_reduction,
                'all_sectors_direct_electricity_reduction_tco2_CONDITIONAL': direct_e_all_t,
                'whole_economy_eeio_electricity_delta_tco2_CONDITIONAL': reconstructed,
            })
    assert len(rows) == 9
    by = {r['case']: r for r in rows}
    name = 'whole_economy_eeio_electricity_delta_tco2_CONDITIONAL'
    direct = 'target_direct_electricity_reduction_tco2_CONDITIONAL'
    # Reduction overlap: R8 - R6 - R2 + R0 (normally negative).
    interactions = {
        'target_direct_factorial_overlap_tco2': by['M8'][direct] - by['M6'][direct]
             - by['M2'][direct] + by['M0'][direct],
        'whole_economy_factorial_overlap_tco2': by['M8'][name] - by['M6'][name]
             - by['M2'][name] + by['M0'][name],
        'incremental_industry_efficiency_at_40pct_grid_reduction_tco2':
             by['M8'][name] - by['M6'][name],
    }
    fingerprint_source = {
        'read_only_baseline_fingerprint': source_metadata.get('read_only_baseline_fingerprint'),
        'sector_code': sector,
        'factor_set': asdict(factor),
        'model_year': MODEL_YEAR,
        'efficiency_levels': list(EFFICIENCY_LEVELS),
        'grid_ef_change_levels': list(GRID_LEVELS),
    }
    run_fingerprint = hashlib.sha256(json.dumps(
        fingerprint_source, sort_keys=True, separators=(',', ':'), allow_nan=False
    ).encode('utf-8')).hexdigest()
    package = {
        'research_run_fingerprint': run_fingerprint,
        'purpose': 'RESEARCH_ONLY_NOT_A_NATIONAL_2020_EMISSIONS_INVENTORY',
        'year': MODEL_YEAR,
        'io_type': 'BPS 2020 TOTAL IO, pinned source SHA256',
        'sector_code': sector,
        'sector_name': TARGET_SECTORS[sector],
        'factor_set': asdict(factor),
        'provenance': source_metadata,
        'baseline_electricity_units_status': 'MWh inferred from legacy 0.613 conversion; physical allocation not independently validated',
        'scope_warning': 'Grid reduction acts on 185 sectors; model-defined EEIO change is NOT steel/glass-only or ISO scopes',
        'emission_warning': 'Absolute fossil/emissions baseline unverified; report electricity-driven scenario DIFFERENCES only',
        'model_warning': 'No IO A/L response, no process emissions, no confirmed sector-specific measured physical electricity',
        'additivity_warning': 'Do NOT sum parameter-level reporting-helper rows; use factorial scenario contrasts',
        'scenarios': rows,
        'contrasts': interactions,
    }
    return package
