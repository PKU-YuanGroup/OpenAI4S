"""Catalyst SAR screening helpers for OpenAI4S.

Fixed pipeline (do not reorder or substitute steps):

1. Build POSCARs from the vendored CONTCAR catalog (exact lookup, else derive).
2. Evaluate metrics with Catalyst-Design-Agent FAIRChem UMA / OC20 only.
3. Analyze structure–activity relationships.
4. Write a lean report: model stamp, structure renders, metric figures, SAR insights.

Agents should only surface ``deliverables`` from ``run_pipeline`` for the
current request — never committed demo shells in this skill directory
(``metal_center_dissolution_*``).
"""

# Provenance: UMA/CalculationTools from https://github.com/ahrehd0506/Catalyst-Design-Agent; POSCAR fixtures from https://github.com/LEDlamar/chem.

from __future__ import annotations

import html
import json
import math
import os
import re
import shlex
import shutil
import tempfile
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

SKILL_ROOT = Path(__file__).resolve().parent
CATALOG_PATH = SKILL_ROOT / "contcar_catalog.json"

CDA_UMA_PROTOCOL = "catalyst-design-agent/uma-s-1p1+oc20"
CDA_UMA_MODEL = "uma-s-1p1"
CDA_UMA_TASK = "oc20"
DEFAULT_ADSORBATES = ["*O", "*OH", "*OOH"]
DEFAULT_REACTION = "ORR"
ORR_EQUILIBRIUM_POTENTIAL = 1.23
ORR_O2_FREE_ENERGY = 4.92

METALS = [
    "Pt",
    "Pd",
    "Ir",
    "Ru",
    "Fe",
    "Co",
    "Mn",
    "Cu",
    "Ni",
    "Cr",
    "V",
    "Ti",
    "Mo",
    "Na",
    "Ta",
    "Ag",
    "Au",
    "Zn",
    "Sn",
    "Bi",
    # Remaining metal centers present in contcar_catalog.json. Listed so the
    # active center is detected (get_vnn_idx keys off METALS) for every vendored
    # slab; dissolution screening additionally requires the energy tables below.
    "Rh",
    "Os",
    "Re",
    "W",
    "Sc",
    "Y",
    "Zr",
    "Nb",
    "Tc",
    "Cd",
    "Hf",
]
COORDATOMS = ["N", "S", "O", "C", "H"]
HETEROATOMS = ["N", "S", "P", "B", "C"]

PERIOD_3D = {"Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn"}
PERIOD_4D = {"Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd"}
PERIOD_5D = {"Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au"}

ATOMIC_REFERENCE_ENERGIES = {"H": -3.477, "N": -8.083, "O": -7.204, "C": -7.282}
METAL_REFERENCE_ENERGIES = {
    "pt": -4.594,
    "pd": -4.607,
    "ir": -8.133,
    "ru": -8.602,
    "fe": -7.197,
    "co": -6.271,
    "mn": -8.433,
    "cu": -3.199,
    "ni": -4.874,
    "cr": -8.911,
    "v": -8.76,
    "ti": -7.377,
    "mo": -9.638,
    "w": -11.785,
    "zr": -8.118,
    "hf": -9.553,
    "na": -1.24,
    "ta": -11.369,
    "ag": -2.443,
    "au": -2.59,
    "zn": -0.66,
    "sn": -3.27,
    "sb": -3.851,
    "bi": -3.587,
}
METAL_REDUCTION_POTENTIAL = {
    "pt": (1.188, 2),
    "pd": (0.951, 2),
    "ir": (1.156, 3),
    "ru": (0.455, 2),
    "fe": (-0.447, 2),
    "co": (-0.28, 2),
    "mn": (-1.185, 2),
    "cu": (0.337, 2),
    "ni": (-0.275, 2),
    "cr": (-0.74, 3),
    "v": (-1.13, 2),
    "ti": (-1.37, 3),
    "mo": (-0.20, 3),
    "w": (-0.119, 4),
    "na": (-2.71, 1),
    "ta": (-0.6, 3),
    "ag": (0.7996, 1),
    "au": (1.52, 3),
    "zn": (-0.7618, 2),
    "sn": (-0.13, 2),
    "bi": (0.308, 3),
}
FREE_ENERGY_CORRECTION = {
    "*O": 0.07 + 0.03 - 0.06,
    "*OH": 0.36 + 0.03 - 0.04,
    "*OOH": 0.44 + 0.05 - 0.09,
}

HOST_ALIASES = {
    "graphene": "graphene",
    "gr": "graphene",
    "small": "small macrocycle",
    "small_macrocycle": "small macrocycle",
    "middle": "middle macrocycle",
    "middle_macrocycle": "middle macrocycle",
    "large": "large macrocycle",
    "large_macrocycle": "large macrocycle",
}
MOTIF_ALIASES = {
    "n4": "pyridineN",
    "pyridine": "pyridineN",
    "pyridinen": "pyridineN",
    "pyrrole": "pyrroleN",
    "pyrrolen": "pyrroleN",
    "4c": "4C",
    "c4": "4C",
    "3c": "3C",
    "c3": "3C",
    "2n2c": "2N2C",
    "n2c2": "2N2C",
}

_SHORTHAND_RE = re.compile(
    r"^([A-Za-z]{1,2})"
    r"-([NSOCH0-9]{1,8})"
    r"(?:-(pyrrole|pyridine|4C|3C|2N2C))?"
    r"(?:@([A-Za-z_]+))?"
    r"(?:-([NSPBC])(\d+))?"
    r"$",
    re.IGNORECASE,
)

_METRIC_ALIASES = {
    "overpotential": "orr",
    "op": "orr",
    "eta": "orr",
    "η": "orr",
    "udiss": "dissolution",
    "u_diss": "dissolution",
    "dissolution_potential": "dissolution",
    "stability": "dissolution",
    "adsorption": "adsorption",
    "ads": "adsorption",
    "binding": "adsorption",
}

_ASK_USER_HF = (
    "STOP — Catalyst-Design-Agent UMA/OC20 screening cannot start.\n"
    "Ask the user in chat for the missing item(s).\n"
    "Do NOT skip this method. Do NOT use tabular/heuristic/another MLIP.\n"
)

_catalog_cache: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Catalog + POSCAR construction
# ---------------------------------------------------------------------------


def load_contcar_catalog(path: str | Path | None = None) -> dict[str, Any]:
    """Load the in-skill CONTCAR catalog.

    Each entry embeds the full POSCAR text under ``poscar``. The public fixture
    is limited to graphene M–N4 (pyridineN) slabs. Runtime never reads an
    external ``chem/`` tree.
    """
    global _catalog_cache
    catalog_path = Path(path) if path else CATALOG_PATH
    if _catalog_cache is not None and path is None:
        return _catalog_cache
    if not catalog_path.is_file():
        raise FileNotFoundError(
            f"Embedded CONTCAR catalog missing: {catalog_path}. "
            "Restore skills/catalyst_sar_screening/contcar_catalog.json."
        )
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    if path is None:
        _catalog_cache = payload
    return payload


def list_catalog_metals(
    *,
    host: str = "graphene",
    motif: str = "pyridineN",
    state: str = "slab",
) -> list[str]:
    """List metals available in the vendored catalog for a host/motif/state."""
    catalog = load_contcar_catalog()
    host_name = _normalize_host(host)
    motif_name = _normalize_motif(motif, catalog)
    metals = {
        e["metal"]
        for e in catalog["entries"]
        if e["host"] == host_name and e["motif"] == motif_name and e["state"] == state
    }
    return sorted(metals)


def parse_structure_description(description: str | dict[str, Any]) -> dict[str, Any]:
    """Normalize a free-form or structured description into a build recipe."""
    if isinstance(description, dict):
        return _normalize_structured(description)

    text = (description or "").strip()
    if not text:
        raise ValueError("structure description is empty")
    if text.startswith("{"):
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("JSON description must be an object")
        return _normalize_structured(payload)

    match = _SHORTHAND_RE.match(text.replace(" ", ""))
    if not match:
        raise ValueError(
            f"unrecognized structure description: {description!r}; "
            "expected shorthand like 'Fe-N4', 'Fe-N4-pyrrole', 'Co-N3C', 'Ir-N4-B1'"
        )
    metal = _canonical_metal(match.group(1))
    coordination = _expand_coordination_token(match.group(2))
    motif = match.group(3)
    host = match.group(4) or "graphene"
    hetero = match.group(5)
    hetero_count = int(match.group(6) or 0)
    return {
        "name": _recipe_name(
            metal, coordination, hetero, hetero_count, motif=motif, host=host
        ),
        "description": text,
        "metal": metal,
        "coordination": coordination,
        "second_shell_dopant": hetero,
        "second_shell_count": hetero_count,
        "host": host,
        "motif": motif,
        "state": "slab",
    }


def build_poscar_from_description(
    description: str | dict[str, Any],
    output_path: str | Path,
) -> dict[str, Any]:
    """Build one POSCAR: exact catalog lookup first, else derive from nearest entry."""
    recipe = parse_structure_description(description)
    catalog = load_contcar_catalog()
    meta = _lookup_catalog_entry(recipe, catalog)
    if meta is None:
        meta = _derive_from_catalog(recipe, catalog)
    if meta is None:
        raise FileNotFoundError(
            f"cannot build POSCAR for {recipe.get('name')}: no catalog hit and "
            "no suitable reference entry to derive from"
        )

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(meta["poscar"], encoding="utf-8")
    result = {**recipe, **meta, "poscar_path": str(out.resolve())}
    result.pop("poscar", None)
    return result


