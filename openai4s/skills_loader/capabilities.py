"""Skill network capability schema, canonical digest, and offline readiness.

A Skill may declare ``capabilities.network`` in frontmatter:

    capabilities:
      network:
        mode: none | host_only | raw_required
        domains: []

The declaration is a requirement, never an authorization. Domains describe
intended ``host_only`` destinations; every request still goes through the
existing user grant, ``check_url()``, ``domain_allowed()``, and per-redirect
checks. Built-in ``raw_required`` is fail-closed this version, including when
``OPENAI4S_KERNEL_ALLOW_RAW_NETWORK`` is set.

Pinned third-party collections (``skills/bioskills/``) are not edited. Their
explicit mode comes from ``bundled_network_inventory.json`` on this package
and is tagged ``legacy`` so a later upstream field cannot grant access.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

NETWORK_MODES = frozenset({"none", "host_only", "raw_required"})
DECLARATIONS = frozenset({"declared", "legacy", "unknown"})
UNKNOWN_MODE = "unknown"


class InvalidSkillCapability(ValueError):
    """Illegal closed-set Skill capability on the import path.

    ``parse_network_frontmatter`` degrades unknown modes to ``unknown`` so a
    third-party historical Skill still loads. Import must not: a bad mode is
    a 400, not a silent grant-shaped unknown.
    """


_INVENTORY_NAME = "bundled_network_inventory.json"
_RAW_NETWORK_ENV = "OPENAI4S_KERNEL_ALLOW_RAW_NETWORK"
_SANDBOX_ENV = "OPENAI4S_KERNEL_SANDBOX"

_UI_LABELS = {
    "none": {
        "en": "no network required",
        "zh": "无需网络",
    },
    "host_only": {
        "en": "host-mediated network only",
        "zh": "仅 Host 网络",
    },
    "raw_required": {
        "en": "raw network required and currently blocked",
        "zh": "需要 raw 网络且当前被阻止",
    },
    UNKNOWN_MODE: {
        "en": "network requirements unknown",
        "zh": "网络需求未知",
    },
}


def canonical_network_digest(mode: str, domains: tuple[str, ...] | list[str]) -> str:
    """Stable SHA-256 of the authorization-relevant network declaration."""

    payload = {
        "mode": str(mode or UNKNOWN_MODE),
        "domains": sorted(
            {str(item).strip().lower() for item in domains if str(item).strip()}
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _norm_domain(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if "://" in text:
        from openai4s.egress import domain_of

        text = domain_of(text) or text
    text = text.rstrip(".")
    if text.startswith("[") and "]" in text:
        text = text[1 : text.index("]")]
    text = text.split("/", 1)[0]
    text = text.split(":", 1)[0]
    if text.startswith("*."):
        text = text[2:]
    return text


def _parse_bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


@dataclass(frozen=True)
class NetworkCapability:
    """Closed-set network declaration for one Skill."""

    mode: str
    domains: tuple[str, ...]
    declaration: str
    source: str
    digest: str
    explicit: bool

    def public_dict(self) -> dict[str, Any]:
        mode = self.mode if self.mode in NETWORK_MODES else UNKNOWN_MODE
        labels = _UI_LABELS.get(mode, _UI_LABELS[UNKNOWN_MODE])
        ui_state = {
            "none": "no_network",
            "host_only": "host_only",
            "raw_required": "raw_required_blocked",
        }.get(mode, "unknown")
        return {
            "network": {
                "mode": mode,
                "domains": list(self.domains),
                "declaration": self.declaration,
                "source": self.source,
                "digest": self.digest,
                "explicit": self.explicit,
                "ui": {
                    "state": ui_state,
                    "label_en": labels["en"],
                    "label_zh": labels["zh"],
                },
            }
        }

    @property
    def grants_nothing(self) -> bool:
        return True


def unknown_capability(
    *, source: str = "missing", declaration: str = "unknown"
) -> NetworkCapability:
    mode = UNKNOWN_MODE
    domains: tuple[str, ...] = ()
    return NetworkCapability(
        mode=mode,
        domains=domains,
        declaration=declaration if declaration in DECLARATIONS else "unknown",
        source=source,
        digest=canonical_network_digest(mode, domains),
        explicit=source in {"inventory", "frontmatter"},
    )


def declared_capability(
    mode: str, domains: tuple[str, ...] | list[str], *, source: str
) -> NetworkCapability:
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in NETWORK_MODES:
        return unknown_capability(source=source, declaration="unknown")
    normalized_domains = tuple(
        sorted({item for item in (_norm_domain(d) for d in domains) if item})
    )
    return NetworkCapability(
        mode=normalized_mode,
        domains=normalized_domains,
        declaration="declared",
        source=source,
        digest=canonical_network_digest(normalized_mode, normalized_domains),
        explicit=True,
    )


def legacy_capability(*, source: str = "inventory") -> NetworkCapability:
    """Third-party / collection Skills: explicit, never granting."""

    mode = UNKNOWN_MODE
    domains: tuple[str, ...] = ()
    return NetworkCapability(
        mode=mode,
        domains=domains,
        declaration="legacy",
        source=source,
        digest=canonical_network_digest(mode, domains),
        explicit=True,
    )


def validate_network_frontmatter_strict(raw_text: str) -> NetworkCapability | None:
    """Reject illegal ``capabilities.network`` instead of degrading to unknown.

    Absence of the field is allowed and returns ``None``. Present-but-invalid
    values raise ``InvalidSkillCapability``. Call this on the import write
    path; do not replace ``parse_network_frontmatter``, which remains the
    tolerant reader for already-stored third-party Skills.
    """

    block = _frontmatter_block(raw_text)
    if block is None:
        return None
    parsed = _parse_capabilities_network(block)
    if parsed is None or parsed.get("missing"):
        return None
    mode = parsed.get("mode")
    domains = parsed.get("domains")
    if not isinstance(mode, str) or not mode.strip():
        raise InvalidSkillCapability(
            "capabilities.network.mode must be one of "
            + ", ".join(sorted(NETWORK_MODES))
        )
    normalized = mode.strip().lower()
    if normalized not in NETWORK_MODES:
        raise InvalidSkillCapability(
            "capabilities.network.mode must be one of "
            + ", ".join(sorted(NETWORK_MODES))
        )
    if domains is None:
        domains = []
    if not isinstance(domains, list):
        raise InvalidSkillCapability("capabilities.network.domains must be a list")
    cleaned: list[str] = []
    for item in domains:
        if not isinstance(item, str) or not str(item).strip():
            raise InvalidSkillCapability(
                "capabilities.network.domains must be a list of domains"
            )
        cleaned.append(item)
    return declared_capability(normalized, cleaned, source="frontmatter")


def parse_network_frontmatter(raw_text: str) -> NetworkCapability | None:
    """Return a declared capability from SKILL.md, or None if the field is absent.

    Malformed ``capabilities.network`` becomes ``unknown`` rather than a grant.
    The general frontmatter parser ignores nested mappings on purpose; this
    reader understands only the closed network schema.
    """

    block = _frontmatter_block(raw_text)
    if block is None:
        return None
    parsed = _parse_capabilities_network(block)
    if parsed is None:
        return None
    if parsed.get("missing"):
        return None
    mode = parsed.get("mode")
    domains = parsed.get("domains")
    if not isinstance(mode, str) or not mode.strip():
        return unknown_capability(source="frontmatter", declaration="unknown")
    if domains is None:
        domains = []
    if not isinstance(domains, list):
        return unknown_capability(source="frontmatter", declaration="unknown")
    return declared_capability(mode, domains, source="frontmatter")


def _frontmatter_block(text: str) -> str | None:
    if not str(text or "").startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    return text[3:end]


def _parse_capabilities_network(block: str) -> dict[str, Any] | None:
    lines = block.splitlines()
    i = 0
    n = len(lines)
    found_capabilities = False
    while i < n:
        line = lines[i]
        if re.match(r"^capabilities\s*:", line):
            found_capabilities = True
            rest = line.split(":", 1)[1].strip()
            i += 1
            nested, i = _collect_indented(lines, i, base_indent=0)
            if rest and not nested:
                return {"missing": True}
            network = _extract_network_mapping(nested)
            if network is None:
                return {"missing": True}
            return network
        i += 1
    if not found_capabilities:
        return None
    return {"missing": True}


def _collect_indented(
    lines: list[str], start: int, *, base_indent: int
) -> tuple[list[str], int]:
    collected: list[str] = []
    i = start
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            collected.append(line)
            i += 1
            continue
        indent = len(line) - len(line.lstrip(" \t"))
        if indent <= base_indent:
            break
        collected.append(line)
        i += 1
    return collected, i


def _extract_network_mapping(nested_lines: list[str]) -> dict[str, Any] | None:
    i = 0
    n = len(nested_lines)
    while i < n:
        stripped = nested_lines[i].strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        indent = len(nested_lines[i]) - len(nested_lines[i].lstrip(" \t"))
        if re.match(r"^network\s*:", stripped):
            rest = stripped.split(":", 1)[1].strip()
            i += 1
            body, i = _collect_indented(nested_lines, i, base_indent=indent)
            if rest.startswith("{") and rest.endswith("}"):
                return _parse_inline_network(rest)
            return _parse_network_body(body, indent=indent)
        i += 1
    return None


def _parse_inline_network(rest: str) -> dict[str, Any]:
    inner = rest.strip()[1:-1]
    mode = None
    domains: list[str] = []
    for part in inner.split(","):
        if ":" not in part:
            continue
        key, _, value = part.partition(":")
        key = key.strip().lower()
        value = value.strip().strip("'\"")
        if key == "mode":
            mode = value
        elif key == "domains":
            cleaned = value.strip()
            if cleaned.startswith("[") and cleaned.endswith("]"):
                cleaned = cleaned[1:-1]
            domains = [
                item.strip().strip("'\"")
                for item in cleaned.split(",")
                if item.strip().strip("'\"")
            ]
    return {"mode": mode, "domains": domains}


def _parse_network_body(body: list[str], *, indent: int) -> dict[str, Any]:
    mode = None
    domains: list[str] | None = None
    i = 0
    n = len(body)
    while i < n:
        raw = body[i]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if ":" in stripped and not stripped.startswith("-"):
            key, _, value = stripped.partition(":")
            key = key.strip().lower()
            value = value.strip()
            key_indent = len(raw) - len(raw.lstrip(" \t"))
            if key == "mode":
                mode = value.strip("'\"")
            elif key == "domains":
                if value in {"", "[]", "~", "null"}:
                    domains = []
                    i += 1
                    extra, i = _collect_indented(body, i, base_indent=key_indent)
                    domains.extend(_parse_domain_list(extra))
                    continue
                if value.startswith("[") and value.endswith("]"):
                    inner = value[1:-1].strip()
                    domains = [
                        item.strip().strip("'\"")
                        for item in inner.split(",")
                        if item.strip().strip("'\"")
                    ]
                else:
                    domains = [value.strip("'\"")] if value else []
            i += 1
            continue
        i += 1
    return {"mode": mode, "domains": domains}


def _parse_domain_list(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- "):
            item = stripped[2:].strip().strip("'\"")
            if item:
                out.append(item)
        elif stripped.startswith("-"):
            item = stripped[1:].strip().strip("'\"")
            if item:
                out.append(item)
    return out


@lru_cache(maxsize=1)
def load_bundled_inventory() -> dict[str, Any]:
    path = Path(__file__).resolve().parent / _INVENTORY_NAME
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {"schema_version": 1, "collections": {}, "skills": {}}
    if not isinstance(payload, dict):
        return {"schema_version": 1, "collections": {}, "skills": {}}
    return payload


def inventory_capability(
    name: str, directory: str, collection: str | None
) -> NetworkCapability | None:
    inventory = load_bundled_inventory()
    collections = inventory.get("collections") or {}
    if collection and isinstance(collections, dict):
        entry = collections.get(collection)
        if isinstance(entry, dict):
            declaration = str(entry.get("declaration") or "legacy").strip().lower()
            if declaration in {"legacy", "unknown"}:
                return legacy_capability(source="inventory")
            mode = str(entry.get("mode") or "").strip().lower()
            domains = entry.get("domains") or []
            if not isinstance(domains, list):
                domains = []
            if mode in NETWORK_MODES:
                # Collection-level granting modes are still fail-closed for
                # third-party trees: the inventory may name a mode for
                # completeness, but authorization stays legacy.
                return legacy_capability(source="inventory")
            return legacy_capability(source="inventory")
    skills = inventory.get("skills") or {}
    if not isinstance(skills, dict):
        return None
    for key in (name, directory):
        entry = skills.get(key)
        if isinstance(entry, dict):
            mode = str(entry.get("mode") or "").strip().lower()
            domains = entry.get("domains") or []
            if not isinstance(domains, list):
                domains = []
            if mode in NETWORK_MODES:
                return declared_capability(mode, domains, source="inventory")
            return unknown_capability(source="inventory", declaration="unknown")
    return None


def resolve_network_capability(
    *,
    raw_text: str,
    name: str,
    directory: str,
    collection: str | None,
    source: str,
) -> NetworkCapability:
    """Resolve the closed-set network declaration for one discovered Skill.

    Collection members never gain authorization from frontmatter (pinned
    third-party recipes, and any field they grow later). Bundled curated
    Skills must have an explicit mode from frontmatter or inventory.
    User-authored Skills without the field are ``unknown`` and do not grant.
    """

    parsed = parse_network_frontmatter(raw_text)
    if collection:
        inventoried = inventory_capability(name, directory, collection)
        if inventoried is not None:
            return inventoried
        return legacy_capability(source="collection-default")

    if source == "bundled":
        if parsed is not None and parsed.declaration == "declared":
            return parsed
        inventoried = inventory_capability(name, directory, None)
        if inventoried is not None:
            return inventoried
        if parsed is not None:
            return parsed
        return unknown_capability(source="missing", declaration="unknown")

    if parsed is not None:
        return parsed
    return unknown_capability(source="missing", declaration="unknown")


def local_sandbox_hint() -> dict[str, Any]:
    """Env-only sandbox observation. No subprocess, no socket, no self-test."""

    mode = (os.environ.get(_SANDBOX_ENV) or "auto").strip().lower() or "auto"
    raw = _parse_bool_env(_RAW_NETWORK_ENV, default=False)
    return {
        "sandbox_mode": mode,
        "raw_network_env": raw,
        "probed": False,
    }


def compose_readiness(
    requirements: Any,
    network: NetworkCapability | None,
    *,
    sandbox_hint: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge hardware readiness with locally observable network posture."""

    from openai4s.skills_loader.loader import (
        NEEDS_SETUP,
        READY,
        UNKNOWN,
        skill_readiness,
    )

    base = skill_readiness(requirements)
    blocked_on = list(base.get("blocked_on") or [])
    cap = network or unknown_capability()
    hint = dict(sandbox_hint) if sandbox_hint is not None else local_sandbox_hint()

    if cap.mode == "raw_required":
        blocked_on.append("raw_network")
    elif cap.mode == "host_only":
        if str(hint.get("sandbox_mode") or "").strip().lower() == "off":
            blocked_on.append("sandbox_not_enforced")
        if hint.get("raw_network_env"):
            blocked_on.append("raw_network_allowed")

    blocked_on = sorted(set(str(item) for item in blocked_on if item))
    state = str(base.get("state") or UNKNOWN)
    if blocked_on:
        state = NEEDS_SETUP
    ready = state == READY
    return {
        "state": state,
        "missing": list(base.get("missing") or []),
        "unverifiable": list(base.get("unverifiable") or []),
        "blocked_on": blocked_on,
        "checked_locally": True,
        "probed": False,
        "ready": ready,
    }


def catalog_fields(
    network: NetworkCapability, readiness: Mapping[str, Any]
) -> dict[str, Any]:
    """Additive catalog projection shared by both catalog surfaces."""

    ready = (
        bool(readiness.get("ready"))
        if "ready" in readiness
        else (str(readiness.get("state") or "") == "ready")
    )
    return {
        "capabilities": network.public_dict(),
        "readiness": dict(readiness),
        "ready": ready,
    }
