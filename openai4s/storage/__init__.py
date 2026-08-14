"""Domain repositories behind the compatibility ``Store`` facade."""

from openai4s.storage.actions import ActionLedgerRepository
from openai4s.storage.activation import SessionActivationRepository
from openai4s.storage.agents import AgentProfileRepository
from openai4s.storage.annotations import AnnotationRepository
from openai4s.storage.artifacts import ArtifactRepository
from openai4s.storage.capabilities import CapabilityStateRepository
from openai4s.storage.checkpoint_state import CheckpointStateRepository
from openai4s.storage.connectors import ConnectorRepository
from openai4s.storage.datapro_index import DataProIndexRepository
from openai4s.storage.frames import FrameRepository
from openai4s.storage.kernels import KernelGenerationRepository
from openai4s.storage.memories import MemoryRepository
from openai4s.storage.metadata import (
    CompactionRepository,
    EndpointRepository,
    FolderRepository,
    HostCallRepository,
    NotesRepository,
)
from openai4s.storage.permissions import PermissionRuleRepository
from openai4s.storage.plans import PlanRepository
from openai4s.storage.recovery import RecoveryJournalRepository
from openai4s.storage.settings import SettingsRepository
from openai4s.storage.skills import SkillVersionRepository
from openai4s.storage.snapshots import SessionSnapshotRepository, WorkspaceCAS

__all__ = [
    "ActionLedgerRepository",
    "SessionActivationRepository",
    "AgentProfileRepository",
    "AnnotationRepository",
    "ArtifactRepository",
    "CapabilityStateRepository",
    "CheckpointStateRepository",
    "ConnectorRepository",
    "DataProIndexRepository",
    "FrameRepository",
    "KernelGenerationRepository",
    "MemoryRepository",
    "CompactionRepository",
    "EndpointRepository",
    "FolderRepository",
    "HostCallRepository",
    "NotesRepository",
    "PermissionRuleRepository",
    "PlanRepository",
    "RecoveryJournalRepository",
    "SessionSnapshotRepository",
    "SettingsRepository",
    "SkillVersionRepository",
    "WorkspaceCAS",
]
