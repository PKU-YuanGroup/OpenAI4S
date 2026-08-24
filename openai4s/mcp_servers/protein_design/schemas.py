"""MCP tool declarations for the atomic protein-design connector."""

from __future__ import annotations

_ATTEMPT = {
    "attempt_id": {
        "type": "string",
        "minLength": 1,
        "maxLength": 128,
        "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]*$",
        "description": "Stable model-execution identifier; one call is one attempt.",
    },
    "seed": {"type": "integer", "minimum": 0, "maximum": 2147483647},
    "output_dir": {
        "type": "string",
        "minLength": 1,
        "description": (
            "Explicit output directory; the server creates one attempt_id "
            "subdirectory for outputs and the terminal manifest."
        ),
    },
    "run_mode": {
        "type": "string",
        "enum": ["canary", "formal"],
        "default": "formal",
        "description": (
            "Use canary for backend bring-up. A formal checkpointed call is "
            "admitted only after the same live server process verifies a canary."
        ),
    },
    "execution_target": {
        "type": "string",
        "minLength": 1,
        "default": "local",
        "description": "Explicit selected route, for example local or ssh:<alias>.",
    },
}

_PIN = {
    "backend_revision": {
        "type": "string",
        "minLength": 7,
        "description": "Pinned source revision or immutable image digest.",
    },
}

_CHECKPOINT = {
    "checkpoint_path": {
        "type": "string",
        "minLength": 1,
        "description": "Already-local checkpoint or checkpoint manifest; never downloaded.",
    },
    "checkpoint_sha256": {
        "type": "string",
        "pattern": "^[0-9a-fA-F]{64}$",
        "description": "Expected SHA-256, verified before model startup.",
    },
}


def _object(properties: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


TOOLS = [
    {
        "name": "generate_backbone",
        "description": (
            "Run exactly one auditable RFdiffusion binder-backbone attempt. "
            "Requires an explicit target chain, validated hotspot residues, seed, "
            "pinned revision and local checkpoint digest; returns both PDB and TRB. "
            "Backbone generation is not evidence of folding or binding."
        ),
        "inputSchema": _object(
            {
                **_ATTEMPT,
                **_PIN,
                **_CHECKPOINT,
                "target_pdb": {"type": "string", "minLength": 1},
                "target_chain": {"type": "string", "minLength": 1, "maxLength": 1},
                "target_chains": {
                    "type": "array",
                    "minItems": 1,
                    "uniqueItems": True,
                    "items": {"type": "string", "minLength": 1, "maxLength": 1},
                    "description": (
                        "Explicit fixed target chains for a multichain target; "
                        "when present, the first item must equal target_chain."
                    ),
                },
                "hotspot_residues": {
                    "type": "array",
                    "minItems": 1,
                    "uniqueItems": True,
                    "items": {"type": "string", "pattern": "^[A-Za-z0-9][0-9]+$"},
                },
                "binder_length": {"type": "integer", "minimum": 20, "maximum": 500},
                "num_designs": {
                    "type": "integer",
                    "const": 1,
                    "default": 1,
                    "description": "One attempt always produces one design.",
                },
                "diffusion_steps": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "default": 50,
                },
                "noise_scale_ca": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 2,
                    "default": 1,
                },
                "noise_scale_frame": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 2,
                    "default": 1,
                },
            },
            [
                "attempt_id",
                "seed",
                "output_dir",
                "backend_revision",
                "checkpoint_path",
                "checkpoint_sha256",
                "target_pdb",
                "target_chain",
                "hotspot_residues",
                "binder_length",
            ],
        ),
    },
    {
        "name": "design_sequence",
        "description": (
            "Run ProteinMPNN with explicit design chains and per-chain fixed positions. "
            "Uses the official --pdb_path_chains and --fixed_positions_jsonl flags, "
            "then independently checks target chains, motifs, lengths and residue maps."
        ),
        "inputSchema": _object(
            {
                **_ATTEMPT,
                **_PIN,
                **_CHECKPOINT,
                "backbone_pdb": {"type": "string", "minLength": 1},
                "design_chains": {
                    "type": "array",
                    "minItems": 1,
                    "uniqueItems": True,
                    "items": {"type": "string", "minLength": 1, "maxLength": 1},
                },
                "fixed_positions": {
                    "type": "object",
                    "minProperties": 1,
                    "description": (
                        "Every input chain must be named. Values are 'all' or "
                        "1-based chain-local sequence positions, matching the "
                        "official ProteinMPNN fixed_positions_jsonl convention."
                    ),
                    "additionalProperties": {
                        "oneOf": [
                            {"type": "string", "const": "all"},
                            {
                                "type": "array",
                                "uniqueItems": True,
                                "items": {"type": "integer"},
                            },
                        ]
                    },
                },
                "num_sequences": {"type": "integer", "minimum": 1, "maximum": 10000},
                "sampling_temp": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1,
                },
                "model_name": {"type": "string", "minLength": 1, "default": "v_48_020"},
            },
            [
                "attempt_id",
                "seed",
                "output_dir",
                "backend_revision",
                "checkpoint_path",
                "checkpoint_sha256",
                "backbone_pdb",
                "design_chains",
                "fixed_positions",
                "num_sequences",
                "sampling_temp",
            ],
        ),
    },
    {
        "name": "predict_structure",
        "description": (
            "Predict one protein chain from sequence with frozen, local model data. "
            "The default formal mode is no-MSA/no-template and records raw confidence "
            "outputs; confidence is model evidence, not folding proof."
        ),
        "inputSchema": _object(
            {
                **_ATTEMPT,
                **_PIN,
                **_CHECKPOINT,
                "sequence": {"type": "string", "minLength": 1},
                "model_type": {"type": "string", "default": "alphafold2_ptm"},
                "recycles": {"type": "integer", "minimum": 0, "maximum": 100},
                "model_count": {"type": "integer", "minimum": 1, "maximum": 5},
                "msa_mode": {
                    "type": "string",
                    "const": "single_sequence",
                    "default": "single_sequence",
                },
                "require_network_isolation": {
                    "type": "boolean",
                    "const": True,
                    "default": True,
                },
            },
            [
                "attempt_id",
                "seed",
                "output_dir",
                "backend_revision",
                "checkpoint_path",
                "checkpoint_sha256",
                "sequence",
                "model_type",
                "recycles",
                "model_count",
            ],
        ),
    },
    {
        "name": "predict_complex",
        "description": (
            "Blindly predict a sequence-only protein complex with no MSA server, "
            "templates or initial guess. Freezes model type, checkpoint, recycles, "
            "seed and model count and preserves raw PAE, interface PAE and ipTM."
        ),
        "inputSchema": _object(
            {
                **_ATTEMPT,
                **_PIN,
                **_CHECKPOINT,
                "sequences": {
                    "type": "array",
                    "minItems": 2,
                    "items": {"type": "string", "minLength": 1},
                },
                "chain_names": {
                    "type": "array",
                    "minItems": 2,
                    "uniqueItems": True,
                    "items": {"type": "string", "minLength": 1, "maxLength": 1},
                },
                "model_type": {"type": "string", "const": "alphafold2_multimer_v3"},
                "recycles": {"type": "integer", "minimum": 0, "maximum": 100},
                "model_count": {"type": "integer", "minimum": 1, "maximum": 5},
                "msa_mode": {
                    "type": "string",
                    "const": "single_sequence",
                    "default": "single_sequence",
                },
                "require_network_isolation": {
                    "type": "boolean",
                    "const": True,
                    "default": True,
                },
            },
            [
                "attempt_id",
                "seed",
                "output_dir",
                "backend_revision",
                "checkpoint_path",
                "checkpoint_sha256",
                "sequences",
                "chain_names",
                "model_type",
                "recycles",
                "model_count",
            ],
        ),
    },
]

