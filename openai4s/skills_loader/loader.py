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

`description` becomes the one-line summary shown in the prompt. `origin` is
lifecycle/display metadata; the configured discovery root, not frontmatter,
determines whether a skill is writable.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from openai4s.capabilities import CapabilityStateService
from openai4s.config import Config, get_config
from openai4s.skills_loader.versions import project_skills_root

_VALID_ORIGINS = ("openai4s", "organization", "personal", "draft", "unknown")
_WORD = re.compile(r"[a-z0-9]+")


def _canonical_skill_name(value: str) -> str:
    """Return the collision identity for a declared skill name.

    Directory names are an implementation detail: capability state and agent
    retrieval use the frontmatter ``name``.  Normalize that public identity so
    a user directory cannot shadow a bundled skill through casing, compatible
    Unicode, or whitespace differences.
    """

    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(normalized.split()).casefold()


class _StoreCapabilityRepository:
    """Resolve the current Store-owned repository for every operation.

    A ``SkillLoader`` can legitimately outlive a server/test Store generation
    (configuration reloads and daemon restarts both replace the SQLite owner).
    Holding the concrete repository would leave the loader pointing at a
    closed connection.  This tiny adapter preserves the Store as the sole
    connection owner while making the default loader safe across that
    lifecycle boundary.  Explicitly injected capability services retain their
    caller-owned lifetime semantics.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    def _call(self, method: str, *args, **kwargs):
        # Lazy import avoids a Store -> skills -> Store initialization cycle.
        from openai4s.store import get_store

        repository = get_store(self._db_path).capability_state().repository
        return getattr(repository, method)(*args, **kwargs)

    def set_enabled(self, *args, **kwargs):
        return self._call("set_enabled", *args, **kwargs)

    def resolve(self, *args, **kwargs):
        return self._call("resolve", *args, **kwargs)

    def snapshot(self, *args, **kwargs):
        return self._call("snapshot", *args, **kwargs)

    def explicit_states(self, *args, **kwargs):
        return self._call("explicit_states", *args, **kwargs)

    def append_event(self, *args, **kwargs):
        return self._call("append_event", *args, **kwargs)

    def list_events(self, *args, **kwargs):
        return self._call("list_events", *args, **kwargs)

    def record_manifest(self, *args, **kwargs):
        return self._call("record_manifest", *args, **kwargs)

    def latest_manifest(self, *args, **kwargs):
        return self._call("latest_manifest", *args, **kwargs)


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


#: How a declared requirement is checked, and every one of these is local.
#:
#: Browsing the catalogue must not reach the network — a user scrolling a Skill
#: list is not asking to contact anything, and the report says so explicitly.
#: So readiness is derived from what this machine can observe about itself, and
#: a requirement nobody knows how to check answers `unknown` rather than
#: guessing in either direction: claiming `ready` invites a failure deep into a
#: task, and claiming `needs_setup` sends a user to install something that may
#: already be there.
def _has_gpu() -> bool:
    import shutil

    # `nvidia-smi` on PATH, not a probe of it. Executing it here would make
    # rendering a catalogue spawn a subprocess per Skill.
    return bool(shutil.which("nvidia-smi"))


_REQUIREMENT_CHECKS: dict[str, "Callable[[], bool]"] = {"gpu": _has_gpu}

#: Readiness is not enabledness. A disabled Skill can be perfectly ready, and
#: an enabled one can be missing its hardware; conflating them means a user who
#: enables a Skill believes they have made it work.
READY = "ready"
NEEDS_SETUP = "needs_setup"
UNKNOWN = "unknown"


def skill_readiness(requirements: "Sequence[str]") -> dict[str, object]:
    """Can this Skill run here? Answered from local state alone."""
    missing: list[str] = []
    unknown: list[str] = []
    for requirement in requirements or ():
        check = _REQUIREMENT_CHECKS.get(str(requirement).lower())
        if check is None:
            unknown.append(str(requirement))
        elif not check():
            missing.append(str(requirement))
    if missing:
        state = NEEDS_SETUP
    elif unknown:
        state = UNKNOWN
    else:
        state = READY
    return {
        "state": state,
        "missing": sorted(missing),
        "unverifiable": sorted(unknown),
        # Said explicitly so a caller cannot mistake this for a probe.
        "checked_locally": True,
    }


def _requirements(value: object) -> tuple[str, ...]:
    """Normalise a frontmatter `requirements:` value to lowercase tokens.

    Tolerant of the three spellings that actually appear in the wild — a YAML
    list, a comma-separated string, a bare word — because rejecting a Skill
    over its punctuation would hide a real Skill for a cosmetic reason. An
    unparseable value yields no requirements rather than a fabricated one:
    claiming a Skill needs something it never declared would send a user to
    install a thing they do not need.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        parts = [
            part.strip() for part in value.replace("[", "").replace("]", "").split(",")
        ]
    elif isinstance(value, (list, tuple, set)):
        parts = [str(part).strip() for part in value]
    else:
        return ()
    return tuple(sorted({part.lower() for part in parts if part}))


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
    # Filesystem discovery source is authoritative for ownership. Frontmatter
    # origin remains lifecycle/display metadata and is intentionally unable to
    # make a bundled directory writable.
    source: str = "bundled"
    keywords: set[str] = field(default_factory=set)
    #: What this Skill needs before it can actually run — `requirements: [gpu]`
    #: in the frontmatter. Fourteen bundled Skills have declared this since they
    #: were written and nothing read it, so a GPU-only Skill looked identical
    #: to one that runs anywhere and the agent discovered the difference at
    #: execution time, deep into a task.
    requirements: tuple[str, ...] = ()
    version: str = ""
    document_sha256: str = ""
    sidecar_sha256: str | None = None
    # Large third-party collections remain individually searchable/loadable,
    # but are represented by one line in the always-on system prompt.  This
    # preserves progressive disclosure when a collection contains hundreds of
    # recipes whose summaries alone would otherwise consume the context.
    collection: str | None = None

    @property
    def read_only(self) -> bool:
        return self.source == "bundled"

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
            "distribution_scope": self.source,
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


