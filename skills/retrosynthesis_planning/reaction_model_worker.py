"""Foreign-environment JSON worker for supported open reaction models."""

from __future__ import annotations

import contextlib
import csv
import importlib.metadata
import json
import math
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

WIRE_SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 8 * 1024 * 1024
OPERATIONS = {
    "aizynthfinder": {"capabilities", "plan_routes"},
    "rxnmapper": {"capabilities", "map_reactions"},
    "reactiont5_forward": {"capabilities", "predict_products"},
    "reactiont5_yield": {"capabilities", "predict_yields"},
    "parrot": {"capabilities", "recommend_conditions"},
}

_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_.-])(?:/[A-Za-z0-9_.~+-]+){2,}")


class RequestError(ValueError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _public_error(value: object, *, limit: int = 1500) -> str:
    """Bound worker errors and remove host-specific absolute filesystem paths."""

    text = _ABSOLUTE_PATH.sub("<path>", str(value))
    return text[:limit]


def _version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _runtime() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "packages": {
            name: _version(name)
            for name in (
                "aizynthfinder",
                "rxnmapper",
                "rdkit",
                "torch",
                "transformers",
                "simpletransformers",
                "pandas",
            )
        },
    }


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RequestError("invalid_request", f"{field} must be a non-empty string")
    return value.strip()


