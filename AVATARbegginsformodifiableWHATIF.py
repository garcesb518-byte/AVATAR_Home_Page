

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_array


# ============================================================
# USER INPUTS — these become website controls later
# ============================================================


@dataclass
class AvatarInputs:
    mode: str = "scenario"  # "optimize" or "scenario"
    displayed_season: str = "winter"
    daily_energy_kwh: float = 5_500.0
    project_budget: float = 2_500_000.0

    selected_panels: int = 250
    selected_bess_units: int = 5
    max_solar_panels: int = 450
    max_bess_units: int = 30

    peco_rate_per_kwh: float = 0.10236
    energy_conservation_percent: float = 0.0
    occupancy_load_change_percent: float = 0.0
    temperature_load_change_percent: float = 0.0

    allow_solar_curtailment: bool = False
    allow_grid_bess_charging: bool = False
    solver_output: bool = False
    forecast_date: str | None = None


USER_INPUTS = AvatarInputs()


# ============================================================
# EQUIPMENT AND MODEL ASSUMPTIONS
# ============================================================


PANEL_POWER_KW = 0.640
PANEL_HARDWARE_COST = 288.0
PANEL_INSTALLATION_COST = 780.0
INSTALLED_PANEL_COST = PANEL_HARDWARE_COST + PANEL_INSTALLATION_COST

BESS_ENERGY_KWH = 277.0
BESS_POWER_KW = 125.0
BESS_ROUNDTRIP_EFFICIENCY = 0.90
BESS_CHARGE_EFFICIENCY = BESS_ROUNDTRIP_EFFICIENCY**0.5
BESS_DISCHARGE_EFFICIENCY = BESS_ROUNDTRIP_EFFICIENCY**0.5
BESS_COST_PER_KWH = 500.0
BESS_UNIT_COST = BESS_ENERGY_KWH * BESS_COST_PER_KWH

SEASON_DAY_WEIGHTS = {
    "winter": 90,
    "spring": 92,
    "summer": 92,
    "fall": 91,
}

GENERATOR_NAMES = [
    "G1 Base Supply",
    "G2 Base Supply",
    "G3 Flexible Supply",
    "G4 Fast Unit",
    "G5 Peaker",
    "G6 Emergency Import",
]
GENERATOR_MIN_KW = [40, 30, 25, 10, 5, 0]
GENERATOR_MAX_KW = [120, 100, 90, 75, 60, 150]
GENERATOR_RAMP_UP_KW = [40, 35, 50, 75, 60, 150]
GENERATOR_RAMP_DOWN_KW = [40, 35, 50, 75, 60, 150]
GENERATOR_INITIAL_ON = [1, 1, 0, 0, 0, 0]
GENERATOR_INITIAL_OUTPUT_KW = [80, 60, 0, 0, 0, 0]

# Relative educational cost functions. A calibration solve scales every term by
# one common factor so the full baseline cost, including commitment costs, equals
# the PECO reference rate of $0.10236/kWh. Scaling all terms equally preserves
# the merit order and unit-commitment behavior.
RAW_GENERATOR_VARIABLE_COST = [0.70, 0.80, 1.00, 1.25, 1.60, 2.00]
RAW_GENERATOR_FIXED_COST = [8, 7, 6, 4, 2, 0]
RAW_GENERATOR_STARTUP_COST = [60, 50, 40, 30, 20, 10]
RAW_GENERATOR_SHUTDOWN_COST = [20, 20, 15, 10, 8, 5]

# Relative shape only. The Bryn Mawr town magnitude is never used. The visible
# screenshot covers hours 00-15; hours 16-23 remain an explicit estimate.
FALLBACK_PROXY_SHAPE = np.array(
    [
        5.65,
        5.56,
        5.53,
        5.52,
        5.54,
        5.57,
        5.64,
        5.82,
        6.08,
        6.35,
        6.62,
        6.88,
        7.10,
        7.32,
        7.50,
        7.42,
        7.28,
        7.12,
        6.95,
        6.76,
        6.52,
        6.26,
        6.00,
        5.80,
    ],
    dtype=float,
)

DEFAULT_PVWATTS_PATH = Path(__file__).resolve().parent / "pvwatts_hourly(1).csv"
DEFAULT_OUTPUT_DIRECTORY = Path(__file__).resolve().parent / "avatar_outputs"


@dataclass
class GeneratorCosts:
    variable_per_kwh: list[float]
    fixed_per_hour: list[float]
    startup: list[float]
    shutdown: list[float]
    scale_factor: float = 1.0

    def scaled(self, scale: float) -> "GeneratorCosts":
        return GeneratorCosts(
            variable_per_kwh=[x * scale for x in self.variable_per_kwh],
            fixed_per_hour=[x * scale for x in self.fixed_per_hour],
            startup=[x * scale for x in self.startup],
            shutdown=[x * scale for x in self.shutdown],
            scale_factor=scale,
        )


RAW_GENERATOR_COSTS = GeneratorCosts(
    variable_per_kwh=RAW_GENERATOR_VARIABLE_COST,
    fixed_per_hour=RAW_GENERATOR_FIXED_COST,
    startup=RAW_GENERATOR_STARTUP_COST,
    shutdown=RAW_GENERATOR_SHUTDOWN_COST,
)


# ============================================================
# INPUT VALIDATION AND FORECAST CONNECTION
# ============================================================


