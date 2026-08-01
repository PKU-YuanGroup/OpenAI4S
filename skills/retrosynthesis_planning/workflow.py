"""Higher-level orchestration for retrosynthesis search and route review."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .kernel import build_aizynth_command, normalize_routes, rank_routes
from .route_review import deduplicate_routes, route_signature, select_diverse_routes
from .structural_audit import audit_route_structure, audit_routes


def _clean_names(values: Iterable[str], *, field_name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{field_name} must be an iterable of strings, not a string")
    cleaned: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise TypeError(f"{field_name} entries must be strings")
        item = value.strip()
        if not item:
            raise ValueError(f"{field_name} entries must not be empty")
        if item not in cleaned:
            cleaned.append(item)
    return tuple(cleaned)


def _clean_args(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError("extra_args must be an iterable of strings, not a string")
    cleaned: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise TypeError("extra_args entries must be strings")
        item = value.strip()
        if not item:
            raise ValueError("extra_args entries must not be empty")
        cleaned.append(item)
    return tuple(cleaned)


def _clean_optional_path(value: str | Path | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    item = str(value).strip()
    if not item:
        raise ValueError(f"{field_name} must not be empty")
    return str(Path(item).expanduser())


@dataclass(frozen=True, slots=True)
class AiZynthSearchSpec:
    """Typed command-line options supported by ``aizynthcli``.

    Algorithm, depth, rewards and bond constraints remain in AiZynthFinder's
    configuration file.  This class exposes only documented CLI switches.
    """

    policies: tuple[str, ...] = field(default_factory=tuple)
    filters: tuple[str, ...] = field(default_factory=tuple)
    stocks: tuple[str, ...] = field(default_factory=tuple)
    cluster: bool = False
    nproc: int | None = None
    checkpoint_path: str | None = None
    log_to_file: bool = False
    post_processing: tuple[str, ...] = field(default_factory=tuple)
    pre_processing: str | None = None
    extra_args: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "policies", _clean_names(self.policies, field_name="policies")
        )
        object.__setattr__(
            self, "filters", _clean_names(self.filters, field_name="filters")
        )
        object.__setattr__(
            self, "stocks", _clean_names(self.stocks, field_name="stocks")
        )
        object.__setattr__(
            self,
            "post_processing",
            _clean_names(self.post_processing, field_name="post_processing"),
        )
        object.__setattr__(self, "extra_args", _clean_args(self.extra_args))
        object.__setattr__(
            self,
            "checkpoint_path",
            _clean_optional_path(self.checkpoint_path, field_name="checkpoint_path"),
        )
        for field_name in ("cluster", "log_to_file"):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"{field_name} must be a boolean")
        if self.pre_processing is not None:
            pre = str(self.pre_processing).strip()
            if not pre:
                raise ValueError("pre_processing must not be empty")
            object.__setattr__(self, "pre_processing", pre)
        if self.nproc is not None:
            if isinstance(self.nproc, bool) or not isinstance(self.nproc, int):
                raise TypeError("nproc must be a positive integer or None")
            if self.nproc < 1:
                raise ValueError("nproc must be at least 1")

    def to_cli_args(self) -> list[str]:
        """Return validated ``aizynthcli`` arguments in deterministic order."""
        args: list[str] = []
        if self.policies:
            args.extend(["--policy", *self.policies])
        if self.filters:
            args.extend(["--filter", *self.filters])
        if self.stocks:
            args.extend(["--stocks", *self.stocks])
        if self.cluster:
            args.append("--cluster")
        if self.nproc is not None:
            args.extend(["--nproc", str(self.nproc)])
        if self.checkpoint_path:
            args.extend(["--checkpoint", self.checkpoint_path])
        if self.log_to_file:
            args.append("--log_to_file")
        if self.post_processing:
            args.extend(["--post_processing", *self.post_processing])
        if self.pre_processing:
            args.extend(["--pre_processing", self.pre_processing])
        args.extend(self.extra_args)
        return args


def build_aizynth_search_command(
    smiles: str,
    config_path: str,
    *,
    output_path: str | None = None,
    conda_env: str | None = None,
    search: AiZynthSearchSpec | None = None,
) -> list[str]:
    """Build a safe ``aizynthcli`` command from a typed search specification."""
    spec = search or AiZynthSearchSpec()
    if not isinstance(spec, AiZynthSearchSpec):
        raise TypeError("search must be an AiZynthSearchSpec or None")
    return build_aizynth_command(
        smiles,
        config_path=config_path,
        output_path=output_path,
        conda_env=conda_env,
        extra_args=spec.to_cli_args(),
    )


def prepare_routes(
    payload: Any,
    *,
    reaction_evidence: dict[str, Any] | list[dict[str, Any]] | None = None,
    constraints: dict[str, Any] | None = None,
    decision_weights: dict[str, float] | None = None,
    max_routes: int = 10,
    similarity_threshold: float = 0.85,
) -> list[dict[str, Any]]:
    """Normalize, rank, de-duplicate, and diversity-select backend routes."""
    ranked = rank_routes(
        normalize_routes(payload),
        reaction_evidence=reaction_evidence,
        constraints=constraints,
        decision_weights=decision_weights,
    )
    return select_diverse_routes(
        deduplicate_routes(ranked),
        max_routes=max_routes,
        similarity_threshold=similarity_threshold,
    )


__all__ = [
    "AiZynthSearchSpec",
    "audit_route_structure",
    "audit_routes",
    "build_aizynth_search_command",
    "deduplicate_routes",
    "prepare_routes",
    "route_signature",
    "select_diverse_routes",
]