def build_poscars_from_descriptions(
    descriptions: list[str | dict[str, Any]],
    output_dir: str | Path,
) -> list[dict[str, Any]]:
    """Build a POSCAR series under ``output_dir``."""
    out_root = Path(output_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    built: list[dict[str, Any]] = []
    used: dict[str, int] = {}
    for description in descriptions:
        recipe = parse_structure_description(description)
        # Sanitize before using the name as a filename so a structured
        # description cannot escape output_dir via path separators or '..'
        # (mirrors export_structure_collage). recipe["name"] itself is untouched.
        base = re.sub(r"[^\w.\-]+", "_", str(recipe["name"])) or "struct"
        count = used.get(base, 0)
        used[base] = count + 1
        filename = f"{base}.POSCAR" if count == 0 else f"{base}_{count + 1}.POSCAR"
        built.append(build_poscar_from_description(recipe, out_root / filename))
    return built


def _lookup_catalog_entry(
    recipe: dict[str, Any], catalog: dict[str, Any]
) -> dict[str, Any] | None:
    """Exact catalog match (method 1). Returns None on miss."""
    if int(recipe.get("second_shell_count") or 0) > 0:
        return None
    host = _normalize_host(recipe.get("host"))
    motif = _infer_motif(recipe)
    motif = _normalize_motif(motif, catalog)
    coordination = recipe.get("coordination") or ["N", "N", "N", "N"]
    if not _coordination_matches_motif(coordination, motif):
        return None
    metal = _canonical_metal(recipe["metal"])
    try:
        period = _metal_period(metal)
    except KeyError:
        period = None
    for entry in catalog["entries"]:
        if (
            entry["host"] == host
            and entry["motif"] == motif
            and entry["state"] == (recipe.get("state") or "slab")
            and entry["metal"].lower() == metal.lower()
        ):
            return {
                "source": "catalog",
                "host": host,
                "motif": motif,
                "state": entry["state"],
                "period": entry.get("period") or period,
                "reference_metal": metal,
                "catalog_key": entry["key"],
                "poscar": entry["poscar"],
                "modifications": [],
            }
    return None


def _derive_from_catalog(
    recipe: dict[str, Any], catalog: dict[str, Any]
) -> dict[str, Any] | None:
    """Derive POSCAR from nearest catalog entry (method 2)."""
    host = _normalize_host(recipe.get("host"))
    motif = _normalize_motif(_infer_motif(recipe) or "pyridineN", catalog)
    metal = _canonical_metal(recipe["metal"])
    state = recipe.get("state") or "slab"
    coordination = list(recipe.get("coordination") or ["N", "N", "N", "N"])
    hetero = recipe.get("second_shell_dopant")
    hetero_count = int(recipe.get("second_shell_count") or 0)

    pool = [
        e
        for e in catalog["entries"]
        if e["host"] == host and e["motif"] == motif and e["state"] == state
    ]
    if not pool:
        pool = [
            e
            for e in catalog["entries"]
            if e["host"] == host
            and e["state"] == state
            and e["motif"] in {"pyridineN", "pyridine"}
        ]
    if not pool:
        return None

    ref = _pick_reference(pool, metal)
    poscar_text = ref["poscar"]
    modifications: list[dict[str, Any]] = []
    ref_metal = ref["metal"]
    if ref_metal.lower() != metal.lower():
        modifications.append(
            {"modification_type": "substitute_metal", "parameters": [ref_metal, metal]}
        )

    ref_coord = _reference_coordination_for_motif(ref["motif"])
    if Counter(coordination) != Counter(ref_coord):
        modifications.extend(_coordination_modifications(ref_coord, coordination))

    if hetero and hetero_count > 0:
        if hetero not in HETEROATOMS:
            raise ValueError(f"unsupported second-shell element: {hetero}")
        for _ in range(hetero_count):
            modifications.append(
                {
                    "modification_type": "substitute_2nd_shell",
                    "parameters": ["C", hetero],
                }
            )

    if modifications:
        # Prefer ASE edits when available; metal-only falls back to text replace.
        only_metal = all(
            m["modification_type"] == "substitute_metal" for m in modifications
        )
        if only_metal:
            for mod in modifications:
                poscar_text = _substitute_metal_text(
                    poscar_text, mod["parameters"][0], mod["parameters"][1]
                )
        else:
            poscar_text = _apply_modifications_optional(poscar_text, modifications)

    return {
        "source": "catalog_derived",
        "host": host,
        "motif": ref["motif"],
        "state": state,
        "period": (
            None
            if metal not in (PERIOD_3D | PERIOD_4D | PERIOD_5D)
            else _metal_period(metal)
        ),
        "reference_metal": ref_metal,
        "catalog_key": ref["key"],
        "poscar": poscar_text,
        "modifications": modifications,
    }


def _pick_reference(pool: list[dict[str, Any]], metal: str) -> dict[str, Any]:
    by_metal = {e["metal"]: e for e in pool}
    if metal in by_metal:
        return by_metal[metal]
    for pref in ("Fe", "Co", "Ni", "Mn", "Ru", "Ir", "Pt"):
        if pref in by_metal:
            return by_metal[pref]
    try:
        period = _metal_period(metal)
    except KeyError:
        period = None
    if period:
        peers = [e for e in pool if e.get("period") == period]
        if peers:
            return peers[0]
    return pool[0]


def _apply_modifications_optional(
    poscar_text: str, modifications: list[dict[str, Any]]
) -> str:
    if not modifications:
        return poscar_text
    try:
        from ase.io import read, write  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "coordination/second-shell derivation requires ase+pymatgen; "
            "install in conda env catagent"
        ) from exc

    tmp_path = ""
    out_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w+", suffix=".POSCAR", delete=False
        ) as handle:
            handle.write(poscar_text)
            handle.flush()
            tmp_path = handle.name
        atoms = read(tmp_path, format="vasp")
        atoms = _apply_modifications_ase(atoms, modifications)
        with tempfile.NamedTemporaryFile("w+", suffix=".POSCAR", delete=False) as out:
            out_path = out.name
        write(out_path, atoms, format="vasp")
        return Path(out_path).read_text(encoding="utf-8")
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)
        if out_path:
            Path(out_path).unlink(missing_ok=True)


def _apply_modifications_ase(atoms: Any, modifications: list[dict[str, Any]]) -> Any:
    current = atoms.copy()
    for mod in modifications:
        mtype = mod["modification_type"]
        params = list(mod["parameters"])
        if mtype == "substitute_metal":
            new_metal = params[1]
            for atom in current:
                if atom.symbol in METALS:
                    atom.symbol = new_metal
        elif mtype == "substitute_coordination":
            current_element, new_element = params
            _, coord_idx, _, _ = get_vnn_idx(current)
            for idx in coord_idx:
                if current[idx].symbol == current_element:
                    current[idx].symbol = new_element
                    break
        elif mtype == "substitute_2nd_shell":
            current_element, new_element = params
            _, _, snn_idx, _ = get_vnn_idx(current)
            for idx in snn_idx:
                if current[idx].symbol == current_element:
                    current[idx].symbol = new_element
                    break
        else:
            raise ValueError(f"unsupported modification_type: {mtype}")
    return current


def _substitute_metal_text(poscar_text: str, old: str, new: str) -> str:
    pattern = re.compile(rf"\b{re.escape(old)}\b")
    lines = poscar_text.splitlines()
    out = []
    for i, line in enumerate(lines):
        if i == 0 or i >= 5:
            out.append(pattern.sub(new, line))
        else:
            out.append(line)
    text = "\n".join(out)
    return text if text.endswith("\n") else text + "\n"


def _normalize_structured(payload: dict[str, Any]) -> dict[str, Any]:
    metal = _canonical_metal(str(payload.get("metal") or "Fe"))
    coordination = payload.get("coordination") or ["N", "N", "N", "N"]
    if isinstance(coordination, str):
        coordination = _expand_coordination_token(coordination)
    coordination = [str(x) for x in coordination]
    if len(coordination) != 4:
        raise ValueError("coordination must contain exactly 4 atoms")
    for atom in coordination:
        if atom not in COORDATOMS:
            raise ValueError(f"unsupported coordination atom: {atom}")
    hetero = payload.get("second_shell_dopant")
    hetero_count = int(payload.get("second_shell_count") or 0)
    host = payload.get("host") or "graphene"
    motif = payload.get("motif")
    return {
        "name": str(
            payload.get("name")
            or _recipe_name(
                metal, coordination, hetero, hetero_count, motif=motif, host=host
            )
        ),
        "description": str(payload.get("description") or payload.get("name") or metal),
        "metal": metal,
        "coordination": coordination,
        "second_shell_dopant": hetero,
        "second_shell_count": hetero_count,
        "host": host,
        "motif": motif,
        "state": payload.get("state") or "slab",
    }


def _canonical_metal(symbol: str) -> str:
    cleaned = symbol.strip()
    for metal in METALS:
        if metal.lower() == cleaned.lower():
            return metal
    if cleaned:
        return cleaned[0].upper() + cleaned[1:].lower()
    raise ValueError("metal symbol is empty")


def _expand_coordination_token(token: str) -> list[str]:
    token = token.strip().upper()
    if len(token) == 4 and all(ch.isalpha() for ch in token):
        atoms = list(token)
    else:
        atoms = []
        for match in re.finditer(r"([NSOCH])(\d*)", token):
            atoms.extend([match.group(1)] * int(match.group(2) or 1))
    if len(atoms) != 4:
        raise ValueError(f"coordination token must expand to 4 atoms, got {token!r}")
    return atoms


