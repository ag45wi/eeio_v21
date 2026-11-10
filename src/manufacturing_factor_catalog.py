"""Explicit research factor sets; no claim of a measured 2020 national EF.

2016 JAMALI 0.877 tCO2/MWh is the application's existing legacy value.
Two +-20% levels are *constructed sensitivity assumptions*, not observations.
This file never updates co2_factor or co2_factor_model_year.
"""
from dataclasses import dataclass

ESDM_2016_JAMALI_URL = (
    'https://gatrik.esdm.go.id/assets/uploads/download_index/files/'
    '28be7-faktor-emisi-grk-tahun-2016.pdf'
)
LEGACY_GRID_EF = 0.877


@dataclass(frozen=True)
class ElectricityFactorSet:
    code: str
    baseline_tco2_per_mwh: float
    assigned_model_year: int
    source_year: int | None
    source_region: str
    provenance_kind: str
    reference: str
    note: str


FACTOR_SETS = {
    'LEGACY_JAMALI_2016': ElectricityFactorSet(
        'LEGACY_JAMALI_2016', LEGACY_GRID_EF, 2020, 2016, 'JAMALI',
        'official_coefficient_reused_out_of_scope', ESDM_2016_JAMALI_URL,
        '2016 regional JAMALI legacy EF used in a conditional national 2020 IO model; '
        'NOT a measured 2020 national EF',
    ),
    'SENS_MINUS_20': ElectricityFactorSet(
        'SENS_MINUS_20', LEGACY_GRID_EF * .8, 2020, None,
        'Hypothetical / no real-world geography', 'hypothetical_sensitivity',
        ESDM_2016_JAMALI_URL,
        'Pure sensitivity: 20% below the 2016 JAMALI legacy coefficient; '
        'NOT a 2020 observation or forecast',
    ),
    'SENS_PLUS_20': ElectricityFactorSet(
        'SENS_PLUS_20', LEGACY_GRID_EF * 1.2, 2020, None,
        'Hypothetical / no real-world geography', 'hypothetical_sensitivity',
        ESDM_2016_JAMALI_URL,
        'Pure sensitivity: 20% above the 2016 JAMALI legacy coefficient; '
        'NOT a 2020 observation or forecast',
    ),
}


def get_factor_set(code: str) -> ElectricityFactorSet:
    if not isinstance(code, str) or code not in FACTOR_SETS:
        raise ValueError('Unknown electricity factor set (no fallback)')
    return FACTOR_SETS[code]
