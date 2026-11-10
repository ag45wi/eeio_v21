from dataclasses import dataclass, field
from typing import Optional
import numpy as np

from util_db import tbl_to_df
from config_scenario import (
    SUPPORTED_TARGET_SCOPES,
    SUPPORTED_PARAMETERS
)


@dataclass
class ScenarioParameter:
    parameter_id: int
    parameter_code: str
    value: float
    unit: Optional[str] = None
    energy_type_code: Optional[str] = None
    sector_code: Optional[str] = None
    target_scope: Optional[str] = None

@dataclass
class ScenarioDefinition:
    scenario_id: int
    scenario_name: str
    description: Optional[str] = None
    scenario_year: Optional[int] = None
    scenario_types: list = field(default_factory=list)
    parameters: list = field(default_factory=list)

@dataclass
class ScenarioInputs:
    mat_finCons: np.ndarray
    CO2_EF: np.ndarray
    parameters: list


#DEFAULT_ENERGY_ORDER = ["COAL", "FUEL", "NATG", "ELEC"]
DEFAULT_ENERGY_ORDER = ["01", "02", "03", "04"]
'''
ENERGY_MAPPING = {
    "01": {"fincons_row": 0, "co2_factor_row": 0, "ef_column": 1, "name": "Coal"},
    "02": {"fincons_row": 1, "co2_factor_row": 1, "ef_column": 1, "name": "Fuel"},
    "03": {"fincons_row": 2, "co2_factor_row": 2, "ef_column": 1, "name": "Natural Gas"},
    "04": {"fincons_row": 3, "co2_factor_row": 3, "ef_column": 0, "name": "Electricity"}
}
'''

from config_scenario import ENERGY_MAPPING, CO2_ADJUSTMENT_MAPPING

'''
def build_energy_row_map():
    """
    Returns a map from energy code to index, eg.
    {
        "01": 0, #COAL
        "02": 1, #FUEL
        "03": 2, #NATG
        "04": 3, #ELEC
    }
    Note: it's only a transitional implementation.
    Later, this information would be moved into EEIOModel
    """
    return {
        code: index
        for index, code in enumerate(DEFAULT_ENERGY_ORDER)
    }
'''

class ScenarioProcessor:
    def __init__(self, model):
        self.energy_mapping = ENERGY_MAPPING
        self.co2_adjustment_mapping = CO2_ADJUSTMENT_MAPPING
        self.sector_column_map = (
            model.sector_column_map
        )

    def apply(self, model, scenario):
        scenario_finCons = model.mat_finCons.copy()
        scenario_CO2_EF = model.CO2_EF.copy()

        for parameter in scenario.parameters:
            self._apply_parameter(
                parameter,
                scenario_finCons,
                scenario_CO2_EF
            )

        return ScenarioInputs(
            mat_finCons=scenario_finCons.copy(),
            CO2_EF=scenario_CO2_EF.copy(),
            parameters=scenario.parameters
        )

    def _apply_parameter(
        self,
        parameter,
        scenario_finCons,
        scenario_CO2_EF
        ):
        if parameter.parameter_code == "energy_demand_change":
            self._apply_energy_demand_change(
                parameter,
                scenario_finCons
            )
        elif parameter.parameter_code == "emission_factor_change":
            self._apply_emission_factor_change(
                parameter,
                scenario_CO2_EF
            )
        else:
            raise ValueError(
                f"Unsupported scenario parameter: "
                f"{parameter.parameter_code}"
            )

    def _apply_energy_demand_change(
        self,
        parameter,
        scenario_finCons
        ):
        if parameter.energy_type_code is None:
            raise ValueError(
                "energy_demand_change requires "
                "energy_type_code"
            )
        mapping = self.energy_mapping[parameter.energy_type_code]
        energy_row = mapping["finCons_row"]

        if parameter.target_scope == "ENERGY":
            scenario_finCons[energy_row, :] *= (
                1.0 + parameter.value
            )
        elif parameter.target_scope == "SECTOR_ENERGY":
            self._apply_sector_energy_change(
                parameter,
                scenario_finCons,
                energy_row
            )
        else:
            raise ValueError(
                "Unsupported target scope"
            )

    def _apply_sector_energy_change(
            self,
            parameter,
            scenario_finCons,
            energy_row
        ):

        if parameter.sector_code is None:
            raise ValueError(
                "SECTOR_ENERGY requires sector_code"
            )
        sector_column = (
            self.sector_column_map[
                parameter.sector_code
            ]
        )
        if parameter.sector_code not in self.sector_column_map:
            raise ValueError(
                f"Unknown sector code: "
                f"{parameter.sector_code}"
            )
        scenario_finCons[energy_row, sector_column] *= (
            1.0 + parameter.value
        )

    def _apply_emission_factor_change(
        self,
        parameter,
        scenario_CO2_EF
        ):
        code = parameter.energy_type_code

        if code not in self.energy_mapping:
            raise ValueError(
                f"Unknown energy type: {code}"
            )
        mapping = self.energy_mapping[code]

        row = mapping["co2_factor_row"]
        #column = mapping["ef_column"]
        column = self.co2_adjustment_mapping[code]["column"]

        scenario_CO2_EF[row, column] *= (1.0 + parameter.value)

