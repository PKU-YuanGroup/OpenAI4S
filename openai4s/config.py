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
    """
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
