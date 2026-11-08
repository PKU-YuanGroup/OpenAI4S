"""Analytic simulator of the AS-96 enzyme-inhibition assay workstation.

A 96-well plate, an 8-channel liquid handler and a kinetic absorbance reader
in one enclosure (see MANUAL.md).  The chemistry is a competitive-inhibition
Michaelis-Menten model:

    v0 = Vmax(T) * S / (Km * (1 + (I_eff / Ki) ** h) + S)

so the measured IC50 obeys the Cheng-Prusoff relation
``IC50 = Ki * (1 + S / Km)``.  Readings carry seeded multiplicative and
additive noise, so a repeated plate gives the same numbers and a fitted IC50
is a genuine estimate rather than a lookup.  The true Ki values are hidden
from the agent: they are not in any read, output or manifest.
"""

from __future__ import annotations

import math
import random
import time
from typing import Any

ROWS = "ABCDEFGH"
DOSE_COLUMNS = 10  # columns 1-10: dose series
VEHICLE_COLUMN = 11  # column 11: enzyme + substrate + vehicle (100 % activity)
BLANK_COLUMN = 12  # column 12: substrate, no enzyme (0 % activity)
WELL_VOLUME_UL = 100
TIP_RACK = 96 * 4
KM_UM = 50.0  # substrate Km of the simulated enzyme
VMAX_30C = 48.0  # mOD/min at 30 Cel
Q10 = 2.0
STOCK_MM = 10.0

# Hidden ground truth: (Ki in uM, Hill slope).  Never exposed by any read.
_LIBRARY: dict[str, tuple[float, float]] = {
    "CPD-101": (0.045, 1.0),
    "CPD-102": (0.62, 1.0),
    "CPD-103": (7.5, 0.9),
    "CPD-104": (5000.0, 1.0),  # inactive in range
    "REF-STS": (0.012, 1.1),  # reference inhibitor
}
# Products of the flow reactor (loaded through the LIMS by vial).
_PRODUCTS: dict[str, tuple[float, float]] = {
    "FR-1": (0.85, 1.0),
    "FR-2": (0.11, 1.0),
    "FR-3": (2.4, 0.95),
}


#: Named poses of the lab bench (mm), shared with the lab card.
REACTOR_OUTLET = (420.0, -180.0, 120.0)
ASSAY_DECK = (380.0, 220.0, 95.0)
_POSE_TOL_MM = 5.0


def _iso_epoch(text: str) -> float:
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _at(position: dict, pose: tuple[float, float, float]) -> bool:
    try:
        x, y, z = float(position["x"]), float(position["y"]), float(position["z"])
    except (KeyError, TypeError, ValueError):
        return False
    return (
        abs(x - pose[0]) <= _POSE_TOL_MM
        and abs(y - pose[1]) <= _POSE_TOL_MM
        and z <= pose[2] + _POSE_TOL_MM
    )


def _arm_delivered(
    since_epoch: float, used: set[str] | None = None
) -> dict[str, Any] | None:
    """The xArm receipts that carried a vial from the reactor outlet to this deck, if any.

    ``used`` holds the place receipts other vials already claimed: one delivery
    moves one vial.
    """
    used = used or set()
    import json

    from openai4s.mhs_lab import lab_dir

    path = lab_dir() / "state" / "xarm" / "evidence.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    picked: dict[str, Any] | None = None
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if (
            rec.get("capability_id") != "cap.motion.cartesian_pose.v1"
            or rec.get("status") != "completed"
        ):
            continue
        if _iso_epoch(rec.get("ended_at", "")) < since_epoch:
            continue
        position = (rec.get("output") or {}).get("position") or {}
        if picked is None and _at(position, REACTOR_OUTLET):
            picked = rec
        elif picked is not None and _at(position, ASSAY_DECK):
            if rec.get("receipt_id") in used:
                picked = None
                continue
            return {
                "pick_receipt": picked.get("receipt_id"),
                "place_receipt": rec.get("receipt_id"),
                "endpoint": "xarm (stdio)",
            }
    return None


