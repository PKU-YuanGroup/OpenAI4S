# ChemGymRL source and provider environment

## Source and license

Upstream: https://github.com/chemgymrl/chemgymrl

Fixed commit: `ab8227b6b33f13617b7e551bdf6b894df7eec68d`.
The installed distribution is `chemistrygym==2.0.0` (`setup.py:20,37`);
upstream documentation reports 1.5.8 (`docs/conf.py:35–38`). The repository
LICENSE is GPL-3.0; source headers explicitly permit version 3 **or later**
(`setup.py:4–7`). OpenAI4S distributes its own adapter and metadata only, **no
upstream source**. Upstream is downloaded into a separate environment only
when the user explicitly installs it. The simulation process boundary does
not assert any change to the upstream license.

## Installation

From the OpenAI4S source root, with all temporary files outside the repository:

```sh
export CG_DATA="$PWD/../_data/W1-B"
mkdir -p "$CG_DATA"
export TMPDIR="$CG_DATA" PIP_CACHE_DIR="$CG_DATA/pip-cache"
export MPLBACKEND=Agg MPLCONFIGDIR="$CG_DATA/mpl" NUMBA_CACHE_DIR="$CG_DATA/numba"
python3.10 -m venv "$CG_DATA/provider-env"
"$CG_DATA/provider-env/bin/python" -m pip install --require-hashes --no-deps \
  -r openai4s_lab_provider/chemgymrl/requirements.lock
"$CG_DATA/provider-env/bin/python" -m pip install --use-pep517 --no-build-isolation --no-deps \
  'git+https://github.com/chemgymrl/chemgymrl@ab8227b6b33f13617b7e551bdf6b894df7eec68d'
"$CG_DATA/provider-env/bin/python" -c 'import importlib.metadata as m,json; d=m.distribution("chemistrygym"); assert d.version=="2.0.0"; assert json.loads(d.read_text("direct_url.json"))["vcs_info"]["commit_id"]=="ab8227b6b33f13617b7e551bdf6b894df7eec68d"'
```

Installation uses the committed lock as is. Only a maintainer changing a pin
regenerates it, and then commits the result:

```sh
uv pip compile openai4s_lab_provider/chemgymrl/requirements.in \
  --generate-hashes --python-version 3.10 --universal \
  --output-file openai4s_lab_provider/chemgymrl/requirements.lock
```

The universal runtime lock includes distribution hashes for multiple platforms,
not only this machine's wheels. It excludes chemistrygym itself. It also pins
upstream's pandas, Pillow, cmocean and PyYAML dependencies and their transitive
dependencies. The lock also pins upstream's build tooling (setuptools and
wheel), and the upstream legacy setup.py is built with `--no-build-isolation`,
so no unpinned build tooling is downloaded either. On the validation
machine pip 23.0.1's legacy `setup.py install` omitted `direct_url.json`.
`--use-pep517` is therefore required; the adapter refuses a missing or mismatched
commit receipt. An existing legacy installation must be reinstalled with
`--force-reinstall --use-pep517 --no-deps`.

Export each descriptor using the provider's own entrypoint. `--portable` drops
the runtime-specific reproducibility claim: a committed manifest is read on every
machine and says `unverified`; a running provider reports `verified_for_profile`
only on the measured runtime described under Reproducibility.

```sh
for profile in WaterOilExtract-v0 GenWurtzExtract-v2; do
  "$CG_DATA/provider-env/bin/python" -I openai4s_lab_provider/__main__.py \
    --backend chemgymrl --describe "$profile" --portable \
    > "openai4s_lab_provider/chemgymrl/manifests/$profile.json"
done
OPENAI4S_LAB_CHEMGYMRL_PYTHON="$CG_DATA/provider-env/bin/python" \
  uv run pytest -m external tests/test_lab_chemgymrl_external.py -q
```

## Mapping evidence

All line references below refer to the fixed upstream commit.

- Both profiles are registered (`chemistrylab/__init__.py:15–23`). Both expand
  to 41 actions, with max_steps 50 (`benches/general_bench.py:59–99`,
  `benches/extract_bench.py:166–189,210–232`).
- Pour parameters are litres (`vessel.py:419–427`), converted to mL (see the
  float-noise note below). Host normalization chooses an exact advertised
  level; the provider requires exact equality and never picks a nearby action.
- Drain parameters count bottom layer pixels (`vessel.py:429–472`), not mL.
  Before stepping, the adapter predicts solvent and dissolved-solute transfer
  from the already sampled layer state to reject overflow without mutation.
