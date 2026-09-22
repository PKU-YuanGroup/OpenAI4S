"""Judgment question templates. Importing a module registers its templates."""

from __future__ import annotations

from . import features as features
from . import literature as literature
from . import safety as safety
from . import skills as skills
from . import task_mode as task_mode

__all__ = ["features", "literature", "safety", "skills", "task_mode"]