def _recipe_name(
    metal: str,
    coordination: list[str],
    hetero: str | None,
    hetero_count: int,
    motif: str | None = None,
    host: str | None = None,
) -> str:
    counts: dict[str, int] = {}
    for atom in coordination:
        counts[atom] = counts.get(atom, 0) + 1
    coord = "".join(f"{atom}{counts[atom]}" for atom in COORDATOMS if atom in counts)
    name = f"{metal}-{coord}"
    if motif and motif.lower() not in {"pyridine", "pyridinen", "n4"}:
        name += f"-{motif}"
    if host and host.lower() not in {"graphene", "gr"}:
        name += f"@{host}"
    if hetero and hetero_count:
        name += f"-{hetero}{hetero_count}"
    return name


def _normalize_host(host: str | None) -> str:
    if host in {
        "graphene",
        "small macrocycle",
        "middle macrocycle",
        "large macrocycle",
    }:
        return str(host)
    key = (host or "graphene").strip().lower().replace(" ", "_").replace("-", "_")
    if key not in HOST_ALIASES:
        raise KeyError(f"unknown host {host!r}")
    return HOST_ALIASES[key]


def _normalize_motif(motif: str | None, catalog: dict[str, Any]) -> str:
    raw = (motif or "pyridineN").strip()
    key = raw.lower().replace(" ", "").replace("-", "").replace("_", "")
    candidate = MOTIF_ALIASES.get(key, raw)
    available = {e["motif"] for e in catalog["entries"]}
    if candidate in available:
        return candidate
    alt = {
        "pyridineN": "pyridine",
        "pyrroleN": "pyrrole",
        "pyridine": "pyridineN",
        "pyrrole": "pyrroleN",
    }.get(candidate)
    if alt and alt in available:
        return alt
    lowered = {name.lower(): name for name in available}
    if candidate.lower() in lowered:
        return lowered[candidate.lower()]
    return candidate


def _infer_motif(recipe: dict[str, Any]) -> str:
    if recipe.get("motif"):
        key = (
            str(recipe["motif"])
            .lower()
            .replace(" ", "")
            .replace("-", "")
            .replace("_", "")
        )
        return MOTIF_ALIASES.get(key, recipe["motif"])
    counts = Counter(recipe.get("coordination") or ["N", "N", "N", "N"])
    if counts == Counter({"N": 4}):
        return "pyridineN"
    if counts.get("N") == 3 and counts.get("C") == 1 and len(counts) == 2:
        return "3C"
    if counts.get("C") == 4:
        return "4C"
    if counts.get("N") == 2 and counts.get("C") == 2:
        return "2N2C"
    return "pyridineN"


def _coordination_matches_motif(coordination: list[str], motif: str) -> bool:
    expected = Counter(coordination)
    key = motif.lower().replace(" ", "")
    if key in {"pyridinen", "pyridine", "pyrrolen", "pyrrole"}:
        return expected == Counter({"N": 4})
    if key == "3c":
        return expected.get("N") == 3 and expected.get("C") == 1 and len(expected) == 2
    if key == "4c":
        return expected == Counter({"C": 4})
    if key == "2n2c":
        return expected.get("N") == 2 and expected.get("C") == 2 and len(expected) == 2
    return True


def _reference_coordination_for_motif(motif: str) -> list[str]:
    key = motif.lower().replace(" ", "")
    if key == "3c":
        return ["N", "N", "N", "C"]
    if key == "4c":
        return ["C", "C", "C", "C"]
    if key == "2n2c":
        return ["N", "N", "C", "C"]
    return ["N", "N", "N", "N"]


def _coordination_modifications(
    current: list[str], target: list[str]
) -> list[dict[str, Any]]:
    cur = list(current)
    mods: list[dict[str, Any]] = []
    leftover = list((Counter(target) - Counter(cur)).elements())
    excess = list((Counter(cur) - Counter(target)).elements())
    for old, new in zip(excess, leftover):
        mods.append(
            {"modification_type": "substitute_coordination", "parameters": [old, new]}
        )
    return mods


def _metal_period(metal: str) -> str:
    symbol = _canonical_metal(metal)
    if symbol in PERIOD_3D:
        return "3d"
    if symbol in PERIOD_4D:
        return "4d"
    if symbol in PERIOD_5D:
        return "5d"
    raise KeyError(f"metal {metal!r} not in period tables")


# ---------------------------------------------------------------------------
# Structure helpers (Catalyst-Design-Agent utils subset)
# ---------------------------------------------------------------------------


def get_vnn_idx(atoms: Any) -> tuple[int, list[int], list[int], list[int]]:
    from pymatgen.analysis.local_env import VoronoiNN
    from pymatgen.io.ase import AseAtomsAdaptor

    struct = AseAtomsAdaptor.get_structure(atoms)
    vnn = VoronoiNN(allow_pathological=True, tol=0.8, cutoff=10)
    center_idx = [idx for idx, atom in enumerate(atoms) if atom.symbol in METALS][0]
    ligand_idx = [idx for idx, atom in enumerate(atoms) if atom.tag == 2]
    fnn_info = vnn.get_nn_info(struct, center_idx)
    fnn_idx = _sort_index_cw(
        atoms,
        center_idx,
        [
            info["site_index"]
            for info in fnn_info
            if info["site_index"] not in ligand_idx
        ],
    )
    snn_info = []
    for idx in fnn_idx:
        snn_info.extend(vnn.get_nn_info(struct, idx))
    snn_idx = _sort_index_cw(
        atoms,
        center_idx,
        [
            info["site_index"]
            for info in snn_info
            if info["site_index"] not in [center_idx] + ligand_idx + fnn_idx
        ],
    )
    tnn_info = []
    for idx in snn_idx:
        tnn_info.extend(vnn.get_nn_info(struct, idx))
    tnn_idx = _sort_index_cw(
        atoms,
        center_idx,
        [
            info["site_index"]
            for info in tnn_info
            if info["site_index"] not in [center_idx] + ligand_idx + fnn_idx + snn_idx
        ],
    )
    return center_idx, fnn_idx, snn_idx, tnn_idx


def _sort_index_cw(
    atoms: Any, center_idx: int, neighbor_indices: list[int]
) -> list[int]:
    import numpy as np

    c = atoms[center_idx].position[:2]
    items = []
    for idx in neighbor_indices:
        v = atoms[idx].position[:2] - c
        angle = float(np.arctan2(v[1], v[0]))
        items.append((idx, (math.pi / 2 - angle) % (2 * math.pi)))
    items.sort(key=lambda x: x[1])
    return [x[0] for x in items]


def get_vnn_positions(atoms: Any):
    center_idx, fnn_idx, snn_idx, tnn_idx = get_vnn_idx(atoms)
    return (
        atoms[center_idx].position,
        atoms[fnn_idx].get_positions(),
        atoms[snn_idx].get_positions(),
        atoms[tnn_idx].get_positions(),
    )


def get_active_string(atoms: Any) -> str:
    import numpy as np
    from ase import Atoms
    from pymatgen.analysis.local_env import VoronoiNN
    from pymatgen.io.ase import AseAtomsAdaptor

    ads_idx = [i for i in range(len(atoms)) if atoms[i].tag == 2]
    slabs = atoms.copy()
    del slabs[ads_idx]
    ads_pos = np.array([atoms.get_positions(wrap=True)[i] for i in ads_idx])
    ads_pos = ads_pos[np.argsort(ads_pos[:, 2])]
    slabs += Atoms("U", positions=[ads_pos[0]])
    u_idx = [i for i in range(len(slabs)) if slabs[i].symbol == "U"][0]
    nn_info = VoronoiNN(allow_pathological=True, tol=0.8, cutoff=10).get_nn_info(
        AseAtomsAdaptor.get_structure(slabs), n=u_idx
    )
    coordinated = [
        info["site"].species_string
        for info in nn_info
        if info["site"].species_string != "U"
    ]
    coordination = "-".join(sorted(coordinated))
    if len(coordinated) == 1:
        pos = " (Top)"
    elif len(coordinated) == 2:
        pos = " (Bridge)"
    else:
        pos = " (Hollow)"
    return coordination + pos


# ---------------------------------------------------------------------------
# Catalyst-Design-Agent CalculationTools (UMA only)
# ---------------------------------------------------------------------------