#: A collection declares itself with this file at its own root.
COLLECTION_MARKER = "COLLECTION.json"


@dataclass(frozen=True)
class SkillCollection:
    """A bundled tree that is ONE catalog entry, not N peer Skills.

    The loader used to hardcode the directory name, the collection id, and an
    eight-line bioinformatics policy paragraph, and `system_context` read back
    exactly one key -- so `collection` was a tag three surfaces interpreted
    differently and a second collection would have been dropped from the
    prompt. The tree now declares itself: drop a `COLLECTION.json` beside its
    LICENSE and README pair and every surface picks it up, with its own
    retrieval guidance living next to the recipes it describes rather than in
    a provider-neutral discovery component.
    """

    id: str
    root: Path
    #: Rendered verbatim as the collection's single line in the system prompt.
    #: ``{count}`` is substituted with the number of members actually visible
    #: to the caller, which is not 561 once an allowlist has filtered them.
    prompt_line: str

    def summary_line(self, count: int) -> str:
        try:
            body = self.prompt_line.format(count=count)
        except (IndexError, KeyError, ValueError):
            # A malformed template must not take the prompt down with it, and
            # must not silently drop the collection either.
            body = self.prompt_line
        return f"- {body}"


def _read_collection(root: Path) -> SkillCollection | None:
    """Return the collection a bundled subdirectory declares, if any."""

    marker = root / COLLECTION_MARKER
    if not marker.is_file():
        return None
    try:
        declared = json.loads(marker.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(declared, dict):
        return None
    identifier = str(declared.get("id") or root.name).strip() or root.name
    prompt_line = str(declared.get("prompt_line") or "").strip()
    if not prompt_line:
        prompt_line = (
            f"{identifier} collection: {{count}} pinned third-party recipes are "
            "available on demand. Use search_skills before writing a pipeline "
            "in this area, and list_skills only when exact enumeration is "
            "required."
        )
    return SkillCollection(id=identifier, root=root, prompt_line=prompt_line)


def _bootstrap_runtime_code(
    manifest: dict,
    roots: list[str],
    denied: frozenset[str] = frozenset(),
    collection_prefixes: frozenset[str] = frozenset(),
) -> str:
    """Generate the in-kernel import gate/tracker for one manifest snapshot.

    `denied` is the capability allowlist's complement, by directory name.
    The gate knew only `disabled` -- the user's own on/off switch -- so a
    skill a Specialist policy withheld was still importable inside the
    child's cell. The allowlist closed the Host RPC and left the sidecar,
    which is the half that runs code.

    `collection_prefixes` names the collection roots that are themselves a
    package under another root -- ``skills/bioskills`` is importable as
    ``bioskills`` because its parent ``skills/`` is on ``sys.path``. The gate
    keys on the leaf directory, so without this the second segment of
    ``bioskills.<dir>.<module>`` was never looked up at all: ``find_spec`` saw
    ``top == 'bioskills'``, found no entry, returned ``None``, and ordinary
    ``PathFinder`` imported a withheld or disabled recipe's code. Both spellings
    must resolve to the same skill identity before any decision is taken.
    """

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
    # Only entries the tracked loader can actually use are embedded. The gate
    # needs a skill's *identity* to deny or disable an import, which is one
    # short directory string; it needs the full entry only to hash, record and
    # mark a sidecar. 561 of the 597 bundled Skills carry no `kernel.py`, so
    # `repr()`-ing the whole manifest into the generated source shipped a
    # quarter-megabyte -- compiled at every kernel start, stored verbatim as
    # `init_hooks` in the durable generation record, and copied into
    # `generation_refs` of every cursor checkpoint -- to feed a sidecar gate
    # that can never fire for them.
    sidecar_entries = [
        entry for entry in entries if (entry.get("sidecar") or {}).get("present")
    ]
    embedded = {
        "manifest_id": manifest.get("manifest_id"),
        "kind": manifest.get("kind"),
        "entries": sidecar_entries,
        "load_events": manifest.get("load_events") or [],
    }
    # Keep this generated snippet self-contained: a scientific kernel may not
    # import openai4s internals from its selected environment.
    return (
        "import base64 as _o4s_base64\n"
        "import hashlib as _o4s_hashlib\n"
        "import importlib.abc as _o4s_abc\n"
        "import importlib.machinery as _o4s_machinery\n"
        "import sys as _o4s_sys\n"
        "import time as _o4s_time\n"
        f"__openai4s_skill_bootstrap_manifest__ = {embedded!r}\n"
        "__openai4s_skill_load_events__ = "
        "__openai4s_skill_bootstrap_manifest__['load_events']\n"
        f"_o4s_skill_roots = {roots!r}\n"
        f"_o4s_skill_dirs = {set(known)!r}\n"
        "_o4s_skill_entries = {\n"
        "    _o4s_entry['directory']: _o4s_entry\n"
        "    for _o4s_entry in "
        "__openai4s_skill_bootstrap_manifest__['entries']\n"
        "}\n"
        f"_o4s_disabled_skills = {disabled!r}\n"
        f"_o4s_denied_skills = {set(denied)!r}\n"
        f"_o4s_collection_prefixes = {set(collection_prefixes)!r}\n"
        "for _o4s_root in reversed(_o4s_skill_roots):\n"
        "    if _o4s_root not in _o4s_sys.path:\n"
        "        _o4s_sys.path.insert(0, _o4s_root)\n"
        "for _o4s_module in list(_o4s_sys.modules):\n"
        "    _o4s_head = _o4s_module.partition('.')[0]\n"
        "    if (\n"
        "        _o4s_head in _o4s_skill_dirs\n"
        "        or _o4s_head in _o4s_collection_prefixes\n"
        "    ):\n"
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
        "        spec = getattr(module, '__spec__', None)\n"
        "        source_path = getattr(spec, 'origin', None)\n"
        "        get_data = getattr(self._delegate, 'get_data', None)\n"
        "        if not source_path or not callable(get_data):\n"
        "            raise ImportError('Skill sidecar source cannot be frozen')\n"
        "        source = get_data(source_path)\n"
        "        if not isinstance(source, bytes):\n"
        "            raise ImportError('Skill sidecar loader returned non-bytes')\n"
        "        if len(source) > 2_000_000:\n"
        "            raise ImportError('Skill sidecar exceeds 2MB capture limit')\n"
        "        captured = sum(\n"
        "            len(item.get('source_b64'))\n"
        "            for item in __openai4s_skill_load_events__\n"
        "            if isinstance(item, dict)\n"
        "            and isinstance(item.get('source_b64'), str)\n"
        "        )\n"
        "        if captured + ((len(source) + 2) // 3 * 4) > 10_000_000:\n"
        "            raise ImportError('Skill sidecar capture budget exceeded')\n"
        "        sidecar = self._entry.get('sidecar') or {}\n"
        "        expected_sha256 = sidecar.get('sha256')\n"
        "        actual_sha256 = _o4s_hashlib.sha256(source).hexdigest()\n"
        "        if not expected_sha256 or actual_sha256 != expected_sha256:\n"
        "            raise ImportError(\n"
        "                'Skill sidecar changed after bootstrap; restart the '\n"
        "                'kernel to accept a new capability manifest'\n"
        "            )\n"
        "        code = compile(source, source_path, 'exec')\n"
        "        exec(code, module.__dict__)\n"
        "        sidecar['loaded'] = True\n"
        "        sidecar['loaded_sha256'] = actual_sha256\n"
        "        event = {\n"
        "            'event': 'sidecar_loaded',\n"
        "            'skill_name': self._entry.get('name') or self._skill_name,\n"
        "            'module': module.__name__,\n"
        "            'version': self._entry.get('version'),\n"
        "            'expected_sha256': sidecar.get('sha256'),\n"
        "            'sha256': actual_sha256,\n"
        "            'source_b64': _o4s_base64.b64encode(source).decode('ascii'),\n"
        "            'source_path': source_path,\n"
        "            'order': len(__openai4s_skill_load_events__),\n"
        "            'exports': [],\n"
        "            'import_mode': 'module',\n"
        "            'loaded_at_ns': _o4s_time.time_ns(),\n"
        "        }\n"
        "        __openai4s_skill_load_events__.append(event)\n"
        "    def __getattr__(self, name):\n"
        "        return getattr(self._delegate, name)\n"
        "class _OpenAI4SSkillGate(_o4s_abc.MetaPathFinder):\n"
        "    _openai4s_skill_gate = True\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        head, _o4s_dot, _o4s_rest = fullname.partition('.')\n"
        "        if head in _o4s_collection_prefixes and _o4s_rest:\n"
        "            top = _o4s_rest.partition('.')[0]\n"
        "            sidecar_module = head + '.' + top + '.kernel'\n"
        "        else:\n"
        "            top = head\n"
        "            sidecar_module = top + '.kernel'\n"
        "        if top not in _o4s_skill_dirs:\n"
        "            return None\n"
        "        entry = _o4s_skill_entries.get(top)\n"
        "        if top in _o4s_denied_skills:\n"
        "            raise ModuleNotFoundError(\n"
        '                f"skill sidecar {top!r} is not available to this agent"\n'
        "            )\n"
        "        if top in _o4s_disabled_skills:\n"
        "            raise ModuleNotFoundError(\n"
        '                f"skill sidecar {top!r} is disabled by capability policy"\n'
        "            )\n"
        "        spec = _o4s_machinery.PathFinder.find_spec(fullname, path)\n"
        "        if (\n"
        "            spec is not None and entry is not None\n"
        "            and fullname == sidecar_module\n"
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
            capabilities = CapabilityStateService(
                _StoreCapabilityRepository(self.cfg.db_path),
                project_id=project_id,
                session_id=session_id,
            )
        elif project_id is not None or session_id is not None:
            capabilities = capabilities.scoped(
                project_id=project_id,
                session_id=session_id,
            )
        self.capabilities = capabilities
        self.project_id = project_id or getattr(capabilities, "project_id", None)
        self.session_id = session_id or getattr(capabilities, "session_id", None)
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
            project_id=(self.project_id if project_id is None else project_id),
            session_id=(self.session_id if session_id is None else session_id),
        )

    def user_skills_dir(self) -> Path:
        """Writable dir for user-authored skills (kept separate from the bundled
        read-only skills). Discovered alongside the bundled ones."""
        return self.cfg.data_dir / "user-skills"

    def project_skills_dir(self) -> Path | None:
        """Writable project overlay, isolated by a hashed project identity."""

        if not self.project_id:
            return None
        return project_skills_root(self.cfg, self.project_id)

    def collections(self) -> dict[str, SkillCollection]:
        """Every bundled collection, discovered from its own marker file.

        Ordinary OpenAI4S Skills remain one directory below ``skills/``. A
        collection is a directory that declares itself with
        ``COLLECTION.json`` and holds its members one level lower, which lets
        its provenance, license and retrieval guidance live at a single stable
        boundary instead of pretending N independently maintained packages.
        """

        found: dict[str, SkillCollection] = {}
        if not self.skills_dir.is_dir():
            return found
        for child in sorted(self.skills_dir.iterdir()):
            if not child.is_dir():
                continue
            collection = _read_collection(child)
            if collection is not None and collection.id not in found:
                found[collection.id] = collection
        return found

    def bundled_roots(self) -> tuple[tuple[Path, str | None], ...]:
        """Return maintained bundled roots and their optional collection id."""

        roots: list[tuple[Path, str | None]] = [(self.skills_dir, None)]
        for collection in self.collections().values():
            roots.append((collection.root, collection.id))
        return tuple(roots)

    @staticmethod
    def parse_document(content: str) -> tuple[dict, str]:
        """Parse one SKILL.md document with the loader's frontmatter rules."""

        return _parse_frontmatter(content)

    def discover(self) -> dict[str, Skill]:
        # Build into a fresh local map and publish it with a single atomic
        # reference swap at the end.  A concurrent reader (search()/get()/
        # catalog(), or another discover() from a parallel skill-read tool)
        # then observes either the complete old map or the complete new one —
        # never a dict being cleared and repopulated in place, which raised
        # "dictionary changed size during iteration".
        discovered: dict[str, Skill] = {}
        # bundled skills first, then user-authored ones. A user skill must NOT
        # silently shadow a trusted BUNDLED skill by directory or declared
        # canonical name. Bundled wins on collision, otherwise the agent could
        # load untrusted content under a trusted capability identity.
        claimed_names: set[str] = set()
        roots: list[tuple[str, Path, str | None]] = [
            ("bundled", root, collection) for root, collection in self.bundled_roots()
        ]
        project_root = self.project_skills_dir()
        if project_root is not None:
            roots.append(("project", project_root, None))
        roots.append(("user", self.user_skills_dir(), None))
        for source, base, collection in roots:
            if not base or not base.exists():
                continue
            is_writable = source in {"user", "project"}
            for child in sorted(base.iterdir()):
                if not child.is_dir():
                    continue
                md = child / "SKILL.md"
                if not md.exists():
                    continue
                if is_writable and child.name in discovered:
                    continue  # bundled skill already claimed this name — keep it
                raw = md.read_text("utf-8")
                meta, body = _parse_frontmatter(raw)
                origin = (meta.get("origin") or "unknown").lower()
                if is_writable:
                    # User-space files cannot claim a trusted bundled origin.
                    # Preserve the host lifecycle's draft -> personal states;
                    # Web-authored documents use the separate ``user`` state.
                    origin = origin if origin in {"draft", "personal"} else "user"
                elif origin not in _VALID_ORIGINS:
                    origin = "unknown"
                description = meta.get("description") or _first_paragraph(body)
                description = " ".join(description.split())  # collapse whitespace
                if len(description) > 200:
                    description = description[:197] + "..."
                name = meta.get("name") or child.name
                canonical_name = _canonical_skill_name(name)
                if canonical_name in claimed_names:
                    continue
                document_sha256 = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                sidecar = child / "kernel.py"
                # One stat, reused below for `has_kernel`. At 597 skills the
                # duplicated `(child / "kernel.py").exists()` was 597 extra
                # syscalls on a path walked by every Host skill RPC.
                has_sidecar = sidecar.exists()
                sidecar_sha256 = None
                if has_sidecar:
                    try:
                        sidecar_sha256 = hashlib.sha256(
                            sidecar.read_bytes()
                        ).hexdigest()
                    except OSError:
                        sidecar_sha256 = None
                version = str(meta.get("version") or "").strip()
                if not version:
                    version = (sidecar_sha256 or document_sha256)[:12]
                discovered[child.name] = Skill(
                    name=name,
                    root=child,
                    doc=body,
                    has_kernel=has_sidecar,
                    description=description,
                    origin=origin,
                    source=source,
                    keywords=_tokenize(name, description, body),
                    requirements=_requirements(meta.get("requirements")),
                    version=version,
                    document_sha256=document_sha256,
                    sidecar_sha256=sidecar_sha256,
                    collection=collection,
                )
                claimed_names.add(canonical_name)
        self._skills = discovered
        return self._skills

    def bundled_name_collision(self, name: str) -> Skill | None:
        """Return the bundled owner of a declared-name identity, if any."""

        wanted = _canonical_skill_name(name)
        if not wanted:
            return None
        for skill in self.discover().values():
            if skill.read_only and _canonical_skill_name(skill.name) == wanted:
                return skill
        return None

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

    def resolve(
        self,
        name: str,
        *,
        permits: "Callable[[str], bool] | None" = None,
    ) -> Skill | None:
        """Resolve a requested name to a Skill, with a GUARDED fuzzy fallback.

        `get()` handles the two exact identities (directory key, declared
        name). The historical fallback then asked the lexical index for its
        single best match, which is fine over 36 curated recipes and actively
        misleading over 597: `load("alpha-fold2")` returned
        `bio-crispr-screens-mageck-analysis` and `load("boltz2")` returned
        `bio-ml-docking-rescoring` -- a different skill's full recipe, under a
        `name` the caller did not ask for, from a tool whose whole contract is
        "load one Skill's guidance BY NAME".

        The distinction is what the request looks like, not how well it
        scores. A single bare token IS a name -- a near-miss for one, usually
        a typo -- so it has to match on the candidate's *name*; that keeps
        `proteinMPNN`, `retrosynthesis` and `literature` working and refuses
        `boltz2` and `esmfold` rather than answering them with something else.
        A multi-word phrase is a description, and matching it against recipe
        bodies is exactly what the fallback is for, so `Fourier signal` still
        resolves to `spectral`.
        """

        skill = self.get(name)
        if skill is not None:
            return skill
        requested = str(name or "").strip()
        wanted = _tokenize(requested)
        if not wanted:
            return None
        name_shaped = len(requested.split()) == 1
        for hit in self.search(requested, limit=5, permits=permits):
            candidate = self.get(str(hit.get("name") or ""))
            if candidate is None:
                continue
            if not name_shaped or wanted <= _tokenize(candidate.name):
                return candidate
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

    def bootstrap_code(self, *, allowed: frozenset[str] | None = None) -> str:
        """Return a scoped sidecar import path, deny gate, and truthful tracker.

        `allowed` is by skill *name*; the gate keys on directory, so the
        complement is computed here from the manifest rather than reimplemented
        in the generator. `None` means unrestricted and yields the previous
        bytes exactly -- `bootstrap_manifest_id` is a durable recovery key, so
        the manifest itself must not move.
        """

        manifest = self.bootstrap_manifest()
        denied: frozenset[str] = frozenset()
        if allowed is not None:
            denied = frozenset(
                str(entry.get("directory"))
                for entry in (manifest.get("entries") or [])
                if entry.get("directory") and entry.get("name") not in allowed
            )
        bundled = self.bundled_roots()
        roots = [str(root) for root, _collection in bundled]
        # A collection root lives *under* `skills/`, which is itself on
        # sys.path, so its directory name is a second importable spelling of
        # every skill inside it. The gate has to know both or it guards one.
        collection_prefixes = frozenset(
            root.name for root, collection in bundled if collection
        )
        project_root = self.project_skills_dir()
        if project_root is not None:
            roots.append(str(project_root))
        roots.append(str(self.user_skills_dir()))
        return _bootstrap_runtime_code(manifest, roots, denied, collection_prefixes)

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

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        permits: "Callable[[str], bool] | None" = None,
    ) -> list[dict]:
        """Keyword-overlap skill retrieval (openai4s's search_skills route).

        Scores each skill by literal token overlap between the query and the
        skill's name/description/body. Purely lexical — no synonym expansion —
        matching the documented limitation of the skill-retrieval prompt.
        Returns the full doc of the top matches so the agent can then use them.

        `permits` is the caller's allowlist predicate, applied AFTER scoring
        and BEFORE the limit slice. Ranking still happens over the whole
        corpus, so a permitted skill keeps the position it earned -- but the
        caller gets `limit` results it can actually open. Slicing first and
        filtering afterwards is what a Specialist saw instead: with 561
        collection recipes in the same lexical index, the global top 5 for
        "protein structure prediction and design pipeline" contains none of
        the two skills it is allowed, so a child whose allowlist is its whole
        reason to exist retrieved nothing at all.
        """
        q_tokens = _tokenize(query)
        scored: list[tuple[float, Skill]] = []
        for s in self.skills().values():
            if permits is not None and not permits(s.name):
                continue
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
                    # `origin` marks the read-only distribution boundary, not
                    # authorship: a vendored collection carries `openai4s`
                    # there too. `collection` is the only field on this
                    # surface that says the recipe is third-party, and search
                    # is the path the collection's own prompt line makes
                    # primary -- `doc` is the frontmatter-stripped body, so
                    # the upstream repository/commit/license never reach the
                    # model through it.
                    "collection": s.collection,
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
                "distribution_scope": s.source,
                "has_kernel": s.has_kernel,
                "enabled": self.is_enabled(s.name),
                # Deliberately beside `enabled` and deliberately not folded
                # into it. A disabled Skill can be perfectly ready and an
                # enabled one can be missing its hardware; merging them means a
                # user who enables a Skill believes they have made it work.
                "requirements": list(s.requirements),
                "readiness": skill_readiness(s.requirements),
                "version": s.version,
                "document_sha256": s.document_sha256,
                "sidecar_sha256": s.sidecar_sha256,
                # Public provenance/filtering metadata. None identifies the
                # ordinary curated/user catalog; "bioskills" identifies the
                # pinned third-party collection without changing Skill names.
                "collection": s.collection,
            }
            for s in self.skills(include_disabled=include_disabled).values()
        ]

    def system_context(self, *, only: frozenset[str] | None = None) -> str:
        """Progressive-disclosure block for the system prompt.

        Only skill NAMES + one-line summaries go here — NOT the full docs.
        The agent uses the declared native search_skills/load_skill functions
        when available, or their host.* counterparts inside a fenced Python
        Cell, to pull a skill's full recipe on demand: analytic tasks retrieve
        skills lazily instead of front-loading every doc into context.

        `only` is the caller's allowlist, and `None` means unrestricted, so the
        default output is byte-identical to before. It exists because a
        delegated child's prompt was rendered from the raw corpus: the exit
        criterion for the Specialist allowlist names the *prompt* as one of the
        surfaces an unlisted resource must be absent from, and advertising a
        skill the child cannot load is both a disclosure and an instruction to
        attempt something that will be refused.
        """
        skills = self.skills()
        if only is not None:
            skills = {
                name: skill for name, skill in skills.items() if skill.name in only
            }
        if not skills:
            return ""
        lines = [
            "# Available skills (progressive disclosure)",
            "These skills exist but their full instructions are NOT loaded yet. "
            "When a task looks relevant to one, use the declared native "
            "`search_skills` / `load_skill` functions when available. Inside a "
            "fenced Python Cell, use `host.search_skills(...)` / "
            "`host.load_skill(...)` instead. Retrieve the full recipe, then "
            "import its sidecar and use it. Do NOT invent a skill, API, or "
            "Cell-runner function. For enumeration or an all-Skills audit, use "
            "native `list_skills`, then native `load_skill` with each exact "
            "returned name; it lists curated Skills by name and each bundled "
            "collection as one entry, so pass `collection=<id>` (paging with "
            "`offset`) to enumerate a collection's members. Only inside a "
            "fenced Python Cell use "
            "`host.skills.list()`. Never use `list_dir` for the Skill catalog. "
            "Catalog metadata is not a path: do not use `read_text_file` or "
            "`glob_files` for Skill retrieval.",
            "",
        ]
        # `only` has already filtered `skills` above, so grouping is
        # unconditional and the aggregate line carries the POST-filter count.
        # Gating it on `only is None` switched the whole prompt-size fix off on
        # the delegation path -- the one surface `only=` exists for -- and a
        # child allowlisted to the collection got 134,759 bytes of summaries
        # where the parent got 8,902.
        collections: dict[str, list[Skill]] = {}
        for skill in skills.values():
            if skill.collection:
                collections.setdefault(skill.collection, []).append(skill)
            else:
                lines.append(skill.summary_line())
        # Drain EVERY bucket. Reading back only "bioskills" would divert any
        # other collection out of the per-skill branch above and then never
        # emit it, so a second root added through `bundled_roots()` would be
        # discoverable, enabled, searchable and loadable while being absent
        # from the prompt entirely -- silently undisclosed rather than
        # compactly disclosed, with no error anywhere to say so.
        declared = self.collections()
        for collection_id, members in sorted(collections.items()):
            if not members:
                continue
            collection = declared.get(collection_id)
            if collection is None:
                collection = SkillCollection(
                    id=collection_id,
                    root=self.skills_dir / collection_id,
                    prompt_line=(
                        f"{collection_id} collection: {{count}} pinned "
                        "third-party recipes are available on demand."
                    ),
                )
            lines.append(collection.summary_line(len(members)))
        return "\n".join(lines)


def discover_skills(
    skills_dir: Path | None = None, cfg: Config | None = None
) -> dict[str, Skill]:
    return SkillLoader(skills_dir, cfg).discover()
