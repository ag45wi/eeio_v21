# Scenario execution versions
SCENARIO_VERSION_V20 = "1.0"
SCENARIO_VERSION_V21 = "2.1"

# Scenario sources
SCENARIO_SOURCE_LEGACY = "legacy"
SCENARIO_SOURCE_DATABASE = "database"

SUPPORTED_TARGET_SCOPES={
    "ENERGY",
    "SECTOR_ENERGY"
}

SUPPORTED_PARAMETERS = {
    "energy_demand_change",
    "emission_factor_change"
}

ENERGY_OPTIONS = [
    {"code": "01", "name": "Coal"},
    {"code": "02", "name": "Fuel"},
    {"code": "03", "name": "Natural Gas"},
    {"code": "04", "name": "Electricity"}
]

ENERGY_MAPPING = {
    "01": {"name": "Coal", "finCons_row": 0, "co2_factor_row": 0},
    "02": {"name": "Fuel", "finCons_row": 1, "co2_factor_row": 1},
    "03": {"name": "Natural Gas", "finCons_row": 2, "co2_factor_row": 2},
    "04": {"name": "Electricity", "finCons_row": 3, "co2_factor_row": 3}
}

#Changed coefficient:
# - Emission Factor column for Coal, Fuel, Natural Gas;
# - Electricity carbon intensity coefficient column for Electricity
CO2_ADJUSTMENT_MAPPING = {
    "01": {"column": 1},
    "02": {"column": 1},
    "03": {"column": 1},
    "04": {"column": 0}
}

PARAMETER_RULES_V21 = {
    "energy_demand_change": {
        "label":
            "Energy Demand Change",
        "allowed_scopes": [
            "ENERGY", "SECTOR_ENERGY"
        ],
        "allowed_energy_types": [
            "01", "02", "03", "04"
        ],
        "allowed_scenario_types": [
            "DEMAND",
            "EFFICIENCY"
        ],
        "implemented": True
    },
    "emission_factor_change": {
        "label":
            "Emission Factor Change",
        "allowed_scopes": [
            "ENERGY"
        ],
        "allowed_energy_types": [
            "01", "02", "03", "04"
        ],
        "allowed_scenario_types": [
            "EMISSION_FACTOR"
        ],
        "implemented": True
    },
    "renewable_share_change": {
        "label":
            "Renewable Electricity Share Change",
        "allowed_scopes": [
            "ENERGY"
        ],
        "allowed_energy_types": [
            "04"
        ],
        "implemented": False
    }
}

SCENARIO_TYPES_V21 = [
    {
        "code": "EFFICIENCY",
        "label": "Energy Efficiency",
        "implemented": True
    },
    {
        "code": "TECHNOLOGY",
        "label": "Technology Transition",
        "implemented": True
    },
    {
        "code": "DECARBONIZATION",
        "label": "Decarbonization",
        "implemented": True
    }
]

CUSTOM_SCENARIO_TYPE_CODE_V21 = "CUSTOM"

PERIOD_YEAR = [2016, 2020]

PARAMETER_MNEMONICS = {
    "energy_demand_change": "EnergDC",
    "emission_factor_change": "EmissFC",
}
ENERGY_MNEMONICS = {
    "01": "Coal",
    "02": "Fuel",
    "03": "NatG",
    "04": "Elec",
}
TARGET_SCOPE_MNEMONICS = {
    "ENERGY": "All",
    "SECTOR_ENERGY": "S"
}

# Temporary constants for Validation Diagnostics
V21_VALIDATION_DIAGNOSTICS = False
ENERGY_ROW_INDEX_V21_TEST = {
    "01": 0,   # Coal
    "02": 1,   # Fuel
    "03": 2,   # Natural Gas
    "04": 3    # Electricity
}

MWH_TO_TJ = 0.0036