def validate_inputs(inputs: AvatarInputs) -> None:
    if inputs.mode not in {"optimize", "scenario"}:
        raise ValueError("mode must be 'optimize' or 'scenario'.")
    if inputs.displayed_season not in SEASON_DAY_WEIGHTS:
        raise ValueError("displayed_season must be winter, spring, summer, or fall.")
    if inputs.daily_energy_kwh <= 0:
        raise ValueError("daily_energy_kwh must be greater than zero.")
    if inputs.project_budget < 0 or inputs.peco_rate_per_kwh <= 0:
        raise ValueError("Project budget cannot be negative and the PECO rate must be positive.")
    if inputs.selected_panels < 0 or inputs.selected_bess_units < 0:
        raise ValueError("Selected equipment quantities cannot be negative.")
    if inputs.max_solar_panels < 0 or inputs.max_bess_units < 0:
        raise ValueError("Maximum equipment quantities cannot be negative.")
    if inputs.selected_panels > inputs.max_solar_panels:
        raise ValueError("Selected panels exceed max_solar_panels.")
    if inputs.selected_bess_units > inputs.max_bess_units:
        raise ValueError("Selected BESS units exceed max_bess_units.")
    if not 0 <= inputs.energy_conservation_percent <= 100:
        raise ValueError("energy_conservation_percent must be between 0 and 100.")
    for name, value in {
        "occupancy_load_change_percent": inputs.occupancy_load_change_percent,
        "temperature_load_change_percent": inputs.temperature_load_change_percent,
    }.items():
        if not -100 <= value <= 100:
            raise ValueError(f"{name} must be between -100 and 100.")
    if inputs.allow_grid_bess_charging:
        raise ValueError(
            "Grid BESS charging is not implemented because the confirmed project "
            "assumption is solar-only charging."
        )
    scenario_cost = (
        inputs.selected_panels * INSTALLED_PANEL_COST
        + inputs.selected_bess_units * BESS_UNIT_COST
    )
    if inputs.mode == "scenario" and scenario_cost > inputs.project_budget:
        raise ValueError(
            f"Selected scenario costs ${scenario_cost:,.2f}, exceeding the "
            f"${inputs.project_budget:,.2f} budget."
        )


def _fallback_forecast(inputs: AvatarInputs) -> tuple[list[float], dict[str, Any]]:
    normalized = FALLBACK_PROXY_SHAPE / FALLBACK_PROXY_SHAPE.sum()
    load_kw = (normalized * inputs.daily_energy_kwh).tolist()
    peak_hour = int(np.argmax(load_kw))
    return load_kw, {
        "method": "scaled_bryn_mawr_reference_shape",
        "forecast_status": "temporary_proxy_not_trained_model",
        "daily_energy_kwh": round(float(sum(load_kw)), 3),
        "average_load_kw": round(float(np.mean(load_kw)), 3),
        "peak_load_kw": round(float(max(load_kw)), 3),
        "peak_hour": peak_hour,
        "warning": (
            "Bryn Mawr supplies only the normalized shape. Villanova magnitude is "
            "5.5 MWh/day, and hours 16-23 are inferred."
        ),
    }


def get_baseline_forecast(inputs: AvatarInputs) -> tuple[list[float], dict[str, Any]]:
    """Load the standalone forecast module while preserving a local fallback."""
    try:
        from avatar_load_forecasting import create_proxy_forecast
    except ImportError:
        return _fallback_forecast(inputs)

    result = create_proxy_forecast(
        daily_energy_mwh=inputs.daily_energy_kwh / 1000,
        forecast_date=inputs.forecast_date,
    )
    load_kw = [float(x) for x in result.forecast["forecast_kw"]]
    summary = dict(result.summary)
    summary["source_module"] = "avatar_load_forecasting.py"
    return load_kw, summary


def apply_load_controls(
    baseline_load_kw: list[float], inputs: AvatarInputs
) -> tuple[list[float], dict[str, float]]:
    conservation_multiplier = 1 - inputs.energy_conservation_percent / 100
    occupancy_multiplier = 1 + inputs.occupancy_load_change_percent / 100
    temperature_multiplier = 1 + inputs.temperature_load_change_percent / 100
    combined_multiplier = (
        conservation_multiplier * occupancy_multiplier * temperature_multiplier
    )
    adjusted = [float(value * combined_multiplier) for value in baseline_load_kw]
    if min(adjusted) < 0:
        raise ValueError("Load controls produced a negative demand value.")
    return adjusted, {
        "conservation_multiplier": conservation_multiplier,
        "occupancy_multiplier": occupancy_multiplier,
        "temperature_multiplier": temperature_multiplier,
        "combined_multiplier": combined_multiplier,
    }


# ============================================================
# PVWATTS PROCESSING
# ============================================================


def load_pvwatts_hourly_profile(
    csv_path: str | Path,
) -> tuple[dict[str, list[float]], float, dict[str, Any]]:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(
            f"PVWatts file not found: {path}. Keep pvwatts_hourly(1).csv beside "
            "this Python file or pass its path to run_avatar()."
        )

    lines = path.read_text(encoding="utf-8-sig").splitlines()
    try:
        header_row = next(
            index for index, line in enumerate(lines) if line.startswith("Month,Day,Hour,")
        )
    except StopIteration as exc:
        raise ValueError("Could not find the PVWatts hourly-data header.") from exc

    pv = pd.read_csv(path, skiprows=header_row)
    required = {"Month", "Day", "Hour", "AC System Output (W)"}
    missing = required.difference(pv.columns)
    if missing:
        raise ValueError(f"PVWatts file is missing columns: {sorted(missing)}")

    for column in required:
        pv[column] = pd.to_numeric(pv[column], errors="coerce")
    if pv[list(required)].isna().any().any():
        raise ValueError("PVWatts contains missing or nonnumeric core hourly values.")
    if len(pv) not in {8760, 8784}:
        raise ValueError(f"Expected 8,760 or 8,784 PVWatts rows; found {len(pv)}.")

    pv["ac_kw_per_kwdc"] = pv["AC System Output (W)"] / 1000
    season_months = {
        "winter": [12, 1, 2],
        "spring": [3, 4, 5],
        "summer": [6, 7, 8],
        "fall": [9, 10, 11],
    }
    profiles: dict[str, list[float]] = {}
    representative_daily: dict[str, float] = {}
    for season, months in season_months.items():
        profile = (
            pv.loc[pv["Month"].isin(months)]
            .groupby("Hour")["ac_kw_per_kwdc"]
            .mean()
            .reindex(range(24), fill_value=0)
        )
        profiles[season] = [float(x) for x in profile]
        representative_daily[season] = float(profile.sum())

    annual_output = float(pv["ac_kw_per_kwdc"].sum())
    weighted_output = sum(
        representative_daily[season] * SEASON_DAY_WEIGHTS[season]
        for season in SEASON_DAY_WEIGHTS
    )
    metadata = {
        "rows": int(len(pv)),
        "annual_ac_kwh_per_kwdc": annual_output,
        "weighted_representative_ac_kwh_per_kwdc": weighted_output,
        "representative_daily_kwh_per_kwdc": representative_daily,
        "annual_reconciliation_difference_kwh_per_kwdc": weighted_output
        - annual_output,
    }
    return profiles, annual_output, metadata