_PDB_OPERATION_COMMON = {
    **_ATTEMPT,
    **_PIN,
    "pdb_path": {"type": "string", "minLength": 1},
}

TOOLS.extend(
    [
        {
            "name": "rosetta_score",
            "description": "Compute Rosetta physical-energy evidence for a PDB; this is not a binding or stability ground truth.",
            "inputSchema": _object(
                {
                    **_PDB_OPERATION_COMMON,
                    "score_function": {"type": "string", "default": "ref2015"},
                },
                ["attempt_id", "seed", "output_dir", "backend_revision", "pdb_path"],
            ),
        },
        {
            "name": "rosetta_relax",
            "description": "Run seeded Rosetta FastRelax trajectories and retain the best explicit output structure.",
            "inputSchema": _object(
                {
                    **_PDB_OPERATION_COMMON,
                    "score_function": {"type": "string", "default": "ref2015"},
                    "nstruct": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 1,
                    },
                    "max_iterations": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10000,
                        "default": 200,
                    },
                },
                ["attempt_id", "seed", "output_dir", "backend_revision", "pdb_path"],
            ),
        },
        {
            "name": "rosetta_interface_score",
            "description": (
                "Compute Rosetta dG_separated, dSASA, packstat and interface residue count. "
                "The hydrogen-bond-related field is correctly named interface_delta_unsat_hbonds."
            ),
            "inputSchema": _object(
                {
                    **_PDB_OPERATION_COMMON,
                    "chains": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9]+_[A-Za-z0-9]+$",
                    },
                    "score_function": {"type": "string", "default": "ref2015"},
                },
                [
                    "attempt_id",
                    "seed",
                    "output_dir",
                    "backend_revision",
                    "pdb_path",
                    "chains",
                ],
            ),
        },
        {
            "name": "score_stability",
            "description": (
                "Compute ESM-2 masked pseudo-log-likelihood as sequence-naturalness evidence. "
                "It is explicitly not thermodynamic stability and must not be used as hard truth."
            ),
            "inputSchema": _object(
                {
                    **_ATTEMPT,
                    **_PIN,
                    **_CHECKPOINT,
                    "sequence": {"type": "string", "minLength": 1},
                    "model_name": {"type": "string", "minLength": 1},
                },
                [
                    "attempt_id",
                    "seed",
                    "output_dir",
                    "backend_revision",
                    "checkpoint_path",
                    "checkpoint_sha256",
                    "sequence",
                    "model_name",
                ],
            ),
        },
        {
            "name": "energy_minimize",
            "description": (
                "Minimize a PDB with OpenMM and a frozen force-field choice. "
                "This is optional refinement evidence, not proof that a design "
                "folds, binds or functions."
            ),
            "inputSchema": _object(
                {
                    **_PDB_OPERATION_COMMON,
                    "force_field": {"type": "string", "default": "amber14-all.xml"},
                    "implicit_solvent": {
                        "type": ["string", "null"],
                        "default": "implicit/gbn2.xml",
                    },
                    "max_iterations": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100000,
                        "default": 500,
                    },
                    "tolerance_kj_mol_nm": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "default": 10,
                    },
                },
                ["attempt_id", "seed", "output_dir", "backend_revision", "pdb_path"],
            ),
        },
    ]
)

TOOL_NAMES = frozenset(tool["name"] for tool in TOOLS)
TOOL_SCHEMAS = {tool["name"]: tool["inputSchema"] for tool in TOOLS}

__all__ = ["TOOLS", "TOOL_NAMES", "TOOL_SCHEMAS"]
