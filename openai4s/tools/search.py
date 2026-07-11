"""Compatibility facade for the class-based workspace search tools."""

from openai4s.tools.content_search import ContentSearchTool
from openai4s.tools.glob_files import GlobFilesTool
from openai4s.tools.registry import get_tool

glob_files = get_tool("glob_files")
content_search = get_tool("content_search")

__all__ = [
    "GlobFilesTool",
    "ContentSearchTool",
    "glob_files",
    "content_search",
]