# ============================================================
# OPEN-SOURCE SCIPY + HIGHS MILP MODEL
# ============================================================


class _MilpBuilder:
    """Small named-model helper around scipy.optimize.milp.

    SciPy accepts a sparse constraint matrix instead of symbolic expressions.
    Keeping the indexing in this helper makes the unit-commitment equations
    readable while still using the open-source HiGHS solver bundled with SciPy.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.variable_names: list[str] = []
        self.objective: list[float] = []
        self.lower_bounds: list[float] = []
        self.upper_bounds: list[float] = []
        self.integrality: list[int] = []
        self.constraint_names: list[str] = []
        self.constraint_lower: list[float] = []
        self.constraint_upper: list[float] = []
        self.row_indices: list[int] = []
        self.column_indices: list[int] = []
        self.matrix_values: list[float] = []

    def add_variable(
        self,
        name: str,
        *,
        lower: float = 0.0,
        upper: float = np.inf,
        integer: bool = False,
        binary: bool = False,
        objective: float = 0.0,
    ) -> int:
        index = len(self.variable_names)
        if binary:
            lower, upper, integer = 0.0, 1.0, True
        self.variable_names.append(name)
        self.objective.append(float(objective))
        self.lower_bounds.append(float(lower))
        self.upper_bounds.append(float(upper))
        self.integrality.append(1 if integer else 0)
        return index

    def add_constraint(
        self,
        coefficients: dict[int, float],
        *,
        lower: float = -np.inf,
        upper: float = np.inf,
        name: str,
    ) -> None:
        row = len(self.constraint_names)
        self.constraint_names.append(name)
        self.constraint_lower.append(float(lower))
        self.constraint_upper.append(float(upper))
        for column, coefficient in coefficients.items():
            if coefficient != 0:
                self.row_indices.append(row)
                self.column_indices.append(column)
                self.matrix_values.append(float(coefficient))

    def solve(self, *, display: bool):
        shape = (len(self.constraint_names), len(self.variable_names))
        matrix = coo_array(
            (
                np.asarray(self.matrix_values, dtype=float),
                (
                    np.asarray(self.row_indices, dtype=np.int32),
                    np.asarray(self.column_indices, dtype=np.int32),
                ),
            ),
            shape=shape,
        ).tocsc()
        return milp(
            c=np.asarray(self.objective, dtype=float),
            integrality=np.asarray(self.integrality, dtype=np.int8),
            bounds=Bounds(self.lower_bounds, self.upper_bounds),
            constraints=LinearConstraint(
                matrix,
                np.asarray(self.constraint_lower, dtype=float),
                np.asarray(self.constraint_upper, dtype=float),
            ),
            options={
                "disp": bool(display),
                "presolve": True,
                "mip_rel_gap": 1e-7,
            },
        )


def _terms(*items: tuple[int, float]) -> dict[int, float]:
    """Combine variable/coefficient pairs into one sparse linear expression."""
    combined: dict[int, float] = {}
    for variable, coefficient in items:
        combined[variable] = combined.get(variable, 0.0) + coefficient
    return combined


def solve_case(
    case_type: str,
    case_name: str,
    load_kw: list[float],
    solar_profiles: dict[str, list[float]],
    inputs: AvatarInputs,
    generator_costs: GeneratorCosts,
) -> dict[str, Any]:
    """Solve one annual weighted case with a shared system size."""
    if case_type not in {"baseline", "scenario", "optimize"}:
        raise ValueError("case_type must be baseline, scenario, or optimize.")
    if len(load_kw) != 24:
        raise ValueError("load_kw must contain exactly 24 hourly values.")

    seasons = list(SEASON_DAY_WEIGHTS)
    gens = range(len(GENERATOR_NAMES))
    hours = range(24)
    model = _MilpBuilder(case_name)

    num_panels = model.add_variable(
        "Number_of_Solar_Panels",
        upper=inputs.max_solar_panels,
        integer=True,
    )
    num_bess_units = model.add_variable(
        "Number_of_BESS_Units",
        upper=inputs.max_bess_units,
        integer=True,
    )

    gen_on: dict[tuple[str, int, int], int] = {}
    gen_start: dict[tuple[str, int, int], int] = {}
    gen_stop: dict[tuple[str, int, int], int] = {}
    gen_output: dict[tuple[str, int, int], int] = {}
    solar_to_load: dict[tuple[str, int], int] = {}
    solar_to_battery: dict[tuple[str, int], int] = {}
    battery_discharge: dict[tuple[str, int], int] = {}
    soc: dict[tuple[str, int], int] = {}
    battery_charging: dict[tuple[str, int], int] = {}
    battery_discharging: dict[tuple[str, int], int] = {}
    solar_curtailed: dict[tuple[str, int], int] | None = (
        {} if inputs.allow_solar_curtailment else None
    )

    for season in seasons:
        weight = SEASON_DAY_WEIGHTS[season]
        for g in gens:
            for t in hours:
                key = (season, g, t)
                gen_on[key] = model.add_variable(
                    f"Generator_On_{season}_{g}_{t}",
                    binary=True,
                    objective=weight * generator_costs.fixed_per_hour[g],
                )
                gen_start[key] = model.add_variable(
                    f"Generator_Start_{season}_{g}_{t}",
                    binary=True,
                    objective=weight * generator_costs.startup[g],
                )
                gen_stop[key] = model.add_variable(
                    f"Generator_Stop_{season}_{g}_{t}",
                    binary=True,
                    objective=weight * generator_costs.shutdown[g],
                )
                gen_output[key] = model.add_variable(
                    f"Generator_Output_{season}_{g}_{t}",
                    objective=weight * generator_costs.variable_per_kwh[g],
                )
        for t in hours:
            key = (season, t)
            solar_to_load[key] = model.add_variable(f"Solar_To_Load_{season}_{t}")
            solar_to_battery[key] = model.add_variable(f"Solar_To_Battery_{season}_{t}")
            battery_discharge[key] = model.add_variable(f"Battery_Discharge_{season}_{t}")
            soc[key] = model.add_variable(f"Battery_State_Of_Charge_{season}_{t}")
            battery_charging[key] = model.add_variable(
                f"Battery_Charging_{season}_{t}", binary=True
            )
            battery_discharging[key] = model.add_variable(
                f"Battery_Discharging_{season}_{t}", binary=True
            )
            if solar_curtailed is not None:
                solar_curtailed[key] = model.add_variable(
                    f"Solar_Curtailed_{season}_{t}"
                )

    model.add_constraint(
        _terms(
            (num_panels, INSTALLED_PANEL_COST),
            (num_bess_units, BESS_UNIT_COST),
        ),
        upper=inputs.project_budget,
        name="Project_Budget",
    )
    if case_type == "baseline":
        model.add_constraint(
            _terms((num_panels, 1)), lower=0, upper=0, name="Baseline_No_Solar"
        )
        model.add_constraint(
            _terms((num_bess_units, 1)), lower=0, upper=0, name="Baseline_No_BESS"
        )
    elif case_type == "scenario":
        model.add_constraint(
            _terms((num_panels, 1)),
            lower=inputs.selected_panels,
            upper=inputs.selected_panels,
            name="Scenario_Solar_Panels",
        )
        model.add_constraint(
            _terms((num_bess_units, 1)),
            lower=inputs.selected_bess_units,
            upper=inputs.selected_bess_units,
            name="Scenario_BESS_Units",
        )

    max_charge_power = inputs.max_bess_units * BESS_POWER_KW
    for season in seasons:
        for t in hours:
            st = (season, t)
            solar_balance_terms = [
                (solar_to_load[st], 1),
                (solar_to_battery[st], 1),
                (num_panels, -PANEL_POWER_KW * solar_profiles[season][t]),
            ]
            if solar_curtailed is not None:
                solar_balance_terms.append((solar_curtailed[st], 1))
            model.add_constraint(
                _terms(*solar_balance_terms),
                lower=0,
                upper=0,
                name=f"Solar_Balance_{season}_{t}",
            )
            model.add_constraint(
                _terms((solar_to_load[st], 1)),
                upper=load_kw[t],
                name=f"Solar_To_Load_Limit_{season}_{t}",
            )
            model.add_constraint(
                _terms(
                    (solar_to_battery[st], 1),
                    (num_bess_units, -BESS_POWER_KW),
                ),
                upper=0,
                name=f"Solar_To_Battery_Capacity_{season}_{t}",
            )
            model.add_constraint(
                _terms(
                    *[(gen_output[season, g, t], 1) for g in gens],
                    (solar_to_load[st], 1),
                    (battery_discharge[st], 1),
                ),
                lower=load_kw[t],
                upper=load_kw[t],
                name=f"Campus_Load_Balance_{season}_{t}",
            )
            model.add_constraint(
                _terms(
                    (solar_to_battery[st], 1),
                    (battery_charging[st], -max_charge_power),
                ),
                upper=0,
                name=f"Battery_Charge_Mode_{season}_{t}",
            )
            model.add_constraint(
                _terms(
                    (battery_discharge[st], 1),
                    (battery_discharging[st], -max_charge_power),
                ),
                upper=0,
                name=f"Battery_Discharge_Mode_{season}_{t}",
            )
            model.add_constraint(
                _terms(
                    (battery_discharge[st], 1),
                    (num_bess_units, -BESS_POWER_KW),
                ),
                upper=0,
                name=f"Battery_Discharge_Capacity_{season}_{t}",
            )
            model.add_constraint(
                _terms(
                    (battery_charging[st], 1),
                    (battery_discharging[st], 1),
                ),
                upper=1,
                name=f"No_Simultaneous_Battery_Mode_{season}_{t}",
            )
            model.add_constraint(
                _terms((soc[st], 1), (num_bess_units, -BESS_ENERGY_KWH)),
                upper=0,
                name=f"SOC_Capacity_{season}_{t}",
            )
            soc_terms = [
                (soc[st], 1),
                (solar_to_battery[st], -BESS_CHARGE_EFFICIENCY),
                (battery_discharge[st], 1 / BESS_DISCHARGE_EFFICIENCY),
            ]
            if t == 0:
                soc_terms.append((num_bess_units, -0.50 * BESS_ENERGY_KWH))
            else:
                soc_terms.append((soc[season, t - 1], -1))
            model.add_constraint(
                _terms(*soc_terms),
                lower=0,
                upper=0,
                name=f"SOC_Balance_{season}_{t}",
            )

        model.add_constraint(
            _terms(
                (soc[season, 23], 1),
                (num_bess_units, -0.50 * BESS_ENERGY_KWH),
            ),
            lower=0,
            upper=0,
            name=f"End_Of_Day_SOC_{season}",
        )

        for g in gens:
            for t in hours:
                key = (season, g, t)
                model.add_constraint(
                    _terms(
                        (gen_output[key], 1),
                        (gen_on[key], -GENERATOR_MIN_KW[g]),
                    ),
                    lower=0,
                    name=f"Generator_Min_{season}_{g}_{t}",
                )
                model.add_constraint(
                    _terms(
                        (gen_output[key], 1),
                        (gen_on[key], -GENERATOR_MAX_KW[g]),
                    ),
                    upper=0,
                    name=f"Generator_Max_{season}_{g}_{t}",
                )
                model.add_constraint(
                    _terms((gen_start[key], 1), (gen_stop[key], 1)),
                    upper=1,
                    name=f"No_Start_Stop_{season}_{g}_{t}",
                )
                transition = [
                    (gen_on[key], 1),
                    (gen_start[key], -1),
                    (gen_stop[key], 1),
                ]
                if t == 0:
                    transition_value = GENERATOR_INITIAL_ON[g]
                    ramp_up_upper = GENERATOR_RAMP_UP_KW[g] + GENERATOR_INITIAL_OUTPUT_KW[g]
                    ramp_down_upper = GENERATOR_RAMP_DOWN_KW[g] - GENERATOR_INITIAL_OUTPUT_KW[g]
                    ramp_up = _terms((gen_output[key], 1))
                    ramp_down = _terms((gen_output[key], -1))
                else:
                    transition.append((gen_on[season, g, t - 1], -1))
                    transition_value = 0
                    ramp_up_upper = GENERATOR_RAMP_UP_KW[g]
                    ramp_down_upper = GENERATOR_RAMP_DOWN_KW[g]
                    ramp_up = _terms(
                        (gen_output[key], 1),
                        (gen_output[season, g, t - 1], -1),
                    )
                    ramp_down = _terms(
                        (gen_output[season, g, t - 1], 1),
                        (gen_output[key], -1),
                    )
                model.add_constraint(
                    _terms(*transition),
                    lower=transition_value,
                    upper=transition_value,
                    name=f"Generator_Transition_{season}_{g}_{t}",
                )
                model.add_constraint(
                    ramp_up,
                    upper=ramp_up_upper,
                    name=f"Ramp_Up_{season}_{g}_{t}",
                )
                model.add_constraint(
                    ramp_down,
                    upper=ramp_down_upper,
                    name=f"Ramp_Down_{season}_{g}_{t}",
                )

    solution = model.solve(display=inputs.solver_output)
    if solution.status != 0 or solution.x is None:
        status_names = {
            1: "limit_reached",
            2: "infeasible",
            3: "unbounded",
            4: "solver_error",
        }
        status = status_names.get(int(solution.status), "not_optimal")
        return {
            "status": status,
            "solver": "SciPy milp / HiGHS",
            "solver_status": int(solution.status),
            "case_name": case_name,
            "conflicting_constraints": [],
            "message": (
                "The scenario is infeasible. With curtailment disabled, try fewer "
                "panels or more BESS capacity."
                if solution.status == 2
                else f"HiGHS did not return an optimal solution: {solution.message}"
            ),
        }

    values = np.asarray(solution.x, dtype=float)

    def value(variable: int) -> float:
        result = float(values[variable])
        return 0.0 if abs(result) < 1e-8 else result

    panels_selected = int(round(value(num_panels)))
    bess_selected = int(round(value(num_bess_units)))
    hourly_by_season: dict[str, pd.DataFrame] = {}
    commitment_by_season: dict[str, pd.DataFrame] = {}
    dispatch_by_season: dict[str, pd.DataFrame] = {}

    annual_totals = {
        "load_kwh": 0.0,
        "grid_supply_kwh": 0.0,
        "solar_available_kwh": 0.0,
        "solar_to_load_kwh": 0.0,
        "solar_to_battery_kwh": 0.0,
        "solar_curtailed_kwh": 0.0,
        "battery_discharge_kwh": 0.0,
    }

    for season in seasons:
        rows = []
        commitment_rows = []
        dispatch_rows = []
        for t in hours:
            generator_values = [value(gen_output[season, g, t]) for g in gens]
            grid_supply = float(sum(generator_values))
            solar_available_value = (
                panels_selected * PANEL_POWER_KW * solar_profiles[season][t]
            )
            curtailed = value(solar_curtailed[season, t]) if solar_curtailed is not None else 0.0
            row = {
                "Hour": t,
                "Load_kW": float(load_kw[t]),
                "Solar_Available_kW": float(solar_available_value),
                "Solar_To_Load_kW": value(solar_to_load[season, t]),
                "Solar_To_Battery_kW": value(solar_to_battery[season, t]),
                "Solar_Curtailed_kW": float(curtailed),
                "Battery_Discharge_kW": value(battery_discharge[season, t]),
                "Battery_SOC_kWh": value(soc[season, t]),
                "Grid_Supply_kW": grid_supply,
            }
            row["Load_Balance_Error_kW"] = (
                row["Grid_Supply_kW"]
                + row["Solar_To_Load_kW"]
                + row["Battery_Discharge_kW"]
                - row["Load_kW"]
            )
            row["Solar_Balance_Error_kW"] = (
                row["Solar_To_Load_kW"]
                + row["Solar_To_Battery_kW"]
                + row["Solar_Curtailed_kW"]
                - row["Solar_Available_kW"]
            )
            rows.append(row)

            commitment_row = {"Hour": t}
            dispatch_row = {"Hour": t}
            for g, name in enumerate(GENERATOR_NAMES):
                commitment_row[name] = int(round(value(gen_on[season, g, t])))
                dispatch_row[name] = value(gen_output[season, g, t])
            commitment_rows.append(commitment_row)
            dispatch_rows.append(dispatch_row)

        frame = pd.DataFrame(rows)
        hourly_by_season[season] = frame
        commitment_by_season[season] = pd.DataFrame(commitment_rows)
        dispatch_by_season[season] = pd.DataFrame(dispatch_rows)
        weight = SEASON_DAY_WEIGHTS[season]
        annual_totals["load_kwh"] += weight * frame["Load_kW"].sum()
        annual_totals["grid_supply_kwh"] += weight * frame["Grid_Supply_kW"].sum()
        annual_totals["solar_available_kwh"] += weight * frame["Solar_Available_kW"].sum()
        annual_totals["solar_to_load_kwh"] += weight * frame["Solar_To_Load_kW"].sum()
        annual_totals["solar_to_battery_kwh"] += weight * frame["Solar_To_Battery_kW"].sum()
        annual_totals["solar_curtailed_kwh"] += weight * frame["Solar_Curtailed_kW"].sum()
        annual_totals["battery_discharge_kwh"] += weight * frame["Battery_Discharge_kW"].sum()

    generator_cost_rows = []
    for g, name in enumerate(GENERATOR_NAMES):
        energy_kwh = sum(
            SEASON_DAY_WEIGHTS[season]
            * sum(value(gen_output[season, g, t]) for t in hours)
            for season in seasons
        )
        energy_cost = energy_kwh * generator_costs.variable_per_kwh[g]
        fixed = sum(
            SEASON_DAY_WEIGHTS[season]
            * sum(value(gen_on[season, g, t]) for t in hours)
            * generator_costs.fixed_per_hour[g]
            for season in seasons
        )
        startup = sum(
            SEASON_DAY_WEIGHTS[season]
            * sum(value(gen_start[season, g, t]) for t in hours)
            * generator_costs.startup[g]
            for season in seasons
        )
        shutdown = sum(
            SEASON_DAY_WEIGHTS[season]
            * sum(value(gen_stop[season, g, t]) for t in hours)
            * generator_costs.shutdown[g]
            for season in seasons
        )
        generator_cost_rows.append(
            {
                "Generator": name,
                "Annual_Energy_kWh": float(energy_kwh),
                "Variable_Rate_$_per_kWh": generator_costs.variable_per_kwh[g],
                "Energy_Cost_$": float(energy_cost),
                "Fixed_Cost_$": float(fixed),
                "Startup_Cost_$": float(startup),
                "Shutdown_Cost_$": float(shutdown),
                "Total_Cost_$": float(energy_cost + fixed + startup + shutdown),
            }
        )

    annual_operating_cost = float(sum(row["Total_Cost_$"] for row in generator_cost_rows))
    annual_totals["operating_cost_$"] = annual_operating_cost
    annual_totals["effective_grid_rate_$_per_kwh"] = (
        annual_operating_cost / annual_totals["grid_supply_kwh"]
        if annual_totals["grid_supply_kwh"] > 0
        else 0.0
    )

    result = {
        "status": "optimal",
        "case_name": case_name,
        "case_type": case_type,
        "panels_selected": panels_selected,
        "bess_selected": bess_selected,
        "solar_capacity_kw_dc": panels_selected * PANEL_POWER_KW,
        "bess_capacity_kwh": bess_selected * BESS_ENERGY_KWH,
        "bess_capacity_kw": bess_selected * BESS_POWER_KW,
        "project_cost_$": (
            panels_selected * INSTALLED_PANEL_COST + bess_selected * BESS_UNIT_COST
        ),
        "annual_totals": annual_totals,
        "hourly_by_season": hourly_by_season,
        "commitment_by_season": commitment_by_season,
        "dispatch_by_season": dispatch_by_season,
        "generator_costs": pd.DataFrame(generator_cost_rows),
        "objective_value": float(solution.fun),
        "solver": "SciPy milp / HiGHS",
        "solver_mip_gap": (
            float(solution.mip_gap) if solution.mip_gap is not None else None
        ),
        "solver_node_count": (
            int(solution.mip_node_count)
            if solution.mip_node_count is not None
            else None
        ),
    }
    return result


def calibrate_generator_costs(
    baseline_load_kw: list[float],
    solar_profiles: dict[str, list[float]],
    inputs: AvatarInputs,
) -> GeneratorCosts:
    """Scale the educational cost functions to the PECO baseline average rate."""
    calibration = solve_case(
        "baseline",
        "baseline_cost_calibration",
        baseline_load_kw,
        solar_profiles,
        inputs,
        RAW_GENERATOR_COSTS,
    )
    if calibration["status"] != "optimal":
        raise RuntimeError(f"Generator-cost calibration failed: {calibration}")
    grid_energy = calibration["annual_totals"]["grid_supply_kwh"]
    raw_cost = calibration["annual_totals"]["operating_cost_$"]
    if grid_energy <= 0 or raw_cost <= 0:
        raise RuntimeError("Generator-cost calibration returned nonpositive totals.")
    required_cost = grid_energy * inputs.peco_rate_per_kwh
    return RAW_GENERATOR_COSTS.scaled(required_cost / raw_cost)


# ============================================================
# RESULTS, WEBSITE PAYLOAD, AND OUTPUTS
# ============================================================


def _records(frame: pd.DataFrame, digits: int = 5) -> list[dict[str, Any]]:
    rounded = frame.copy()
    numeric = rounded.select_dtypes(include=[np.number]).columns
    rounded[numeric] = rounded[numeric].round(digits)
    return rounded.to_dict(orient="records")


def _case_error(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": case.get("status", "error"),
        "message": case.get("message", "Optimization failed."),
        "case_name": case.get("case_name"),
        "conflicting_constraints": case.get("conflicting_constraints", []),
    }


def run_avatar(
    inputs: AvatarInputs = USER_INPUTS,
    pvwatts_csv_path: str | Path = DEFAULT_PVWATTS_PATH,
) -> dict[str, Any]:
    validate_inputs(inputs)
    solar_profiles, annual_pvwatts_output, pvwatts_metadata = load_pvwatts_hourly_profile(
        pvwatts_csv_path
    )
    baseline_load_kw, forecast_summary = get_baseline_forecast(inputs)
    adjusted_load_kw, load_control_factors = apply_load_controls(baseline_load_kw, inputs)

    calibrated_costs = calibrate_generator_costs(
        baseline_load_kw, solar_profiles, inputs
    )
    raw_baseline = solve_case(
        "baseline",
        "unadjusted_baseline",
        baseline_load_kw,
        solar_profiles,
        inputs,
        calibrated_costs,
    )
    if raw_baseline["status"] != "optimal":
        return _case_error(raw_baseline)

    adjusted_baseline = solve_case(
        "baseline",
        "adjusted_load_without_der",
        adjusted_load_kw,
        solar_profiles,
        inputs,
        calibrated_costs,
    )
    if adjusted_baseline["status"] != "optimal":
        return _case_error(adjusted_baseline)

    avatar_case_type = "optimize" if inputs.mode == "optimize" else "scenario"
    avatar = solve_case(
        avatar_case_type,
        "avatar_pv_bess",
        adjusted_load_kw,
        solar_profiles,
        inputs,
        calibrated_costs,
    )
    if avatar["status"] != "optimal":
        return _case_error(avatar)

    baseline_cost = raw_baseline["annual_totals"]["operating_cost_$"]
    adjusted_no_der_cost = adjusted_baseline["annual_totals"]["operating_cost_$"]
    avatar_cost = avatar["annual_totals"]["operating_cost_$"]
    annual_load = avatar["annual_totals"]["load_kwh"]
    annual_grid = avatar["annual_totals"]["grid_supply_kwh"]
    annual_solar_delivered = (
        avatar["annual_totals"]["solar_to_load_kwh"]
        + avatar["annual_totals"]["battery_discharge_kwh"]
    )

    all_hourly = list(avatar["hourly_by_season"].values())
    max_load_balance_error = max(
        float(frame["Load_Balance_Error_kW"].abs().max()) for frame in all_hourly
    )
    max_solar_balance_error = max(
        float(frame["Solar_Balance_Error_kW"].abs().max()) for frame in all_hourly
    )
    selected = inputs.displayed_season
    selected_hourly = avatar["hourly_by_season"][selected]
    expected_annual_solar = avatar["solar_capacity_kw_dc"] * annual_pvwatts_output

    summary = {
        "status": "optimal",
        "optimization_solver": "SciPy milp / HiGHS (open source)",
        "optimization_basis": "four weighted representative seasonal days",
        "operating_objective": "minimize calibrated annual supply cost under project budget",
        "peco_reference_rate_$_per_kwh": inputs.peco_rate_per_kwh,
        "calibrated_baseline_effective_rate_$_per_kwh": (
            baseline_cost / raw_baseline["annual_totals"]["grid_supply_kwh"]
        ),
        "baseline_daily_energy_kwh": float(sum(baseline_load_kw)),
        "adjusted_daily_energy_kwh": float(sum(adjusted_load_kw)),
        "baseline_peak_kw": float(max(baseline_load_kw)),
        "adjusted_peak_kw": float(max(adjusted_load_kw)),
        "estimated_peak_hour": int(np.argmax(adjusted_load_kw)),
        "panels_selected": avatar["panels_selected"],
        "solar_capacity_kw_dc": avatar["solar_capacity_kw_dc"],
        "bess_units_selected": avatar["bess_selected"],
        "bess_energy_capacity_kwh": avatar["bess_capacity_kwh"],
        "bess_power_capacity_kw": avatar["bess_capacity_kw"],
        "project_cost_$": avatar["project_cost_$"],
        "budget_remaining_$": inputs.project_budget - avatar["project_cost_$"],
        "annual_baseline_operating_cost_$": baseline_cost,
        "annual_adjusted_load_cost_without_der_$": adjusted_no_der_cost,
        "annual_avatar_operating_cost_$": avatar_cost,
        "annual_load_adjustment_savings_$": baseline_cost - adjusted_no_der_cost,
        "annual_der_operating_savings_$": adjusted_no_der_cost - avatar_cost,
        "annual_total_operating_savings_$": baseline_cost - avatar_cost,
        "annual_load_kwh": annual_load,
        "annual_grid_supply_kwh": annual_grid,
        "annual_grid_energy_reduction_kwh": (
            raw_baseline["annual_totals"]["grid_supply_kwh"] - annual_grid
        ),
        "annual_solar_generation_kwh": avatar["annual_totals"]["solar_available_kwh"],
        "annual_solar_delivered_to_load_kwh": annual_solar_delivered,
        "renewable_share_of_load_percent": (
            100 * annual_solar_delivered / annual_load if annual_load else 0.0
        ),
        "pvwatts_exact_annual_solar_estimate_kwh": expected_annual_solar,
        "displayed_season": selected,
    }

    checks = {
        "season_day_weights_sum": sum(SEASON_DAY_WEIGHTS.values()),
        "baseline_rate_error_$_per_kwh": (
            summary["calibrated_baseline_effective_rate_$_per_kwh"]
            - inputs.peco_rate_per_kwh
        ),
        "max_hourly_load_balance_error_kw": max_load_balance_error,
        "max_hourly_solar_balance_error_kw": max_solar_balance_error,
        "annual_pvwatts_reconciliation_error_kwh": (
            avatar["annual_totals"]["solar_available_kwh"] - expected_annual_solar
        ),
        "annual_load_supply_reconciliation_error_kwh": (
            annual_grid + annual_solar_delivered - annual_load
        ),
    }

    forecast_tab = {
        "title": "Villanova Load Forecast",
        "summary": forecast_summary,
        "hourly": [
            {
                "hour": hour,
                "forecast_kw": round(float(baseline_load_kw[hour]), 5),
            }
            for hour in range(24)
        ],
    }
    what_if_tab = {
        "title": "PV and BESS What-If Calculator",
        "inputs": asdict(inputs),
        "load_control_factors": load_control_factors,
        "summary": summary,
        "checks": checks,
        "selected_season_hourly": _records(selected_hourly),
        "unit_commitment": _records(avatar["commitment_by_season"][selected], 0),
        "economic_dispatch": _records(avatar["dispatch_by_season"][selected]),
        "annual_generator_costs": _records(avatar["generator_costs"]),
    }

    return {
        "status": "optimal",
        "forecast_tab": forecast_tab,
        "what_if_tab": what_if_tab,
        "summary": summary,
        "checks": checks,
        "pvwatts_metadata": pvwatts_metadata,
        "generator_cost_calibration": {
            "scale_factor": calibrated_costs.scale_factor,
            "variable_rates_$_per_kwh": calibrated_costs.variable_per_kwh,
            "fixed_costs_$_per_hour": calibrated_costs.fixed_per_hour,
            "startup_costs_$": calibrated_costs.startup,
            "shutdown_costs_$": calibrated_costs.shutdown,
        },
        "baseline_load_kw": baseline_load_kw,
        "adjusted_load_kw": adjusted_load_kw,
        "raw_baseline": raw_baseline,
        "adjusted_baseline": adjusted_baseline,
        "avatar": avatar,
    }


def website_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Return only JSON-safe fields intended for the eventual website."""
    if result.get("status") != "optimal":
        return result
    return {
        "status": "optimal",
        "forecast_tab": result["forecast_tab"],
        "what_if_tab": result["what_if_tab"],
        "pvwatts_metadata": result["pvwatts_metadata"],
    }


