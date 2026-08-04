"""Skill loader for the Code-as-Action paradigm.

A skill is a directory:
 <skill_name>/
 SKILL.md recipe-centric doc (code examples, not JSON schema)
 kernel.py optional importable sidecar module (helper functions)
 resources/ optional data/assets

Skills are consumed by WRITING CODE, not by filling params: the loader surfaces
each skill's SKILL.md (and the import path of its kernel.py) into the agent's
system context, so the model imports the sidecar and calls its functions.
"""

from openai4s.skills_loader.loader import Skill, SkillLoader, discover_skills
from openai4s.skills_loader.versions import SkillVersionService

__all__ = ["Skill", "SkillLoader", "SkillVersionService", "discover_skills"]
