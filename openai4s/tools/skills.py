"""Progressive-disclosure Skill control tools."""

from __future__ import annotations

from typing import Any

from openai4s.tools.base import Tool
from openai4s.tools.contexts import ControlToolContext
from openai4s.tools.taxonomy import RUNTIME_MUTATION, resource_key


class ListSkillsTool(Tool):
    """List the complete Skill catalog visible to the current agent."""

    name = "list_skills"
    # Keep the native projection separate from the SDK's ``skills_list``
    # method: host.skills.list() returns full metadata and is a public Cell
    # contract, while this control-plane view is deliberately compact.
    host_method = "list_skills"
    description = (
        "Return the exact count and names of every Skill available to this agent. "
        "Curated Skills are listed by name; each bundled collection is one entry "
        "with its id and size. To enumerate a collection, call again with "
        "collection=<id> (and offset to page through it). "
        "For all-Skills audits, then call load_skill with each returned name. "
        "Do not use workspace file tools."
    )
    parameters = {
        "properties": {
            "collection": {
                "type": "string",
                "description": "Enumerate this collection's Skill names instead.",
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "description": "Start index when paging a collection listing.",
            },
        },
        "required": [],
    }
    requires_approval = False
    #: Names per page when enumerating one collection. Chosen so a full page
    #: stays inside `output_limit` with room for the wrapper.
    page_size = 150
    output_limit = 10_000
    resource_key_prefix = "skill"
    resource_target_default = "catalog"

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        # The Host catalog also carries descriptions, readiness, content
        # digests, and version ids for UI reconciliation. The full catalog JSON
        # exceeds the bounded model-observation window and is archived to an
        # internal blob; Ark then mistook that archive hint for a workspace
        # path. Native enumeration needs only stable names. Full metadata stays
        # available through host.skills.list() inside a foreground Cell.
        #
        # A bundled collection is ONE entry here, the same way it is one line
        # in the system prompt. Listing its members as peers of `alphafold2`
        # was what forced the truncation ceiling up in the first place: the
        # catalog the rest of the system treats as ~36 curated recipes is not
        # the same object as a pinned 561-recipe import.
        spec = arguments if isinstance(arguments, dict) else {}
        rows = runtime.invoke(self.host_method)
        wanted = str(spec.get("collection") or "").strip()
        if wanted:
            names = [
                str(row.get("name") or "")
                for row in rows
                if str(row.get("collection") or "") == wanted
            ]
            names = [name for name in names if name]
            try:
                offset = max(0, int(spec.get("offset") or 0))
            except (TypeError, ValueError):
                offset = 0
            page = names[offset : offset + self.page_size]
            result: dict[str, Any] = {
                "collection": wanted,
                "count": len(names),
                "offset": offset,
                "names": page,
            }
            if offset + len(page) < len(names):
                result["next_offset"] = offset + len(page)
            return result

        curated = [
            str(row.get("name") or "") for row in rows if not row.get("collection")
        ]
        curated = [name for name in curated if name]
        counts: dict[str, int] = {}
        for row in rows:
            identifier = str(row.get("collection") or "")
            if identifier:
                counts[identifier] = counts.get(identifier, 0) + 1
        collections = [
            {"id": identifier, "count": counts[identifier]}
            for identifier in sorted(counts)
        ]
        return {
            "count": len(curated) + sum(counts.values()),
            "names": curated,
            "collections": collections,
        }


