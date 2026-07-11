"""Host-side services used by the kernel RPC dispatcher."""

from openai4s.host.completion import CompletionService
from openai4s.host.credentials import CredentialService
from openai4s.host.endpoints import EndpointService
from openai4s.host.files import WorkspaceFileService, is_secret_path
from openai4s.host.mcp import MCPService
from openai4s.host.progress import ProgressService
from openai4s.host.skills import SkillService

__all__ = [
    "CompletionService",
    "CredentialService",
    "EndpointService",
    "MCPService",
    "ProgressService",
    "SkillService",
    "WorkspaceFileService",
    "is_secret_path",
]
