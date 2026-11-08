"""OpenMHS driver of the simulated FR-10 continuous-flow reactor with inline HPLC.

Served with ``python3 -m mhs serve <this directory> --backend sim``.  Every
limit cites MANUAL.md and is enforced by the endpoint before the simulator
runs; a request outside it is refused with MHS.SAFETY.ENVELOPE_VIOLATION.
"""

from mhs.driver import Driver, DriverFailed, Fact, Field, Limit, action, estop, read

REAGENTS = ["B1", "B2", "B3"]


def _lim(ref: str, **kw):
    return Limit(source="vendor_doc", ref=f"MANUAL.md:{ref}", **kw)


def _fail(exc: Exception):
    return DriverFailed("MHS.DEVICE.PRECONDITION_UNMET", str(exc))


_CONDITIONS = {
    "temperature_C": Field("number", unit="Cel", description="Coil temperature", limit=_lim("8", min=25, max=150)),
    "residence_time_min": Field("number", unit="min", description="Residence time in the 10 mL coil",
                                limit=_lim("9", min=0.5, max=20)),
    "equiv_b": Field("number", description="Amine equivalents (B per A)", limit=_lim("10", min=1.0, max=2.0)),
    "concentration_M": Field("number", unit="mol/L", description="Substrate A concentration in the coil",
                             limit=_lim("11", min=0.1, max=1.0)),
    "reagent_b": Field("string", enum=REAGENTS, description="Amine: B1 aniline, B2 4-fluoroaniline, "
                                                            "B3 4-methoxyaniline"),
}

_HPLC_OUT = {
    "experiment_id": Field("string", description="Experiment id"),
    "reagent_b": Field("string", description="Amine"),
    "product_id": Field("string", description="Product compound id"),
    "temperature_C": Field("number", unit="Cel", description="Coil temperature"),
    "residence_time_min": Field("number", unit="min", description="Residence time"),
    "equiv_b": Field("number", description="Amine equivalents"),
    "concentration_M": Field("number", unit="mol/L", description="Substrate concentration"),
    "yield_pct": Field("number", unit="%", description="HPLC assay yield of the product"),
    "conversion_pct": Field("number", unit="%", description="Conversion of substrate A"),
    "selectivity_pct": Field("number", unit="%", description="Product per converted A"),
    "side_product_S_pct": Field("number", unit="%", description="Hydrolysis side product S"),
    "degradation_D_pct": Field("number", unit="%", description="Thermal degradation product D"),
    "over_addition_X_pct": Field("number", unit="%", description="Over-addition product X"),
    "hplc_purity_pct": Field("number", unit="%", description="HPLC area purity of the product"),
    "space_time_yield_mol_L_h": Field("number", description="Space-time yield (mol/L/h)"),
    "pressure_bar": Field("number", unit="bar", description="System pressure"),
    "flow_mL_min": Field("number", unit="mL/min", description="Total pump flow"),
    "material_used_mL": Field("number", unit="mL", description="Reagent volume consumed"),
}