class ScenarioValidator:
    '''
    SUPPORTED_PARAMETERS = {
        "energy_demand_change",
        "emission_factor_change"
    }
    SUPPORTED_SCOPES = {
        "ENERGY"
    }
    '''
    @staticmethod
    def validate(scenario):
        if not scenario.scenario_name:
            #raise ValueError(
            raise ScenarioNotRunnableError(
                "Scenario name cannot be empty"
            )
        for parameter in scenario.parameters:
            if parameter.parameter_code not in (
                SUPPORTED_PARAMETERS
                ):
                #raise ValueError(
                raise ScenarioNotRunnableError(
                    f"Unsupported parameter: "
                    f"{parameter.parameter_code}"
                )
            if parameter.value <= -1.0:
                #raise ValueError(
                raise ScenarioNotRunnableError(
                    f"{parameter.parameter_code} "
                    "cannot be <= -100%"
                )
            if parameter.target_scope not in (SUPPORTED_TARGET_SCOPES):
                #raise ValueError(
                raise ScenarioNotRunnableError(
                    f"Unsupported target scope: "
                    f"{parameter.target_scope}"
                )

class ScenarioLoader:
    def __init__(self, db):
        self.db = db

    def _load_definition(self, scenario_id):
        scenario_id = int(scenario_id)
        query = f"""
            SELECT *
            FROM scenario_definition
            WHERE scenario_id = {scenario_id}
        """
        df = tbl_to_df(
            self.db,
            query
        )
        if df.empty:
            raise ValueError(
                f"Scenario not found: {scenario_id}"
            )

        return df.iloc[0].to_dict()

    def _load_types(self, scenario_id):
        scenario_id = int(scenario_id)
        query = f"""
            SELECT
                st.scenario_type_id,
                st.type_code,
                st.type_name,
                stm.is_primary
            FROM scenario_type_mapping stm
            JOIN scenario_type st
                ON stm.scenario_type_id =
                   st.scenario_type_id
            WHERE stm.scenario_id = {scenario_id}
            ORDER BY
                stm.is_primary DESC,
                st.type_name
        """
        df = tbl_to_df(
            self.db,
            query
        )
        return df.to_dict(
            orient="records"
        )

    def _load_parameters(self, scenario_id):
        scenario_id = int(scenario_id)
        query = f"""
            SELECT
                sp.parameter_id,
                sp.parameter_code,
                sp.parameter_value,
                sp.unit,
                st.target_id,
                st.energy_type_code,
                st.sector_code,
                st.target_scope
            FROM scenario_parameter sp
            LEFT JOIN scenario_target st
                ON sp.parameter_id =
                   st.parameter_id
            WHERE sp.scenario_id = {scenario_id}
            ORDER BY sp.parameter_id
        """

        df = tbl_to_df(
            self.db,
            query
        )
        return df

    def load(self, scenario_id):
        definition = self._load_definition(
            scenario_id
        )
        types = self._load_types(
            scenario_id
        )
        parameter_df = self._load_parameters(
            scenario_id
        )

        if parameter_df[
            "parameter_id"
        ].duplicated().any():
            raise ValueError(
                "Multiple targets per parameter are "
                "not supported in EEIO 2.1 v1"
            )
        parameters = []
        for _, row in parameter_df.iterrows():
            parameters.append(
                ScenarioParameter(
                    parameter_id=int(
                        row["parameter_id"]
                    ),
                    parameter_code=row[
                        "parameter_code"
                    ],
                    value=float(
                        row["parameter_value"]
                    ),
                    unit=row.get("unit"),
                    energy_type_code=row.get(
                        "energy_type_code"
                    ),
                    sector_code=row.get(
                        "sector_code"
                    ),
                    target_scope=row.get(
                        "target_scope"
                    )
                )
            )
        scenario = ScenarioDefinition(
            scenario_id=int(
                definition["scenario_id"]
            ),
            scenario_name=definition[
                "scenario_name"
            ],
            description=definition.get(
                "description"
            ),
            scenario_year=definition.get(
                "scenario_year"
            ),
            scenario_types=types,
            parameters=parameters
        )
        ScenarioValidator.validate(
            scenario
        )

        return scenario

def validate_target_scope(p):
    if p["target_scope"]=="SECTOR_ENERGY":
        if p["sector_code"] is None:
            raise ValueError(
                "SECTOR_ENERGY requires sector_code"
            )

class ScenarioNotRunnableError(ValueError):
    """
    Raised when a stored scenario definition cannot
    be executed by the current EEIO implementation.
    """

    pass