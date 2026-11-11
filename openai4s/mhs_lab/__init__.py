"""OpenAI4S x OpenMHS simulated laboratory ("Lab").

This optional package connects OpenAI4S to OpenMHS (the open Model Hardware
Standard, https://mhs.openai4s.org) endpoints that stand in for laboratory
instruments.  It ships:

* ``instruments/`` -- two simulated instruments written as ``mhs.driver``
  packages (an enzyme-inhibition assay workstation and a continuous-flow
  reactor with inline HPLC).  They declare themselves ``simulated``; every
  limit cites the instrument manual next to the driver;
* :mod:`openai4s.mhs_lab.supervisor` -- starts and stops the lab endpoints,
  writes the OpenMHS settings document and the MCP connector rows;
* :mod:`openai4s.mhs_lab.activity` -- one timeline of every receipt, whatever
  access path produced it (MCP connector, HTTP JSON-RPC, stdio JSON-RPC);
* :mod:`openai4s.mhs_lab.kernel` -- the helper agent cells import to reach a
  lab endpoint over HTTP or stdio with the receipts recorded.

Nothing here is imported by the OpenAI4S core.  It needs the OpenMHS SDK
(import name ``mhs``) only when a lab is started or a cell connects.  The lab is
simulation only: no instrument here controls hardware.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["INSTRUMENTS_DIR", "lab_dir"]

INSTRUMENTS_DIR = Path(__file__).resolve().parent / "instruments"


def lab_dir() -> Path:
    """The directory holding the lab's runtime state (settings, logs, LIMS)."""
    explicit = os.environ.get("OPENAI4S_MHS_LAB_DIR", "").strip()
    if explicit:
        path = Path(explicit).expanduser()
    else:
        data = os.environ.get("OPENAI4S_DATA_DIR", "").strip()
        base = Path(data).expanduser() if data else Path.home() / ".openai4s"
        path = base / "mhs-lab"
    path.mkdir(parents=True, exist_ok=True)
    return path
