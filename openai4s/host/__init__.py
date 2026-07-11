"""Host-side services used by the kernel RPC dispatcher."""

from openai4s.host.files import WorkspaceFileService, is_secret_path
from openai4s.host.skills import SkillService

__all__ = ["SkillService", "WorkspaceFileService", "is_secret_path"]
