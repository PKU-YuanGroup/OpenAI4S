"""Generic entrypoint — one loader for every compute provider.

    python -I .../openai4s_compute_provider/__main__.py oneshot <provider.py> <op> <stage> <expectConfined>
    python -I .../openai4s_compute_provider/__main__.py repl    <provider.py>

Invoked as a script (not -m) because -I strips PYTHONPATH/cwd from sys.path,
so this file inserts the package's parent only — provider.py is loaded via
spec_from_file_location, so the skill dir is never on sys.path and unverified
sibling .py files there cannot shadow stdlib or third-party imports.

Secret scrubbing happens in TWO stages so provider code at import time cannot
read credential-shaped or known-prefix environment variables (a name-based
heuristic — a secret in an unrecognized variable name is NOT scrubbed): the
provider-agnostic baseline scrub runs
HERE, before ``exec_module`` loads provider.py; the resident prologue then
re-scrubs with the loaded provider's own declared ``secret_env_prefixes``
before the credential is read from stdin/fd-3."""
import importlib.util
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))


def _load_own_package():
    """Import this package by file location, without its parent on sys.path.

    ``sys.path.insert(0, dirname(_here))`` used to be how the package below was
    imported — and a package import *lists* the directory it searches, so the
    parent had to be readable from inside the confinement. In a source or
    editable install that parent is the repository root, so the boundary had to
    hand back an untracked ``.env``, ``.git``, and every unrelated file beside
    them, to a process that by design also has the network.

    Loading from ``_here`` with ``submodule_search_locations`` set gives the
    package a ``__path__`` of its own directory, so its relative imports resolve
    exactly as before while nothing above it needs to be readable at all.
    """
    name = os.path.basename(_here)
    spec = importlib.util.spec_from_file_location(
        name,
        os.path.join(_here, "__init__.py"),
        submodule_search_locations=[_here],
    )
    module = importlib.util.module_from_spec(spec)
    # Registered before exec so the package's own relative imports resolve.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_pkg = _load_own_package()
ByocResident = _pkg.ByocResident
scrub_secret_env = _pkg.scrub_secret_env

mode, provider_py, *rest = sys.argv[1:]

# Baseline env scrub BEFORE the provider module is imported: provider.py's
# top-level code must not see credential-shaped or known-prefix env vars (a
# name-based heuristic). The provider's own declared prefixes (unknowable
# until the module loads) are folded in by the resident prologue.
scrub_secret_env()

spec = importlib.util.spec_from_file_location("_byoc_provider", provider_py)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

r = ByocResident(mod.PROVIDER(repl=(mode == "repl")))
if mode == "repl":
    r.run_repl()
else:
    # argv[0] is a placeholder (script-name slot, ignored); run_oneshot
    # reads argv[1:4] = op, stage, expect_confined.
    r.run_oneshot([provider_py, *rest])