class CalculationTools:
    """FAIRChem UMA / OC20 calculator — the only allowed energy engine."""

    def __init__(self, calculator_name: str = "UMA", device: str = "cuda") -> None:
        if calculator_name not in {"UMA", "uma"}:
            raise ValueError(
                f"unsupported calculator {calculator_name!r}. "
                f"This skill hard-locks {CDA_UMA_PROTOCOL}. "
                "Ask the user for HF_TOKEN / HF_ENDPOINT if UMA cannot start."
            )
        try:
            from fairchem.core import pretrained_mlip
        except ImportError as exc:
            raise ImportError(
                "CalculationTools requires fairchem-core in conda env `catagent`. "
                "Ask the user for HF_TOKEN / HF_ENDPOINT if model download fails."
            ) from exc
        self.calculator_name = "UMA"
        self.protocol = CDA_UMA_PROTOCOL
        self.atomic_reference_energies = dict(ATOMIC_REFERENCE_ENERGIES)
        self.metal_reference_energies = dict(METAL_REFERENCE_ENERGIES)
        self.metal_reduction_potential = dict(METAL_REDUCTION_POTENTIAL)
        self.free_energy_correciton = dict(FREE_ENERGY_CORRECTION)
        self.predictor = pretrained_mlip.get_predict_unit(CDA_UMA_MODEL, device=device)

    def _optimize_atoms(
        self, atoms: Any, max_steps: int = 200
    ) -> tuple[Any, float | None]:
        from ase.optimize import BFGS
        from fairchem.core import FAIRChemCalculator

        atoms = atoms.copy()
        atoms.calc = FAIRChemCalculator(self.predictor, task_name=CDA_UMA_TASK)
        opt = BFGS(atoms, logfile=None)
        if not opt.run(0.05, max_steps):
            return atoms, None
        return atoms, float(atoms.get_potential_energy())

    def _add_adsorbate(self, atoms: Any, adsorbate: str) -> Any:
        import numpy as np
        from ase import Atoms

        atoms = atoms.copy()
        center_pos, _, _, _ = get_vnn_positions(atoms)
        if adsorbate == "*O":
            atoms += Atoms("O", [center_pos + np.array([0.0, 0.0, 1.8])], tags=[2])
        elif adsorbate == "*OH":
            atoms += Atoms("O", [center_pos + np.array([0.0, 0.0, 1.8])], tags=[2])
            atoms += Atoms("H", [center_pos + np.array([0.0, 0.8, 2.3])], tags=[2])
        elif adsorbate == "*OOH":
            atoms += Atoms("O", [center_pos + np.array([0.0, 0.0, 1.8])], tags=[2])
            atoms += Atoms("O", [center_pos + np.array([-1.2, 0.0, 2.5])], tags=[2])
            atoms += Atoms("H", [center_pos + np.array([-1.2, 1.0, 2.5])], tags=[2])
        else:
            raise ValueError(f"unsupported adsorbate: {adsorbate}")
        return atoms

    def calculate_metal_binding_energy(self, atoms: Any) -> float | None:
        import numpy as np

        slabs = atoms.copy()
        cavity = atoms.copy()
        for atom in slabs:
            atom.tag = 1
        center_idx, _, _, _ = get_vnn_idx(slabs)
        metal_element = slabs[center_idx].symbol
        mu = self.metal_reference_energies[metal_element.lower()]
        del cavity[center_idx]
        _, slabs_e = self._optimize_atoms(slabs)
        _, cavity_e = self._optimize_atoms(cavity)
        if None in (slabs_e, cavity_e):
            return None
        return float(np.round(slabs_e - cavity_e - mu, 3))

    calculate_metal_binidng_energy = calculate_metal_binding_energy

    def calculate_dissolution_potential(self, atoms: Any) -> float | None:
        import numpy as np

        slabs = atoms.copy()
        for atom in slabs:
            atom.tag = 1
        center_idx, _, _, _ = get_vnn_idx(slabs)
        metal_element = slabs[center_idx].symbol
        srp, electron = self.metal_reduction_potential[metal_element.lower()]
        metal_bind_e = self.calculate_metal_binding_energy(atoms)
        if metal_bind_e is None:
            return None
        return float(np.round(srp - (metal_bind_e / electron), 3))

    def calculate_binding_energy(
        self, atoms: Any, adsorbates: list[str] | None = None
    ) -> tuple[Any | None, dict[str, Any] | None]:
        adsorbates = list(adsorbates or DEFAULT_ADSORBATES)
        results: dict[str, Any] = {"optimized_adslabs": [], "binding_sites": []}
        slabs = atoms.copy()
        for atom in slabs:
            atom.tag = 1
        optimized_slabs, slabs_e = self._optimize_atoms(slabs)
        if slabs_e is None:
            return None, None
        for adsorbate in adsorbates:
            adslabs = self._add_adsorbate(optimized_slabs, adsorbate)
            optimized_adslabs, adslabs_e = self._optimize_atoms(adslabs)
            if adslabs_e is None:
                return None, None
            results["optimized_adslabs"].append(optimized_adslabs)
            try:
                results["binding_sites"].append(get_active_string(optimized_adslabs))
            except Exception:
                results["binding_sites"].append("unknown")
            gas_e = sum(
                self.atomic_reference_energies[atom.symbol]
                for atom in adslabs
                if atom.tag == 2
            )
            free_e = self.free_energy_correciton[adsorbate]
            results[f"{adsorbate}_gibbs_free_bind_e"] = float(
                adslabs_e + free_e - slabs_e - gas_e
            )
        return optimized_slabs, results

    def calculate_overpotential(
        self, results: dict[str, Any], reaction: str = DEFAULT_REACTION
    ) -> tuple[float, str]:
        if reaction != "ORR":
            raise ValueError(f"unsupported reaction: {reaction}")
        import numpy as np

        dG_OOH = results["*OOH_gibbs_free_bind_e"]
        dG_OH = results["*OH_gibbs_free_bind_e"]
        dG_O = results["*O_gibbs_free_bind_e"]
        dGs = np.array(
            [dG_OOH - ORR_O2_FREE_ENERGY, dG_O - dG_OOH, dG_OH - dG_O, 0.0 - dG_OH]
        )
        labels = [
            "deltaG_OOH - 4.92",
            "deltaG_O - deltaG_OOH",
            "deltaG_OH - deltaG_O",
            "0.00 - deltaG_OH",
        ]
        # The potential/rate-determining step is the bottleneck (largest dG),
        # the same step that sets U_L = -max(dGs) below — hence argmax, not argmin.
        rds = labels[int(np.argmax(dGs))]
        return float(ORR_EQUILIBRIUM_POTENTIAL - (-np.max(dGs))), rds

    def evaluate_structure(
        self,
        atoms: Any,
        metrics: list[str] | None = None,
        adsorbates: list[str] | None = None,
        reaction: str = DEFAULT_REACTION,
    ) -> dict[str, Any]:
        import numpy as np

        requested = normalize_metrics(metrics)
        center_idx, fnn_idx, _, _ = get_vnn_idx(atoms)
        payload: dict[str, Any] = {
            "metal": atoms[center_idx].symbol,
            "coordination": [atoms[i].symbol for i in fnn_idx],
            "converged": True,
            "backend": "uma",
            "protocol": self.protocol,
            "mlip_model": CDA_UMA_MODEL,
            "mlip_task": CDA_UMA_TASK,
        }
        need_diss = "dissolution" in requested
        need_orr = "orr" in requested
        need_ads = "adsorption" in requested or need_orr

        if need_diss or need_orr:
            payload["dissolution_potential"] = self.calculate_dissolution_potential(
                atoms
            )
            if need_diss and payload["dissolution_potential"] is None and not need_orr:
                payload["converged"] = False

        if need_ads or need_orr:
            ads = list(adsorbates or DEFAULT_ADSORBATES)
            _, bind = self.calculate_binding_energy(atoms, ads)
            if bind is None:
                payload["converged"] = False
                payload["adsorption_energies"] = None
                if need_orr:
                    payload["overpotential"] = None
                    payload["rds"] = None
            else:
                payload["adsorption_energies"] = {
                    a: bind.get(f"{a}_gibbs_free_bind_e") for a in ads
                }
                payload["binding_sites"] = bind.get("binding_sites")
                if need_orr:
                    op, rds = self.calculate_overpotential(bind, reaction=reaction)
                    payload["overpotential"] = float(np.round(op, 4))
                    payload["rds"] = rds
        return payload


def calculate_dissolution_potential_from_binding(
    metal: str, metal_bind_e: float
) -> float:
    """``U_diss = SRP - E_bind / n_e`` (CDA formula, offline helper)."""
    key = metal.lower()
    if key not in METAL_REDUCTION_POTENTIAL:
        raise KeyError(f"no reduction potential for metal {metal}")
    srp, electrons = METAL_REDUCTION_POTENTIAL[key]
    return round(srp - (metal_bind_e / electrons), 3)


def calculate_orr_overpotential(
    dG_O: float, dG_OH: float, dG_OOH: float
) -> tuple[float, str, dict[str, float]]:
    """ORR overpotential from *O/*OH/*OOH Gibbs bindings (CDA formula)."""
    steps = {
        "deltaG_OOH - 4.92": dG_OOH - ORR_O2_FREE_ENERGY,
        "deltaG_O - deltaG_OOH": dG_O - dG_OOH,
        "deltaG_OH - deltaG_O": dG_OH - dG_O,
        "0.00 - deltaG_OH": 0.0 - dG_OH,
    }
    values = list(steps.values())
    # RDS is the bottleneck step (largest dG), the same one that fixes
    # u_lim = -max(values) below; select argmax over the raw step energies.
    rds = list(steps.keys())[int(max(range(len(values)), key=lambda i: values[i]))]
    u_lim = -max(values)
    return float(ORR_EQUILIBRIUM_POTENTIAL - u_lim), rds, steps


# ---------------------------------------------------------------------------
# UMA readiness + metric helpers
# ---------------------------------------------------------------------------


def available_dependencies() -> dict[str, bool]:
    import importlib.util

    return {
        "ase": importlib.util.find_spec("ase") is not None,
        "pymatgen": importlib.util.find_spec("pymatgen") is not None,
        "fairchem": importlib.util.find_spec("fairchem") is not None,
        "torch": importlib.util.find_spec("torch") is not None,
        "numpy": importlib.util.find_spec("numpy") is not None,
        "matplotlib": importlib.util.find_spec("matplotlib") is not None,
        "contcar_catalog": CATALOG_PATH.is_file(),
    }


def command_to_shell(command: Iterable[str]) -> str:
    return shlex.join(list(command))


def build_uma_python_command(
    script: str,
    *,
    conda_env: str = "catagent",
    hf_endpoint: str = "https://hf-mirror.com",
) -> list[str]:
    prelude = (
        f"export HF_ENDPOINT={shlex.quote(hf_endpoint)}; "
        'if [ -z "${HF_TOKEN:-}" ] && [ -z "${HUGGING_FACE_HUB_TOKEN:-}" ]; then '
        'echo "HF_TOKEN is required for gated facebook/UMA weights" >&2; exit 2; fi; '
        'export HUGGING_FACE_HUB_TOKEN="${HUGGING_FACE_HUB_TOKEN:-$HF_TOKEN}"; '
        f"python - <<'PY'\n{script}\nPY"
    )
    return ["conda", "run", "-n", conda_env, "bash", "-lc", prelude]


