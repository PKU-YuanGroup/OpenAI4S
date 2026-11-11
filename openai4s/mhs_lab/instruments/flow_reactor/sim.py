"""Kinetic simulator of the FR-10 continuous-flow reactor with inline HPLC.

A 10 mL heated coil fed by two pumps (substrate A, amine B) and a back-pressure
regulator; an inline HPLC samples the outlet at steady state (see MANUAL.md).
The chemistry is an SNAr-type amination with three competing pathways::

    A + B -> P     (desired product,   k1, Ea1)
    P     -> D     (thermal degradation, k2, high Ea: punishes heat)
    A     -> S     (hydrolysis side product, k3, low Ea: punishes slow runs)
    P + B -> X     (over-addition,      k4)

integrated with RK4 over the residence time (adaptive sub-steps keep it stable
at high temperature).  Yield therefore shows the classic time-temperature
ridge: hot-and-short and cool-and-long both work, the corners do not.  HPLC
numbers carry seeded noise (about 0.6 % absolute), so replicates differ a
little, as on a real instrument.  The kinetic constants are hidden from the
agent: no read, output or manifest exposes them.
"""

from __future__ import annotations

import math
import random
import time
from typing import Any

R_GAS = 8.314
REACTOR_ML = 10.0
BPR_BAR = 8.0

# Amine reagent B -> (product id, rate multiplier on A1, display name)
REAGENTS: dict[str, tuple[str, float, str]] = {
    "B1": ("FR-1", 1.0, "aniline"),
    "B2": ("FR-2", 0.55, "4-fluoroaniline"),
    "B3": ("FR-3", 1.9, "4-methoxyaniline"),
}
# Hidden kinetic constants (A in L/mol/s or 1/s, Ea in J/mol).
_K = {
    "A1": 3.0e7,
    "E1": 62000.0,
    "A2": 1.0e10,
    "E2": 95000.0,
    "A3": 1.0e3,
    "E3": 40000.0,
    "A4": 2.0e7,
    "E4": 70000.0,
}


def _rates(temp_c: float, mult: float) -> tuple[float, float, float, float]:
    tk = temp_c + 273.15
    k1 = mult * _K["A1"] * math.exp(-_K["E1"] / (R_GAS * tk))
    k2 = _K["A2"] * math.exp(-_K["E2"] / (R_GAS * tk))
    k3 = _K["A3"] * math.exp(-_K["E3"] / (R_GAS * tk))
    k4 = _K["A4"] * math.exp(-_K["E4"] / (R_GAS * tk))
    return k1, k2, k3, k4


def integrate(
    temp_c: float, tau_min: float, equiv: float, conc: float, mult: float = 1.0
) -> dict[str, float]:
    """Outlet composition (mol/L) after ``tau_min`` minutes in the coil."""
    k1, k2, k3, k4 = _rates(temp_c, mult)
    a, b, p, d, s, x = conc, conc * equiv, 0.0, 0.0, 0.0, 0.0
    total = tau_min * 60.0
    stiff = max(k1 * conc * max(equiv, 1.0), k2, k3, k4 * conc * equiv, 1e-9)
    steps = int(min(20000, max(200, math.ceil(total * stiff / 0.1))))
    dt = total / steps

    def f(
        a: float, b: float, p: float
    ) -> tuple[float, float, float, float, float, float]:
        r1, r2, r3, r4 = k1 * a * b, k2 * p, k3 * a, k4 * p * b
        return (-r1 - r3, -r1 - r4, r1 - r2 - r4, r2, r3, r4)

    for _ in range(steps):
        s1 = f(a, b, p)
        s2 = f(a + dt / 2 * s1[0], b + dt / 2 * s1[1], p + dt / 2 * s1[2])
        s3 = f(a + dt / 2 * s2[0], b + dt / 2 * s2[1], p + dt / 2 * s2[2])
        s4 = f(a + dt * s3[0], b + dt * s3[1], p + dt * s3[2])
        inc = [dt / 6 * (s1[i] + 2 * s2[i] + 2 * s3[i] + s4[i]) for i in range(6)]
        a, b, p = max(a + inc[0], 0.0), max(b + inc[1], 0.0), max(p + inc[2], 0.0)
        d, s, x = d + inc[3], s + inc[4], x + inc[5]
    return {"A": a, "B": b, "P": p, "D": d, "S": s, "X": x}


