"""ClusterProfile: the one file that knows partition and QoS names (M3a-7).

`<data_dir>/cluster.toml` maps a profile name a scientist can say
(`gpu-interactive`) onto the scheduler settings an administrator controls
(`partition = "gpu"`, `qos = "interactive"`). Decision D5 puts that mapping
*here and nowhere else*: the user never types a partition, the agent never
sees one, and the orchestration core cannot name one — INV-2's leak guard
checks exactly that, and this module lives inside the backend subpackage
precisely so it is allowed to do this job.

Parsed with `tomllib` where it exists (stdlib 3.11+) and with a deliberately
tiny fallback where it does not. The fallback is not optional politeness:
`requires-python` is 3.10, CI's matrix runs 3.10, and a tomllib-only reader
would have made the entire cluster feature dead on the floor version — the
one an administrator is most likely to be running on an older distribution.
A third-party `tomli` is not an option either; the core is zero-dependency.

The fallback understands exactly what this file needs — `[table]` and
`[table.sub]` headers, `key = "string"`, `key = 123`, `key = true`, and
`#` comments — and **refuses** anything else with the line number. That
refusal is the whole design: a parser that guesses at an array or a
multiline string would hand back a config that looks right and schedules
onto the wrong queue. Failing to read is recoverable; misreading is not.

A malformed file is an error, not a silent empty config. An operator who
mistypes a profile should learn it from a refusal that names the file, not
from a job that never schedules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai4s.orchestration.models import ResourceProfile

#: Where an operator puts it, relative to the data dir.
CLUSTER_CONFIG_FILENAME = "cluster.toml"

#: Shipped as documentation and as the shape `openai4s cluster` prints when
#: nothing is configured. Deliberately three profiles, because the trio is
#: the actual decision a user faces: cheap and interactive, expensive and
#: interactive, or expensive and batched.
EXAMPLE_CLUSTER_TOML = """\
# OpenAI4S cluster profiles. This file is the ONLY place partition and QoS
# names appear: users and agents name a profile, never a queue (decision D5).
#
# Copy to <data_dir>/cluster.toml and edit for your site.

[cluster]
name = "lab-cluster"
# Prefix for every job this daemon submits, so `squeue` is readable and an
# operator can find our jobs without knowing our internals.
job_name_prefix = "openai4s"

[profiles.cpu-interactive]
partition = "cpu"
cpus = 4
memory_mb = 8192
walltime_s = 14400          # 4h
description = "Interactive analysis without a GPU."

[profiles.gpu-interactive]
partition = "gpu"
qos = "interactive"
cpus = 8
memory_mb = 32768
gpus = 1
walltime_s = 14400
description = "Interactive work that needs a GPU."