class FlowReactorDriver(Driver):
    device_id = "flow-reactor"
    device_kind = "process.controller"
    display_name = "FR-10 continuous-flow reactor with inline HPLC (simulated)"
    mode = "simulated"
    risk = "R1"
    native = "openai4s_lab.flow:FlowReactor"
    backends = {"sim": ".sim:FlowReactor", "fake": ".sim:FlowReactor"}
    vendor, model = "OpenAI4S Lab (simulated)", "FR-10"
    tags = ["flow-chemistry", "reactor", "hplc", "optimization", "simulated"]
    facts = [
        Fact("Coil temperature 25 to 150 Cel", source="vendor_doc", ref="MANUAL.md:8"),
        Fact("Residence time 0.5 to 20 min in a 10 mL coil", source="vendor_doc", ref="MANUAL.md:9"),
        Fact("The emergency stop switches the pumps and heater off", source="vendor_doc", ref="MANUAL.md:18"),
    ]
    safe_state = "both pumps stopped, heater off, coil vented to the back-pressure regulator"
    fidelity = {
        "engine": "openai4s-lab:snar-kinetics-rk4",
        "fidelity_level": "F1",
        "determinism": {
            "kind": "seeded",
            "seed_control": "none",
            "notes": "Arrhenius kinetics integrated with RK4; HPLC noise is seeded per experiment number.",
        },
        "timestep_ms": 1.0,
        "realtime_factor": 0,
        "reset": "full",
        "snapshot_restore": False,
        "gaps": [
            "ideal plug flow: no axial dispersion, mixing time, heat of reaction or start-up transient",
            "four-reaction lumped kinetic model with hidden constants; no solvent or concentration-dependent "
            "rate changes, no fouling",
            "HPLC noise is Gaussian (about 0.6 % absolute on yield)",
            "no physical FR-10 exists; nothing was validated against hardware",
        ],
    }

    @read("temperature", unit="Cel", description="Coil temperature")
    def temperature(self):
        return float(self.native.temperature_c)

    @read("pressure", unit="bar", description="System pressure")
    def pressure(self):
        return float(self.native.pressure_bar)

    @read("flow", unit="mL/min", description="Total pump flow")
    def flow(self):
        return float(self.native.flow_ml_min)

    @read("experiments", type="integer", description="Steady-state experiments run since start-up")
    def experiments(self):
        return int(self.native.experiments)

    @read("reagents", type="object", description="Reagent reservoirs left (mL): A and each amine B")
    def reagents(self):
        return {"A": self.native.reagent_a_ml, **self.native.reagent_b_ml}

    @read("halted", type="boolean", description="True after an emergency stop until the re-enable runs")
    def halted(self):
        return bool(self.native.halted)

    @action(name="x-cap.openai4s-lab.run_condition.v1",
            description="Run one steady-state flow experiment and analyse the outlet by inline HPLC "
                        "(yield, conversion, selectivity, impurities)",
            input=dict(_CONDITIONS), output=dict(_HPLC_OUT), max_duration_ms=20000)
    def run_condition(self, temperature_C, residence_time_min, equiv_b, concentration_M, reagent_b):
        try:
            return self.native.run_condition(temperature_C, residence_time_min, equiv_b, concentration_M, reagent_b)
        except (ValueError, LookupError, RuntimeError) as exc:
            raise _fail(exc) from exc

    @action(name="x-cap.openai4s-lab.collect_product.v1",
            description="Run at the given conditions and collect product into a LIMS-labelled vial at the "
                        "reactor outlet",
            input={**_CONDITIONS,
                   "volume_mL": Field("number", unit="mL", description="Volume to collect",
                                      limit=_lim("13", min=1, max=50))},
            output={
                "vial_id": Field("string", description="LIMS vial id"),
                "product_id": Field("string", description="Product compound id"),
                "location": Field("string", description="Where the vial is"),
                "volume_mL": Field("number", unit="mL", description="Collected volume"),
                "concentration_mM": Field("number", unit="mmol/L", description="Product concentration"),
                "yield_pct": Field("number", unit="%", description="HPLC yield of the collected stream"),
                "hplc_purity_pct": Field("number", unit="%", description="HPLC purity"),
            },
            max_duration_ms=20000)
    def collect_product(self, temperature_C, residence_time_min, equiv_b, concentration_M, reagent_b, volume_mL):
        try:
            return self.native.collect_product(temperature_C, residence_time_min, equiv_b, concentration_M,
                                               reagent_b, volume_mL)
        except (ValueError, LookupError, RuntimeError) as exc:
            raise _fail(exc) from exc

    @action(name="x-cap.openai4s-lab.resume.v1", reenable=True,
            description="Re-enable pumps and heater control after an operator reset of the emergency stop",
            output={"halted": Field("boolean", description="False once re-enabled")})
    def resume(self):
        self.native.resume()
        return {"halted": False}

    @estop
    def stop(self):
        self.native.halt()