def _plot_results(result: dict[str, Any], output_dir: Path) -> list[str]:
    inputs = result["what_if_tab"]["inputs"]
    season = inputs["displayed_season"]
    avatar = result["avatar"]
    hourly = avatar["hourly_by_season"][season]
    commitment = avatar["commitment_by_season"][season]
    dispatch = avatar["dispatch_by_season"][season]
    hours = np.arange(24)
    files: list[str] = []

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(hours, result["baseline_load_kw"], label="Baseline forecast", linewidth=2)
    ax.plot(hours, result["adjusted_load_kw"], label="Adjusted what-if load", linewidth=2)
    ax.set(title="Baseline and Adjusted Campus Load", xlabel="Hour", ylabel="Demand (kW)")
    ax.set_xticks(range(0, 24, 2))
    ax.grid(alpha=0.25)
    ax.legend(frameon=False)
    fig.tight_layout()
    path = output_dir / "load_comparison.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    files.append(path.name)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(hours, hourly["Load_kW"], color="black", linewidth=2.3, label="Load")
    ax.stackplot(
        hours,
        hourly["Grid_Supply_kW"],
        hourly["Solar_To_Load_kW"],
        hourly["Battery_Discharge_kW"],
        labels=["Grid supply", "Solar to load", "Battery discharge"],
        alpha=0.78,
    )
    ax.set(
        title=f"{season.title()} Hourly Energy Supply",
        xlabel="Hour",
        ylabel="Power (kW)",
    )
    ax.set_xticks(range(0, 24, 2))
    ax.legend(frameon=False, ncol=2)
    fig.tight_layout()
    path = output_dir / "hourly_energy_supply.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    files.append(path.name)

    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(hours, hourly["Battery_SOC_kWh"], color="#6f4aa8", linewidth=2.2)
    ax.set(title="Battery State of Charge", xlabel="Hour", ylabel="Energy (kWh)")
    ax.set_xticks(range(0, 24, 2))
    ax.grid(alpha=0.25)
    fig.tight_layout()
    path = output_dir / "battery_state_of_charge.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    files.append(path.name)

    fig, ax = plt.subplots(figsize=(11, 4.5))
    matrix = commitment[GENERATOR_NAMES].to_numpy().T
    ax.imshow(matrix, aspect="auto", cmap="YlGn", vmin=0, vmax=1)
    ax.set(title="Unit Commitment", xlabel="Hour")
    ax.set_xticks(range(24))
    ax.set_yticks(range(len(GENERATOR_NAMES)), GENERATOR_NAMES)
    fig.tight_layout()
    path = output_dir / "unit_commitment.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    files.append(path.name)

    fig, ax = plt.subplots(figsize=(11, 5))
    ax.stackplot(
        hours,
        *[dispatch[name] for name in GENERATOR_NAMES],
        labels=GENERATOR_NAMES,
        alpha=0.82,
    )
    ax.set(title="Economic Dispatch", xlabel="Hour", ylabel="Grid Supply (kW)")
    ax.set_xticks(range(0, 24, 2))
    ax.legend(frameon=False, ncol=3, fontsize=8)
    fig.tight_layout()
    path = output_dir / "economic_dispatch.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    files.append(path.name)
    return files


