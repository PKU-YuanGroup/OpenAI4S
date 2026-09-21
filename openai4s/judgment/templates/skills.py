"""skills.suggest: staged Skill recommendation on top of JudgmentService.

Questions are English. Each ``instructions`` names the state field it reads
(``state.request``) because question keys are not sent to the model.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence

from openai4s.judgment.registry import get_template, register_template
from openai4s.judgment.types import Answer, Choice, JudgmentResult, Noul, Question

GATE = 0.30
FIT = 0.50
BIO_EXPAND = 0.20
MAX_SUGGESTIONS = 3
TEMPLATE_VERSION = "1"

TEMPLATE_ID = "skills.suggest"
TEMPLATE_ID_BIO = "skills.suggest.bio"
TEMPLATE_ID_FIT = "skills.suggest.fit"
PURPOSE = "skill_suggest"

MAX_CHOICE_OPTIONS = 255
MAX_BIO_MEMBERS = 82
MAX_DESCRIPTION_CHARS = 200
SKILL_MD_HEAD_CHARS = 1200
TOP_AREAS = 2
TOP_CURATED_FIT = 3
TOP_BIO_FIT = 3
MAX_REQUESTS = 3

NONE_OF_THESE = "none_of_these"
NONE = "none"
BIOSKILLS_ID = "bioskills"

NONE_OF_THESE_DESC = (
    "No listed Skill fits the user's request in state.request; "
    "a prose answer or general coding is enough."
)
NONE_DESC = "None of these bioSkills recipes fits the user's request in state.request."
NOT_BIO_DESC = "The request in state.request is not a bioinformatics task."

_LOAD_SKILL_RE = re.compile(
    r"(?:host\.)?load_skill\s*\(\s*"
    r"(?:(?P<quote>['\"])(?P<quoted>.*?)(?P=quote)|"
    r"(?P<bare>[A-Za-z0-9][A-Za-z0-9_.-]*))\s*\)",
    re.IGNORECASE,
)

_AREA_INDEX: dict[str, Any] | None = None


def policy_version() -> str:
    """Cache-key component. Threshold edits must miss the in-process LRU."""

    return (
        f"{TEMPLATE_VERSION}:"
        f"gate={GATE:.2f}:fit={FIT:.2f}:bio={BIO_EXPAND:.2f}:k={MAX_SUGGESTIONS}"
    )


POLICY_VERSION = policy_version()


def clip_text(text: str, limit: int) -> str:
    collapsed = " ".join(str(text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(0, limit - 3)].rstrip() + "..."


def load_area_index() -> dict[str, Any]:
    global _AREA_INDEX
    if _AREA_INDEX is None:
        path = Path(__file__).with_name("bioskills_areas.json")
        _AREA_INDEX = json.loads(path.read_text(encoding="utf-8"))
    return _AREA_INDEX


def is_explicit_request(query: str, names: Sequence[str] | set[str]) -> bool:
    """True when the query names one visible Skill exactly or via load_skill()."""

    wanted = {str(name) for name in names if str(name).strip()}
    if not wanted:
        return False
    folded = {name.casefold(): name for name in wanted}
    raw = str(query or "").strip()
    if not raw:
        return False
    if raw in wanted or raw.casefold() in folded:
        return True
    for match in _LOAD_SKILL_RE.finditer(raw):
        token = match.group("quoted")
        if token is None:
            token = match.group("bare")
        if not token:
            continue
        if token in wanted or token.casefold() in folded:
            return True
    return False


def cap_choice_options(
    options: Mapping[str, str],
    *,
    none_key: str,
    none_desc: str,
) -> tuple[dict[str, str], bool]:
    """Keep Choice within 2–255 options. Longest descriptions drop first."""

    body = {
        str(key): clip_text(str(value), MAX_DESCRIPTION_CHARS)
        for key, value in options.items()
        if str(key) and str(key) != none_key
    }
    truncated = False
    budget = MAX_CHOICE_OPTIONS - 1
    if len(body) > budget:
        ranked = sorted(body.items(), key=lambda item: (len(item[1]), item[0]))
        body = dict(ranked[:budget])
        truncated = True
    if not body:
        return {}, truncated
    ordered = {key: body[key] for key in sorted(body)}
    ordered[none_key] = none_desc
    return ordered, truncated


@dataclass(frozen=True)
class SkillCandidate:
    name: str
    description: str
    version: str
    collection: str | None
    doc: str


@dataclass(frozen=True)
class AreaBucket:
    area_id: str
    label: str
    description: str
    members: tuple[str, ...]


@dataclass(frozen=True)
class SuggestCatalog:
    curated: tuple[SkillCandidate, ...]
    collections: tuple[SkillCandidate, ...]
    areas: tuple[AreaBucket, ...]
    by_name: Mapping[str, SkillCandidate] = field(default_factory=dict)

    def names(self) -> set[str]:
        return {candidate.name for candidate in self.by_name.values()}


RunFn = Callable[..., JudgmentResult]


def empty_payload(
    status: str,
    *,
    requests: int = 0,
    latency_ms: int = 0,
    error_code: str | None = None,
    candidate_truncated: bool = False,
) -> dict[str, Any]:
    return {
        "semantic_status": status,
        "semantic_suggestions": [],
        "requests": int(requests),
        "latency_ms": int(latency_ms),
        "error_code": error_code,
        "candidate_truncated": bool(candidate_truncated),
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }


def _noul_value(answers: Mapping[str, Answer], key: str) -> float | None:
    answer = answers.get(key)
    if answer is None or answer.kind != "noul":
        return None
    try:
        return float(answer.value)
    except (TypeError, ValueError):
        return None


def _choice_probs(answer: Answer | None) -> dict[str, float]:
    if answer is None or answer.kind != "choice":
        return {}
    if answer.probabilities:
        return {str(key): float(value) for key, value in answer.probabilities.items()}
    if isinstance(answer.value, str) and answer.value:
        return {answer.value: 1.0}
    return {}


def _rank_choice(
    answer: Answer | None, *, exclude: set[str]
) -> list[tuple[str, float]]:
    ranked = [
        (name, prob)
        for name, prob in _choice_probs(answer).items()
        if name not in exclude
    ]
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return ranked


def _fanout_questions(params: Mapping[str, Any]) -> Mapping[str, Question]:
    questions: dict[str, Question] = {}
    best = params.get("best_skill_options")
    if isinstance(best, Mapping) and 2 <= len(best) <= MAX_CHOICE_OPTIONS:
        questions["best_skill"] = Choice(
            instructions=(
                "Which single Skill from the candidates best fits the user's "
                "request in state.request? Choose none_of_these if no candidate "
                "fits."
            ),
            options=dict(best),
        )
    areas = params.get("bio_area_options")
    if isinstance(areas, Mapping) and 2 <= len(areas) <= MAX_CHOICE_OPTIONS:
        questions["bio_area"] = Choice(
            instructions=(
                "If the request in state.request is a bioinformatics task, which "
                "area of the bioSkills collection is most relevant? Choose "
                "none_of_these if it is not a bioinformatics task."
            ),
            options=dict(areas),
        )
    questions["wants_action"] = Noul(
        instructions=(
            "Does the user in state.request want a computational task carried "
            "out, rather than only an explanation?"
        )
    )
    questions["wants_procedure"] = Noul(
        instructions=(
            "Does the user's request in state.request require following a "
            "documented procedure or recipe, rather than answering from general "
            "knowledge?"
        )
    )
    questions["prose_enough"] = Noul(
        instructions=(
            "Would a prose-only answer be insufficient for the user's request "
            "in state.request, so that loading a Skill is needed? Answer yes "
            "when a Skill should be used."
        )
    )
    return questions


def _fanout_policy(
    answers: Mapping[str, Answer], _params: Mapping[str, Any]
) -> Literal["ok", "uncertain"]:
    values = []
    for key in ("wants_action", "wants_procedure", "prose_enough"):
        value = _noul_value(answers, key)
        if value is None:
            return "uncertain"
        values.append(value)
    if sum(values) / len(values) >= GATE:
        return "ok"
    return "uncertain"


def _bio_questions(params: Mapping[str, Any]) -> Mapping[str, Question]:
    options = params.get("member_options")
    if not isinstance(options, Mapping) or not (
        2 <= len(options) <= MAX_CHOICE_OPTIONS
    ):
        raise ValueError("skills.suggest.bio requires 2–255 member_options")
    return {
        "best_member": Choice(
            instructions=(
                "Which of these bioSkills recipes, if any, is the right one to "
                "load for the user's request in state.request? Choose none if "
                "none of them fit."
            ),
            options=dict(options),
        )
    }


def _bio_policy(
    answers: Mapping[str, Answer], _params: Mapping[str, Any]
) -> Literal["ok", "uncertain"]:
    answer = answers.get("best_member")
    if answer is None or answer.kind != "choice":
        return "uncertain"
    if str(answer.value) == NONE:
        return "uncertain"
    return "ok"


def _fit_questions(params: Mapping[str, Any]) -> Mapping[str, Question]:
    candidates = params.get("fit_candidates")
    if not isinstance(candidates, Sequence) or not candidates:
        raise ValueError("skills.suggest.fit requires fit_candidates")
    questions: dict[str, Question] = {}
    for item in candidates:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        description = clip_text(
            str(item.get("description") or ""), MAX_DESCRIPTION_CHARS
        )
        excerpt = str(item.get("excerpt") or "")[:SKILL_MD_HEAD_CHARS]
        questions[f"fits::{name}"] = Noul(
            instructions=(
                f"Does the Skill '{name}' do the specific thing that the user's "
                f"request in state.request asks for? It is described as: "
                f"{description}. Opening of its SKILL.md: {excerpt}"
            )
        )
    if not questions:
        raise ValueError("skills.suggest.fit requires at least one named candidate")
    return questions


def _fit_policy(
    answers: Mapping[str, Answer], _params: Mapping[str, Any]
) -> Literal["ok", "uncertain"]:
    values = [_noul_value(answers, key) for key in answers if key.startswith("fits::")]
    present = [value for value in values if value is not None]
    if not present:
        return "uncertain"
    if max(present) >= FIT:
        return "ok"
    return "uncertain"


def _register() -> None:
    try:
        get_template(TEMPLATE_ID)
        return
    except KeyError:
        pass
    shared = policy_version()
    register_template(
        TEMPLATE_ID,
        TEMPLATE_VERSION,
        _fanout_questions,
        _fanout_policy,
        purpose=PURPOSE,
        policy_version=shared,
    )
    register_template(
        TEMPLATE_ID_BIO,
        TEMPLATE_VERSION,
        _bio_questions,
        _bio_policy,
        purpose=PURPOSE,
        policy_version=shared,
    )
    register_template(
        TEMPLATE_ID_FIT,
        TEMPLATE_VERSION,
        _fit_questions,
        _fit_policy,
        purpose=PURPOSE,
        policy_version=shared,
    )


_register()


def _versions_map(catalog: SuggestCatalog, names: Sequence[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in names:
        candidate = catalog.by_name.get(name)
        out[name] = candidate.version if candidate is not None else ""
    return out


def _area_option_text(area: AreaBucket) -> str:
    return clip_text(f"{area.label}. {area.description}", MAX_DESCRIPTION_CHARS)


def _pick_bio_members(
    catalog: SuggestCatalog,
    ranked_areas: Sequence[tuple[str, float]],
) -> list[str]:
    by_id = {area.area_id: area for area in catalog.areas}
    picked: list[str] = []
    seen: set[str] = set()
    for area_id, _prob in ranked_areas[:TOP_AREAS]:
        area = by_id.get(area_id)
        if area is None:
            continue
        for name in area.members:
            if name in seen or name not in catalog.by_name:
                continue
            seen.add(name)
            picked.append(name)
            if len(picked) >= MAX_BIO_MEMBERS:
                return picked
    return picked


def _suggestion(
    *,
    name: str,
    p_fit: float,
    choice_prob: float,
    confidence: float,
    stage: str,
    reason_fields: Sequence[str],
) -> dict[str, Any]:
    return {
        "name": name,
        "p_fit": float(p_fit),
        "choice_prob": float(choice_prob),
        "confidence": float(confidence),
        "stage": stage,
        "reason_fields": list(reason_fields),
        "template_version": TEMPLATE_VERSION,
    }


def suggest(
    *,
    request: str,
    catalog: SuggestCatalog,
    run: RunFn,
    scope: object = None,
    max_requests: int = MAX_REQUESTS,
) -> dict[str, Any]:
    """Run at most three judgment requests and return the SkillService payload."""

    started = time.monotonic()
    bound = max(1, min(MAX_REQUESTS, int(max_requests)))
    requests = 0
    truncated = False
    # Billed tokens across the up-to-three backend calls. The payload already
    # reported how many requests it made and how long they took, but not what
    # they cost, so a live evaluation scored every J1/J2 run at $0. A cache
    # hit sent nothing and is not counted.
    usage = {"input_tokens": 0, "output_tokens": 0}

    def charge(result: Any) -> None:
        if getattr(result, "cache_hit", False):
            return
        for key in usage:
            usage[key] += int((getattr(result, "usage", None) or {}).get(key) or 0)

    state = {
        "request": request,
        "catalog_note": ("Candidates are OpenAI4S Skills available in this session."),
    }

    def finish(
        status: str,
        suggestions: list[dict[str, Any]] | None = None,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        latency_ms = max(0, int(round((time.monotonic() - started) * 1000)))
        payload = empty_payload(
            status,
            requests=requests,
            latency_ms=latency_ms,
            error_code=error_code,
            candidate_truncated=truncated,
        )
        payload["usage"] = dict(usage)
        if suggestions:
            payload["semantic_suggestions"] = suggestions[:MAX_SUGGESTIONS]
        return payload

    best_body: dict[str, str] = {}
    for candidate in catalog.curated:
        best_body[candidate.name] = clip_text(
            candidate.description, MAX_DESCRIPTION_CHARS
        )
    for candidate in catalog.collections:
        best_body[candidate.name] = clip_text(
            candidate.description, MAX_DESCRIPTION_CHARS
        )
    best_options, trunc_best = cap_choice_options(
        best_body, none_key=NONE_OF_THESE, none_desc=NONE_OF_THESE_DESC
    )
    truncated = truncated or trunc_best

    area_body = {area.area_id: _area_option_text(area) for area in catalog.areas}
    area_options, trunc_area = cap_choice_options(
        area_body, none_key=NONE_OF_THESE, none_desc=NOT_BIO_DESC
    )
    truncated = truncated or trunc_area

    fanout_params: dict[str, Any] = {
        "best_skill_options": best_options,
        "bio_area_options": area_options,
        "candidate_versions": _versions_map(
            catalog, list(best_options) + list(area_options)
        ),
    }
    first = run(
        purpose=PURPOSE,
        template_id=TEMPLATE_ID,
        state=state,
        params=fanout_params,
        scope=scope,
    )
    requests += 1
    charge(first)
    if first.status == "unavailable":
        return finish("unavailable", error_code=first.error_code)
    if first.status == "disabled":
        return finish("disabled")

    gate_values = [
        _noul_value(first.answers, key)
        for key in ("wants_action", "wants_procedure", "prose_enough")
    ]
    if any(value is None for value in gate_values):
        return finish("uncertain")
    gate_mean = sum(float(value) for value in gate_values if value is not None) / 3.0
    if gate_mean < GATE:
        return finish("ok")

    best_answer = first.answers.get("best_skill")
    area_answer = first.answers.get("bio_area")
    curated_ranked = _rank_choice(best_answer, exclude={NONE_OF_THESE, BIOSKILLS_ID})
    collection_ids = {item.name for item in catalog.collections}
    curated_ranked = [item for item in curated_ranked if item[0] not in collection_ids]
    best_probs = _choice_probs(best_answer)
    p_bioskills = float(best_probs.get(BIOSKILLS_ID, 0.0))
    selected = ""
    if best_answer is not None and best_answer.kind == "choice":
        selected = str(best_answer.value)

    bio_ranked: list[tuple[str, float]] = []
    need_expand = (
        bound >= 2
        and catalog.areas
        and (selected == BIOSKILLS_ID or p_bioskills >= BIO_EXPAND)
    )
    if need_expand:
        area_ranked = _rank_choice(area_answer, exclude={NONE_OF_THESE, NONE})
        member_names = _pick_bio_members(catalog, area_ranked)
        member_body = {
            name: clip_text(catalog.by_name[name].description, MAX_DESCRIPTION_CHARS)
            for name in member_names
            if name in catalog.by_name
        }
        member_options, trunc_members = cap_choice_options(
            member_body, none_key=NONE, none_desc=NONE_DESC
        )
        truncated = truncated or trunc_members
        if member_options:
            second = run(
                purpose=PURPOSE,
                template_id=TEMPLATE_ID_BIO,
                state=state,
                params={
                    "member_options": member_options,
                    "candidate_versions": _versions_map(catalog, list(member_options)),
                },
                scope=scope,
            )
            requests += 1
            charge(second)
            if second.status == "unavailable":
                return finish("unavailable", error_code=second.error_code)
            if second.status == "disabled":
                return finish("disabled")
            bio_ranked = _rank_choice(
                second.answers.get("best_member"), exclude={NONE, NONE_OF_THESE}
            )

    if bound < 3:
        suggestions = []
        for name, prob in curated_ranked[:MAX_SUGGESTIONS]:
            stage = (
                "bioskills"
                if catalog.by_name.get(name) is not None
                and catalog.by_name[name].collection
                else "curated"
            )
            suggestions.append(
                _suggestion(
                    name=name,
                    p_fit=prob,
                    choice_prob=prob,
                    confidence=prob,
                    stage=stage,
                    reason_fields=["description"],
                )
            )
        return finish(
            first.status if first.status in ("ok", "uncertain") else "ok", suggestions
        )

    fit_names: list[str] = []
    seen: set[str] = set()
    for name, _prob in curated_ranked[:TOP_CURATED_FIT] + bio_ranked[:TOP_BIO_FIT]:
        if name in seen or name not in catalog.by_name:
            continue
        seen.add(name)
        fit_names.append(name)
    if not fit_names:
        return finish("ok")

    fit_candidates = []
    for name in fit_names:
        candidate = catalog.by_name[name]
        fit_candidates.append(
            {
                "name": name,
                "description": candidate.description,
                "excerpt": candidate.doc[:SKILL_MD_HEAD_CHARS],
            }
        )
    third = run(
        purpose=PURPOSE,
        template_id=TEMPLATE_ID_FIT,
        state=state,
        params={
            "fit_candidates": fit_candidates,
            "candidate_versions": _versions_map(catalog, fit_names),
        },
        scope=scope,
    )
    requests += 1
    charge(third)
    if third.status == "unavailable":
        return finish("unavailable", error_code=third.error_code)
    if third.status == "disabled":
        return finish("disabled")

    choice_lookup = dict(curated_ranked)
    choice_lookup.update(bio_ranked)
    suggestions = []
    for name in fit_names:
        p_fit = _noul_value(third.answers, f"fits::{name}")
        if p_fit is None or p_fit < FIT:
            continue
        candidate = catalog.by_name[name]
        stage = "bioskills" if candidate.collection else "curated"
        choice_prob = float(choice_lookup.get(name, 0.0))
        suggestions.append(
            _suggestion(
                name=name,
                p_fit=p_fit,
                choice_prob=choice_prob,
                confidence=max(p_fit, choice_prob),
                stage=stage,
                reason_fields=["description", "skill_md_head"],
            )
        )
    suggestions.sort(key=lambda item: (-float(item["p_fit"]), str(item["name"])))
    status = (
        "ok" if suggestions else ("uncertain" if third.status == "uncertain" else "ok")
    )
    return finish(status, suggestions)


__all__ = [
    "BIO_EXPAND",
    "BIOSKILLS_ID",
    "FIT",
    "GATE",
    "MAX_BIO_MEMBERS",
    "MAX_CHOICE_OPTIONS",
    "MAX_REQUESTS",
    "MAX_SUGGESTIONS",
    "NONE",
    "NONE_OF_THESE",
    "POLICY_VERSION",
    "PURPOSE",
    "SKILL_MD_HEAD_CHARS",
    "TEMPLATE_ID",
    "TEMPLATE_ID_BIO",
    "TEMPLATE_ID_FIT",
    "TEMPLATE_VERSION",
    "AreaBucket",
    "SkillCandidate",
    "SuggestCatalog",
    "cap_choice_options",
    "clip_text",
    "empty_payload",
    "is_explicit_request",
    "load_area_index",
    "policy_version",
    "suggest",
]