[profiles.gpu-batch]
partition = "gpu"
qos = "normal"
cpus = 16
memory_mb = 65536
gpus = 1
walltime_s = 172800         # 48h
description = "Long unattended runs."
"""


class ClusterConfigError(ValueError):
    """cluster.toml exists but cannot be used, with the reason and the path."""


@dataclass(frozen=True)
class ClusterProfile:
    """One named profile: what a user asks for, plus how to ask the scheduler.

    `resources` is the part the orchestration core may see; `partition` and
    `qos` never leave this subpackage — the broker consumes them when it
    builds argv and nothing else reads them.
    """

    name: str
    resources: ResourceProfile
    partition: str | None = None
    qos: str | None = None
    description: str = ""

    def public(self) -> dict[str, Any]:
        """The admin-readable view. Scheduler settings are summarized as a
        boolean rather than echoed: the read-only route exists so an admin
        can see which profiles are available, and a partition name in a JSON
        body is the same leak by a slower route."""
        return {
            "name": self.name,
            "description": self.description,
            "cpus": self.resources.cpus,
            "memory_mb": self.resources.memory_mb,
            "gpus": self.resources.gpus,
            "walltime_s": self.resources.walltime_s,
            "nodes": self.resources.nodes,
        }


@dataclass(frozen=True)
class ClusterConfig:
    """The whole file: a name, a job prefix, and the profiles."""

    name: str = ""
    job_name_prefix: str = "openai4s"
    profiles: dict[str, ClusterProfile] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.profiles is None:
            object.__setattr__(self, "profiles", {})

    @property
    def configured(self) -> bool:
        return bool(self.profiles)

    def profile(self, name: str) -> ClusterProfile:
        try:
            return self.profiles[name]
        except KeyError:
            known = ", ".join(sorted(self.profiles)) or "(none configured)"
            raise ClusterConfigError(
                f"unknown profile {name!r}; configured profiles: {known}"
            ) from None

    def public(self) -> dict[str, Any]:
        return {
            "cluster": self.name,
            "configured": self.configured,
            "profiles": [p.public() for p in self.profiles.values()],
        }


def _require_int(table: dict, key: str, default: int, *, where: str) -> int:
    value = table.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ClusterConfigError(f"{where}.{key} must be an integer, got {value!r}")
    return value


def _optional_str(table: dict, key: str, *, where: str) -> str | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ClusterConfigError(f"{where}.{key} must be a non-empty string")
    return value.strip()


def _parse_scalar(raw: str, *, where: str) -> Any:
    """One TOML value, from the subset this file uses.

    Anything outside that subset raises. Arrays, inline tables, floats,
    dates and multiline strings are all *refused* rather than approximated:
    a config that parses into something subtly different from what the
    operator wrote is worse than one that will not parse at all.
    """
    value = raw.strip()
    if not value:
        raise ClusterConfigError(f"{where}: missing value")
    if value[0] in "\"'":
        quote = value[0]
        if len(value) < 2 or value[-1] != quote:
            raise ClusterConfigError(f"{where}: unterminated string {value!r}")
        inner = value[1:-1]
        if quote in inner:
            raise ClusterConfigError(f"{where}: unsupported quoting in {value!r}")
        return inner
    if value in ("true", "false"):
        return value == "true"
    if re.fullmatch(r"[+-]?\d+", value):
        return int(value)
    if re.fullmatch(r"[+-]?\d[\d_]*", value):
        return int(value.replace("_", ""))
    raise ClusterConfigError(
        f"{where}: this reader supports strings, integers and booleans only "
        f"(got {value!r}). Arrays, floats and dates are refused rather than "
        f"guessed at."
    )


def _parse_toml_subset(text: str, *, source: str) -> dict[str, Any]:
    """The 3.10 fallback. See the module docstring for the supported subset."""
    data: dict[str, Any] = {}
    current: dict[str, Any] = data
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        where = f"{source}:{lineno}"
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # A '#' inside a quoted value is not a comment; only strip one that
        # starts a bare token. Cheap to get wrong, so it is explicit.
        if line.startswith("["):
            if not line.endswith("]"):
                raise ClusterConfigError(f"{where}: malformed table header {line!r}")
            if line.startswith("[["):
                raise ClusterConfigError(
                    f"{where}: array-of-tables is not supported by this reader"
                )
            path = [part.strip().strip('"') for part in line[1:-1].split(".")]
            if not all(path):
                raise ClusterConfigError(f"{where}: malformed table header {line!r}")
            current = data
            for part in path:
                nested = current.get(part)
                if nested is None:
                    nested = {}
                    current[part] = nested
                elif not isinstance(nested, dict):
                    raise ClusterConfigError(f"{where}: {part!r} is not a table")
                current = nested
            continue
        if "=" not in line:
            raise ClusterConfigError(f"{where}: expected `key = value`, got {line!r}")
        key, _, value_text = line.partition("=")
        key = key.strip().strip('"')
        if not key:
            raise ClusterConfigError(f"{where}: empty key")
        value_text = value_text.strip()
        if not value_text.startswith(("'", '"')):
            value_text = value_text.split("#", 1)[0].strip()
        current[key] = _parse_scalar(value_text, where=where)
    return data


def parse_cluster_config(text: str, *, source: str = "cluster.toml") -> ClusterConfig:
    """Parse the TOML text. Raises ClusterConfigError with the source named."""
    try:
        import tomllib
    except ModuleNotFoundError:
        data = _parse_toml_subset(text, source=source)
    else:
        try:
            data = tomllib.loads(text)
        except Exception as exc:  # tomllib.TOMLDecodeError, but be liberal
            raise ClusterConfigError(f"{source} is not valid TOML: {exc}") from exc

    cluster = data.get("cluster") or {}
    if not isinstance(cluster, dict):
        raise ClusterConfigError(f"{source}: [cluster] must be a table")
    profiles_table = data.get("profiles") or {}
    if not isinstance(profiles_table, dict):
        raise ClusterConfigError(f"{source}: [profiles] must be a table")

    profiles: dict[str, ClusterProfile] = {}
    for name, raw in profiles_table.items():
        where = f"{source}: profiles.{name}"
        if not isinstance(raw, dict):
            raise ClusterConfigError(f"{where} must be a table")
        try:
            resources = ResourceProfile(
                name=str(name),
                cpus=_require_int(raw, "cpus", 1, where=where),
                memory_mb=_require_int(raw, "memory_mb", 4096, where=where),
                gpus=_require_int(raw, "gpus", 0, where=where),
                walltime_s=_require_int(raw, "walltime_s", 3600, where=where),
                nodes=_require_int(raw, "nodes", 1, where=where),
            )
        except ValueError as exc:
            # ResourceProfile's own validation, re-raised against the file so
            # the operator learns which profile and which key.
            raise ClusterConfigError(f"{where}: {exc}") from exc
        profiles[str(name)] = ClusterProfile(
            name=str(name),
            resources=resources,
            partition=_optional_str(raw, "partition", where=where),
            qos=_optional_str(raw, "qos", where=where),
            description=str(raw.get("description") or ""),
        )

    return ClusterConfig(
        name=str(cluster.get("name") or ""),
        job_name_prefix=str(cluster.get("job_name_prefix") or "openai4s"),
        profiles=profiles,
    )


def load_cluster_config(data_dir: Path | str) -> ClusterConfig:
    """Read `<data_dir>/cluster.toml`, or return an unconfigured config.

    An absent file is not an error — most installs have no cluster — but a
    file that exists and is wrong IS one. Those two cases are the whole
    reason this returns rather than raising on the common path.
    """
    path = Path(data_dir).expanduser() / CLUSTER_CONFIG_FILENAME
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ClusterConfig()
    except OSError as exc:
        raise ClusterConfigError(f"cannot read {path}: {exc}") from exc
    return parse_cluster_config(text, source=str(path))


__all__ = [
    "CLUSTER_CONFIG_FILENAME",
    "EXAMPLE_CLUSTER_TOML",
    "ClusterConfig",
    "ClusterConfigError",
    "ClusterProfile",
    "load_cluster_config",
    "parse_cluster_config",
]
