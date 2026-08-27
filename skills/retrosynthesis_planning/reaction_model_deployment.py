"""Pure-stdlib environment plans and artifact manifests for reaction models."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

CHUNK_SIZE = 1024 * 1024


class ReactionModelDeploymentError(RuntimeError):
    """Raised when an external environment or artifact fails verification."""


@dataclass(frozen=True, slots=True)
class EnvironmentSpec:
    name: str
    python: str
    packages: tuple[str, ...]
    source_url: str
    source_revision: str
    model: str
    training_dataset: str
    code_license: str
    checkpoint_license: str
    requires_terms_review: bool = False

    def prefix(self, root: str | Path) -> Path:
        return Path(root).resolve() / "envs" / self.name

    def cache(self, root: str | Path) -> Path:
        return Path(root).resolve() / "cache" / self.name

    def install_commands(self, root: str | Path) -> list[list[str]]:
        prefix = self.prefix(root)
        commands = [
            [
                "conda",
                "create",
                "--prefix",
                str(prefix),
                f"python={self.python}",
                "pip",
                "-y",
            ]
        ]
        packages = list(self.packages)
        torch_packages = [item for item in packages if item.startswith("torch==")]
        if torch_packages:
            commands.append(
                [
                    "conda",
                    "run",
                    "--prefix",
                    str(prefix),
                    "python",
                    "-m",
                    "pip",
                    "install",
                    "--no-compile",
                    *torch_packages,
                    "--index-url",
                    "https://download.pytorch.org/whl/cpu",
                ]
            )
            packages = [item for item in packages if item not in torch_packages]
        if packages:
            commands.append(
                [
                    "conda",
                    "run",
                    "--prefix",
                    str(prefix),
                    "python",
                    "-m",
                    "pip",
                    "install",
                    "--no-compile",
                    *packages,
                ]
            )
        return commands

    def to_dict(self, root: str | Path) -> dict[str, Any]:
        return {
            "name": self.name,
            "python": self.python,
            "packages": list(self.packages),
            "source_url": self.source_url,
            "source_revision": self.source_revision,
            "model": self.model,
            "training_dataset": self.training_dataset,
            "code_license": self.code_license,
            "checkpoint_license": self.checkpoint_license,
            "requires_terms_review": self.requires_terms_review,
            "environment_prefix": str(self.prefix(root)),
            "cache_dir": str(self.cache(root)),
            "install_commands": self.install_commands(root),
            "artifact_commands": artifact_commands(self.name, root),
        }


ENVIRONMENTS = {
    spec.name: spec
    for spec in (
        EnvironmentSpec(
            name="aizynthfinder-4.4.1",
            python="3.11",
            packages=("aizynthfinder[all]==4.4.1",),
            source_url="https://github.com/MolecularAI/aizynthfinder",
            source_revision="9859f5bc6c04c342b828aff20001504c238d7ac1",
            model="AiZynthFinder",
            training_dataset="public policy and stock bundle; snapshot required",
            code_license="MIT",
            checkpoint_license="review-required",
            requires_terms_review=True,
        ),
        EnvironmentSpec(
            name="rxnmapper-0.4.3",
            python="3.11",
            packages=(
                "torch==2.4.1",
                "setuptools==75.1.0",
                "transformers==4.40.2",
                "tokenizers==0.19.1",
                "rxnmapper[rdkit]==0.4.3",
                "rxn-chem-utils==1.6.0",
                "rdkit==2024.3.5",
                "numpy==1.26.4",
                "pandas==2.2.3",
                "scipy==1.14.1",
            ),
            source_url="https://github.com/rxn4chemistry/rxnmapper",
            source_revision="640d9ddd304d28eb338482f4e9c2dd6b1a25de7c",
            model="RXNMapper",
            training_dataset="RXNMapper unsupervised reaction corpus",
            code_license="MIT",
            checkpoint_license="MIT",
        ),
        EnvironmentSpec(
            name="reactiont5v2",
            python="3.11",
            packages=(
                "torch==2.4.1",
                "transformers==4.40.2",
                "tokenizers==0.19.1",
                "sentencepiece==0.2.0",
                "safetensors==0.4.5",
                "rdkit==2024.3.5",
            ),
            source_url="https://github.com/sagawatatsuya/ReactionT5v2",
            source_revision="76eb08068e10fe255cae5d563a91e1c1e9abac54",
            model="ReactionT5v2",
            training_dataset="Open Reaction Database with declared benchmark exclusions",
            code_license="MIT",
            checkpoint_license="MIT",
        ),
        EnvironmentSpec(
            name="parrot-0fb2325",
            python="3.8",
            packages=(
                "torch==1.13.1",
                "transformers==4.18.0",
                "simpletransformers==0.63.6",
                "rdkit-pypi==2022.9.5",
                "pandas==1.5.3",
                "gdown==5.2.0",
                "pyyaml==6.0.2",
            ),
            source_url="https://github.com/wangxr0526/Parrot",
            source_revision="0fb2325567e21011589641544e32427c8244e2a9",
            model="Parrot",
            training_dataset="USPTO-Condition or separately licensed Reaxys configuration",
            code_license="MIT",
            checkpoint_license="review-required",
            requires_terms_review=True,
        ),
        EnvironmentSpec(
            name="parrot-hf-b9ef6049",
            python="3.8",
            packages=(
                "torch==1.13.1",
                "transformers==4.18.0",
                "simpletransformers==0.63.6",
                "rdkit-pypi==2022.9.5",
                "numpy==1.21.5",
                "pandas==1.3.5",
                "scipy==1.4.1",
                "scikit-learn==0.23.1",
                "rxnfp==0.1.0",
                "pyyaml==6.0.2",
            ),
            source_url=(
                "https://huggingface.co/xiaoruiwang/" "ChemEnzyRetroPlanner_metadata"
            ),
            source_revision="b9ef6049d341bfc62d835f09ad6ce33b6f86b047",
            model="Parrot",
            training_dataset=(
                "USPTO-Condition categorical labels; temperature unsupported"
            ),
            code_license="MIT",
            checkpoint_license="MIT",
        ),
    )
}

UPSTREAM_DISTRIBUTIONS = {
    "aizynthfinder-4.4.1-wheel": {
        "filename": "aizynthfinder-4.4.1-py3-none-any.whl",
        "bytes": 133_909,
        "sha256": "e1259cfc45610b4801d38ed512d31290aa13204bfd5df9fe601fe845ddcbe3d6",
        "url": "https://pypi.org/project/aizynthfinder/4.4.1/",
    },
    "rxnmapper-0.4.3-wheel": {
        "filename": "rxnmapper-0.4.3-py3-none-any.whl",
        "bytes": 3_007_424,
        "sha256": "27876a4286881aafd286fd6f24a6a56a4ca6ba22d68e035a0ea120106c541ba5",
        "url": "https://pypi.org/project/rxnmapper/0.4.3/",
    },
}

HF_REVISIONS = {
    "ReactionT5v2-forward": {
        "repository": "sagawa/ReactionT5v2-forward",
        "revision": "933114058cb2604dc1bf536dbebdfcefbe83d4fc",
        "training_dataset": "ORD; USPTO_MIT test reactions excluded as declared by author",
        "license": "MIT",
    },
    "ReactionT5v2-yield": {
        "repository": "sagawa/ReactionT5v2-yield",
        "revision": "f0658bfd360bceaaf560f11b850781c50221fe0b",
        "training_dataset": "ORD; Buchwald-Hartwig C-N test reactions excluded as declared by author",
        "license": "MIT",
    },
}

PARROT_HF_ARTIFACTS = {
    "repository": "xiaoruiwang/ChemEnzyRetroPlanner_metadata",
    "revision": "b9ef6049d341bfc62d835f09ad6ce33b6f86b047",
    "license": "MIT",
    "files": {
        "USPTO_condition.mar": {
            "bytes": 101_022_989,
            "sha256": (
                "4418693a91a7a3b5f2aa101a39d58702" "b154e58901ddbf1ac94edc4c28de8e7d"
            ),
        },
        "condition_predictor_metadata.zip": {
            "bytes": 143_606_133,
            "sha256": (
                "dfdf7fff11fe2d52af49146b1080dd63" "04ddd2b51665907fa759ffd4c5fca820"
            ),
        },
    },
}


def artifact_commands(environment: str, root: str | Path) -> list[list[str]]:
    """Return reviewable network commands; never execute them implicitly."""

    base = Path(root).resolve()
    prefix = ENVIRONMENTS[environment].prefix(base)
    artifacts = base / "artifacts"
    if environment == "aizynthfinder-4.4.1":
        return [
            [
                "conda",
                "run",
                "--prefix",
                str(prefix),
                "download_public_data",
                str(artifacts / environment),
            ]
        ]
    if environment == "reactiont5v2":
        return [
            [
                "conda",
                "run",
                "--prefix",
                str(prefix),
                "huggingface-cli",
                "download",
                details["repository"],
                "--revision",
                details["revision"],
                "--local-dir",
                str(artifacts / model),
            ]
            for model, details in HF_REVISIONS.items()
        ]
    if environment == "parrot-0fb2325":
        repository = artifacts / "Parrot"
        return [
            ["git", "clone", ENVIRONMENTS[environment].source_url, str(repository)],
            [
                "git",
                "-C",
                str(repository),
                "checkout",
                "--detach",
                ENVIRONMENTS[environment].source_revision,
            ],
        ]
    if environment == "parrot-hf-b9ef6049":
        destination = artifacts / environment
        return [
            [
                "conda",
                "run",
                "--prefix",
                str(prefix),
                "huggingface-cli",
                "download",
                PARROT_HF_ARTIFACTS["repository"],
                *PARROT_HF_ARTIFACTS["files"],
                "--revision",
                PARROT_HF_ARTIFACTS["revision"],
                "--local-dir",
                str(destination),
            ]
        ]
    return []


def _sha256_file(path: Path) -> tuple[int, str]:
    size = 0
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def snapshot_artifacts(
    paths: Iterable[str | Path], *, base: str | Path
) -> dict[str, Any]:
    root = Path(base).resolve()
    files: list[dict[str, Any]] = []
    for raw in paths:
        path = Path(raw).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ReactionModelDeploymentError(
                f"artifact {path} is outside snapshot base {root}"
            ) from exc
        candidates = (
            sorted(item for item in path.rglob("*") if item.is_file())
            if path.is_dir()
            else [path]
        )
        for candidate in candidates:
            relative_file = candidate.relative_to(root).as_posix()
            size, digest = _sha256_file(candidate)
            files.append({"path": relative_file, "bytes": size, "sha256": digest})
    files.sort(key=lambda item: item["path"])
    if not files:
        raise ReactionModelDeploymentError(
            "artifact snapshot must contain at least one file"
        )
    payload: dict[str, Any] = {"schema_version": 1, "files": files}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["snapshot_sha256"] = hashlib.sha256(encoded).hexdigest()
    return payload


def verify_artifact_snapshot(
    manifest: dict[str, Any], *, base: str | Path
) -> dict[str, Any]:
    if (
        set(manifest) != {"schema_version", "files", "snapshot_sha256"}
        or manifest.get("schema_version") != 1
    ):
        raise ReactionModelDeploymentError("unsupported artifact snapshot schema")
    unhashed = {"schema_version": 1, "files": manifest["files"]}
    fingerprint = hashlib.sha256(
        json.dumps(unhashed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if fingerprint != manifest["snapshot_sha256"]:
        raise ReactionModelDeploymentError("artifact snapshot fingerprint mismatch")
    root = Path(base).resolve()
    failures: list[str] = []
    for entry in manifest["files"]:
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ReactionModelDeploymentError("artifact snapshot contains unsafe path")
        path = root / relative
        if not path.is_file():
            failures.append(f"missing:{entry['path']}")
            continue
        size, digest = _sha256_file(path)
        if size != entry["bytes"] or digest != entry["sha256"]:
            failures.append(f"changed:{entry['path']}")
    return {
        "ok": not failures,
        "failures": failures,
        "verified_files": len(manifest["files"]),
        "snapshot_sha256": fingerprint,
    }


def model_manifest(
    environment: str,
    *,
    checkpoint_id: str,
    checkpoint_sha256: str,
    source_url: str | None = None,
    checkpoint_license: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spec = ENVIRONMENTS[environment]
    digest = checkpoint_sha256.strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ReactionModelDeploymentError(
            "checkpoint_sha256 must be a 64-character SHA-256"
        )
    return {
        "schema_version": 1,
        "provider": spec.source_url.split("/")[2],
        "model": spec.model,
        "model_version": spec.source_revision,
        "checkpoint_id": checkpoint_id,
        "checkpoint_sha256": digest,
        "training_dataset": spec.training_dataset,
        "code_license": spec.code_license,
        "checkpoint_license": checkpoint_license or spec.checkpoint_license,
        "source_url": source_url or spec.source_url,
        "metadata": {
            "environment": spec.name,
            "source_revision": spec.source_revision,
            **(metadata or {}),
        },
    }


def write_json(path: str | Path, payload: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=destination.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    try:
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("environment", choices=sorted(ENVIRONMENTS))
    plan.add_argument("--root", type=Path, required=True)
    snapshot = commands.add_parser("snapshot")
    snapshot.add_argument("--base", type=Path, required=True)
    snapshot.add_argument("--output", type=Path, required=True)
    snapshot.add_argument("paths", nargs="+")
    verify = commands.add_parser("verify")
    verify.add_argument("manifest", type=Path)
    verify.add_argument("--base", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "plan":
        result = ENVIRONMENTS[args.environment].to_dict(args.root)
    elif args.command == "snapshot":
        result = snapshot_artifacts(args.paths, base=args.base)
        write_json(args.output, result)
    else:
        with args.manifest.open(encoding="utf-8") as handle:
            manifest = json.load(handle)
        result = verify_artifact_snapshot(manifest, base=args.base)
        if not result["ok"]:
            print(json.dumps(result, indent=2, sort_keys=True))
            return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ENVIRONMENTS",
    "HF_REVISIONS",
    "UPSTREAM_DISTRIBUTIONS",
    "EnvironmentSpec",
    "ReactionModelDeploymentError",
    "artifact_commands",
    "model_manifest",
    "snapshot_artifacts",
    "verify_artifact_snapshot",
]
