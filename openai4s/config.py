"""Global configuration for openai4s.

Data-dir layout (~/.openai4s):
    ~/.openai4s/
        logs/
        artifacts/
        tool-results/
        compaction-history/
        openai4s.db          (reserved, not used in v0.1)
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv() -> None:
    """Zero-dependency .env loader (stdlib only).

    Walks up from this file to the repo root looking for a `.env`, and loads
    any KEY=VALUE lines into os.environ WITHOUT overriding vars already set in
    the real environment (so an explicit `export` always wins). This keeps
    secrets like OPENAI4S_LLM_API_KEY out of source while still letting the app
    run with a single local, git-ignored file.

    ``OPENAI4S_SKIP_DOTENV=1`` skips the load entirely. The offline test suite
    (tests/conftest.py) sets it before this module is first imported so a
    developer's real .env can never configure the tests — several dataclass
    defaults below are frozen at import time, where no fixture can undo a
    leaked value.
    """
    if os.environ.get("OPENAI4S_SKIP_DOTENV", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return
    here = Path(__file__).resolve()
    for base in (here.parent, *here.parents):
        candidate = base / ".env"
        if candidate.is_file():
            try:
                for raw in candidate.read_text(encoding="utf-8").splitlines():
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    # strip optional surrounding quotes
                    val = val.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = val
            except OSError:
                pass
            break


_load_dotenv()


# Obvious template stubs copied verbatim from .env.example — never a real key.
# Filtering these means cfg.llm.api_key (and everything derived from it:
# effective_api_key, profile seeding, has_api_key) can never mistake a template
# stub for a configured secret. NOTE: deliberately excludes test values like
# "test-key" — those are used by the offline test suite (tests/conftest.py).
_PLACEHOLDER_API_KEYS = {
    "your-api-key-here",
    "your_api_key_here",
    "your-api-key",
    "your_api_key",
    "your-key-here",
    "your_key_here",
    "placeholder",
    "changeme",
    "replace-me",
}


def is_placeholder_api_key(k: str | None) -> bool:
    """True if `k` is empty or an obvious template placeholder, not a real key."""
    k = (k or "").strip().lower()
    return (not k) or k in _PLACEHOLDER_API_KEYS


def _default_data_dir() -> Path:
    env = os.environ.get("OPENAI4S_DATA_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".openai4s"


# Conventional provider-native API-key env vars, tried as a last resort so the
# app works with keys a user already has exported for other tools.
_NATIVE_KEY_ENV = {
    "ark": ("ARK_API_KEY", "DOUBAO_API_KEY"),
    "chatgpt": ("OPENAI_API_KEY",),
    "openai_responses": ("OPENAI_API_KEY",),
    "claude": ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}


@dataclass
class LLMConfig:
    """Multi-provider base-model config.

    A single `provider` selects one of the wire adapters in ``llm.PROVIDERS``
    (ark / chatgpt / openai_responses / claude / gemini). ``base_url``
    and ``model`` are left empty by default — ``llm.chat`` fills in the
    provider's built-in defaults — but can be overridden per provider.

    Env-var resolution (checked in order, first non-placeholder value wins for keys):
        api_key  -> OPENAI4S_<PROVIDER>_API_KEY then OPENAI4S_LLM_API_KEY
        base_url -> OPENAI4S_<PROVIDER>_BASE_URL then OPENAI4S_LLM_BASE_URL
        model    -> OPENAI4S_<PROVIDER>_MODEL then OPENAI4S_LLM_MODEL

    So e.g. `OPENAI4S_CLAUDE_API_KEY` / `OPENAI4S_GEMINI_API_KEY` can coexist, while a
    single `OPENAI4S_LLM_API_KEY` still works for the active provider. Secrets are
    NEVER hard-coded — they come from the environment or the git-ignored .env.
    """

    # Active provider id (see llm.PROVIDERS). Defaults to the Volcengine Ark
    # plan gateway (multi-model, one shared endpoint + key).
    provider: str = os.environ.get("OPENAI4S_LLM_PROVIDER", "ark")
    # Empty -> llm.chat resolves the provider's built-in default endpoint.
    base_url: str = ""
    # Empty -> llm.chat resolves the provider's built-in default model id.
    model: str = ""
    # Secret: sourced from the environment (or the git-ignored .env). Empty
    # when unset; llm.chat then raises a clear error.
    api_key: str = ""
    # Deep-thinking models: keep a conservative default output cap.
    max_tokens: int = int(os.environ.get("OPENAI4S_LLM_MAX_TOKENS", "4096"))
    temperature: float = float(os.environ.get("OPENAI4S_LLM_TEMPERATURE", "0.7"))
    timeout_s: float = float(os.environ.get("OPENAI4S_LLM_TIMEOUT", "120"))

    def __post_init__(self) -> None:
        # Provider ids may be hyphenated; environment-variable names use the
        # shell-safe underscore form (``lab-openai`` -> ``LAB_OPENAI``).
        p = self.provider.strip().upper().replace("-", "_")

        def _resolve(field_val: str, specific: str, generic: str) -> str:
            if field_val:
                return field_val
            return os.environ.get(specific) or os.environ.get(generic, "")

        def _resolve_api_key(field_val: str, specific: str, generic: str) -> str:
            for raw in (
                field_val,
                os.environ.get(specific, ""),
                os.environ.get(generic, ""),
            ):
                val = (raw or "").strip()
                if not is_placeholder_api_key(val):
                    return val
            return ""

        self.api_key = _resolve_api_key(
            self.api_key, f"OPENAI4S_{p}_API_KEY", "OPENAI4S_LLM_API_KEY"
        )
        self.base_url = _resolve(
            self.base_url, f"OPENAI4S_{p}_BASE_URL", "OPENAI4S_LLM_BASE_URL"
        )
        self.model = _resolve(self.model, f"OPENAI4S_{p}_MODEL", "OPENAI4S_LLM_MODEL")

        # Last-resort: fall back to each provider's conventional native env var
        # (so a user who already has OPENAI_API_KEY / ANTHROPIC_API_KEY set — e.g.
        # for the reference demo — gets a working agent with zero extra config).
        if not self.api_key:
            for native in _NATIVE_KEY_ENV.get(self.provider.strip().lower(), ()):
                val = (os.environ.get(native) or "").strip()
                if not is_placeholder_api_key(val):
                    self.api_key = val
                    break

        # Fill provider built-in defaults so base_url/model are always concrete
        # (status pages, turn logs, etc. read cfg.llm.model directly). Lazy
        # import avoids a config<->llm import cycle.
        if not self.base_url or not self.model:
            try:
                from .llm import PROVIDERS

                spec = PROVIDERS.get(self.provider.strip().lower())
                if spec:
                    self.base_url = self.base_url or spec["base_url"]
                    self.model = self.model or spec["model"]
            except Exception:
                pass


def _env_flag(name: str, default: bool) -> bool:
    """Truthy env flag: unset -> default; '0'/'false'/'no'/'off' -> False."""
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() not in ("0", "false", "no", "off", "")


_STRICT_TRUE_VALUES = frozenset(("1", "true", "yes", "on"))
_STRICT_FALSE_VALUES = frozenset(("0", "false", "no", "off"))


def _strict_env_flag(name: str, default: bool = False) -> bool:
    """Read a security-sensitive rollout flag without truthy fall-through.

    The older, general-purpose :func:`_env_flag` deliberately treats any value
    outside its false vocabulary as true.  That is convenient for established
    opt-in controls, but unsafe for dormant roadmap capabilities: a typo such
    as ``OPENAI4S_AUTO_MODE=flase`` must not enable autonomous behaviour.
    Unknown values therefore reject configuration instead of silently choosing
    either branch.
    """

    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _STRICT_TRUE_VALUES:
        return True
    if value in _STRICT_FALSE_VALUES:
        return False
    choices = ", ".join(sorted(_STRICT_TRUE_VALUES | _STRICT_FALSE_VALUES))
    raise ValueError(f"invalid {name}: expected one of {choices}")


def _strict_env_choice(name: str, default: str, allowed: frozenset[str]) -> str:
    """Read and validate a closed-vocabulary environment setting."""

    value = os.environ.get(name, default).strip().lower()
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"invalid {name}: expected one of {choices}")
    return value


def _strict_env_int(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    """Read an ASCII integer constrained to a fail-closed safety range."""

    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip()
    if not value or not value.isascii() or not value.isdigit():
        raise ValueError(f"invalid {name}: expected an integer")
    parsed = int(value, 10)
    if not minimum <= parsed <= maximum:
        raise ValueError(
            f"invalid {name}: expected an integer in [{minimum}, {maximum}]"
        )
    return parsed


def _strict_env_float(
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    """Read a finite float constrained to a fail-closed safety range."""

    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip()
    if not value:
        raise ValueError(f"invalid {name}: expected a finite number")
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"invalid {name}: expected a finite number") from exc
    if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
        raise ValueError(
            f"invalid {name}: expected a finite number in [{minimum}, {maximum}]"
        )
    return parsed


def _auto_mode_enabled() -> bool:
    """Parse the Stage 0 Auto Mode preset spelling.

    ``autonomous`` is the product name for the same bounded preset selected by
    the ordinary strict true spellings.  It is deliberately accepted only for
    this setting, not for the individual roadmap flags.
    """

    raw = os.environ.get("OPENAI4S_AUTO_MODE")
    if raw is None:
        return False
    value = raw.strip().lower()
    if value == "autonomous" or value in _STRICT_TRUE_VALUES:
        return True
    if value in _STRICT_FALSE_VALUES:
        return False
    choices = ", ".join(
        sorted(_STRICT_TRUE_VALUES | _STRICT_FALSE_VALUES | {"autonomous"})
    )
    raise ValueError(f"invalid OPENAI4S_AUTO_MODE: expected one of {choices}")


_AUTO_MODE_SELECTION_ENV_FIELDS = (
    ("OPENAI4S_AUTO_MODE", "preset"),
    ("OPENAI4S_RESULT_REVIEW_MODE", "result_review_mode"),
    ("OPENAI4S_APPROVALS_REVIEWER", "approvals_reviewer"),
)


def _auto_mode_deployment_explicit_fields() -> tuple[str, ...]:
    """Selection fields the operator actually set for this Config generation.

    Capturing this beside the parsed values is load-bearing.  Looking at the
    environment later cannot distinguish an unset deployment default from an
    explicit ``off`` after tests, launch wrappers, or embedders have changed
    ``os.environ``.  The Stage 2 resolver needs that distinction so a built-in
    default does not erase an older frame's explicit result-review setting.
    """

    return tuple(
        field_name
        for env_name, field_name in _AUTO_MODE_SELECTION_ENV_FIELDS
        if env_name in os.environ
    )


def _auto_mode_deployment_explicit() -> bool:
    return any(
        env_name in os.environ for env_name, _ in _AUTO_MODE_SELECTION_ENV_FIELDS
    )


@dataclass
class SecurityConfig:
    """Toggles for the defense-in-depth safety layer (openai4s.security).

    A three-layer defense pipeline:
    a pre-exec code-safety classifier, an in-kernel CPython audit hook, and the
    biosecurity / prompt-injection screeners. Everything is opt-out via env so a
    single-user local install keeps working, but the cheap static gates default
    ON so an out-of-the-box run still refuses the obvious attacks.

        safety_mode (OPENAI4S_SAFETY):
            "off"        - no pre-exec code gate at all
            "heuristic"  - static pattern scan only (no LLM cost) [default]
            "llm"        - static fast-path + the e6w LLM classifier for the
                           residual "uncertain" code (needs an API key)
        audit_hook (OPENAI4S_SAFETY_AUDIT_HOOK, default on):
            install the in-kernel dlopen guard.
        biosecurity (OPENAI4S_BIOSECURITY, default on):
            splice the calibrated-accountability (oiO) prompt AND run the diO
            trajectory screener when biosecurity-relevant content is detected.
        injection_scan (OPENAI4S_INJECTION_SCAN, default on):
            screen tool-returned content (web/pdf/mcp) for prompt injection.

    Also carries the network egress fence. ``egress_mode``
    mirrors the enforcement mode read by :mod:`openai4s.egress`:

    * ``off`` (default) — fail-open; no allowlist enforcement, so an install that
      relies on "networking is ON" is unaffected;
    * ``allowlist`` — host.web_fetch / host.web_search / host.bash outbound calls
      are checked against ``egress_allowlist``; a blocked domain returns a
      proxy-403 soft error and the agent must call ``request_network_access``.

    ``egress_allowlist`` is the grouped, host-owned base allowlist (the canonical
    ``egress.EGRESS_GROUPS``); the gateway's Customize → Network panel renders it.
    The hot-path check in :mod:`openai4s.egress` reads ``OPENAI4S_EGRESS``
    fresh on each call so a UI toggle or a test takes effect without rebuilding
    this singleton — this dataclass is the declarative surface, egress.py the
    enforcement engine.
    """

    # default_factory (not a bare default) so the env vars are read at INSTANCE
    # time — a fresh get_config() after `export OPENAI4S_SAFETY=llm` picks it
    # up, rather than being frozen at import.
    safety_mode: str = field(
        default_factory=lambda: os.environ.get("OPENAI4S_SAFETY", "heuristic")
        .strip()
        .lower()
    )
    audit_hook: bool = field(
        default_factory=lambda: _env_flag("OPENAI4S_SAFETY_AUDIT_HOOK", True)
    )
    biosecurity: bool = field(
        default_factory=lambda: _env_flag("OPENAI4S_BIOSECURITY", True)
    )
    injection_scan: bool = field(
        default_factory=lambda: _env_flag("OPENAI4S_INJECTION_SCAN", True)
    )

    def __post_init__(self) -> None:
        if self.safety_mode not in ("off", "heuristic", "llm"):
            self.safety_mode = "heuristic"

    @property
    def code_gate_enabled(self) -> bool:
        return self.safety_mode != "off"

    @property
    def use_llm_classifier(self) -> bool:
        return self.safety_mode == "llm"

    # Read at construction (not class-definition) time so a UI toggle / test that
    # sets OPENAI4S_EGRESS is reflected without reloading the module — matching
    # the fresh-env read in egress.egress_mode().
    egress_mode: str = field(
        default_factory=lambda: os.environ.get("OPENAI4S_EGRESS", "off").strip().lower()
    )
    egress_allowlist: list[dict] = field(default_factory=lambda: _egress_groups())

    def allowlisted_domains(self) -> frozenset[str]:
        """Flattened base domains of the configured allowlist (subdomains match by
        suffix at enforcement time)."""
        return frozenset(
            d.strip().lower()
            for g in self.egress_allowlist
            for d in g.get("domains", [])
        )

    @property
    def egress_enforced(self) -> bool:
        return self.egress_mode in ("allowlist", "allow_list", "on", "1", "enforce")


def _egress_groups() -> list[dict]:
    """Lazy import of the canonical allowlist so config.py stays import-light and
    there is a single source of truth shared with enforcement + the gateway."""
    try:
        from .egress import EGRESS_GROUPS

        return [dict(g, domains=list(g.get("domains", []))) for g in EGRESS_GROUPS]
    except Exception:  # noqa: BLE001 — never let the allowlist break config load
        return []


@dataclass
class ShareConfig:
    """Outbound web-share tunnel config (see docs/webshare.md).

    All values resolve from the environment / git-ignored .env at instance time.
    The auth token is a secret and is filtered like an API key; it is named to
    end in ``AUTH_TOKEN`` so the session-package secret scanners catch it too.
    Sharing is inert until both ``relay_url`` and ``auth_token`` are set.
    """

    relay_url: str = field(
        default_factory=lambda: os.environ.get("OPENAI4S_SHARE_RELAY_URL", "").strip()
    )
    auth_token: str = ""
    base_domain: str = field(
        default_factory=lambda: os.environ.get("OPENAI4S_SHARE_BASE_DOMAIN", "").strip()
    )
    allow_insecure: bool = field(
        default_factory=lambda: _env_flag("OPENAI4S_SHARE_ALLOW_INSECURE", False)
    )

    def __post_init__(self) -> None:
        raw = (
            self.auth_token or os.environ.get("OPENAI4S_SHARE_AUTH_TOKEN", "")
        ).strip()
        self.auth_token = "" if is_placeholder_api_key(raw) else raw

    @property
    def configured(self) -> bool:
        return bool(self.relay_url and self.auth_token)

    def public_url(self, share_id: str) -> str:
        from urllib.parse import urlparse

        domain = self.base_domain or (urlparse(self.relay_url).hostname or "localhost")
        return f"https://{share_id}.{domain}/"


#: Suffix that marks a data root read-only: `OPENAI4S_DATA_ROOTS=/data/sets=ro:/scratch`.
#: `=` rather than `:` because `:` is the list separator. D8 names a
#: "read-only datasets area" as one of the three kinds of root; without a
#: way to say so, every root was writable and a member could put files
#: into -- or over -- the reference datasets everybody analyses.
DATA_ROOT_READONLY_SUFFIX = "=ro"
DATA_ROOT_READWRITE_SUFFIX = "=rw"

#: The subdirectory of a writable root that holds members' personal areas
#: (`<root>/users/<username>/`). A fixed namespace, so "is this another
#: member's scratch?" is a question about a path and not a guess about
#: whether a directory named `alice` is a person or a dataset.
DATA_ROOT_USERS_DIR = "users"


def _data_roots() -> list[Path]:
    """Parse OPENAI4S_DATA_ROOTS (colon-separated allowlist of directories for
    the team file area). Empty/unset -> [] = the file routes stay disabled and
    single-user behavior is untouched (INV-1).

    Root policy rides on the same value: `path=ro` is a read-only root. The
    policy is kept alongside the path (see `data_root_policies`) so callers
    that only want the paths keep getting a plain list."""
    return [path for path, _writable in data_root_policies()]


def data_root_policies() -> list[tuple[Path, bool]]:
    """`(path, writable)` for every configured root."""
    raw = os.environ.get("OPENAI4S_DATA_ROOTS", "").strip()
    if not raw:
        return []
    roots: list[tuple[Path, bool]] = []
    for part in raw.split(":"):
        part = part.strip()
        if not part:
            continue
        writable = True
        if part.endswith(DATA_ROOT_READONLY_SUFFIX):
            part, writable = part[: -len(DATA_ROOT_READONLY_SUFFIX)], False
        elif part.endswith(DATA_ROOT_READWRITE_SUFFIX):
            part = part[: -len(DATA_ROOT_READWRITE_SUFFIX)]
        if part:
            roots.append((Path(part).expanduser(), writable))
    return roots


@dataclass(frozen=True)
class RoadmapFeatureFlags:
    """Stage 1--12 rollout reservations from the Auto Mode master plan.

    Every flag defaults off. Stage 1--10 consume only their own flags for
    trusted delivery, durable Auto Mode storage/projection, Scientific
    Reviewer shadow recording, the review completion gate, bounded auto-fix,
    Guardian shadow/enforcement, the official live Notebook lineage path,
    the Artifact workbench/Ketcher, and production ClinVar/PubMed/trials
    connectors; later stages remain inert until their own Go/No-Go gate
    passes.
    """

    stage1_trusted_delivery: bool = field(
        default_factory=lambda: _strict_env_flag("OPENAI4S_STAGE1_TRUSTED_DELIVERY")
    )
    stage2_auto_run_storage: bool = field(
        default_factory=lambda: _strict_env_flag("OPENAI4S_STAGE2_AUTO_RUN_STORAGE")
    )
    stage3_scientific_review_shadow: bool = field(
        default_factory=lambda: _strict_env_flag(
            "OPENAI4S_STAGE3_SCIENTIFIC_REVIEW_SHADOW"
        )
    )
    stage4_review_completion_gate: bool = field(
        default_factory=lambda: _strict_env_flag(
            "OPENAI4S_STAGE4_REVIEW_COMPLETION_GATE"
        )
    )
    stage5_auto_repair: bool = field(
        default_factory=lambda: _strict_env_flag("OPENAI4S_STAGE5_AUTO_REPAIR")
    )
    stage6_guardian_shadow: bool = field(
        default_factory=lambda: _strict_env_flag("OPENAI4S_STAGE6_GUARDIAN_SHADOW")
    )
    stage7_guardian_enforcement: bool = field(
        default_factory=lambda: _strict_env_flag("OPENAI4S_STAGE7_GUARDIAN_ENFORCEMENT")
    )
    stage8_live_notebook_lineage: bool = field(
        default_factory=lambda: _strict_env_flag(
            "OPENAI4S_STAGE8_LIVE_NOTEBOOK_LINEAGE"
        )
    )
    stage9_artifact_workbench: bool = field(
        default_factory=lambda: _strict_env_flag("OPENAI4S_STAGE9_ARTIFACT_WORKBENCH")
    )
    stage10_scientific_connectors: bool = field(
        default_factory=lambda: _strict_env_flag(
            "OPENAI4S_STAGE10_SCIENTIFIC_CONNECTORS"
        )
    )
    stage11_durable_remote_compute: bool = field(
        default_factory=lambda: _strict_env_flag(
            "OPENAI4S_STAGE11_DURABLE_REMOTE_COMPUTE"
        )
    )
    stage12_auto_mode_ga: bool = field(
        default_factory=lambda: _strict_env_flag("OPENAI4S_STAGE12_AUTO_MODE_GA")
    )

    def __post_init__(self) -> None:
        # Dataclasses do not enforce annotations for direct construction.  Do
        # not let ``1`` or a non-empty string become an accidental enable.
        for name, value in self.__dict__.items():
            if type(value) is not bool:
                raise ValueError(f"{name} must be a bool")


_RESULT_REVIEW_MODES = frozenset(("off", "review_only", "auto_fix"))
_APPROVAL_REVIEWERS = frozenset(("user", "auto_review"))

# Stage 0 freezes selection precedence without making it a runtime resolver.
# A later stage must preserve this order when it adds durable project/frame
# settings.  ``deployment_explicit`` intentionally differs from the built-in
# default: an unset deployment value must not erase an older frame's result-
# review preference during compatibility migration.
AUTO_MODE_SELECTION_PRECEDENCE = (
    "import_quarantine",
    "frame",
    "project",
    "deployment_explicit",
    "legacy_result_review",
    "built_in_defaults",
)
AUTO_MODE_LEGACY_RESULT_REVIEW_MODE = "review_only"
AUTO_MODE_LEGACY_CAN_ENABLE_PERMISSION_REVIEW = False
AUTO_MODE_IMPORT_QUARANTINE_SELECTION = (False, "off", "user")


@dataclass(frozen=True)
class AutoModeBudgets:
    """Fail-closed deployment ceilings for the autonomous preset.

    Stage 2 publishes these as read-only policy and freezes them into durable
    runs; it does not accept a partial project/frame budget override. A later
    policy layer may add a complete resolver whose overrides only tighten them.
    """

    max_review_rounds: int = field(
        default_factory=lambda: _strict_env_int(
            "OPENAI4S_AUTO_MAX_REVIEW_ROUNDS", 2, minimum=1, maximum=2
        )
    )
    max_repair_rounds: int = field(
        default_factory=lambda: _strict_env_int(
            "OPENAI4S_AUTO_MAX_REPAIR_ROUNDS", 2, minimum=0, maximum=2
        )
    )
    repair_turns_per_round: int = field(
        default_factory=lambda: _strict_env_int(
            "OPENAI4S_AUTO_REPAIR_TURNS_PER_ROUND", 12, minimum=0, maximum=12
        )
    )
    max_extra_cells: int = field(
        default_factory=lambda: _strict_env_int(
            "OPENAI4S_AUTO_MAX_EXTRA_CELLS", 30, minimum=0, maximum=30
        )
    )
    wall_time_s: int = field(
        default_factory=lambda: _strict_env_int(
            "OPENAI4S_AUTO_WALL_TIME_S", 900, minimum=1, maximum=900
        )
    )
    extra_token_multiplier: float = field(
        default_factory=lambda: _strict_env_float(
            "OPENAI4S_AUTO_EXTRA_TOKEN_MULTIPLIER",
            1.5,
            minimum=0.0,
            maximum=1.5,
        )
    )
    repeated_finding_limit: int = field(
        default_factory=lambda: _strict_env_int(
            "OPENAI4S_AUTO_REPEATED_FINDING_LIMIT", 2, minimum=1, maximum=2
        )
    )
    same_action_no_delta_limit: int = field(
        default_factory=lambda: _strict_env_int(
            "OPENAI4S_AUTO_SAME_ACTION_NO_DELTA_LIMIT", 3, minimum=1, maximum=3
        )
    )
    no_progress_turn_limit: int = field(
        default_factory=lambda: _strict_env_int(
            "OPENAI4S_AUTO_NO_PROGRESS_TURN_LIMIT", 5, minimum=1, maximum=5
        )
    )
    guardian_timeout_s: int = field(
        default_factory=lambda: _strict_env_int(
            "OPENAI4S_AUTO_GUARDIAN_TIMEOUT_S", 90, minimum=1, maximum=90
        )
    )
    guardian_consecutive_denial_limit: int = field(
        default_factory=lambda: _strict_env_int(
            "OPENAI4S_AUTO_GUARDIAN_CONSECUTIVE_DENIAL_LIMIT",
            3,
            minimum=1,
            maximum=3,
        )
    )
    guardian_window_size: int = field(
        default_factory=lambda: _strict_env_int(
            "OPENAI4S_AUTO_GUARDIAN_WINDOW_SIZE", 50, minimum=50, maximum=50
        )
    )
    guardian_window_denial_limit: int = field(
        default_factory=lambda: _strict_env_int(
            "OPENAI4S_AUTO_GUARDIAN_WINDOW_DENIAL_LIMIT",
            10,
            minimum=1,
            maximum=10,
        )
    )

    def __post_init__(self) -> None:
        integer_ranges = {
            "max_review_rounds": (1, 2),
            "max_repair_rounds": (0, 2),
            "repair_turns_per_round": (0, 12),
            "max_extra_cells": (0, 30),
            "wall_time_s": (1, 900),
            "repeated_finding_limit": (1, 2),
            "same_action_no_delta_limit": (1, 3),
            "no_progress_turn_limit": (1, 5),
            "guardian_timeout_s": (1, 90),
            "guardian_consecutive_denial_limit": (1, 3),
            "guardian_window_size": (50, 50),
            "guardian_window_denial_limit": (1, 10),
        }
        for name, (minimum, maximum) in integer_ranges.items():
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")
        token_multiplier = self.extra_token_multiplier
        if (
            isinstance(token_multiplier, bool)
            or not isinstance(token_multiplier, (int, float))
            or not math.isfinite(float(token_multiplier))
            or not 0.0 <= float(token_multiplier) <= 1.5
        ):
            raise ValueError("extra_token_multiplier must be in [0.0, 1.5]")
        object.__setattr__(self, "extra_token_multiplier", float(token_multiplier))
        if self.guardian_window_denial_limit > self.guardian_window_size:
            raise ValueError(
                "guardian_window_denial_limit must not exceed guardian_window_size"
            )


@dataclass(frozen=True)
class AutoModeConfig:
    """Deployment selection parsed for the Stage 2 Auto Mode resolver.

    ``enabled`` represents the future UI/CLI preset, not a permission bypass.
    The preset is normalized here so no contradictory configuration can exist:
    enabled always means ``auto_fix`` + ``auto_review`` + bounded budgets.  No
    Stage 2 only stores/resolves this selection; later stages own execution.
    """

    enabled: bool = field(default_factory=_auto_mode_enabled)
    result_review_mode: str = field(
        default_factory=lambda: _strict_env_choice(
            "OPENAI4S_RESULT_REVIEW_MODE", "off", _RESULT_REVIEW_MODES
        )
    )
    approvals_reviewer: str = field(
        default_factory=lambda: _strict_env_choice(
            "OPENAI4S_APPROVALS_REVIEWER", "user", _APPROVAL_REVIEWERS
        )
    )
    budgets: AutoModeBudgets = field(default_factory=AutoModeBudgets)
    # These are captured metadata, not another enable switch.  In particular,
    # OPENAI4S_AUTO_MODE=off is semantically different from an unset variable
    # even though both normalize to the same values.
    deployment_explicit: bool = field(default_factory=_auto_mode_deployment_explicit)
    deployment_explicit_fields: tuple[str, ...] = field(
        default_factory=_auto_mode_deployment_explicit_fields
    )

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("enabled must be a bool")
        if not isinstance(self.result_review_mode, str):
            raise ValueError("result_review_mode must be a string")
        if not isinstance(self.approvals_reviewer, str):
            raise ValueError("approvals_reviewer must be a string")
        if not isinstance(self.budgets, AutoModeBudgets):
            raise ValueError("budgets must be an AutoModeBudgets")
        if type(self.deployment_explicit) is not bool:
            raise ValueError("deployment_explicit must be a bool")
        if not isinstance(self.deployment_explicit_fields, tuple) or any(
            not isinstance(field_name, str)
            or field_name not in {"preset", "result_review_mode", "approvals_reviewer"}
            for field_name in self.deployment_explicit_fields
        ):
            raise ValueError("deployment_explicit_fields contains an unknown field")
        if len(set(self.deployment_explicit_fields)) != len(
            self.deployment_explicit_fields
        ):
            raise ValueError("deployment_explicit_fields must not contain duplicates")
        if self.deployment_explicit_fields and not self.deployment_explicit:
            # Direct construction can supply metadata too.  A named explicit
            # field and an explicit=false bit cannot both be true; reject the
            # ambiguous input rather than silently choosing whichever a caller
            # happened to inspect.
            raise ValueError(
                "deployment_explicit must be true when explicit fields are present"
            )
        result_mode = self.result_review_mode.strip().lower()
        approvals_reviewer = self.approvals_reviewer.strip().lower()
        if result_mode not in _RESULT_REVIEW_MODES:
            choices = ", ".join(sorted(_RESULT_REVIEW_MODES))
            raise ValueError(f"invalid result_review_mode: expected one of {choices}")
        if approvals_reviewer not in _APPROVAL_REVIEWERS:
            choices = ", ".join(sorted(_APPROVAL_REVIEWERS))
            raise ValueError(f"invalid approvals_reviewer: expected one of {choices}")
        if self.enabled:
            result_mode = "auto_fix"
            approvals_reviewer = "auto_review"
        object.__setattr__(self, "result_review_mode", result_mode)
        object.__setattr__(self, "approvals_reviewer", approvals_reviewer)

    @property
    def preset(self) -> str:
        return "autonomous" if self.enabled else "off"


@dataclass
class Config:
    data_dir: Path = field(default_factory=_default_data_dir)
    host: str = os.environ.get("OPENAI4S_HOST", "127.0.0.1")
    port: int = int(os.environ.get("OPENAI4S_PORT", "8760"))
    llm: LLMConfig = field(default_factory=LLMConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    share: ShareConfig = field(default_factory=ShareConfig)
    # skills root: repo-local skills/ dir by default
    skills_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get(
                "OPENAI4S_SKILLS_DIR",
                str(Path(__file__).resolve().parent.parent / "skills"),
            )
        )
    )
    # max agent turns per user message (outer Code-as-Action loop bound)
    max_turns: int = int(os.environ.get("OPENAI4S_MAX_TURNS", "64"))
    # turn budget for explore mode (autonomous deep exploration) — deliberately
    # larger than max_turns so an open-ended investigation can run to completion
    explore_max_turns: int = int(os.environ.get("OPENAI4S_EXPLORE_MAX_TURNS", "96"))
    # Model context window in tokens (default 256k; override per model/provider).
    context_window_tokens: int = int(
        os.environ.get("OPENAI4S_CONTEXT_WINDOW", "262144")
    )
    # Compact when the estimated prompt token count crosses this FRACTION of the
    # context window (compaction triggers as the window fills, not by a
    # raw message count). 0.75 leaves headroom for the next reply.
    compaction_trigger_ratio: float = float(
        os.environ.get("OPENAI4S_COMPACTION_TRIGGER_RATIO", "0.75")
    )
    # replay: when true, the root agent records every host_call into a tape
    # so an exported notebook can be replayed offline.
    record_tape: bool = os.environ.get("OPENAI4S_RECORD_TAPE", "") not in ("", "0")
    # read-only Notebook by default; set OPENAI4S_NOTEBOOK_REPL=1 to re-enable the
    # in-Notebook developer REPL.
    notebook_repl: bool = field(
        default_factory=lambda: _env_flag("OPENAI4S_NOTEBOOK_REPL", False)
    )
    # Team Server mode (docs/team-server-plan.md): ON forces web login and
    # ownership filtering; OFF (default) keeps single-user behavior unchanged
    # (INV-1). Read at instance time so tests/UI toggles see a fresh value.
    # Declared LAST (with data_roots): the constructor's positional prefix is
    # a pinned public contract (test_public_api_contract), so new fields
    # append rather than insert.
    team_mode: bool = field(
        default_factory=lambda: _env_flag("OPENAI4S_TEAM_MODE", False)
    )
    # Allowlisted roots for the team file area (OPENAI4S_DATA_ROOTS, colon-
    # separated). Empty = feature dormant.
    data_roots: list[Path] = field(default_factory=_data_roots)
    # Stage 0 reservations are appended to preserve Config's positional prefix.
    # They are intentionally inert until later stages wire the corresponding
    # service/state-machine behavior and pass their own Go/No-Go gates.
    roadmap_features: RoadmapFeatureFlags = field(default_factory=RoadmapFeatureFlags)
    auto_mode: AutoModeConfig = field(default_factory=AutoModeConfig)

    def ensure_dirs(self) -> None:
        from openai4s.security.permissions import harden_dir

        # The data dir holds the credential database, artifacts, and logs. It
        # was created at the process umask (0755 on most systems), so every
        # local account could list and read it.
        self.data_dir.mkdir(parents=True, exist_ok=True)
        harden_dir(self.data_dir)
        for sub in ("logs", "artifacts", "tool-results", "compaction-history"):
            path = self.data_dir / sub
            path.mkdir(parents=True, exist_ok=True)
            harden_dir(path)

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    @property
    def shares_dir(self) -> Path:
        return self.data_dir / "shares"

    @property
    def compaction_dir(self) -> Path:
        return self.data_dir / "compaction-history"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "openai4s.db"

    @property
    def tape_path(self) -> Path:
        return self.data_dir / "openai4s_tape.json"

    @property
    def pidfile(self) -> Path:
        return self.data_dir / "openai4s.pid"

    @property
    def statefile(self) -> Path:
        return self.data_dir / "daemon.json"


_CONFIG: Config | None = None


def get_config() -> Config:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = Config()
        _CONFIG.ensure_dirs()
    return _CONFIG