def probe_huggingface_hub(
    *, endpoint: str | None = None, timeout: float = 8.0
) -> dict[str, Any]:
    ep = (endpoint or os.environ.get("HF_ENDPOINT") or "https://huggingface.co").rstrip(
        "/"
    )
    last_error = ""
    for url in (f"{ep}/api/models", f"{ep}/"):
        try:
            req = urllib.request.Request(
                url,
                method="GET",
                headers={"User-Agent": "openai4s-catalyst-sar-screening"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                code = getattr(resp, "status", 200) or 200
            if int(code) < 500:
                return {
                    "ok": True,
                    "endpoint": ep,
                    "probed_url": url,
                    "status": int(code),
                    "error": None,
                }
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
    return {
        "ok": False,
        "endpoint": ep,
        "probed_url": f"{ep}/api/models",
        "status": None,
        "error": last_error or "unreachable",
    }


def check_uma_readiness(
    *,
    require_cuda: bool = False,
    probe_conda_env: str | None = "catagent",
    probe_hub: bool = True,
    hub_timeout: float = 8.0,
) -> dict[str, Any]:
    """Return whether a live FAIRChem UMA (OC20) run can start."""
    import subprocess

    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    hf_endpoint = os.environ.get("HF_ENDPOINT") or "https://huggingface.co"
    deps = available_dependencies()
    probed_env = False
    if probe_conda_env:
        probe = (
            "import importlib.util, json\n"
            "print(json.dumps({"
            "'ase': importlib.util.find_spec('ase') is not None, "
            "'pymatgen': importlib.util.find_spec('pymatgen') is not None, "
            "'fairchem': importlib.util.find_spec('fairchem') is not None, "
            "'torch': importlib.util.find_spec('torch') is not None}))\n"
            "try:\n import torch\n print('CUDA=' + str(bool(torch.cuda.is_available())))\n"
            "except Exception:\n print('CUDA=False')\n"
        )
        try:
            proc = subprocess.run(
                ["conda", "run", "-n", probe_conda_env, "python", "-c", probe],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if proc.returncode == 0:
                lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
                payload = json.loads(lines[0])
                deps.update(
                    {
                        k: bool(payload.get(k))
                        for k in ("ase", "pymatgen", "fairchem", "torch")
                    }
                )
                cuda_line = next(
                    (ln for ln in lines if ln.startswith("CUDA=")), "CUDA=False"
                )
                deps["_cuda_probed"] = cuda_line.split("=", 1)[1] == "True"
                probed_env = True
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError, IndexError):
            probed_env = False

    missing: list[str] = []
    ask_hints: list[str] = []
    if not probed_env and probe_conda_env:
        missing.append(f"conda env `{probe_conda_env}`")
    for key, label in (
        ("ase", "ase"),
        ("pymatgen", "pymatgen"),
        ("fairchem", "fairchem-core"),
        ("torch", "torch"),
    ):
        if not deps.get(key):
            missing.append(f"{label} (install in conda env catagent)")
    if not token:
        missing.append("HF_TOKEN or HUGGING_FACE_HUB_TOKEN (gated facebook/UMA)")
        ask_hints.append(
            "Please paste a Hugging Face access token with access to facebook/UMA "
            "(export as HF_TOKEN). Do not skip UMA."
        )
    hub = {"ok": True, "endpoint": hf_endpoint, "error": None}
    if probe_hub:
        hub = probe_huggingface_hub(endpoint=hf_endpoint, timeout=hub_timeout)
        if not hub["ok"]:
            missing.append(
                f"HF_ENDPOINT mirror (hub unreachable at {hub.get('endpoint')}; "
                f"error={hub.get('error')})"
            )
            ask_hints.append(
                "Please provide a reachable Hugging Face mirror base URL "
                "(example: https://hf-mirror.com) to set as HF_ENDPOINT. "
                "Do not skip UMA or switch calculators."
            )
    cuda_ok = deps.get("_cuda_probed")
    if cuda_ok is None and (require_cuda or deps.get("torch")):
        try:
            import torch

            cuda_ok = bool(torch.cuda.is_available())
        except Exception:
            cuda_ok = False
    if require_cuda and not cuda_ok:
        missing.append("CUDA GPU (torch.cuda.is_available() is False)")

    ask_lines = [_ASK_USER_HF.strip(), "", "Missing:", *[f"- {m}" for m in missing]]
    if ask_hints:
        ask_lines.extend(["", "What to ask the user:", *[f"- {h}" for h in ask_hints]])
    ask_lines.extend(
        ["", f"Fixed protocol: FAIRChem {CDA_UMA_MODEL}, task_name='{CDA_UMA_TASK}'."]
    )
    return {
        "ok": not missing,
        "missing": missing,
        "ask_user": "\n".join(ask_lines) if missing else "",
        "dependencies": {k: v for k, v in deps.items() if not str(k).startswith("_")},
        "has_hf_token": bool(token),
        "hf_endpoint": hub.get("endpoint") or hf_endpoint,
        "hf_hub_ok": bool(hub.get("ok")),
        "model": CDA_UMA_MODEL,
        "task_name": CDA_UMA_TASK,
        "conda_env": probe_conda_env or "catagent",
    }


def require_uma_ready(**kwargs: Any) -> dict[str, Any]:
    ready = check_uma_readiness(**kwargs)
    if not ready["ok"]:
        raise RuntimeError(ready["ask_user"])
    return ready


def normalize_metrics(metrics: list[str] | None) -> list[str]:
    if not metrics:
        return ["dissolution", "adsorption", "orr"]
    out: list[str] = []
    for raw in metrics:
        key = _METRIC_ALIASES.get(str(raw).strip().lower(), str(raw).strip().lower())
        if key not in {"dissolution", "adsorption", "orr"}:
            raise ValueError(
                f"unknown metric {raw!r}; expected dissolution, adsorption, or orr"
            )
        if key not in out:
            out.append(key)
    return out


def supported_dissolution_metals() -> set[str]:
    """Metal centers with BOTH a vendored reduction potential and reference energy.

    Dissolution potential is ``U_diss = SRP - E_bind / n_e``; a metal missing
    from either table cannot be screened and no value is fabricated for it.
    """
    return {
        _canonical_metal(metal)
        for metal in METAL_REDUCTION_POTENTIAL
        if metal in METAL_REFERENCE_ENERGIES
    }


def unsupported_dissolution_metals(metals: Iterable[str]) -> list[str]:
    """Requested metals that lack the vendored data needed for U_diss (sorted, unique)."""
    supported = supported_dissolution_metals()
    missing: dict[str, None] = {}
    for metal in metals:
        symbol = _canonical_metal(str(metal))
        if symbol not in supported:
            missing[symbol] = None
    return sorted(missing)


# ---------------------------------------------------------------------------
# Evaluation + SAR analysis
# ---------------------------------------------------------------------------


def evaluate_poscars(
    built: list[dict[str, Any]],
    *,
    metrics: list[str] | None = None,
    adsorbates: list[str] | None = None,
    reaction: str = DEFAULT_REACTION,
) -> list[dict[str, Any]]:
    """Evaluate POSCARs with UMA only. Raises ask-user errors on readiness failure."""
    requested = normalize_metrics(metrics)
    # Dissolution (and ORR, which also computes U_diss) needs vendored reduction
    # + reference energies. Fail fast with a clear message for metals we cannot
    # screen, rather than IndexError/KeyError deep in the UMA path — and never
    # fabricate the missing constants.
    if "dissolution" in requested or "orr" in requested:
        unsupported = unsupported_dissolution_metals(
            str(meta.get("metal") or "") for meta in built
        )
        if unsupported:
            raise ValueError(
                "Dissolution potential needs vendored reduction + reference "
                "energies (U_diss = SRP - E_bind / n_e); none are fabricated. "
                "No reference data for: " + ", ".join(unsupported) + ". "
                "Choose metals from the supported set: "
                + ", ".join(sorted(supported_dissolution_metals()))
                + "."
            )
    require_uma_ready(require_cuda=False)
    try:
        calc = CalculationTools(calculator_name="UMA")
    except ImportError as exc:
        raise RuntimeError(
            "STOP — FAIRChem UMA is required. Run under conda env `catagent`. "
            "Ask the user for HF_TOKEN and HF_ENDPOINT if model download fails. "
            f"Do NOT skip UMA.\nOriginal error: {exc}"
        ) from exc

    from ase.io import read

    results = []
    for meta in built:
        atoms = read(str(meta["poscar_path"]))
        row = calc.evaluate_structure(
            atoms, metrics=requested, adsorbates=adsorbates, reaction=reaction
        )
        row.update(
            {
                "name": meta.get("name"),
                "description": meta.get("description"),
                "source": meta.get("source"),
                "host": meta.get("host"),
                "motif": meta.get("motif"),
                "coordination_label": "".join(
                    f"{el}{(meta.get('coordination') or []).count(el)}"
                    for el in dict.fromkeys(meta.get("coordination") or [])
                ),
                "catalog_key": meta.get("catalog_key"),
                "poscar_path": meta.get("poscar_path"),
            }
        )
        results.append(row)
    return results


def resolve_analysis_mode(metrics: list[str] | None = None) -> str:
    requested = set(normalize_metrics(metrics))
    if requested == {"dissolution"}:
        return "dissolution"
    if requested == {"adsorption"}:
        return "adsorption"
    if "orr" in requested and "dissolution" in requested:
        return "multi"
    if "orr" in requested:
        return "orr"
    return "multi"


def rank_candidates(
    results: list[dict[str, Any]],
    *,
    mode: str | None = None,
    max_overpotential: float | None = 0.8,
    min_dissolution: float | None = 0.0,
) -> list[dict[str, Any]]:
    mode = mode or resolve_analysis_mode()

    def _f(value: Any) -> float | None:
        try:
            if value is None:
                return None
            number = float(value)
            return None if math.isnan(number) or math.isinf(number) else number
        except (TypeError, ValueError):
            return None

    def key(row: dict[str, Any]) -> tuple:
        converged = 1 if row.get("converged") else 0
        if mode == "dissolution":
            ud = _f(row.get("dissolution_potential"))
            return (
                -converged,
                -(ud if ud is not None else -1e6),
                str(row.get("name") or ""),
            )
        if mode == "adsorption":
            ads = row.get("adsorption_energies") or {}
            oh = _f(ads.get("*OH"))
            return (
                -converged,
                oh if oh is not None else 1e6,
                str(row.get("name") or ""),
            )
        op = _f(row.get("overpotential"))
        ud = _f(row.get("dissolution_potential"))
        return (
            -converged,
            op if op is not None else 1e6,
            -(ud if ud is not None else -1e6),
            str(row.get("name") or ""),
        )

    ranked = [dict(row) for row in results]
    ranked.sort(key=key)
    for idx, row in enumerate(ranked, start=1):
        row["rank"] = idx
        op = _f(row.get("overpotential"))
        ud = _f(row.get("dissolution_potential"))
        row["passes_filters"] = True
        if max_overpotential is not None and op is not None and op > max_overpotential:
            row["passes_filters"] = False
        if min_dissolution is not None and (ud is None or ud < min_dissolution):
            if mode in {"dissolution", "orr", "multi"}:
                row["passes_filters"] = False
        if mode == "dissolution":
            row["composite_score"] = ud
        elif mode == "adsorption":
            ads = row.get("adsorption_energies") or {}
            row["composite_score"] = _f(ads.get("*OH"))
        else:
            row["composite_score"] = (
                None if op is None or ud is None else round(ud - op, 4)
            )
    return ranked


def analyze_structure_activity(
    results: list[dict[str, Any]],
    *,
    metrics: list[str] | None = None,
    max_overpotential: float | None = 0.8,
    min_dissolution: float | None = 0.0,
) -> dict[str, Any]:
    mode = resolve_analysis_mode(metrics)
    ranked = rank_candidates(
        results,
        mode=mode,
        max_overpotential=max_overpotential,
        min_dissolution=min_dissolution,
    )
    trends = _summarize_metal_trends(ranked)
    insights = _build_insights(ranked, trends, mode=mode)
    return {
        "mode": mode,
        "metrics": normalize_metrics(metrics),
        "ranked": ranked,
        "metal_trends": trends,
        "insights": insights,
        "filters": {
            "max_overpotential": max_overpotential,
            "min_dissolution": min_dissolution,
        },
        "n_total": len(ranked),
        "n_converged": sum(1 for r in ranked if r.get("converged")),
        "n_passing": sum(1 for r in ranked if r.get("passes_filters")),
    }


def _summarize_metal_trends(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        groups[str(row.get("metal") or "unknown")].append(row)
    trends = []
    for metal, rows in sorted(groups.items()):
        ops = [
            float(r["overpotential"])
            for r in rows
            if r.get("overpotential") is not None
        ]
        uds = [
            float(r["dissolution_potential"])
            for r in rows
            if r.get("dissolution_potential") is not None
        ]
        trends.append(
            {
                "metal": metal,
                "n": len(rows),
                "mean_overpotential": round(sum(ops) / len(ops), 4) if ops else None,
                "mean_dissolution_potential": (
                    round(sum(uds) / len(uds), 4) if uds else None
                ),
                "best_overpotential": min(ops) if ops else None,
                "best_dissolution_potential": max(uds) if uds else None,
            }
        )
    return trends


def _build_insights(
    ranked: list[dict[str, Any]], trends: list[dict[str, Any]], *, mode: str
) -> list[str]:
    insights: list[str] = []
    if not ranked:
        return ["No structures were evaluated."]
    sources = Counter(str(r.get("source") or "unknown") for r in ranked)
    insights.append(
        "Structure provenance: "
        + ", ".join(f"{k}×{v}" for k, v in sources.items())
        + "."
    )
    if mode == "dissolution":
        best = next(
            (r for r in ranked if r.get("dissolution_potential") is not None), None
        )
        if best:
            insights.append(
                f"Highest dissolution potential is {best.get('name')} "
                f"(U_diss={best.get('dissolution_potential')} V)."
            )
        metal_rows = [
            r
            for r in ranked
            if r.get("dissolution_potential") is not None and r.get("metal")
        ]
        if len({r.get("metal") for r in metal_rows}) >= 3:
            order = ", ".join(
                f"{r.get('metal')}({r.get('dissolution_potential')} V)"
                for r in metal_rows
            )
            insights.append(
                f"Metal-center ranking by dissolution potential (high → low): {order}."
            )
    else:
        best = next((r for r in ranked if r.get("overpotential") is not None), None)
        if best:
            insights.append(
                f"Lowest overpotential candidate is {best.get('name')} "
                f"(OP={best.get('overpotential')} V, "
                f"U_diss={best.get('dissolution_potential')} V)."
            )
    if trends:
        by_ud = sorted(
            [t for t in trends if t.get("mean_dissolution_potential") is not None],
            key=lambda t: -float(t["mean_dissolution_potential"]),
        )
        if by_ud:
            insights.append(
                f"{by_ud[0]['metal']} leads mean dissolution potential "
                f"({by_ud[0]['mean_dissolution_potential']} V)."
            )
    passing = sum(1 for r in ranked if r.get("passes_filters"))
    insights.append(f"{passing}/{len(ranked)} candidates pass the configured filters.")
    return insights


# ---------------------------------------------------------------------------
# Visualization + reports
# ---------------------------------------------------------------------------


def _lattice_determinant(matrix: list[list[float]]) -> float:
    (a, b, c), (d, e, f), (g, h, i) = matrix
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def parse_poscar(text: str) -> dict[str, Any]:
    raw = text.splitlines()
    if len(raw) < 8:
        raise ValueError("POSCAR is too short")
    scale = float(raw[1].split()[0])
    lattice_raw = [[float(x) for x in raw[i].split()[:3]] for i in range(2, 5)]
    if scale < 0:
        # VASP convention: a negative value is the target cell VOLUME, not a
        # linear multiplier — rescale the lattice vectors uniformly to it.
        volume = abs(_lattice_determinant(lattice_raw))
        factor = (abs(scale) / volume) ** (1.0 / 3.0) if volume else 1.0
    else:
        factor = scale
    lattice = [[factor * component for component in row] for row in lattice_raw]
    species = raw[5].split()
    if not species or not re.search(r"[A-Za-z]", species[0]):
        raise ValueError("POSCAR missing species names")
    counts = [int(x) for x in raw[6].split()[: len(species)]]
    idx = 7
    mode_line = raw[idx].strip()
    if mode_line.lower().startswith("s"):
        idx += 1
        mode_line = raw[idx].strip()
    coord_mode = mode_line[0].lower()
    idx += 1
    symbols = [sym for sym, count in zip(species, counts) for _ in range(count)]
    coords = []
    for i, _symbol in enumerate(symbols):
        if idx + i >= len(raw):
            raise ValueError("POSCAR declares more atoms than it has coordinate lines")
        parts = raw[idx + i].split()
        if len(parts) < 3:
            raise ValueError("POSCAR coordinate line has fewer than 3 components")
        vec = [float(parts[0]), float(parts[1]), float(parts[2])]
        if coord_mode == "d":
            coords.append(
                [
                    vec[0] * lattice[0][0]
                    + vec[1] * lattice[1][0]
                    + vec[2] * lattice[2][0],
                    vec[0] * lattice[0][1]
                    + vec[1] * lattice[1][1]
                    + vec[2] * lattice[2][1],
                    vec[0] * lattice[0][2]
                    + vec[1] * lattice[1][2]
                    + vec[2] * lattice[2][2],
                ]
            )
        else:
            coords.append(vec)
    return {"lattice": lattice, "symbols": symbols, "coords": coords}


def export_publication_figures(
    analysis: dict[str, Any], output_dir: str | Path
) -> list[dict[str, Any]]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []

    ranked = analysis.get("ranked") or []
    mode = analysis.get("mode") or "orr"
    figures: list[dict[str, Any]] = []
    if mode == "dissolution":
        usable = [
            r
            for r in ranked
            if r.get("dissolution_potential") is not None and r.get("metal")
        ]
        if usable:
            by_metal: dict[str, dict[str, Any]] = {}
            for row in usable:
                metal = str(row["metal"])
                if metal not in by_metal or float(row["dissolution_potential"]) > float(
                    by_metal[metal]["dissolution_potential"]
                ):
                    by_metal[metal] = row
            ordered = sorted(by_metal.values(), key=lambda r: str(r["metal"]))
            metals = [str(r["metal"]) for r in ordered]
            vals = [float(r["dissolution_potential"]) for r in ordered]
            best = max(vals)
            fig, ax = plt.subplots(figsize=(max(4.8, 0.42 * len(metals) + 1.2), 3.4))
            colors = ["#a33b2b" if v == best else "#1f4e79" for v in vals]
            ax.bar(range(len(metals)), vals, color=colors, width=0.72)
            ax.set_xticks(range(len(metals)))
            ax.set_xticklabels(metals)
            ax.set_ylabel(r"$U_{\mathrm{diss}}$ / V")
            ax.set_title("Dissolution potential by metal center")
            ax.axhline(0.0, color="#7a8b84", linewidth=0.6)
            fig.tight_layout()
            png = out / "fig01_udiss_by_metal.png"
            fig.savefig(png, dpi=200)
            plt.close(fig)
            figures.append(
                {
                    "id": "fig01_udiss_by_metal",
                    "title": "Dissolution potential by metal center",
                    "caption": f"U_diss for {len(metals)} metal centers.",
                    "png": png.name,
                    "relative_png": f"figures/{png.name}",
                    "png_path": str(png.resolve()),
                }
            )
    elif mode == "adsorption":
        usable = [
            r
            for r in ranked
            if (r.get("adsorption_energies") or {}).get("*OH") is not None
            and r.get("name")
        ]
        if usable:
            names = [str(r["name"]) for r in usable]
            vals = [float(r["adsorption_energies"]["*OH"]) for r in usable]
            fig, ax = plt.subplots(figsize=(max(4.8, 0.42 * len(names) + 1.2), 3.4))
            ax.bar(range(len(names)), vals, color="#1f4e79", width=0.72)
            ax.set_xticks(range(len(names)))
            ax.set_xticklabels(names, rotation=30, ha="right")
            ax.set_ylabel(r"$\Delta G_{\mathrm{*OH}}$ / eV")
            ax.set_title("*OH adsorption free energy by candidate")
            ax.axhline(0.0, color="#7a8b84", linewidth=0.6)
            fig.tight_layout()
            png = out / "fig01_oh_adsorption.png"
            fig.savefig(png, dpi=200)
            plt.close(fig)
            figures.append(
                {
                    "id": "fig01_oh_adsorption",
                    "title": "*OH adsorption free energy by candidate",
                    "caption": f"OH adsorption free energy for {len(names)} candidates.",
                    "png": png.name,
                    "relative_png": f"figures/{png.name}",
                    "png_path": str(png.resolve()),
                }
            )
    else:
        pts = [
            (
                float(r["overpotential"]),
                float(r["dissolution_potential"]),
                r.get("name"),
            )
            for r in ranked
            if r.get("overpotential") is not None
            and r.get("dissolution_potential") is not None
        ]
        if pts:
            fig, ax = plt.subplots(figsize=(4.8, 3.6))
            ax.scatter([p[0] for p in pts], [p[1] for p in pts], s=36, c="#1f4e79")
            ax.set_xlabel("Overpotential / V")
            ax.set_ylabel(r"$U_{\mathrm{diss}}$ / V")
            ax.set_title("Overpotential vs dissolution potential")
            fig.tight_layout()
            png = out / "fig01_op_vs_udiss.png"
            fig.savefig(png, dpi=200)
            plt.close(fig)
            figures.append(
                {
                    "id": "fig01_op_vs_udiss",
                    "title": "Overpotential vs dissolution potential",
                    "caption": f"n={len(pts)} candidates.",
                    "png": png.name,
                    "relative_png": f"figures/{png.name}",
                    "png_path": str(png.resolve()),
                }
            )
    return figures


def export_structure_collage(
    structures: list[dict[str, Any]], output_dir: str | Path, *, max_structures: int = 5
) -> list[dict[str, Any]]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []

    selected = [
        s
        for s in structures
        if s.get("poscar_path") and Path(s["poscar_path"]).is_file()
    ]
    selected = selected[:max_structures]
    if len(selected) < 1:
        return []

    panels = []
    scratch = out / "_scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    for meta in selected:
        parsed = parse_poscar(Path(meta["poscar_path"]).read_text(encoding="utf-8"))
        fig, ax = plt.subplots(figsize=(3.2, 3.0))
        xs = [c[0] for c in parsed["coords"]]
        ys = [c[1] for c in parsed["coords"]]
        colors = [
            "#c0392b" if s == meta.get("metal") else "#4a5560"
            for s in parsed["symbols"]
        ]
        sizes = [80 if s == meta.get("metal") else 28 for s in parsed["symbols"]]
        ax.scatter(xs, ys, c=colors, s=sizes, edgecolors="#1a1a1a", linewidths=0.4)
        ax.set_aspect("equal")
        ax.axis("off")
        ax.set_title(str(meta.get("name") or ""), fontsize=9)
        struct_name = str(meta.get("name") or "struct")
        safe_name = re.sub(r"[^\w.\-]+", "_", struct_name)
        path = scratch / f"{safe_name}.png"
        fig.savefig(path, dpi=120, facecolor="#f7f9fb")
        plt.close(fig)
        panels.append(path)

    if len(panels) == 1:
        dest = out / "structures_collage.png"
        shutil.copy2(panels[0], dest)
    else:
        cols = min(3, len(panels))
        rows = int(math.ceil(len(panels) / cols))
        fig, axes = plt.subplots(
            rows, cols, figsize=(2.2 * cols, 2.0 * rows), squeeze=False
        )
        for ax in axes.flat:
            ax.set_axis_off()
        for i, panel in enumerate(panels):
            r, c = divmod(i, cols)
            axes[r][c].imshow(plt.imread(panel))
            axes[r][c].set_axis_off()
            axes[r][c].set_title(selected[i].get("name") or "", fontsize=8, loc="left")
        fig.suptitle("Curated SAC structures", fontsize=11, x=0.02, ha="left")
        fig.tight_layout(rect=(0, 0, 1, 0.97))
        dest = out / "structures_collage.png"
        fig.savefig(dest, dpi=120, facecolor="#f7f9fb")
        plt.close(fig)
    shutil.rmtree(scratch, ignore_errors=True)
    return [
        {
            "id": "struct_collage",
            "title": "Curated SAC structure panel",
            "caption": f"Ball-and-stick renders of {len(selected)} curated candidates.",
            "png": dest.name,
            "relative_png": f"figures/{dest.name}",
            "png_path": str(dest.resolve()),
        }
    ]


def build_markdown_report(
    analysis: dict[str, Any], *, title: str = "Catalyst Structure-Activity Report"
) -> str:
    ranked = analysis.get("ranked") or []
    computation = analysis.get("computation") or {}
    lines = [
        f"# {title}",
        "",
        "## Computation model",
        "",
        f"- Calculator: **`{computation.get('calculator', 'uma')}`** (only allowed mode)",
        f"- MLIP model: **`{computation.get('mlip_model', CDA_UMA_MODEL)}`**",
        f"- FAIRChem task: **`{computation.get('mlip_task', CDA_UMA_TASK)}`**",
        f"- Protocol id: `{computation.get('protocol', CDA_UMA_PROTOCOL)}`",
        f"- Reference: {computation.get('reference', 'Catalyst-Design-Agent CalculationTools')}",
        f"- Runtime env: `{computation.get('conda_env', 'catagent')}`",
        "",
        "## Summary",
        "",
        f"- Structures evaluated: **{analysis.get('n_total', len(ranked))}**",
        f"- Converged: **{analysis.get('n_converged', 0)}**",
        f"- Mode: **{analysis.get('mode')}**",
        f"- Passing filters: **{analysis.get('n_passing', 0)}**",
        "",
        "## Key insights",
        "",
    ]
    for insight in analysis.get("insights") or []:
        lines.append(f"- {insight}")

    lines.extend(["", "## Figures", ""])
    for fig in analysis.get("figures") or []:
        lines.append(f"**{fig.get('title')}**")
        lines.append("")
        lines.append(f"![{fig.get('title')}]({fig.get('relative_png')})")
        lines.append("")
        if fig.get("caption"):
            lines.append(f"*{fig['caption']}*")
            lines.append("")

    lines.extend(["", "## Structure renders", ""])
    for rend in analysis.get("structure_renders") or []:
        lines.append(f"**{rend.get('title')}**")
        lines.append("")
        lines.append(f"![{rend.get('title')}]({rend.get('relative_png')})")
        lines.append("")

    lines.extend(
        [
            "",
            "## Ranked candidates",
            "",
            "| Rank | Name | Metal | Source | OP (V) | U_diss (V) | Pass |",
            "|---:|---|---|---|---:|---:|:---:|",
        ]
    )
    for row in ranked:
        lines.append(
            "| {rank} | `{name}` | {metal} | {src} | {op} | {ud} | {ok} |".format(
                rank=row.get("rank", ""),
                name=row.get("name", ""),
                metal=row.get("metal", ""),
                src=row.get("source") or "",
                op=_fmt(row.get("overpotential")),
                ud=_fmt(row.get("dissolution_potential")),
                ok="yes" if row.get("passes_filters") else "no",
            )
        )
    lines.extend(
        [
            "",
            "## Methods note",
            "",
            "Structures come from the embedded POSCAR texts in "
            "`contcar_catalog.json` (exact lookup, else derived by "
            "metal/coordination edits). Energies follow Catalyst-Design-Agent "
            f"FAIRChem UMA (`{CDA_UMA_MODEL}`, task `{CDA_UMA_TASK}`): "
            "`U_diss = E°_red − E_bind / n_e`; ORR overpotential from *O/*OH/*OOH Gibbs "
            "bindings with 4.92 eV / 1.23 V references.",
            "",
        ]
    )
    return "\n".join(lines)


def render_sar_dashboard(
    analysis: dict[str, Any], *, title: str = "Catalyst SAR screening dashboard"
) -> str:
    ranked = analysis.get("ranked") or []
    computation = analysis.get("computation") or {}
    insights = "".join(
        f"<li>{html.escape(text)}</li>" for text in (analysis.get("insights") or [])
    )
    rows = []
    for row in ranked:
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('rank', '')))}</td>"
            f"<td><code>{html.escape(str(row.get('name', '')))}</code></td>"
            f"<td>{html.escape(str(row.get('metal', '')))}</td>"
            f"<td>{html.escape(str(row.get('source') or ''))}</td>"
            f"<td>{html.escape(_fmt(row.get('overpotential')))}</td>"
            f"<td>{html.escape(_fmt(row.get('dissolution_potential')))}</td>"
            f"<td>{'pass' if row.get('passes_filters') else 'fail'}</td>"
            "</tr>"
        )
    fig_cards = []
    for fig in analysis.get("figures") or []:
        rel = html.escape(str(fig.get("relative_png") or ""))
        fig_cards.append(
            f'<figure><img src="{rel}" alt="{html.escape(str(fig.get("title") or ""))}"/>'
            f"<figcaption>{html.escape(str(fig.get('title') or ''))}</figcaption></figure>"
        )
    for rend in analysis.get("structure_renders") or []:
        rel = html.escape(str(rend.get("relative_png") or ""))
        fig_cards.append(
            f'<figure><img src="{rel}" alt="{html.escape(str(rend.get("title") or ""))}"/>'
            f"<figcaption>{html.escape(str(rend.get('title') or ''))}</figcaption></figure>"
        )
    payload = {
        "mode": analysis.get("mode"),
        "metrics": analysis.get("metrics"),
        "computation": computation,
        "ranked": [
            {
                k: v
                for k, v in row.items()
                if k not in {"poscar_path", "adsorption_energies"}
                or k == "adsorption_energies"
            }
            for row in ranked
        ],
        "insights": analysis.get("insights") or [],
        "figures": [
            {k: v for k, v in f.items() if k != "png_path"}
            for f in (analysis.get("figures") or [])
        ],
        "structure_renders": [
            {k: v for k, v in f.items() if k != "png_path"}
            for f in (analysis.get("structure_renders") or [])
        ],
    }
    payload_json = json.dumps(payload, ensure_ascii=True, default=str).replace(
        "<", "\\u003c"
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{html.escape(title)}</title>
<style>
body {{ margin:0; font-family:"Source Sans 3","Segoe UI",sans-serif; color:#1c2b24;
  background:linear-gradient(180deg,#eef4f1 0%,transparent 28%), #f4f7f5; }}
header, main {{ max-width:1100px; margin:0 auto; padding:1.5rem; }}
h1 {{ font-family:Georgia,serif; font-weight:600; }}
.panel {{ background:#fff; border:1px solid #d5ddd8; border-radius:14px; padding:1rem; margin:1rem 0; }}
.kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:.75rem; }}
.kpi span {{ display:block; color:#5a6b62; font-size:.82rem; }}
.kpi strong {{ font-size:1.3rem; }}
table {{ width:100%; border-collapse:collapse; font-size:.9rem; }}
th, td {{ text-align:left; padding:.4rem; border-bottom:1px solid #d5ddd8; }}
.figure-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:1rem; }}
.figure-grid img {{ width:100%; height:auto; border:1px solid #d5ddd8; border-radius:10px; }}
code {{ font-family:ui-monospace,monospace; font-size:.84em; }}
</style>
</head>
<body>
<header>
  <h1>{html.escape(title)}</h1>
  <p>Required report contents: computation model, statistical figures, structure renders, SAR insights.</p>
</header>
<main>
  <section class="panel" id="computation-model-panel">
    <h2>Computation model</h2>
    <ul>
      <li>Calculator: <code>{html.escape(str(computation.get('calculator', 'uma')))}</code></li>
      <li>MLIP model: <strong>{html.escape(str(computation.get('mlip_model', CDA_UMA_MODEL)))}</strong></li>
      <li>FAIRChem task: <code>{html.escape(str(computation.get('mlip_task', CDA_UMA_TASK)))}</code></li>
      <li>Protocol: <code>{html.escape(str(computation.get('protocol', CDA_UMA_PROTOCOL)))}</code></li>
    </ul>
  </section>
  <section class="kpis">
    <div class="kpi panel"><span>Structures</span><strong>{analysis.get('n_total', 0)}</strong></div>
    <div class="kpi panel"><span>Converged</span><strong>{analysis.get('n_converged', 0)}</strong></div>
    <div class="kpi panel"><span>Passing</span><strong>{analysis.get('n_passing', 0)}</strong></div>
    <div class="kpi panel"><span>Mode</span><strong>{html.escape(str(analysis.get('mode') or ''))}</strong></div>
  </section>
  <section class="panel" id="figures-panel">
    <h2>Figures &amp; structure renders</h2>
    <div class="figure-grid" id="structure-renders-panel">{''.join(fig_cards)}</div>
  </section>
  <section class="panel">
    <h2>SAR insights</h2>
    <ul>{insights or '<li>No insights.</li>'}</ul>
  </section>
  <section class="panel">
    <h2>Ranked candidates</h2>
    <table>
      <thead><tr><th>Rank</th><th>Name</th><th>Metal</th><th>Source</th><th>OP</th><th>U_diss</th><th>Filter</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
  </section>
</main>
<script type="application/json" id="sar-data">{payload_json}</script>
</body>
</html>
"""


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return str(value)


# ---------------------------------------------------------------------------
# Pipeline entry points
# ---------------------------------------------------------------------------


def run_pipeline(
    descriptions: list[str | dict[str, Any]],
    workdir: str | Path,
    *,
    metrics: list[str] | None = None,
    max_overpotential: float | None = 0.8,
    min_dissolution: float | None = 0.0,
    reaction: str = DEFAULT_REACTION,
    adsorbates: list[str] | None = None,
) -> dict[str, Any]:
    """Run the fixed SAR pipeline: POSCAR → UMA metrics → SAR → lean report."""
    root = Path(workdir)
    root.mkdir(parents=True, exist_ok=True)
    requested = normalize_metrics(metrics)

    built = build_poscars_from_descriptions(descriptions, root / "poscars")
    results = evaluate_poscars(
        built, metrics=requested, adsorbates=adsorbates, reaction=reaction
    )
    analysis = analyze_structure_activity(
        results,
        metrics=requested,
        max_overpotential=max_overpotential,
        min_dissolution=min_dissolution,
    )
    analysis["computation"] = {
        "calculator": "uma",
        "protocol": CDA_UMA_PROTOCOL,
        "mlip_model": CDA_UMA_MODEL,
        "mlip_task": CDA_UMA_TASK,
        "reference": "Catalyst-Design-Agent CalculationTools (FAIRChem UMA / OC20)",
        "conda_env": "catagent",
    }

    figures_dir = root / "figures"
    analysis["figures"] = export_publication_figures(analysis, figures_dir)
    analysis["structure_renders"] = export_structure_collage(built, figures_dir)
    if not analysis["figures"]:
        raise RuntimeError(
            "Statistical figures were not generated (need matplotlib). Re-run after fixing the environment."
        )
    if not analysis["structure_renders"]:
        raise RuntimeError(
            "Structure collage was not generated. Re-run after fixing POSCAR inputs / matplotlib."
        )

    summary_path = root / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "mode": analysis["mode"],
                "metrics": requested,
                "computation": analysis["computation"],
                "n_total": analysis["n_total"],
                "n_converged": analysis["n_converged"],
                "n_passing": analysis["n_passing"],
                "insights": analysis["insights"],
                "ranked": [
                    {k: v for k, v in row.items() if k not in {"poscar_path"}}
                    for row in analysis["ranked"]
                ],
                "figures": [
                    {k: v for k, v in f.items() if k != "png_path"}
                    for f in analysis["figures"]
                ],
                "structure_renders": [
                    {k: v for k, v in f.items() if k != "png_path"}
                    for f in analysis["structure_renders"]
                ],
            },
            indent=2,
            ensure_ascii=True,
            default=str,
        ),
        encoding="utf-8",
    )

    report = build_markdown_report(analysis)
    html_doc = render_sar_dashboard(analysis)
    report_path = root / "catalyst_sar_report.md"
    html_path = root / "catalyst_sar_dashboard.html"
    report_path.write_text(report, encoding="utf-8")
    html_path.write_text(html_doc, encoding="utf-8")

    figure_paths = sorted(str(p.resolve()) for p in figures_dir.glob("*.png"))
    deliverables = [
        str(report_path.resolve()),
        str(html_path.resolve()),
        str(summary_path.resolve()),
        *figure_paths,
    ]
    return {
        "workdir": str(root.resolve()),
        "structures": built,
        "results": results,
        "analysis": analysis,
        "html_path": str(html_path.resolve()),
        "report_path": str(report_path.resolve()),
        "summary_path": str(summary_path.resolve()),
        "metrics": requested,
        "mode": analysis["mode"],
        "deliverables": deliverables,
    }


def run_metal_center_dissolution_case(
    descriptions: list[str | dict[str, Any]],
    workdir: str | Path | None = None,
    *,
    min_dissolution: float = 0.0,
) -> dict[str, Any]:
    """Case study helper: fixed M–N4 motif, vary metal centers, U_diss only.

    ``descriptions`` is required (pass the user's metal list, e.g.
    ``["Mn-N4", "Fe-N4", "Cu-N4"]``). Do not load committed demo shells as
    user results — this helper only runs ``run_pipeline`` into ``workdir``.
    """
    import tempfile

    if not descriptions:
        raise ValueError(
            "descriptions is required (e.g. ['Mn-N4', 'Fe-N4', 'Cu-N4']). "
            "Do not use metal_center_dissolution_* demo files as substitutes "
            "for a live run."
        )
    if workdir is None:
        workdir = Path(tempfile.mkdtemp(prefix="catalyst_sar_"))
    return run_pipeline(
        descriptions,
        workdir=workdir,
        metrics=["dissolution"],
        min_dissolution=min_dissolution,
    )


def load_descriptions(path: str | Path) -> list[str | dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("descriptions JSON must be a list")
    return payload
