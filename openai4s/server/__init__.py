"""HTTP gateway: full web UI + REST + WebSocket (pure stdlib).

`serve`/`build_server` front the openai4s-local web UI backed by the
Code-as-Action engine (see gateway.py). They are the only servers this package
offers. A second, minimal one lived in `daemon.py` until it was removed: it
exposed `POST /run` straight onto `Agent.run` with no Host allowlist, no Origin
check, no token gate and no security headers, nothing imported it, and this
docstring advertised it under two names (`serve_minimal`/`build_minimal_server`)
that the file never defined. An unauthenticated code-execution endpoint kept
alive by a docstring is one stray import from being reachable.
"""
from openai4s.server.gateway import build_app_server as build_server
from openai4s.server.gateway import serve_app as serve

__all__ = ["build_server", "serve"]
