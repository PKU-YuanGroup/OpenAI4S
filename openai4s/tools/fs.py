"""Compatibility facade for the class-based workspace file tools."""

from openai4s.tools.list_directory import ListDirectoryTool
from openai4s.tools.read_text_file import ReadTextFileTool
from openai4s.tools.registry import get_tool
from openai4s.tools.write_file import WriteFileTool

list_dir = get_tool("list_dir")
read_text_file = get_tool("read_text_file")
write_file = get_tool("write_file")

__all__ = [
    "ListDirectoryTool",
    "ReadTextFileTool",
    "WriteFileTool",
    "list_dir",
    "read_text_file",
    "write_file",
]