class SearchSkillsTool(Tool):
    """Retrieve full recipes only when a task needs them."""

    name = "search_skills"
    host_method = "search_skills"
    description = "Find relevant Skills and load their full recipes on demand."
    parameters = {
        "properties": {
            "query": {
                "type": "string",
                "minLength": 1,
                "description": "Keywords describing the needed method or workflow.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "description": "Maximum matching recipes to return (default 5).",
            },
        },
        "required": ["query"],
    }
    requires_approval = False
    # Each hit carries a full recipe, and the imported collection's documents
    # are ~3x the curated median (18k vs 5k chars), so the inherited 20k
    # default cut a default `limit=5` search from 101,701 chars down to a
    # single visible hit -- on the very path the collection's prompt line
    # makes primary.
    output_limit = 50_000
    #: Ceiling on the recipe text one result set may carry.
    doc_budget = 44_000
    #: Reserved per hit for everything that is not `doc` -- name, description,
    #: import hint, score, sidecar gate, JSON punctuation. `limit` reaches 20,
    #: so at the wide end this reserve is what keeps the whole set inside
    #: `output_limit`.
    row_overhead = 700
    #: Preferred floor per hit; a stub too small to judge relevance by is the
    #: same as a dropped hit. Lowered automatically when the budget cannot pay
    #: for it, because a short hit still beats an invisible one.
    min_doc_chars = 2_000
    resource_key_prefix = "skill"
    resource_target_key = "query"

    def execute(self, runtime: ControlToolContext, arguments: dict) -> list:
        rows = runtime.invoke(
            self.host_method,
            {
                "query": arguments.get("query", ""),
                "limit": int(arguments.get("limit") or 5),
            },
        )
        return self.fit_to_budget(rows)

    def fit_to_budget(self, rows: Any) -> Any:
        """Shorten the longest recipes instead of losing the last hits.

        ``format_tool_result`` truncates the rendered result from the tail, so
        an oversized result set does not degrade -- it deletes hits, names and
        all, mid-JSON. Ranking them and then letting the transport decide which
        survive is the worst of both. Small docs are handed back whole and only
        the long ones are shortened, each with an explicit pointer at
        `load_skill`, which returns the complete document.
        """

        if not isinstance(rows, list):
            return rows
        indexed = [
            (index, row) for index, row in enumerate(rows) if isinstance(row, dict)
        ]
        if not indexed:
            return rows
        total = sum(len(str(row.get("doc") or "")) for _i, row in indexed)
        if total + self.row_overhead * len(indexed) + 2_000 <= self.output_limit:
            return rows

        budget = min(
            self.doc_budget,
            max(0, self.output_limit - self.row_overhead * len(indexed) - 2_000),
        )
        floor = min(self.min_doc_chars, budget // len(indexed))
        allowances: dict[int, int] = {}
        remaining = budget
        # Shortest first, so a small doc never forfeits budget it cannot use
        # and the surplus flows to the documents that actually need it.
        pending = sorted(indexed, key=lambda pair: len(str(pair[1].get("doc") or "")))
        for position, (index, row) in enumerate(pending):
            share = max(floor, remaining // (len(pending) - position))
            allowance = min(len(str(row.get("doc") or "")), share)
            allowances[index] = allowance
            remaining = max(0, remaining - allowance)

        out = list(rows)
        for index, row in indexed:
            doc = str(row.get("doc") or "")
            allowance = allowances[index]
            if len(doc) <= allowance:
                continue
            name = str(row.get("name") or "")
            out[index] = {
                **row,
                "doc": doc[:allowance]
                + f"\n\n… [{len(doc) - allowance} more characters. This recipe was "
                + f'shortened to fit the result set; call load_skill("{name}") '
                + "for the complete document.]",
            }
        return out


class LoadSkillTool(Tool):
    """Load one exact/fuzzy Skill document through the scoped loader."""

    name = "load_skill"
    host_method = "load_skill"
    description = "Load one Skill's complete SKILL.md guidance by name."
    parameters = {
        "properties": {
            "name": {
                "type": "string",
                "minLength": 1,
                "description": "Skill name from the available-skill catalog.",
            }
        },
        "required": ["name"],
    }
    requires_approval = False
    # This tool's whole promise is "complete SKILL.md guidance". Under the
    # inherited 20k default, 210 of the 597 bundled documents came back ending
    # in a truncation marker -- the largest is 42,452 chars -- so the agent
    # read a protocol's setup and lost its validation and caveat tail while
    # believing it had the whole recipe. 50k covers every bundled document.
    output_limit = 50_000
    resource_key_prefix = "skill"
    resource_target_key = "name"

    def resource_keys(self, arguments: Any) -> tuple[str, ...]:
        name = (
            arguments if isinstance(arguments, str) else (arguments or {}).get("name")
        )
        return (resource_key("skill", name or "*"),)

    def execute(
        self,
        runtime: ControlToolContext,
        arguments: dict | str,
    ) -> dict:
        name = (
            arguments
            if isinstance(arguments, str)
            else str((arguments or {}).get("name") or "")
        )
        return runtime.invoke(self.host_method, name)


class SkillStatusTool(Tool):
    """Inspect one exact personal/project Skill activation without reading bytes."""

    name = "skill_status"
    host_method = "skills_status"
    description = "Inspect the active version and safe manifest for one Skill scope."
    parameters = {
        "properties": {
            "name": {
                "type": "string",
                "minLength": 1,
                "description": "Exact declared Skill name.",
            },
            "scope": {
                "type": "string",
                "enum": ["personal", "project"],
                "description": "Personal library or the current project overlay.",
            },
        },
        "required": ["name", "scope"],
    }
    requires_approval = False
    resource_key_prefix = "skill"
    resource_target_key = "name"

    def resource_keys(self, arguments: Any) -> tuple[str, ...]:
        arguments = arguments if isinstance(arguments, dict) else {}
        target = (
            f"{arguments.get('scope') or 'personal'}/{arguments.get('name') or '*'}"
        )
        return (resource_key("skill", target),)

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        return runtime.invoke(
            self.host_method,
            {
                "name": str(arguments.get("name") or ""),
                "scope": str(arguments.get("scope") or ""),
            },
        )


class SkillHistoryTool(SkillStatusTool):
    """List immutable Skill versions and lifecycle events without source bytes."""

    name = "skill_history"
    host_method = "skills_history"
    description = "List immutable versions and install/publish/rollback events."
    parameters = {
        "properties": {
            **SkillStatusTool.parameters["properties"],
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 200,
                "description": "Maximum lifecycle events to return (default 50).",
            },
        },
        "required": ["name", "scope"],
    }
    provider_strict = False

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        return runtime.invoke(
            self.host_method,
            {
                "name": str(arguments.get("name") or ""),
                "scope": str(arguments.get("scope") or ""),
                "limit": int(arguments.get("limit") or 50),
            },
        )


class RollbackSkillVersionTool(SkillStatusTool):
    """Human-approved pointer change to a retained immutable Skill version."""

    name = "rollback_skill_version"
    host_method = "skills_rollback"
    description = (
        "Roll back a writable personal/project Skill to a retained version. "
        "Bundled Skills are immutable."
    )
    parameters = {
        "properties": {
            **SkillStatusTool.parameters["properties"],
            "version_id": {
                "type": "string",
                "minLength": 71,
                "maxLength": 71,
                "description": "Exact version_id returned by skill_history.",
            },
        },
        "required": ["name", "scope", "version_id"],
    }
    read_only = False
    requires_approval = True
    side_effect_class = RUNTIME_MUTATION

    def permission_target(self, arguments: Any) -> str:
        arguments = arguments if isinstance(arguments, dict) else {}
        return (
            f"{arguments.get('scope') or 'personal'}/"
            f"{arguments.get('name') or '*'}/"
            f"{arguments.get('version_id') or '*'}"
        )

    def native_precheck(self, arguments: dict) -> str | None:
        version_id = str(arguments.get("version_id") or "")
        digest = version_id.removeprefix("skillv-")
        if (
            not version_id.startswith("skillv-")
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            return "version_id must be 'skillv-' followed by 64 lowercase hex digits"
        return None

    def execute(self, runtime: ControlToolContext, arguments: dict) -> dict:
        return runtime.invoke(
            self.host_method,
            {
                "name": str(arguments.get("name") or ""),
                "scope": str(arguments.get("scope") or ""),
                "version_id": str(arguments.get("version_id") or ""),
            },
        )


__all__ = [
    "ListSkillsTool",
    "LoadSkillTool",
    "RollbackSkillVersionTool",
    "SearchSkillsTool",
    "SkillHistoryTool",
    "SkillStatusTool",
]
