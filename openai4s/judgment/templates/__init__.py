"""Judgment question templates. Importing a module registers its templates."""

from __future__ import annotations

from . import literature as literature
from . import safety as safety
from . import skills as skills

__all__ = ["literature", "safety", "skills"]
