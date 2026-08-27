"""Inference-only CLI for the reviewed Parrot TorchServe model archive."""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import types
from pathlib import Path


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--config_path", required=True)
    parser.add_argument("--input_path", required=True)
    parser.add_argument("--output_path", required=True)
    parser.add_argument("--num_workers", type=int, default=1)
    parser.add_argument("--inference_batch_size", type=int, default=8)
    parser.add_argument("--gpu", type=int, default=-1)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    model_dir = Path(args.model_dir).resolve()
    required = {
        "model.py",
        "pytorch_model.bin",
        "USPTO_condition_alldata_idx.pkl",
    }
    missing = sorted(name for name in required if not (model_dir / name).is_file())
    if missing:
        raise RuntimeError("Parrot model archive is missing: " + ", ".join(missing))

    # W&B is training telemetry and is not needed for offline inference. The
    # archived model imports it unconditionally, so provide only the inert
    # symbol when the optional package is absent.
    if importlib.util.find_spec("wandb") is None:
        wandb = types.ModuleType("wandb")
        wandb.run = None
        sys.modules["wandb"] = wandb

    sys.path.insert(0, str(model_dir))
    import pandas as pd
    import torch
    import yaml
    from model import (
        ParrotConditionPredictionModel,
        caonicalize_rxn_smiles,
        get_output_results,
        inference_load,
    )

    with Path(args.config_path).open(encoding="utf-8") as handle:
        config = yaml.load(handle, Loader=yaml.FullLoader)
    model_args = config["model_args"]
    model_args.update(
        {
            "use_multiprocessing": False,
            "use_multiprocessing_for_evaluation": False,
            "dataloader_num_workers": 0,
            "wandb_project": None,
            "wandb_kwargs": {},
            "best_model_dir": str(model_dir),
            "output_dir": str(model_dir),
            "pretrained_path": str(model_dir),
        }
    )
    dataset_args = config["dataset_args"]
    dataset_args["dataset_root"] = str(model_dir)
    model_args["use_temperature"] = bool(dataset_args.get("use_temperature", False))
    if model_args["use_temperature"]:
        raise RuntimeError("The reviewed USPTO checkpoint does not support temperature")

    condition_mapping = inference_load(**dataset_args)
    model_args["decoder_args"].update(
        {
            "tgt_vocab_size": len(condition_mapping[0]),
            "condition_label_mapping": condition_mapping,
        }
    )
    use_cuda = args.gpu >= 0 and torch.cuda.is_available()
    model = ParrotConditionPredictionModel(
        "bert",
        str(model_dir),
        args=model_args,
        use_cuda=use_cuda,
        cuda_device=args.gpu if use_cuda else -1,
    )

    with Path(args.input_path).open(encoding="utf-8") as handle:
        reactions = [line.strip() for line in handle if line.strip()]
    table = pd.DataFrame({"text": reactions, "labels": [[0] * 7 for _ in reactions]})
    table["text"] = table.text.apply(caonicalize_rxn_smiles)
    if (table["text"] == "").any():
        raise ValueError("At least one reaction SMILES could not be canonicalized")

    predicted_conditions, predicted_temperatures = model.condition_beam_search(
        table,
        output_dir=str(model_dir),
        beam=config["inference_args"]["beam"],
        test_batch_size=args.inference_batch_size,
        calculate_topk_accuracy=False,
    )
    output = get_output_results(
        table.text.tolist(),
        predicted_conditions,
        predicted_temperatures,
        output_dataframe=True,
    )
    output.to_csv(os.path.abspath(args.output_path), index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
