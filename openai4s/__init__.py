"""openai4s — a Code-as-Action independent agent system.

A pure-Python agent whose action space is Turing-complete code running in a
persistent kernel. Linux daemon form-factor.
"""

__version__ = "0.3.1"

# OpenMHS is optional. Report a broken installed host instead of silently disabling it.
try:
    import openai4s_mhs
except ModuleNotFoundError as exc:
    if exc.name != "openai4s_mhs":
        raise
else:
    openai4s_mhs.install()
