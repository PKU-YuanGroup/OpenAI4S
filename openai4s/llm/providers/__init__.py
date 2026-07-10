"""Wire-specific provider adapters."""

from .anthropic import _chat_anthropic
from .gemini import _chat_gemini
from .openai import _chat_openai
from .responses import _chat_responses

_WIRE_DISPATCH = {
    "openai": _chat_openai,
    "anthropic": _chat_anthropic,
    "gemini": _chat_gemini,
    "responses": _chat_responses,
}
