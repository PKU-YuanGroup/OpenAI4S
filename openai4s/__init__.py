"""openai4s — a Code-as-Action independent agent system.

A pure-Python agent whose action space is Turing-complete code running in a
persistent kernel. Linux daemon form-factor.
"""

__version__ = "0.3.1"

# Optional OpenMHS developer overlay (integration/openai4s).  Importing it
# registers the mhs_* tools and HostDispatcher methods while the MHS integration
# is enabled; a missing overlay or SDK must never make `import openai4s` fail.
try:
    import openai4s_mhs

    openai4s_mhs.install()
except Exception:
    pass
