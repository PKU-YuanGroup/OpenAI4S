"""Host-side services used by the kernel RPC dispatcher."""

from openai4s.host.accelerators import (
    AcceleratorRoutingService,
    LocalAcceleratorService,
)
from openai4s.host.completion import CompletionService
from openai4s.host.credentials import CredentialService
from openai4s.host.data import HostDataService
from openai4s.host.delegation import DelegationService
from openai4s.host.endpoints import EndpointService
from openai4s.host.files import WorkspaceFileService, is_secret_path
from openai4s.host.llm import LLMService
from openai4s.host.mcp import MCPService
from openai4s.host.progress import ProgressService
from openai4s.host.remote_capabilities import RemoteCapabilityService
from openai4s.host.remote_science import RemoteScienceService
from openai4s.host.session import SessionControlService
from openai4s.host.skills import SkillService

__all__ = [
    "CompletionService",
    "CredentialService",
    "HostDataService",
    "DelegationService",
    "EndpointService",
    "LLMService",
    "AcceleratorRoutingService",
    "LocalAcceleratorService",
    "MCPService",
    "ProgressService",
    "RemoteCapabilityService",
    "RemoteScienceService",
    "SessionControlService",
    "SkillService",
    "WorkspaceFileService",
    "is_secret_path",
]
