"""Auditable, atomic protein-design MCP tools for OpenAI4S.

The stdio server and orchestration layer are pure standard-library Python.
Model-specific dependencies are loaded only in a separately configured
scientific worker process.
"""

from .service import ProteinDesignService

__all__ = ["ProteinDesignService"]