def save_outputs(
    result: dict[str, Any], output_dir: str | Path = DEFAULT_OUTPUT_DIRECTORY
) -> dict[str, Any]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    payload = website_payload(result)
    with (output_path / "avatar_web_payload.json").open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)

    if result.get("status") != "optimal":
        return {"output_directory": str(output_path), "files": ["avatar_web_payload.json"]}

    season = result["summary"]["displayed_season"]
    avatar = result["avatar"]
    hourly = avatar["hourly_by_season"][season]
    commitment = avatar["commitment_by_season"][season]
    dispatch = avatar["dispatch_by_season"][season]
    hourly.to_csv(output_path / "hourly_dispatch.csv", index=False)
    commitment.to_csv(output_path / "unit_commitment.csv", index=False)
    dispatch.to_csv(output_path / "economic_dispatch.csv", index=False)
    avatar["generator_costs"].to_csv(output_path / "annual_generator_costs.csv", index=False)
    pd.DataFrame(
        [{"Metric": key, "Value": value} for key, value in result["summary"].items()]
    ).to_csv(output_path / "summary.csv", index=False)

    with pd.ExcelWriter(output_path / "avatar_highs_results.xlsx", engine="openpyxl") as writer:
        pd.DataFrame(
            [{"Metric": key, "Value": value} for key, value in result["summary"].items()]
        ).to_excel(writer, sheet_name="Summary", index=False)
        hourly.to_excel(writer, sheet_name="Hourly Dispatch", index=False)
        commitment.to_excel(writer, sheet_name="Unit Commitment", index=False)
        dispatch.to_excel(writer, sheet_name="Economic Dispatch", index=False)
        avatar["generator_costs"].to_excel(writer, sheet_name="Generator Costs", index=False)

    plot_files = _plot_results(result, output_path)
    files = [
        "avatar_web_payload.json",
        "summary.csv",
        "hourly_dispatch.csv",
        "unit_commitment.csv",
        "economic_dispatch.csv",
        "annual_generator_costs.csv",
        "avatar_highs_results.xlsx",
        *plot_files,
    ]
    return {"output_directory": str(output_path), "files": files}


