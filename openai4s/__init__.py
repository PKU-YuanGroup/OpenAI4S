"""openai4s — a Code-as-Action independent agent system.

A pure-Python agent whose action space is Turing-complete code running in a
persistent kernel. Linux daemon form-factor.
"""

__version__ = "1.0.0"

# OpenMHS is optional. Find its checkout (the third_party/OpenMHS submodule or
# `openai4s lab setup`) so no PYTHONPATH is needed, then report a broken
# installed host instead of silently disabling it. An explicit ``off`` must
# also prevent an already-importable adapter from installing its native tools.
from openai4s.mhs_lab.openmhs import bootstrap as _bootstrap_openmhs
from openai4s.mhs_lab.openmhs import disabled as _openmhs_disabled

_bootstrap_openmhs()

if not _openmhs_disabled():
    try:
        import openai4s_mhs
    except ModuleNotFoundError as exc:
        if exc.name != "openai4s_mhs":
            raise
    else:
        openai4s_mhs.install()
