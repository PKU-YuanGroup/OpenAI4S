"""Launch a deployment-configured rollout JSONL worker.

Usage: python -m openai4s.rl.serve trusted_module:build_environment
The factory is administrator configuration, never a field accepted from RPC.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from contextlib import ExitStack, redirect_stdout
from typing import Sequence

from .environment import RolloutEnvironment, RolloutEnvironmentAdapter
from .rpc import serve


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Serve a trusted rollout factory over stdin/stdout JSONL"
    )
    parser.add_argument(
        "factory", help="trusted installed module:callable (no RPC-supplied code)"
    )
    options = parser.parse_args(argv)
    module_name, separator, name = options.factory.partition(":")
    if not separator or not module_name or not name.isidentifier():
        parser.error("factory must be an installed module:callable")
    output = sys.stdout
    with ExitStack() as stack, redirect_stdout(sys.stderr):
        factory = getattr(importlib.import_module(module_name), name)
        runtime = factory()
        if hasattr(runtime, "__enter__"):
            runtime = stack.enter_context(runtime)
        adapter = (
            RolloutEnvironmentAdapter(runtime)
            if isinstance(runtime, RolloutEnvironment)
            else runtime
        )
        if not isinstance(adapter, RolloutEnvironmentAdapter):
            raise TypeError("factory must provide a RolloutEnvironment or its adapter")
        # Logs go to stderr. EOF does not invent a terminal result for an
        # unfinished scientific run; durable recovery remains explicit.
        try:
            serve(adapter, sys.stdin, output)
        finally:
            if adapter.finished:
                adapter.release()
            else:
                # serve is synchronous: EOF is only processed after any active
                # request has unwound. Local kernels can close; unknown remote
                # work and its receipt are deliberately not marked terminal.
                adapter.environment.abandon()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
