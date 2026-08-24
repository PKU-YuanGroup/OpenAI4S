"""The Slurm backend: the only subpackage allowed to name a scheduler.

INV-2 is a claim about the orchestration *core*, not a claim that no code
anywhere may know about Slurm — something has to. Concentrating that
knowledge here is what makes the rule checkable: the leak guard skips this
directory by name, so anything scheduler-shaped outside it is a defect
rather than a judgement call.
"""

from openai4s.orchestration.slurm.backend import SlurmBackend
from openai4s.orchestration.slurm.broker import (
    JobStatus,
    SlurmBroker,
    SlurmCommandError,
    SubmitSpec,
)
from openai4s.orchestration.slurm.profiles import (
    CLUSTER_CONFIG_FILENAME,
    EXAMPLE_CLUSTER_TOML,
    ClusterConfig,
    ClusterConfigError,
    ClusterProfile,
    load_cluster_config,
    parse_cluster_config,
)

__all__ = [
    "CLUSTER_CONFIG_FILENAME",
    "EXAMPLE_CLUSTER_TOML",
    "ClusterConfig",
    "ClusterConfigError",
    "ClusterProfile",
    "JobStatus",
    "SlurmBackend",
    "SlurmBroker",
    "SlurmCommandError",
    "SubmitSpec",
    "load_cluster_config",
    "parse_cluster_config",
]