def print_summary(result: dict[str, Any]) -> None:
    if result.get("status") != "optimal":
        print(json.dumps(result, indent=2))
        return
    summary = result["summary"]
    print("\nAVATAR FOUR-SEASON PV + BESS RESULTS")
    print("=" * 55)
    print(f"Mode: {result['what_if_tab']['inputs']['mode']}")
    print(f"Campus baseline: {summary['baseline_daily_energy_kwh']:,.1f} kWh/day")
    print(
        f"Estimated peak: {summary['baseline_peak_kw']:,.1f} kW "
        f"at hour {summary['estimated_peak_hour']:02d}:00"
    )
    print(f"PECO reference rate: ${summary['peco_reference_rate_$_per_kwh']:.5f}/kWh")
    print(
        "Calibrated baseline rate: "
        f"${summary['calibrated_baseline_effective_rate_$_per_kwh']:.5f}/kWh"
    )
    print(f"Solar panels selected: {summary['panels_selected']}")
    print(f"BESS units selected: {summary['bess_units_selected']}")
    print(f"Project cost: ${summary['project_cost_$']:,.2f}")
    print(f"Annual baseline operating cost: ${summary['annual_baseline_operating_cost_$']:,.2f}")
    print(f"Annual AVATAR operating cost: ${summary['annual_avatar_operating_cost_$']:,.2f}")
    print(f"Annual operating savings: ${summary['annual_total_operating_savings_$']:,.2f}")
    print(f"Annual grid reduction: {summary['annual_grid_energy_reduction_kwh']:,.1f} kWh")
    print(f"Renewable share of load: {summary['renewable_share_of_load_percent']:.1f}%")


def main() -> None:
    result = run_avatar(USER_INPUTS, DEFAULT_PVWATTS_PATH)
    print_summary(result)
    outputs = save_outputs(result, DEFAULT_OUTPUT_DIRECTORY)
    print(f"\nOutputs written to: {outputs['output_directory']}")


if __name__ == "__main__":
    main()