def _array(value: Any, field: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or not all(
        isinstance(item, Mapping) for item in value
    ):
        raise RequestError("invalid_request", f"{field} must be an array of objects")
    return value


def _validate(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RequestError("invalid_request", "request must be an object")
    expected = {
        "schema_version",
        "request_id",
        "backend",
        "operation",
        "inputs",
        "options",
        "model_location",
        "repository_dir",
        "model_manifest",
    }
    if set(value) != expected or value.get("schema_version") != WIRE_SCHEMA_VERSION:
        raise RequestError(
            "invalid_request", "request fields or schema version are invalid"
        )
    backend = _text(value["backend"], "backend")
    operation = _text(value["operation"], "operation")
    if backend not in OPERATIONS or operation not in OPERATIONS[backend]:
        raise RequestError(
            "unsupported_operation", f"{backend} does not support {operation}"
        )
    if not isinstance(value["options"], Mapping) or not isinstance(
        value["model_manifest"], Mapping
    ):
        raise RequestError(
            "invalid_request", "options and model_manifest must be objects"
        )
    return dict(value)


def _map_reactions(request: Mapping[str, Any]) -> dict[str, Any]:
    from rdkit import Chem
    from rxnmapper import BatchedMapper

    rows = _array(request["inputs"], "inputs")
    batch_size = request["options"].get("batch_size", 32)
    if (
        isinstance(batch_size, bool)
        or not isinstance(batch_size, int)
        or not 1 <= batch_size <= 1024
    ):
        raise RequestError("invalid_request", "batch_size must be between 1 and 1024")
    reactions = [_text(row.get("reaction_smiles"), "reaction_smiles") for row in rows]
    with contextlib.redirect_stdout(sys.stderr):
        mapper = BatchedMapper(batch_size=batch_size)
        mapped = list(mapper.map_reactions_with_info(reactions))
    records = []
    for row, output in zip(rows, mapped):
        output = output if isinstance(output, Mapping) else {}
        mapped_reaction = output.get("mapped_rxn") or ""
        correspondence: list[dict[str, Any]] = []
        error = None
        try:
            left, right = mapped_reaction.split(">>")
            sides: list[dict[int, str]] = []
            for prefix, side in (("r", left), ("p", right)):
                atoms: dict[int, str] = {}
                for component_index, component in enumerate(side.split(".")):
                    molecule = Chem.MolFromSmiles(component)
                    if molecule is None:
                        raise ValueError(f"cannot parse component {component!r}")
                    for atom in molecule.GetAtoms():
                        map_num = int(atom.GetAtomMapNum())
                        if map_num > 0:
                            atoms[map_num] = (
                                f"{prefix}{component_index}:a{atom.GetIdx()}"
                            )
                sides.append(atoms)
            correspondence = [
                {
                    "map_num": map_num,
                    "reactant_atom": sides[0][map_num],
                    "product_atom": sides[1][map_num],
                }
                for map_num in sorted(set(sides[0]) & set(sides[1]))
            ]
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        records.append(
            {
                "reaction_id": _text(row.get("reaction_id"), "reaction_id"),
                "mapped_reaction": mapped_reaction,
                "confidence": output.get("confidence"),
                "atom_correspondence": correspondence,
                "error": error
                or (None if mapped_reaction else "mapper returned an empty record"),
            }
        )
    return {"records": records}


def _name_array(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise RequestError("invalid_request", f"{field} must be an array")
    names = [_text(item, field) for item in value]
    if len(names) != len(set(names)):
        raise RequestError("invalid_request", f"{field} must not contain duplicates")
    return names


def _plan_routes(request: Mapping[str, Any]) -> dict[str, Any]:
    from aizynthfinder.aizynthfinder import AiZynthFinder

    rows = _array(request["inputs"], "inputs")
    options = request["options"]
    config_path = options.get("config_path")
    if not isinstance(config_path, str) or not Path(config_path).is_file():
        raise RequestError(
            "configuration_required", "an existing AiZynthFinder config is required"
        )
    policies = _name_array(options.get("policies", []), "policies")
    filters = _name_array(options.get("filters", []), "filters")
    stocks = _name_array(options.get("stocks", []), "stocks")
    max_routes = options.get("max_routes", 10)
    if (
        isinstance(max_routes, bool)
        or not isinstance(max_routes, int)
        or not 1 <= max_routes <= 100
    ):
        raise RequestError("invalid_request", "max_routes must be between 1 and 100")

    with contextlib.redirect_stdout(sys.stderr):
        finder = AiZynthFinder(configfile=config_path)
        finder.stock.select(stocks or finder.stock.items)
        finder.expansion_policy.select(policies or finder.expansion_policy.items[0])
        if filters:
            finder.filter_policy.select(filters)
        else:
            finder.filter_policy.select_all()

    records = []
    for row in rows:
        target_id = _text(row.get("target_id"), "target_id")
        target_smiles = _text(row.get("target_smiles"), "target_smiles")
        started = time.monotonic()
        try:
            with contextlib.redirect_stdout(sys.stderr):
                finder.target_smiles = target_smiles
                finder.prepare_tree()
                finder.tree_search(show_progress=False)
                finder.build_routes()
                finder.routes.compute_scores(*finder.scorers.objects())
                statistics = dict(finder.extract_statistics())
                route_trees = list(
                    finder.routes.dict_with_extra(
                        include_metadata=True, include_scores=True
                    )
                )[:max_routes]
        except Exception as exc:
            records.append(
                {
                    "target_id": target_id,
                    "routes": [],
                    "termination_reason": "backend_error",
                    "search_stats": {
                        "wall_seconds": round(time.monotonic() - started, 6),
                        "error": f"{type(exc).__name__}: {_public_error(exc)}",
                    },
                }
            )
            continue
        routes = []
        for tree in route_trees:
            metadata = tree.get("metadata") if isinstance(tree, Mapping) else None
            scores = tree.get("scores") if isinstance(tree, Mapping) else None
            routes.append(
                {
                    "tree": tree,
                    "solved": (
                        metadata.get("is_solved")
                        if isinstance(metadata, Mapping)
                        else None
                    ),
                    "scores": dict(scores) if isinstance(scores, Mapping) else {},
                }
            )
        statistics["wall_seconds"] = round(time.monotonic() - started, 6)
        solved = bool(statistics.get("is_solved"))
        records.append(
            {
                "target_id": target_id,
                "routes": routes,
                "termination_reason": "solved" if solved else "search_exhausted",
                "search_stats": statistics,
            }
        )
    return {"records": records}


def _device(value: Any):
    import torch

    name = _text(value or "cpu", "device")
    if name.startswith("cuda") and not torch.cuda.is_available():
        raise RequestError(
            "device_unavailable", f"requested device {name} is unavailable"
        )
    return torch.device(name)


def _require_model_location(request: Mapping[str, Any]) -> str:
    location = request.get("model_location")
    if not isinstance(location, str) or not Path(location).is_dir():
        raise RequestError(
            "checkpoint_required",
            "an existing local model_location is required; worker downloads are disabled",
        )
    return location


def _predict_products(request: Mapping[str, Any]) -> dict[str, Any]:
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    rows = _array(request["inputs"], "inputs")
    top_k = request["options"].get("top_k", 5)
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 10:
        raise RequestError("invalid_request", "top_k must be between 1 and 10")
    max_new_tokens = request["options"].get("max_new_tokens", 128)
    if (
        isinstance(max_new_tokens, bool)
        or not isinstance(max_new_tokens, int)
        or not 1 <= max_new_tokens <= 256
    ):
        raise RequestError(
            "invalid_request", "max_new_tokens must be between 1 and 256"
        )
    device = _device(request["options"].get("device"))
    location = _require_model_location(request)
    with contextlib.redirect_stdout(sys.stderr):
        tokenizer = AutoTokenizer.from_pretrained(location, local_files_only=True)
        model = (
            AutoModelForSeq2SeqLM.from_pretrained(location, local_files_only=True)
            .to(device)
            .eval()
        )
    records = []
    for row in rows:
        reaction_id = _text(row.get("reaction_id"), "reaction_id")
        text = f"REACTANT:{_text(row.get('reactants'), 'reactants')}REAGENT:{str(row.get('reagents') or '')}"
        inputs = tokenizer(text, return_tensors="pt")
        inputs = {key: value.to(device) for key, value in inputs.items()}
        with torch.inference_mode():
            generated = model.generate(
                **inputs,
                num_beams=top_k,
                num_return_sequences=top_k,
                return_dict_in_generate=True,
                output_scores=True,
                max_new_tokens=max_new_tokens,
                early_stopping=top_k > 1,
            )
        sequences = tokenizer.batch_decode(
            generated.sequences, skip_special_tokens=True
        )
        sequence_scores = getattr(generated, "sequences_scores", None)
        scores = (
            sequence_scores.detach().cpu().tolist()
            if sequence_scores is not None
            else [None] * len(sequences)
        )
        predictions = [
            {
                "rank": index,
                "product_smiles": sequence.replace(" ", "").rstrip("."),
                "score": score,
            }
            for index, (sequence, score) in enumerate(zip(sequences, scores), start=1)
        ]
        records.append(
            {"reaction_id": reaction_id, "predictions": predictions, "error": None}
        )
    return {"records": records}


def _yield_class():
    import torch
    import torch.nn as nn
    from transformers import AutoConfig, PreTrainedModel, T5ForConditionalGeneration

    class ReactionT5Yield(PreTrainedModel):
        config_class = AutoConfig

        def __init__(self, config):
            super().__init__(config)
            self.model = T5ForConditionalGeneration.from_pretrained(
                config._name_or_path, local_files_only=True
            )
            self.model.resize_token_embeddings(config.vocab_size)
            self.fc1 = nn.Linear(config.hidden_size, config.hidden_size // 2)
            self.fc2 = nn.Linear(config.hidden_size, config.hidden_size // 2)
            self.fc3 = nn.Linear(config.hidden_size, config.hidden_size)
            self.fc4 = nn.Linear(config.hidden_size, config.hidden_size)
            self.fc5 = nn.Linear(config.hidden_size, 1)

        def forward(self, inputs):
            encoder = self.model.encoder(**inputs)[0]
            decoder_ids = torch.full(
                (inputs["input_ids"].size(0), 1),
                self.config.decoder_start_token_id,
                dtype=torch.long,
                device=inputs["input_ids"].device,
            )
            decoder = self.model.decoder(
                input_ids=decoder_ids, encoder_hidden_states=encoder
            )[0]
            output1 = self.fc1(decoder.reshape(-1, self.config.hidden_size))
            output2 = self.fc2(encoder[:, 0, :].reshape(-1, self.config.hidden_size))
            return self.fc5(self.fc4(self.fc3(torch.hstack((output1, output2))))) * 100

    return ReactionT5Yield


def _predict_yields(request: Mapping[str, Any]) -> dict[str, Any]:
    import torch
    from rdkit import Chem
    from transformers import AutoTokenizer

    rows = _array(request["inputs"], "inputs")
    device = _device(request["options"].get("device"))
    input_max_length = request["options"].get("input_max_length", 400)
    if (
        isinstance(input_max_length, bool)
        or not isinstance(input_max_length, int)
        or not 32 <= input_max_length <= 1024
    ):
        raise RequestError(
            "invalid_request", "input_max_length must be between 32 and 1024"
        )
    location = _require_model_location(request)
    reaction_t5_yield = _yield_class()
    with contextlib.redirect_stdout(sys.stderr):
        tokenizer = AutoTokenizer.from_pretrained(location, local_files_only=True)
        model = (
            reaction_t5_yield.from_pretrained(location, local_files_only=True)
            .to(device)
            .eval()
        )
    records = []
    for row in rows:

        def canonical_mixture(value: Any, field: str, *, blank: bool = False) -> str:
            raw = str(value or "").strip()
            if not raw and blank:
                return " "
            raw = _text(raw, field)
            components = []
            for component in raw.split("."):
                molecule = Chem.MolFromSmiles(component)
                if molecule is None:
                    raise RequestError(
                        "invalid_request", f"{field} contains invalid SMILES"
                    )
                components.append(Chem.MolToSmiles(molecule, canonical=True))
            return ".".join(sorted(components))

        text = (
            f"REACTANT:{canonical_mixture(row.get('reactants'), 'reactants')}"
            f"REAGENT:{canonical_mixture(row.get('reagents'), 'reagents', blank=True)}"
            f"PRODUCT:{canonical_mixture(row.get('product'), 'product')}"
        )
        inputs = tokenizer(
            [text],
            add_special_tokens=True,
            max_length=input_max_length,
            padding="max_length",
            truncation=True,
            return_attention_mask=True,
            return_tensors="pt",
        )
        inputs = {key: value.to(device) for key, value in inputs.items()}
        with torch.inference_mode():
            predicted = float(model(inputs).detach().cpu().reshape(-1)[0].item())
        records.append(
            {
                "reaction_id": _text(row.get("reaction_id"), "reaction_id"),
                "predicted_yield_percent": predicted,
                "interval_lower": None,
                "interval_upper": None,
                "domain_status": "screening_only",
            }
        )
    return {"records": records}


def _recommend_conditions(request: Mapping[str, Any]) -> dict[str, Any]:
    rows = _array(request["inputs"], "inputs")
    repository = request.get("repository_dir")
    options = request["options"]
    config_path = options.get("config_path")
    workspace = options.get("workspace_dir")
    if (
        not isinstance(repository, str)
        or not Path(repository).is_dir()
        or not isinstance(config_path, str)
        or not Path(config_path).is_file()
    ):
        raise RequestError(
            "checkpoint_required", "Parrot repository and config_path must exist"
        )
    if not isinstance(workspace, str) or not Path(workspace).is_dir():
        raise RequestError(
            "invalid_request", "workspace_dir must be an existing external workspace"
        )
    gpu = options.get("gpu", -1)
    if isinstance(gpu, bool) or not isinstance(gpu, int):
        raise RequestError("invalid_request", "gpu must be an integer")
    with tempfile.TemporaryDirectory(prefix="parrot-wire-", dir=workspace) as temporary:
        temporary_path = Path(temporary)
        input_path = temporary_path / "reactions.txt"
        output_path = temporary_path / "conditions.csv"
        input_path.write_text(
            "\n".join(
                _text(row.get("reaction_smiles"), "reaction_smiles") for row in rows
            )
            + "\n",
            encoding="utf-8",
        )
        command = [
            sys.executable,
            str(Path(repository) / "inference.py"),
            "--config_path",
            config_path,
            "--input_path",
            str(input_path),
            "--output_path",
            str(output_path),
            "--num_workers",
            "1",
            "--inference_batch_size",
            "8",
            "--gpu",
            str(gpu),
        ]
        completed = subprocess.run(
            command, cwd=repository, stdout=sys.stderr, stderr=sys.stderr, check=False
        )
        if completed.returncode or not output_path.is_file():
            raise RequestError(
                "inference_failed",
                f"Parrot inference exited with status {completed.returncode}",
            )
        with output_path.open(newline="", encoding="utf-8-sig") as handle:
            table = list(csv.DictReader(handle))
    groups: list[list[dict[str, str]]] = []
    current: list[dict[str, str]] = []
    for output in table:
        if output.get("rxn_smiles"):
            if current:
                groups.append(current)
            current = [output]
        elif current:
            current.append(output)
        else:
            raise RequestError(
                "inference_failed", "Parrot output starts with an unowned beam row"
            )
    if current:
        groups.append(current)
    if len(groups) != len(rows):
        raise RequestError(
            "inference_failed", "Parrot output reaction groups do not match inputs"
        )
    aliases = {
        "catalyst1": ("catalyst1", "catalyst"),
        "solvent1": ("solvent1", "solvent_1"),
        "solvent2": ("solvent2", "solvent_2"),
        "reagent1": ("reagent1", "reagent_1"),
        "reagent2": ("reagent2", "reagent_2"),
    }
    records = []
    for source, outputs in zip(rows, groups):
        predictions = []
        for ordinal, output in enumerate(outputs, start=1):
            conditions = {
                slot: next(
                    (
                        output.get(name)
                        for name in names
                        if output.get(name) not in (None, "")
                    ),
                    None,
                )
                for slot, names in aliases.items()
            }
            rank_text = str(output.get("top-k") or "")
            try:
                rank = int(rank_text.removeprefix("top-"))
            except ValueError:
                rank = ordinal
            try:
                score = float(output["scores"])
                if not math.isfinite(score):
                    score = None
            except (KeyError, TypeError, ValueError):
                score = None
            predictions.append({"rank": rank, "score": score, "conditions": conditions})
        records.append(
            {
                "reaction_id": _text(source.get("reaction_id"), "reaction_id"),
                "predictions": predictions,
                "raw_output": outputs,
                "error": None,
            }
        )
    return {"records": records}


def _handle(request: Mapping[str, Any]) -> dict[str, Any]:
    operation = request["operation"]
    if operation == "capabilities":
        return {"operations": sorted(OPERATIONS[request["backend"]])}
    if operation == "map_reactions":
        return _map_reactions(request)
    if operation == "plan_routes":
        return _plan_routes(request)
    if operation == "predict_products":
        return _predict_products(request)
    if operation == "predict_yields":
        return _predict_yields(request)
    return _recommend_conditions(request)


def _response(
    request: Mapping[str, Any],
    *,
    ok: bool,
    started: float,
    result: Any = None,
    error: Any = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "request_id": request.get("request_id", "unknown"),
        "backend": request.get("backend", "unknown"),
        "operation": request.get("operation", "unknown"),
        "ok": ok,
        "runtime": _runtime(),
        "warnings": [],
        "elapsed_seconds": round(time.monotonic() - started, 6),
    }
    if ok:
        payload.update({"result": result, "model_manifest": request["model_manifest"]})
    else:
        payload["error"] = error
    return payload


def _reserve_stdout() -> int | None:
    try:
        reserved = os.dup(1)
        os.set_inheritable(reserved, False)
        os.dup2(2, 1)
        return reserved
    except OSError:
        return None


def main() -> int:
    reserved = _reserve_stdout()
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    started = time.monotonic()
    request: dict[str, Any] = {
        "request_id": "unknown",
        "backend": "unknown",
        "operation": "unknown",
    }
    try:
        if len(raw) > MAX_REQUEST_BYTES:
            raise RequestError("request_too_large", "request exceeded 8 MiB")
        request = _validate(json.loads(raw.decode("utf-8")))
        response = _response(request, ok=True, started=started, result=_handle(request))
    except (json.JSONDecodeError, UnicodeDecodeError):
        response = _response(
            request,
            ok=False,
            started=started,
            error={
                "code": "invalid_json",
                "message": "stdin did not contain one JSON object",
                "retryable": False,
            },
        )
    except RequestError as exc:
        response = _response(
            request,
            ok=False,
            started=started,
            error={
                "code": exc.code,
                "message": _public_error(exc),
                "retryable": exc.retryable,
            },
        )
    except Exception as exc:
        response = _response(
            request,
            ok=False,
            started=started,
            error={
                "code": "worker_failure",
                "message": f"{type(exc).__name__}: {_public_error(exc)}",
                "retryable": False,
            },
        )
    encoded = (json.dumps(response, sort_keys=True) + "\n").encode("utf-8")
    if reserved is None:
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
    else:
        os.write(reserved, encoded)
        os.close(reserved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
