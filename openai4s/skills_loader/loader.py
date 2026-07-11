"""Skill discovery, progressive disclosure, and sidecar structure gate.

Mirrors openai4s's skill model at three levels:
  1. Discovery      — scan skills_dir for <name>/SKILL.md (+ optional kernel.py).
  2. Progressive    — the system prompt only lists skill name + one-line summary;
     disclosure       full docs are pulled on demand via host.search_skills().
  3. Sidecar gate   — kernel.py sidecars are compile-checked before use, returning
                      {ok, error?} (openai4s's `sidecar_gate` structure).

SKILL.md may start with a YAML-ish frontmatter block:

    ---
    name: stats
    description: descriptive-statistics helpers (mean/std/quantile/zscore)
    origin: personal
    ---

`description` becomes the one-line summary shown in the prompt. `origin` is one
of openai4s|organization|personal|draft|unknown and drives the permission gate.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from openai4s.capabilities import CapabilityStateService
from openai4s.config import Config, get_config

_VALID_ORIGINS = ("openai4s", "organization", "personal", "draft", "unknown")
# origins whose sidecar/doc is read-only (cannot be edited/deleted via CRUD)
_READONLY_ORIGINS = ("openai4s",)
_WORD = re.compile(r"[a-z0-9]+")


def _strip_scalar(v: str) -> str:
    """Normalize an inline YAML scalar: drop inline comments and surrounding
    quotes. Only strips a `#` comment on *unquoted* values so a `#` inside a
    quoted description survives."""
    v = v.strip()
    if len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0]:
        return v[1:-1]
    # unquoted: a ` #` starts a trailing comment
    return v.split(" #", 1)[0].strip()


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split an optional leading `--- ... ---` frontmatter block off the body.

    Understands a deliberately small YAML subset — enough for skill
    frontmatter, not a general parser:

      * top-level `key: scalar` (quoted or unquoted, with inline comments);
      * top-level `key: >` / `key: |` **block scalars** (folded/literal), whose
        value is the following more-indented lines. Folded (`>`) joins lines
        with spaces; literal (`|`) preserves newlines. Chomping indicators
        (`-`/`+`) are accepted and ignored — descriptions are collapsed anyway.

    Indented lines that are NOT a block-scalar continuation belong to a nested
    mapping/sequence (e.g. metadata.third_party[].name) and are ignored so they
    cannot clobber a top-level key of the same name.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    raw = text[3:end].strip("\n")
    body = text[end + 4 :].lstrip("\n")
    meta: dict = {}
    lines = raw.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        # Only TOP-LEVEL keys start at column 0. Skip blanks, comments, list
        # items, and any indented (nested) lines.
        if not line or line[0] in (" ", "\t", "#", "-"):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        k, _, v = line.partition(":")
        key = k.strip().lower()
        marker = v.strip()
        # strip an optional chomping indicator to detect a block scalar
        if marker and marker[0] in "|>" and marker[1:] in ("", "-", "+"):
            folded = marker[0] == ">"
            block: list[str] = []
            i += 1
            while i < n and (lines[i] == "" or lines[i][0] in (" ", "\t")):
                block.append(lines[i])
                i += 1
            # dedent by the minimum indent of the non-blank block lines
            indents = [len(ln) - len(ln.lstrip(" \t")) for ln in block if ln.strip()]
            pad = min(indents) if indents else 0
            dedented = [ln[pad:] if ln.strip() else "" for ln in block]
            sep = " " if folded else "\n"
            meta[key] = sep.join(x.strip() if folded else x for x in dedented).strip()
            continue
        meta[key] = _strip_scalar(v)
        i += 1
    return meta, body


def _first_paragraph(body: str) -> str:
    for block in body.split("\n\n"):
        cleaned = " ".join(
            ln.strip().lstrip("#").strip()
            for ln in block.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ).strip()
        if cleaned:
            return cleaned
    # fall back to first non-heading line
    for ln in body.splitlines():
        s = ln.strip().lstrip("#").strip()
        if s:
            return s
    return ""


def _tokenize(*texts: str) -> set[str]:
    toks: set[str] = set()
    for t in texts:
        toks.update(_WORD.findall(t.lower()))
    return toks


@dataclass
class Skill:
    name: str
    root: Path
    doc: str  # SKILL.md body (frontmatter stripped)
    has_kernel: bool  # kernel.py sidecar present?
    description: str = ""  # one-line summary for progressive disclosure
    origin: str = "unknown"
    keywords: set[str] = field(default_factory=set)
    version: str = ""
    document_sha256: str = ""
    sidecar_sha256: str | None = None

    @property
    def read_only(self) -> bool:
        return self.origin in _READONLY_ORIGINS

    @property
    def import_hint(self) -> str | None:
        """How the agent imports this skill's sidecar inside a kernel cell.

        The sidecar lives on disk under the *directory* name (which is what
        `bootstrap_code` puts on `sys.path`), so imports must use the dir name,
        not the declared frontmatter `name`. Directory names may contain
        hyphens (e.g. `pdf-explore`), which are not valid Python identifiers —
        `from pdf-explore.kernel import *` is a SyntaxError. For those, emit an
        `importlib.import_module(...)` hint, which resolves the sidecar as a
        namespace submodule and works with hyphenated dir names.
        """
        if not self.has_kernel:
            return None
        mod = self.root.name
        if mod.isidentifier():
            return f"from {mod}.kernel import * # or: import {mod}.kernel as k"
        return (
            f'import importlib; k = importlib.import_module("{mod}.kernel") '
            f"# '{mod}' isn't a valid identifier; import * won't work"
        )

    def summary_line(self) -> str:
        return f"- {self.name}: {self.description or '(no description)'}"

    def sidecar_gate(self) -> dict:
        """Compile-check the kernel.py sidecar (openai4s's structure gate).

        Returns {"ok": bool, "error": str|None}. A skill with no sidecar is
        trivially ok. This catches syntax errors BEFORE the agent tries to
        import the sidecar mid-task.
        """
        if not self.has_kernel:
            return {"ok": True, "error": None}
        path = self.root / "kernel.py"
        try:
            src = path.read_text("utf-8")
            compile(src, str(path), "exec")
            return {"ok": True, "error": None}
        except SyntaxError as e:
            return {"ok": False, "error": f"{e.__class__.__name__}: {e}"}
        except OSError as e:
            return {"ok": False, "error": f"cannot read sidecar: {e}"}

    def manifest_entry(self, state: dict) -> dict:
        """Describe discovery/bootstrap state without claiming an import.

        ``loaded`` starts false.  The generated kernel import hook changes it
        only after the sidecar loader's ``exec_module`` succeeds.
        """

        gate = self.sidecar_gate()
        return {
            "name": self.name,
            "directory": self.root.name,
            "origin": self.origin,
            "enabled": bool(state.get("enabled", True)),
            "state_scope": state.get("scope", "default"),
            "state_scope_id": state.get("scope_id", ""),
            "version": self.version,
            "document_sha256": self.document_sha256,
            "sidecar": {
                "present": self.has_kernel,
                "sha256": self.sidecar_sha256,
                "gate": gate,
                "loaded": False,
            },
        }


def _bootstrap_runtime_code(manifest: dict, roots: list[str]) -> str:
    """Generate the in-kernel import gate/tracker for one manifest snapshot."""

    entries = manifest.get("entries") or []
    known = {
        str(entry.get("directory")): entry
        for entry in entries
        if entry.get("directory")
    }
    disabled = {
        directory
        for directory, entry in known.items()
        if not entry.get("enabled", True)
    }
    # Keep this generated snippet self-contained: a scientific kernel may not
    # import openai4s internals from its selected environment.
    return (
        "import importlib.abc as _o4s_abc\n"
        "import importlib.machinery as _o4s_machinery\n"
        "import sys as _o4s_sys\n"
        "import time as _o4s_time\n"
        f"__openai4s_skill_bootstrap_manifest__ = {manifest!r}\n"
        "__openai4s_skill_load_events__ = "
        "__openai4s_skill_bootstrap_manifest__['load_events']\n"
        f"_o4s_skill_roots = {roots!r}\n"
        "_o4s_skill_entries = {\n"
        "    _o4s_entry['directory']: _o4s_entry\n"
        "    for _o4s_entry in "
        "__openai4s_skill_bootstrap_manifest__['entries']\n"
        "}\n"
        f"_o4s_disabled_skills = {disabled!r}\n"
        "for _o4s_root in reversed(_o4s_skill_roots):\n"
        "    if _o4s_root not in _o4s_sys.path:\n"
        "        _o4s_sys.path.insert(0, _o4s_root)\n"
        "for _o4s_module in list(_o4s_sys.modules):\n"
        "    if _o4s_module.partition('.')[0] in _o4s_skill_entries:\n"
        "        _o4s_sys.modules.pop(_o4s_module, None)\n"
        "_o4s_sys.meta_path[:] = [\n"
        "    _o4s_finder for _o4s_finder in _o4s_sys.meta_path\n"
        "    if not getattr(_o4s_finder, '_openai4s_skill_gate', False)\n"
        "]\n"
        "class _OpenAI4STrackedSkillLoader:\n"
        "    def __init__(self, delegate, skill_name, entry):\n"
        "        self._delegate = delegate\n"
        "        self._skill_name = skill_name\n"
        "        self._entry = entry\n"
        "    def create_module(self, spec):\n"
        "        create = getattr(self._delegate, 'create_module', None)\n"
        "        return create(spec) if create else None\n"
        "    def exec_module(self, module):\n"
        "        self._delegate.exec_module(module)\n"
        "        sidecar = self._entry.get('sidecar') or {}\n"
        "        sidecar['loaded'] = True\n"
        "        event = {\n"
        "            'event': 'sidecar_loaded',\n"
        "            'name': self._entry.get('name') or self._skill_name,\n"
        "            'module': module.__name__,\n"
        "            'version': self._entry.get('version'),\n"
        "            'sidecar_sha256': sidecar.get('sha256'),\n"
        "            'loaded_at_ns': _o4s_time.time_ns(),\n"
        "        }\n"
        "        __openai4s_skill_load_events__.append(event)\n"
        "    def __getattr__(self, name):\n"
        "        return getattr(self._delegate, name)\n"
        "class _OpenAI4SSkillGate(_o4s_abc.MetaPathFinder):\n"
        "    _openai4s_skill_gate = True\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        top = fullname.partition('.')[0]\n"
        "        entry = _o4s_skill_entries.get(top)\n"
        "        if entry is None:\n"
        "            return None\n"
        "        if top in _o4s_disabled_skills:\n"
        "            raise ModuleNotFoundError(\n"
        '                f"skill sidecar {top!r} is disabled by capability policy"\n'
        "            )\n"
        "        spec = _o4s_machinery.PathFinder.find_spec(fullname, path)\n"
        "        if (\n"
        "            spec is not None and fullname == top + '.kernel'\n"
        "            and spec.loader is not None\n"
        "        ):\n"
        "            spec.loader = _OpenAI4STrackedSkillLoader(\n"
        "                spec.loader, top, entry\n"
        "            )\n"
        "        return spec\n"
        "_o4s_sys.meta_path.insert(0, _OpenAI4SSkillGate())\n"
    )


class SkillLoader:
    def __init__(
        self,
        skills_dir: Path | None = None,
        cfg: Config | None = None,
        *,
        capabilities: CapabilityStateService | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
    ):
        self.cfg = cfg or get_config()
        self.skills_dir = Path(skills_dir) if skills_dir else self.cfg.skills_dir
        if capabilities is None:
            # Lazy import avoids coupling Store's schema initialization back to
            # skill discovery while still making persistence the default for
            # every runtime loader.
            from openai4s.store import get_store

            capabilities = get_store(self.cfg.db_path).capability_state(
                project_id=project_id,
                session_id=session_id,
            )
        elif project_id is not None or session_id is not None:
            capabilities = capabilities.scoped(
                project_id=project_id,
                session_id=session_id,
            )
        self.capabilities = capabilities
        self._skills: dict[str, Skill] = {}
        self._last_manifest: dict | None = None

    def scoped(
        self,
        *,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> "SkillLoader":
        return SkillLoader(
            self.skills_dir,
            self.cfg,
            capabilities=self.capabilities.scoped(
                project_id=project_id,
                session_id=session_id,
            ),
        )

    def user_skills_dir(self) -> Path:
        """Writable dir for user-authored skills (kept separate from the bundled
        read-only skills). Discovered alongside the bundled ones."""
        return self.cfg.data_dir / "user-skills"

    def discover(self) -> dict[str, Skill]:
        self._skills = {}
        # bundled skills first, then user-authored ones. A user skill must NOT
        # silently shadow a trusted BUNDLED skill of the same dir-name — bundled
        # wins on collision (else agent loads untrusted content under a trusted name).
        for base in (self.skills_dir, self.user_skills_dir()):
            if not base or not base.exists():
                continue
            is_user = base.resolve() == self.user_skills_dir().resolve()
            for child in sorted(base.iterdir()):
                if not child.is_dir():
                    continue
                md = child / "SKILL.md"
                if not md.exists():
                    continue
                if is_user and child.name in self._skills:
                    continue  # bundled skill already claimed this name — keep it
                raw = md.read_text("utf-8")
                meta, body = _parse_frontmatter(raw)
                origin = (meta.get("origin") or "unknown").lower()
                if is_user:
                    origin = "user"
                elif origin not in _VALID_ORIGINS:
                    origin = "unknown"
                description = meta.get("description") or _first_paragraph(body)
                description = " ".join(description.split())  # collapse whitespace
                if len(description) > 200:
                    description = description[:197] + "..."
                name = meta.get("name") or child.name
                document_sha256 = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                sidecar = child / "kernel.py"
                sidecar_sha256 = None
                if sidecar.exists():
                    try:
                        sidecar_sha256 = hashlib.sha256(
                            sidecar.read_bytes()
                        ).hexdigest()
                    except OSError:
                        sidecar_sha256 = None
                version = str(meta.get("version") or "").strip()
                if not version:
                    version = (sidecar_sha256 or document_sha256)[:12]
                self._skills[child.name] = Skill(
                    name=name,
                    root=child,
                    doc=body,
                    has_kernel=(child / "kernel.py").exists(),
                    description=description,
                    origin=origin,
                    keywords=_tokenize(name, description, body),
                    version=version,
                    document_sha256=document_sha256,
                    sidecar_sha256=sidecar_sha256,
                )
        return self._skills

    def is_enabled(self, name: str) -> bool:
        return self.capabilities.is_enabled("skill", name)

    def set_enabled(
        self,
        name: str,
        enabled: bool,
        *,
        scope: str = "global",
        scope_id: str | None = None,
    ) -> dict:
        skill = self.get(name, include_disabled=True)
        canonical = skill.name if skill is not None else str(name)
        return self.capabilities.set_enabled(
            "skill",
            canonical,
            enabled,
            scope=scope,
            scope_id=scope_id,
            metadata={
                "directory": skill.root.name if skill is not None else None,
                "origin": skill.origin if skill is not None else None,
                "version": skill.version if skill is not None else None,
                "sidecar_sha256": (skill.sidecar_sha256 if skill is not None else None),
            },
        )

    def skills(self, *, include_disabled: bool = False) -> dict[str, Skill]:
        if not self._skills:
            self.discover()
        if include_disabled:
            return self._skills
        return {
            key: skill
            for key, skill in self._skills.items()
            if self.is_enabled(skill.name)
        }

    def get(self, name: str, *, include_disabled: bool = False) -> Skill | None:
        skills = self.skills(include_disabled=include_disabled)
        if name in skills:
            return skills[name]
        # allow lookup by declared skill.name too
        for s in skills.values():
            if s.name == name:
                return s
        return None

    def read(self, name: str, path: str = "SKILL.md") -> str:
        """Read an enabled skill resource without escaping its directory."""

        skill = self.get(name)
        if skill is None:
            raise KeyError(f"no such skill (or disabled): {name!r}")
        root = skill.root.resolve()
        target = (root / path).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"path escapes skill dir: {path!r}")
        return target.read_text("utf-8")

    def bootstrap_manifest(self, *, persist: bool = True) -> dict:
        """Build the exact enabled/disabled skill snapshot for a kernel.

        A stored manifest is a bootstrap *intent* snapshot.  Sidecars remain
        ``loaded=false`` until the generated import hook observes a successful
        import in that kernel.
        """

        all_skills = self.skills(include_disabled=True)
        states = self.capabilities.snapshot(
            "skill",
            [skill.name for skill in all_skills.values()],
        )
        entries = [
            skill.manifest_entry(states[skill.name]) for skill in all_skills.values()
        ]
        digest = hashlib.sha256(
            json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        manifest = {
            "manifest_id": f"local-{digest[:20]}",
            "kind": "skill",
            "entries": entries,
            "load_events": [],
        }
        if persist:
            stored = self.capabilities.record_manifest("skill", entries)
            if stored is not None:
                manifest["manifest_id"] = stored["manifest_id"]
        self._last_manifest = manifest
        return manifest

    def bootstrap_code(self) -> str:
        """Return a scoped sidecar import path, deny gate, and truthful tracker."""

        manifest = self.bootstrap_manifest()
        roots = [str(self.skills_dir), str(self.user_skills_dir())]
        return _bootstrap_runtime_code(manifest, roots)

    def record_sidecar_loaded(
        self,
        name: str,
        *,
        module: str | None = None,
        manifest_id: str | None = None,
    ) -> dict:
        """Persist a load event reported by a runtime/checkpoint integrator."""

        skill = self.get(name, include_disabled=True)
        if skill is None:
            raise KeyError(f"no such skill: {name!r}")
        return self.capabilities.record_event(
            "skill",
            skill.name,
            "sidecar_loaded",
            metadata={
                "module": module or f"{skill.root.name}.kernel",
                "manifest_id": manifest_id
                or (self._last_manifest or {}).get("manifest_id"),
                "version": skill.version,
                "sidecar_sha256": skill.sidecar_sha256,
            },
        )

    def search(self, query: str, *, limit: int = 5) -> list[dict]:
        """Keyword-overlap skill retrieval (openai4s's search_skills route).

        Scores each skill by literal token overlap between the query and the
        skill's name/description/body. Purely lexical — no synonym expansion —
        matching the documented limitation of the skill-retrieval prompt.
        Returns the full doc of the top matches so the agent can then use them.
        """
        q_tokens = _tokenize(query)
        scored: list[tuple[float, Skill]] = []
        for s in self.skills().values():
            if not q_tokens:
                score = 0.0
            else:
                overlap = len(q_tokens & s.keywords)
                # bias toward name/description hits
                name_hit = len(q_tokens & _tokenize(s.name, s.description))
                score = overlap + 1.5 * name_hit
            if score > 0:
                scored.append((score, s))
        scored.sort(key=lambda t: t[0], reverse=True)
        results = []
        for score, s in scored[:limit]:
            gate = s.sidecar_gate()
            results.append(
                {
                    "name": s.name,
                    "origin": s.origin,
                    "description": s.description,
                    "import": s.import_hint,
                    "score": round(score, 2),
                    "doc": s.doc.strip(),
                    "sidecar_gate": gate,
                }
            )
        return results

    def catalog(self, *, include_disabled: bool = False) -> list[dict]:
        """Lightweight listing (name/description/origin) — no full docs."""
        return [
            {
                "name": s.name,
                "description": s.description,
                "origin": s.origin,
                "has_kernel": s.has_kernel,
                "enabled": self.is_enabled(s.name),
                "version": s.version,
                "document_sha256": s.document_sha256,
                "sidecar_sha256": s.sidecar_sha256,
            }
            for s in self.skills(include_disabled=include_disabled).values()
        ]

    def system_context(self) -> str:
        """Progressive-disclosure block for the system prompt.

        Only skill NAMES + one-line summaries go here — NOT the full docs.
        The agent calls host.search_skills(query) to pull a skill's full recipe
        on demand: analytic tasks retrieve skills lazily instead of
        front-loading every doc into context.
        """
        skills = self.skills()
        if not skills:
            return ""
        lines = [
            "# Available skills (progressive disclosure)",
            "These skills exist but their full instructions are NOT loaded yet. "
            "When a task looks relevant to one, call "
            '`host.search_skills("<keywords>")` in a code cell to retrieve its '
            "full recipe, then import its sidecar and use it. Do NOT invent "
            "skills or APIs you have not retrieved.",
            "",
        ]
        for s in skills.values():
            lines.append(s.summary_line())
        return "\n".join(lines)


def discover_skills(
    skills_dir: Path | None = None, cfg: Config | None = None
) -> dict[str, Skill]:
    return SkillLoader(skills_dir, cfg).discover()