class FlowReactor:
    """Backend object of the flow reactor (served by driver.FlowReactorDriver)."""

    def __init__(self, seed: int = 20261107, **_options: Any) -> None:
        self.seed = int(seed)
        self.temperature_c = 25.0
        self.flow_ml_min = 0.0
        self.pressure_bar = 1.0
        self.reagent_a_ml = 2000.0
        self.reagent_b_ml = {key: 1500.0 for key in REAGENTS}
        self.experiments = 0
        self.collections = 0
        self.halted = False
        self.last: dict[str, Any] | None = None

    def _check(self) -> None:
        if self.halted:
            raise RuntimeError(
                "reactor halted by emergency stop: pumps off, heater off"
            )

    @staticmethod
    def _pressure(temp_c: float, tau_min: float) -> float:
        flow = REACTOR_ML / tau_min
        viscosity = math.exp(-0.015 * (temp_c - 25.0))
        return round(BPR_BAR + 0.45 * flow * viscosity, 2)

    def _run(
        self,
        temp_c: float,
        tau_min: float,
        equiv: float,
        conc: float,
        reagent: str,
        volume_ml: float,
    ) -> tuple[dict[str, float], dict[str, Any]]:
        self._check()
        if reagent not in REAGENTS:
            raise LookupError(
                f"reagent {reagent!r} is not loaded; loaded: {sorted(REAGENTS)}"
            )
        product, mult, _name = REAGENTS[reagent]
        flow = REACTOR_ML / tau_min
        a_ml = volume_ml / (1.0 + equiv)
        b_ml = volume_ml - a_ml
        if self.reagent_a_ml < a_ml or self.reagent_b_ml[reagent] < b_ml:
            raise ValueError("not enough reagent left for this run")
        self.reagent_a_ml = round(self.reagent_a_ml - a_ml, 2)
        self.reagent_b_ml[reagent] = round(self.reagent_b_ml[reagent] - b_ml, 2)
        self.temperature_c, self.flow_ml_min = float(temp_c), round(flow, 3)
        self.pressure_bar = self._pressure(temp_c, tau_min)
        comp = integrate(temp_c, tau_min, equiv, conc, mult)
        return comp, {"product": product, "flow": flow}

    def _hplc(self, comp: dict[str, float], conc: float, tag: str) -> dict[str, float]:
        rng = random.Random(f"{self.seed}:{tag}")
        noise = lambda sd: rng.gauss(0.0, sd)  # noqa: E731
        y = max(0.0, 100.0 * comp["P"] / conc + noise(0.6))
        conv = min(100.0, max(0.0, 100.0 * (1.0 - comp["A"] / conc) + noise(0.4)))
        side = max(0.0, 100.0 * comp["S"] / conc + noise(0.2))
        deg = max(0.0, 100.0 * comp["D"] / conc + noise(0.2))
        bis = max(0.0, 100.0 * comp["X"] / conc + noise(0.2))
        area = comp["P"] + comp["D"] + comp["S"] + comp["X"] + comp["A"]
        purity = max(
            0.0, min(1.0, comp["P"] / area if area > 0 else 0.0) + noise(0.004)
        )
        return {
            "yield_pct": round(y, 2),
            "conversion_pct": round(conv, 2),
            "selectivity_pct": round(100.0 * y / conv, 2) if conv > 0.5 else 0.0,
            "side_product_S_pct": round(side, 2),
            "degradation_D_pct": round(deg, 2),
            "over_addition_X_pct": round(bis, 2),
            "hplc_purity_pct": round(100.0 * purity, 2),
        }

    def run_condition(
        self,
        temperature_c: float,
        residence_time_min: float,
        equiv_b: float,
        concentration_m: float,
        reagent_b: str,
    ) -> dict[str, Any]:
        volume = 3.0 * REACTOR_ML  # three reactor volumes to reach steady state
        comp, meta = self._run(
            temperature_c,
            residence_time_min,
            equiv_b,
            concentration_m,
            reagent_b,
            volume,
        )
        self.experiments += 1
        hplc = self._hplc(comp, concentration_m, f"run:{self.experiments}")
        sty = (
            hplc["yield_pct"] / 100.0 * concentration_m * 60.0 / residence_time_min
        )  # mol/L/h
        result = {
            "experiment_id": f"EXP-{self.experiments:03d}",
            "reagent_b": reagent_b,
            "product_id": meta["product"],
            "temperature_C": float(temperature_c),
            "residence_time_min": float(residence_time_min),
            "equiv_b": float(equiv_b),
            "concentration_M": float(concentration_m),
            **hplc,
            "space_time_yield_mol_L_h": round(sty, 3),
            "pressure_bar": self.pressure_bar,
            "flow_mL_min": self.flow_ml_min,
            "material_used_mL": volume,
        }
        self.last = result
        time.sleep(0.2)
        return result

    def collect_product(
        self,
        temperature_c: float,
        residence_time_min: float,
        equiv_b: float,
        concentration_m: float,
        reagent_b: str,
        volume_ml: float,
    ) -> dict[str, Any]:
        comp, meta = self._run(
            temperature_c,
            residence_time_min,
            equiv_b,
            concentration_m,
            reagent_b,
            volume_ml + 2.0 * REACTOR_ML,
        )
        self.collections += 1
        hplc = self._hplc(comp, concentration_m, f"collect:{self.collections}")
        conc_mm = (
            concentration_m * hplc["yield_pct"] / 100.0 * 1000.0
        )  # product in the outlet stream
        from openai4s.mhs_lab import lims

        vial = lims.register_vial(
            {
                "compound_id": meta["product"],
                "reagent_b": reagent_b,
                "concentration_mM": round(conc_mm, 3),
                "volume_mL": float(volume_ml),
                "purity": round(hplc["hplc_purity_pct"] / 100.0, 4),
                "yield_pct": hplc["yield_pct"],
                "conditions": {
                    "temperature_C": temperature_c,
                    "residence_time_min": residence_time_min,
                    "equiv_b": equiv_b,
                    "concentration_M": concentration_m,
                },
                "location": "reactor_outlet",
            }
        )
        time.sleep(0.2)
        return {
            "vial_id": vial["vial_id"],
            "product_id": meta["product"],
            "location": "reactor_outlet",
            "volume_mL": float(volume_ml),
            "concentration_mM": vial["concentration_mM"],
            "yield_pct": hplc["yield_pct"],
            "hplc_purity_pct": hplc["hplc_purity_pct"],
        }

    def state(self) -> dict[str, Any]:
        return {
            "temperature_C": self.temperature_c,
            "flow_mL_min": self.flow_ml_min,
            "pressure_bar": self.pressure_bar,
            "experiments": self.experiments,
            "collections": self.collections,
            "reagent_A_mL": self.reagent_a_ml,
            "reagent_B_mL": dict(self.reagent_b_ml),
        }

    def halt(self) -> None:
        self.halted = True
        self.flow_ml_min = 0.0
        self.temperature_c = 25.0
        self.pressure_bar = 1.0

    def resume(self) -> None:
        self.halted = False