class AssayWorkcell:
    """Backend object of the assay workstation (served by driver.AssayStation)."""

    def __init__(self, seed: int = 20261107, **_options: Any) -> None:
        self.seed = int(seed)
        self.temperature_c = 30.0
        self.lid_closed = True
        self.tips = TIP_RACK
        self.reads = 0
        self.plate_serial = 1
        self.halted = False
        self.inventory: dict[str, dict[str, Any]] = {
            cid: {
                "stock_mM": STOCK_MM,
                "volume_ul": 50.0,
                "purity": 1.0,
                "source": "library",
            }
            for cid in _LIBRARY
        }
        self._new_plate()

    # -- plate ------------------------------------------------------------
    def _new_plate(self) -> None:
        self.plate: dict[str, dict[str, Any]] = {
            row: {
                "state": "empty",
                "compound": None,
                "conc_uM": [],
                "substrate_uM": None,
            }
            for row in ROWS
        }

    def new_plate(self) -> dict[str, Any]:
        self._check_running()
        self.plate_serial += 1
        self._new_plate()
        return {"plate_id": self.plate_id()}

    def plate_id(self) -> str:
        return f"PL-{self.plate_serial:04d}"

    def _check_running(self) -> None:
        if self.halted:
            raise RuntimeError("workstation halted by emergency stop")

    # -- liquid handling ----------------------------------------------------
    def prepare_dose_series(
        self,
        row: str,
        compound_id: str,
        top_concentration_um: float,
        dilution_factor: float,
        points: int,
    ) -> dict[str, Any]:
        self._check_running()
        well_row = self.plate.get(row)
        if well_row is None:
            raise KeyError(f"row {row!r} does not exist")
        if well_row["state"] != "empty":
            raise ValueError(
                f"row {row} already holds {well_row['compound']}; load a new plate"
            )
        stock = self.inventory.get(compound_id)
        if stock is None:
            raise LookupError(f"compound {compound_id!r} is not in the inventory")
        # Top well: stock diluted into 100 uL; serial dilution reuses the top well.
        need_ul = (
            top_concentration_um * WELL_VOLUME_UL / (stock["stock_mM"] * 1000.0) * 1.5
        )
        if stock["volume_ul"] < need_ul:
            raise ValueError(
                f"{compound_id}: {stock['volume_ul']:.1f} uL left, {need_ul:.1f} uL needed"
            )
        tips_needed = points + 2
        if self.tips < tips_needed:
            raise ValueError(f"{self.tips} tips left, {tips_needed} needed")
        stock["volume_ul"] = round(stock["volume_ul"] - need_ul, 3)
        self.tips -= tips_needed
        conc = [
            round(top_concentration_um / dilution_factor**i, 6) for i in range(points)
        ]
        well_row.update(state="dosed", compound=compound_id, conc_uM=conc)
        time.sleep(0.15)
        return {
            "plate_id": self.plate_id(),
            "row": row,
            "compound_id": compound_id,
            "wells": [f"{row}{c}" for c in range(1, points + 1)],
            "concentrations_uM": conc,
            "vehicle_well": f"{row}{VEHICLE_COLUMN}",
            "blank_well": f"{row}{BLANK_COLUMN}",
            "stock_used_ul": round(need_ul, 3),
            "tips_remaining": self.tips,
        }

    def start_reaction(
        self, rows: list[str], substrate_um: float, temperature_c: float
    ) -> dict[str, Any]:
        self._check_running()
        for row in rows:
            if self.plate.get(row, {}).get("state") != "dosed":
                raise ValueError(
                    f"row {row} is not dosed (state {self.plate.get(row, {}).get('state')})"
                )
        for row in rows:
            self.plate[row].update(state="reacting", substrate_uM=float(substrate_um))
        self.temperature_c = float(temperature_c)
        self.tips -= 2 * len(rows)
        time.sleep(0.15)
        return {
            "plate_id": self.plate_id(),
            "rows": list(rows),
            "substrate_uM": float(substrate_um),
            "temperature_C": self.temperature_c,
            "tips_remaining": self.tips,
        }

    # -- reader -------------------------------------------------------------
    def _truth(self, compound_id: str) -> tuple[float, float]:
        if compound_id in _LIBRARY:
            return _LIBRARY[compound_id]
        base = compound_id.split("@", 1)[0]
        return _PRODUCTS.get(base, (1.0e4, 1.0))

    def read_kinetics(
        self, rows: list[str], wavelength_nm: int, duration_s: int, interval_s: int
    ) -> dict[str, Any]:
        self._check_running()
        if not self.lid_closed:
            raise ValueError("reader lid open")
        out: dict[str, Any] = {}
        for row in rows:
            state = self.plate.get(row, {})
            if state.get("state") not in ("reacting", "read"):
                raise ValueError(
                    f"row {row} has no running reaction (state {state.get('state')})"
                )
        self.reads += 1
        vmax = VMAX_30C * Q10 ** ((self.temperature_c - 30.0) / 10.0)
        # Short reads are noisier: fewer points on the linear initial slope.
        n_points = max(3, int(duration_s // interval_s))
        cv = 0.025 * math.sqrt(10.0 / n_points)
        for row in rows:
            state = self.plate[row]
            compound = state["compound"]
            ki, hill = self._truth(compound)
            purity = float(self.inventory.get(compound, {}).get("purity", 1.0))
            s = float(state["substrate_uM"])
            rng = random.Random(f"{self.seed}:{self.plate_id()}:{row}:{self.reads}")
            rates = []
            for conc in state["conc_uM"]:
                i_eff = conc * purity
                v = vmax * s / (KM_UM * (1.0 + (i_eff / ki) ** hill) + s)
                rates.append(
                    round(v * (1.0 + rng.gauss(0.0, cv)) + rng.gauss(0.0, 0.35), 3)
                )
            vehicle = vmax * s / (KM_UM + s)
            vehicle_obs = round(
                vehicle * (1.0 + rng.gauss(0.0, cv)) + rng.gauss(0.0, 0.35), 3
            )
            blank_obs = round(0.4 + rng.gauss(0.0, 0.25), 3)
            out[row] = {
                "compound_id": compound,
                "concentrations_uM": list(state["conc_uM"]),
                "rate_mOD_per_min": rates,
                "vehicle_rate_mOD_per_min": vehicle_obs,
                "blank_rate_mOD_per_min": blank_obs,
            }
            state["state"] = "read"
        time.sleep(0.25)
        return {
            "plate_id": self.plate_id(),
            "read_id": f"RD-{self.reads:04d}",
            "wavelength_nm": int(wavelength_nm),
            "temperature_C": self.temperature_c,
            "kinetic_points": n_points,
            "rows": out,
        }

    # -- samples from the LIMS ---------------------------------------------
    def load_sample(self, vial_id: str) -> dict[str, Any]:
        self._check_running()
        from openai4s.mhs_lab import lims

        vial = lims.get_vial(vial_id)
        if vial is None:
            raise LookupError(f"vial {vial_id!r} is not registered in the LIMS")
        if vial.get("location") == "reactor_outlet":
            # Deck presence sensor: the vial is on the deck only if the robot arm's own
            # evidence shows it descended to the reactor outlet and then to this deck
            # after the vial was filled.
            used = {
                str((v.get("delivered_by") or {}).get("place_receipt"))
                for v in lims.list_vials()
                if v.get("delivered_by")
            }
            delivered = _arm_delivered(float(vial.get("registered_at", 0.0)), used)
            if delivered is None:
                raise ValueError(
                    f"vial {vial_id} is still at the reactor outlet: no robot-arm delivery to the assay deck "
                    "recorded after it was filled (descend to the outlet, then to the deck)"
                )
            lims.update_vial(vial_id, location="assay_deck", delivered_by=delivered)
            vial = lims.get_vial(vial_id) or vial
        if vial.get("location") not in ("assay_deck",):
            raise ValueError(
                f"vial {vial_id} is at {vial.get('location')!r}, not on the assay deck"
            )
        compound = str(vial["compound_id"])
        self.inventory[compound] = {
            "stock_mM": round(float(vial["concentration_mM"]), 4),
            "volume_ul": round(float(vial["volume_mL"]) * 1000.0, 1),
            "purity": round(float(vial.get("purity", 1.0)), 4),
            "source": f"vial {vial_id}",
        }
        lims.update_vial(vial_id, location="assay_station", loaded=True)
        time.sleep(0.1)
        return {"vial_id": vial_id, "compound_id": compound, **self.inventory[compound]}

    # -- state ------------------------------------------------------------
    def plate_summary(self) -> dict[str, Any]:
        return {
            "plate_id": self.plate_id(),
            "rows": {
                row: {
                    "state": v["state"],
                    "compound": v["compound"],
                    "points": len(v["conc_uM"]),
                }
                for row, v in self.plate.items()
            },
        }

    def inventory_summary(self) -> dict[str, Any]:
        return {
            cid: {k: v for k, v in item.items()} for cid, item in self.inventory.items()
        }

    def halt(self) -> None:
        self.halted = True

    def resume(self) -> None:
        self.halted = False