- Upstream `mix` advances a settling clock (`extract_algorithms/separate.py:212–215,283–312`):
  a negative step shakes the vessel, a positive step lets its layers settle.
  The operation carries the sign, so both `mix_model.duration` and
  `settle_model.duration` advertise the positive magnitude (mixing 0.2–1.0,
  settling 0.01–0.16) and the adapter sends `-duration` upstream for
  `mix_model`. `duration` is **model time**, not elapsed physical time.
  Settling acts on three vessels together; there is one capability with all
  three resources, no independently controllable per-vessel substitutes.
- Advertised levels drop upstream float noise: 0.6 L arrives as
  0.6000000000000001 and is advertised as 600.0 mL. Levels are rounded to nine
  decimals in the one function both directions of the mapping use, so every
  advertised level still maps back to exactly one upstream action.
- Resources derive from unique snake_case labels. A stock source has outgoing
  transfers and no incoming transfer. Waste Vessel is a vessel, despite being
  outside WaterOil's observed working shelf (`lab/shelf.py:25–29`).
- Observation vectors are vessel-major (`benches/characterization_bench.py:90–104`).
  Layers have shape [2,100] (WaterOil) or [3,100] (GenWurtz); the channel's
  `axes` name the vessel each row shows (`extraction_vessel`, `beaker_1`, and
  `beaker_2` for GenWurtz) and leave the pixel axis unlabelled; target one-hots
  decode to the declared task target, independent of actual material contents
  (`:177–186`). No material legend, pressure, reward or composition is included.
- Pressure is not modeled here. The adapter never calls `render()` (whose
  legends expose materials, `util/Visualization.py:215–220`) or `Lab()`.
- There is no upstream global model clock. Provider `sim_time` is an accounting
  convention: sum each applied action's `dt` plus its positive mix parameter,
  once per multi-vessel action. Negative mixing does not reverse time.
  This is not wall-clock time and adds no physical-model assumption.
- Full states and rewards exist only under `evaluation`. Initial evaluation
  reads the actual baseline `env.unwrapped.initial_reward` computed by upstream
  `_reset` (`benches/general_bench.py:230–231`); subsequent rewards are the
  raw values returned by step (including its terminal baseline subtraction). Third-party stdout/stderr are discarded
  at fd level. Unexpected exceptions expose type and generic summary plus
  stack locations, never exception values, locals or source lines.

## Reproducibility

Measured on **2026-10-05**, macOS 27.0.1 arm64, CPython **3.10.21**, with this
runtime lock and the fixed source commit. These results are **not evidence for
other platforms, profiles, dependency versions, seeds or arbitrary action sequences**.

Each profile was run twice with seed **42** and the same provider-normalized
sequence corresponding to internal actions
`[30,9,35,0,3,35,8,35,0,4,35,0,40]`. All 13 commands were applied, ending by the
explicit end action. We compared all 14 observations (including initial state),
final ground truth and final reward, without writing those values to logs.

| Profile | Numba seeded | All observations equal | First differing observation | Final truth equal | Final reward equal |
| --- | --- | --- | --- | --- | --- |
| WaterOilExtract-v0 | No | No | 0 (initial) | No | No |
| WaterOilExtract-v0 | Yes | Yes | None | Yes | Yes |
| GenWurtzExtract-v2 | No | No | 0 (initial) | Yes | Yes |
| GenWurtzExtract-v2 | Yes | Yes | None | Yes | Yes |

Both profiles are `verified_for_profile` within this measured scope. Runtime
platform, Python and every pinned distribution version are checked against the
measured SHA-256 fingerprint
`1183b4fe19b2ae3a83a3181b03a455ac399b42e008965bc428d67ab2e30831b2`.
Any different runtime reports `unverified`, even when the upstream commit
matches. The fingerprint is SHA-256 of canonical JSON with keys `platform`
(`platform.platform()`), `python` (`platform.python_version()`) and `packages`
(distribution name to installed version for every requirements.in entry).
Describe after open returns the cached descriptor for the active profile;
other profiles are refused so inspection cannot consume the session RNGs. The
controlled change was adding `np.random.seed(seed)` inside a `numba.njit`
function. Python `random.seed` and NumPy global seeding were retained in both
conditions; all seeding occurred after `gym.make` and before explicit
`env.reset(seed=42)`. The constructor itself calls reset with seed=None, so
seeding only before creation is insufficient. The unseeded Numba generator
changes layer pixel sampling (`extract_algorithms/separate.py:126`); the
experiment establishes that seeding it resolves the observed difference for
these runs, not that all future randomness has been exhaustively excluded.

The external regression repeats the seeded comparison in separate real
provider processes and requires exact descriptor equality with the committed
manifests. To reproduce the unseeded control, keep `_seed`'s Python/NumPy calls
but omit only `seed_compiled(seed)` in a disposable copy, then repeat the same
sequence twice. Restore that call for the seeded condition. The W1-B handoff
records the actual script, commands, durations and comparison-only JSON report.
