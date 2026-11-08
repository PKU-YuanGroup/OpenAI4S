"""openai4s — a Code-as-Action independent agent system.

A pure-Python agent whose action space is Turing-complete code running in a
persistent kernel. Linux daemon form-factor.
"""

__version__ = "0.3.1"

# OpenMHS is optional. Find its checkout (the third_party/OpenMHS submodule or
# `openai4s lab setup`) so no PYTHONPATH is needed, then report a broken
# installed host instead of silently disabling it.
from openai4s.mhs_lab.openmhs import bootstrap as _bootstrap_openmhs

_bootstrap_openmhs()

try:
    import openai4s_mhs
except ModuleNotFoundError as exc:
    if exc.name != "openai4s_mhs":
        raise
else:
    openai4s_mhs.install()
