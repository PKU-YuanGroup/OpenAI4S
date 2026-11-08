"""OpenMHS driver of the simulated AS-96 enzyme-inhibition assay workstation.

Served with ``python3 -m mhs serve <this directory> --backend sim``.  The
endpoint, not the agent, enforces every limit below (each cites MANUAL.md);
the backend is the analytic model in sim.py.
"""

from mhs.driver import Driver, DriverFailed, Fact, Field, Limit, action, estop, read

ROWS = list("ABCDEFGH")
_SRC = {"source": "vendor_doc"}


def _lim(ref: str, **kw):
    return Limit(source="vendor_doc", ref=f"MANUAL.md:{ref}", **kw)


def _fail(exc: Exception):
    return DriverFailed("MHS.DEVICE.PRECONDITION_UNMET", str(exc))


class AssayStation(Driver):
    device_id = "assay-station"
    device_kind = "lab.liquid_handler"
    display_name = "AS-96 enzyme-inhibition assay workstation (simulated)"
    mode = "simulated"
    risk = "R1"
    native = "openai4s_lab.assay:AssayWorkcell"
    backends = {"sim": ".sim:AssayWorkcell", "fake": ".sim:AssayWorkcell"}
    vendor, model = "OpenAI4S Lab (simulated)", "AS-96"
    tags = ["assay", "enzyme", "ic50", "plate-reader", "liquid-handler", "simulated"]
    facts = [
        Fact("Dose series of 3 to 10 points per row; column 11 vehicle, column 12 blank", source="vendor_doc",
             ref="MANUAL.md:8"),
        Fact("Kinetic absorbance read at 405 or 410 nm", source="vendor_doc", ref="MANUAL.md:13"),
        Fact("The emergency stop halts dispensing and reading", source="vendor_doc", ref="MANUAL.md:18"),
    ]
    safe_state = "liquid handler idle, reader idle, nothing dispensed after the latch"
    fidelity = {
        "engine": "openai4s-lab:competitive-inhibition-michaelis-menten",
        "fidelity_level": "F1",
        "determinism": {
            "kind": "seeded",
            "seed_control": "none",
            "notes": "Readings use a fixed seed per plate, row and read, so a repeated protocol repeats its numbers.",
        },
        "timestep_ms": 1.0,
        "realtime_factor": 0,
        "reset": "full",
        "snapshot_restore": False,
        "gaps": [
            "analytic competitive-inhibition model with Cheng-Prusoff behaviour; no enzyme instability, "
            "substrate depletion, inner-filter effect or compound fluorescence",
            "noise is Gaussian (multiplicative CV about 2.5 %, additive 0.35 mOD/min); no edge or drift effects",
            "no physical AS-96 exists; nothing was validated against hardware",
        ],
    }

    # -- reads ----------------------------------------------------------------
    @read("plate", type="object", description="Plate id and the state of each row (empty, dosed, reacting, read)")
    def plate(self):
        return self.native.plate_summary()

    @read("inventory", type="object", description="Compound stocks: concentration (mM), volume (uL), purity, source")
    def inventory(self):
        return self.native.inventory_summary()

    @read("tips_remaining", type="integer", description="Disposable tips left in the racks")
    def tips_remaining(self):
        return int(self.native.tips)

    @read("temperature", unit="Cel", description="Incubation temperature")
    def temperature(self):
        return float(self.native.temperature_c)

    @read("halted", type="boolean", description="True after an emergency stop until the re-enable runs")
    def halted(self):
        return bool(self.native.halted)

    # -- actions --------------------------------------------------------------
    @action(name="x-cap.openai4s-lab.prepare_dose_series.v1",
            description="Pipette a serial dilution of one compound across columns 1..points of a row, "
                        "plus the vehicle (col 11) and blank (col 12) controls",
            input={
                "row": Field("string", enum=ROWS, description="Plate row A-H"),
                "compound_id": Field("string", description="Compound id from the inventory read"),
                "top_concentration_uM": Field("number", unit="umol/L", description="Concentration in column 1",
                                              limit=_lim("9", min=0.001, max=200)),
                "dilution_factor": Field("number", description="Fold dilution between neighbouring columns",
                                         limit=_lim("10", min=1.5, max=10)),
                "points": Field("integer", description="Number of dose points (columns 1..points)",
                                limit=_lim("8", min=3, max=10)),
            },
            output={
                "plate_id": Field("string", description="Plate id"),
                "row": Field("string", description="Row"),
                "compound_id": Field("string", description="Compound"),
                "wells": Field("array", items="string", description="Dosed wells"),
                "concentrations_uM": Field("array", items="number", unit="umol/L",
                                           description="Concentration per dosed well"),
                "vehicle_well": Field("string", description="Vehicle control well"),
                "blank_well": Field("string", description="No-enzyme blank well"),
                "stock_used_ul": Field("number", unit="uL", description="Stock volume consumed"),
                "tips_remaining": Field("integer", description="Tips left"),
            },
            max_duration_ms=10000)
    def prepare_dose_series(self, row, compound_id, top_concentration_uM, dilution_factor, points):
        try:
            return self.native.prepare_dose_series(row, compound_id, top_concentration_uM, dilution_factor, points)
        except (ValueError, LookupError, KeyError, RuntimeError) as exc:
            raise _fail(exc) from exc

    @action(name="x-cap.openai4s-lab.start_reaction.v1",
            description="Add enzyme and substrate to dosed rows and start the reaction at a temperature",
            input={
                "rows": Field("array", items=Field("string", enum=ROWS), min_items=1, max_items=8,
                              description="Dosed rows to start"),
                "substrate_uM": Field("number", unit="umol/L", description="Substrate concentration",
                                      limit=_lim("11", min=5, max=500)),
                "temperature_C": Field("number", unit="Cel", description="Incubation temperature",
                                       limit=_lim("12", min=20, max=40)),
            },
            output={
                "plate_id": Field("string", description="Plate id"),
                "rows": Field("array", items="string", description="Started rows"),
                "substrate_uM": Field("number", unit="umol/L", description="Substrate concentration"),
                "temperature_C": Field("number", unit="Cel", description="Incubation temperature"),
                "tips_remaining": Field("integer", description="Tips left"),
            },
            max_duration_ms=10000)
    def start_reaction(self, rows, substrate_uM, temperature_C):
        try:
            return self.native.start_reaction(list(rows), substrate_uM, temperature_C)
        except (ValueError, LookupError, KeyError, RuntimeError) as exc:
            raise _fail(exc) from exc

    @action(name="x-cap.openai4s-lab.read_kinetics.v1",
            description="Kinetic absorbance read of started rows; returns the initial rate of every well "
                        "(mOD/min) with the vehicle and blank controls",
            input={
                "rows": Field("array", items=Field("string", enum=ROWS), min_items=1, max_items=8,
                              description="Rows to read"),
                "wavelength_nm": Field("integer", unit="nm", description="Read wavelength",
                                       limit=_lim("13", allowed=[405, 410])),
                "duration_s": Field("integer", unit="s", description="Read duration",
                                    limit=_lim("13", min=60, max=1800)),
                "interval_s": Field("integer", unit="s", description="Interval between kinetic points",
                                    limit=_lim("13", min=10, max=120)),
            },
            output={
                "plate_id": Field("string", description="Plate id"),
                "read_id": Field("string", description="Read id"),
                "wavelength_nm": Field("integer", unit="nm", description="Wavelength"),
                "temperature_C": Field("number", unit="Cel", description="Temperature"),
                "kinetic_points": Field("integer", description="Kinetic points per well"),
                "rows": Field("object", description="Per row: compound_id, concentrations_uM, rate_mOD_per_min, "
                                                    "vehicle_rate_mOD_per_min, blank_rate_mOD_per_min"),
            },
            max_duration_ms=15000)
    def read_kinetics(self, rows, wavelength_nm, duration_s, interval_s):
        try:
            return self.native.read_kinetics(list(rows), wavelength_nm, duration_s, interval_s)
        except (ValueError, LookupError, KeyError, RuntimeError) as exc:
            raise _fail(exc) from exc

    @action(name="x-cap.openai4s-lab.new_plate.v1", description="Discard the plate and load a fresh one",
            output={"plate_id": Field("string", description="New plate id")})
    def new_plate(self):
        try:
            return self.native.new_plate()
        except RuntimeError as exc:
            raise _fail(exc) from exc

    @action(name="x-cap.openai4s-lab.load_sample.v1",
            description="Scan a LIMS vial on the assay deck and add its compound to the inventory",
            input={"vial_id": Field("string", description="LIMS vial id, e.g. V-001")},
            output={
                "vial_id": Field("string", description="Vial"),
                "compound_id": Field("string", description="Compound"),
                "stock_mM": Field("number", unit="mmol/L", description="Stock concentration"),
                "volume_ul": Field("number", unit="uL", description="Volume"),
                "purity": Field("number", description="HPLC purity fraction"),
                "source": Field("string", description="Origin"),
            })
    def load_sample(self, vial_id):
        try:
            return self.native.load_sample(vial_id)
        except (ValueError, LookupError, KeyError, RuntimeError) as exc:
            raise _fail(exc) from exc

    @action(name="x-cap.openai4s-lab.resume.v1", reenable=True,
            description="Re-enable the workstation after an operator reset of the emergency stop",
            output={"halted": Field("boolean", description="False once re-enabled")})
    def resume(self):
        self.native.resume()
        return {"halted": False}

    @estop
    def stop(self):
        self.native.halt()
