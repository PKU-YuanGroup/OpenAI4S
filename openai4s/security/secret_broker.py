"""Credential storage behind an opaque reference.

Business tables should hold a *reference* to a secret, not the secret. Legacy
deployments held model-profile API keys, ``llm_api_key``, ``tavily_api_key``,
``agent_plan_key``, and connector credentials directly in SQLite. Current save
and migration paths replace those values with opaque broker references. The
data directory is owner-only too, but a file mode is not encryption and does
nothing for a backup, an rsync, a container layer, or a support bundle.

This broker is the mechanism for fixing that:

    put(scope, name, secret) -> secret_ref
    get(secret_ref)          -> the value, for the moment it is needed
    delete(secret_ref)
    describe(secret_ref)     -> metadata only, never the value

## Backends

Stdlib only, so no ``keyring``: the system stores are driven through their own
CLIs.

* **macOS** — ``security`` against the login keychain. The password is fed on
  stdin, never argv: ``security``'s own help says "Use of the -p or -w options
  is insecure", and a value on the command line is readable by any local ``ps``
  for the life of the call.
* **Linux desktop** — ``secret-tool`` (libsecret), i.e. the Secret Service the
  session keyring already implements. Also stdin.
* **env injection** — what a server actually has. Credentials arrive in the
  process environment (systemd ``EnvironmentFile``, a Kubernetes Secret, …) and
  nothing is written to disk at all, which is stronger than the keychain case
  rather than a fallback from it. Read-only on purpose: if the environment owns
  the secret, the app must not be able to overwrite it behind the operator's
  back.
* **plaintext** — the old behaviour. Reachable only by asking for it by name.

There is deliberately **no** obfuscated-file backend. Base64, XOR, or a
hand-rolled cipher over a key stored beside the ciphertext is not a security
boundary; it is a way to describe a plaintext store using words that suggest
otherwise. If the keychain is unavailable, the honest options are to say so or
to fail.

## Policy

``OPENAI4S_SECRET_STORE`` mirrors ``OPENAI4S_KERNEL_SANDBOX``'s vocabulary,
because it is the same shape of decision and the codebase already has one:

``auto`` (default)
    Try the system keychain (verified by a real round-trip), then environment
    injection. If neither is available, **fail closed** — refuse to handle
    credentials at all.
``keychain``
    Keychain only. Fail closed.
``env``
    Environment injection only. Fail closed.
``plaintext``
    Store credentials in the database in the clear. Never implicit; an operator
    has to ask for it by name.

`auto` used to degrade to plaintext with a warning. That inverted the risk: the
deployment least able to protect a secret — a Linux server, with neither a
keychain nor a session bus — was exactly the one that silently got no
protection, while a developer laptop that needed it least got the keychain. A
warning printed at boot is not a control; it scrolls away and the credential
stays in the clear. Storing a secret unprotected is now a decision someone
makes, not a default they inherit.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import threading

_STORE_ENV = "OPENAI4S_SECRET_STORE"
_VALID_MODES = frozenset({"auto", "keychain", "env", "plaintext"})

# One service name per install, so a user can find and revoke these by hand.
_KEYCHAIN_SERVICE = "openai4s"
_CLI_TIMEOUT_S = 10.0

_REF_V1_PREFIX = "secret://v1/"
_REF_V2_PREFIX = "secret://v2/"
_NAMESPACE_DOMAIN = b"openai4s-secret-store-v2\x00"


class SecretBrokerError(RuntimeError):
    """A secret could not be stored or retrieved."""


class SecretStoreUnavailable(SecretBrokerError):
    """``keychain`` was demanded and no usable keychain exists."""


def store_namespace(store) -> str:
    """Stable, non-secret identity for one canonical Store path.

    The path itself never enters a reference or backend attribute.  Including
    the canonical real path in a domain-separated digest means copying an
    ``openai4s.db`` to another data directory creates a different credential
    namespace; a random id stored inside the copied database would not.
    """

    db_path = getattr(store, "db_path", None)
    if db_path is None:
        raise SecretBrokerError("a Store is required for namespaced secrets")
    canonical = os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(db_path))))
    digest = hashlib.sha256(_NAMESPACE_DOMAIN + os.fsencode(canonical)).hexdigest()
    return digest[:32]


def make_ref(scope: str, name: str, namespace: str | None = None) -> str:
    """The opaque handle a business table stores instead of the secret.

    Deliberately not a URL to anything and deliberately not derived from the
    value: it identifies *which* Store and logical secret, and leaks neither
    the credential nor the Store path. ``namespace=None`` constructs only the
    legacy v1 shape for compatibility; all Broker writes produce v2.
    """
    scope = _sanitize(scope, "scope")
    name = _sanitize(name, "name")
    if namespace is None:
        # Compatibility constructor for rows written before Store namespaces.
        # SecretBroker.put always supplies its v2 namespace.
        return f"{_REF_V1_PREFIX}{scope}/{name}"
    namespace = _sanitize_namespace(namespace)
    return f"{_REF_V2_PREFIX}{namespace}/{scope}/{name}"


def is_ref(value: object) -> bool:
    return isinstance(value, str) and value.startswith((_REF_V1_PREFIX, _REF_V2_PREFIX))


def parse_ref(ref: str) -> tuple[int, str | None, str, str]:
    """Return ``(version, namespace, scope, name)`` for a supported ref."""

    if not isinstance(ref, str):
        raise SecretBrokerError(f"not a secret reference: {ref!r}")
    if ref.startswith(_REF_V2_PREFIX):
        parts = ref[len(_REF_V2_PREFIX) :].split("/")
        if len(parts) != 3:
            raise SecretBrokerError(f"malformed secret reference: {ref!r}")
        namespace, scope, name = parts
        return (
            2,
            _sanitize_namespace(namespace),
            _sanitize(scope, "scope"),
            _sanitize(name, "name"),
        )
    if ref.startswith(_REF_V1_PREFIX):
        parts = ref[len(_REF_V1_PREFIX) :].split("/")
        if len(parts) != 2:
            raise SecretBrokerError(f"malformed secret reference: {ref!r}")
        scope, name = parts
        return 1, None, _sanitize(scope, "scope"), _sanitize(name, "name")
    raise SecretBrokerError(f"not a secret reference: {ref!r}")


def split_ref(ref: str) -> tuple[str, str]:
    _version, _namespace, scope, name = parse_ref(ref)
    return scope, name


def _sanitize_namespace(namespace: str) -> str:
    text = str(namespace or "").strip().lower()
    if len(text) != 32 or any(ch not in "0123456789abcdef" for ch in text):
        raise SecretBrokerError("secret namespace is invalid")
    return text


def _sanitize(part: str, label: str) -> str:
    text = str(part or "").strip()
    if not text:
        raise SecretBrokerError(f"secret {label} must not be empty")
    # The ref becomes a keychain account/service and rides in logs; keep it to
    # characters that cannot confuse either.
    bad = set(text) - set(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
    )
    if bad:
        raise SecretBrokerError(
            f"secret {label} {text!r} contains unsupported characters: "
            f"{''.join(sorted(bad))!r}"
        )
    return text


def _validate_secret(secret: str) -> str:
    text = str(secret)
    if "\n" in text or "\r" in text:
        # The keychain CLIs read one line from stdin. A multi-line secret would
        # be silently truncated — storing something that is not what the caller
        # handed us, and only failing later at the provider.
        raise SecretBrokerError("a secret must not contain a newline")
    return text


# --------------------------------------------------------------------------
# backends
# --------------------------------------------------------------------------


class _Backend:
    name = "none"
    persistent = False
    secure = False
    # A backend the app can read but not write: the operator owns the value.
    read_only = False

    def available(self) -> bool:
        return False

    def put(self, namespace: str, scope: str, name: str, secret: str) -> None:
        raise NotImplementedError

    def get(self, namespace: str, scope: str, name: str) -> str | None:
        raise NotImplementedError

    def delete(self, namespace: str, scope: str, name: str) -> None:
        raise NotImplementedError


def _run(argv: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        input=stdin.encode("utf-8") if stdin is not None else None,
        capture_output=True,
        timeout=_CLI_TIMEOUT_S,
    )


class KeychainBackend(_Backend):
    """macOS login keychain via ``security``."""

    name = "macos-keychain"
    persistent = True
    secure = True

    def available(self) -> bool:
        return sys.platform == "darwin" and bool(shutil.which("security"))

    def _account(self, namespace: str, scope: str, name: str) -> str:
        if not namespace:
            return f"{scope}/{name}"
        return f"v2/{namespace}/{scope}/{name}"

    def put(self, namespace: str, scope: str, name: str, secret: str) -> None:
        # -w LAST and the value on stdin. `security` prompts twice (enter +
        # confirm), hence the doubled line. Putting it in argv instead would
        # publish it to `ps`.
        proc = _run(
            [
                "security",
                "add-generic-password",
                "-U",
                "-a",
                self._account(namespace, scope, name),
                "-s",
                _KEYCHAIN_SERVICE,
                "-D",
                "OpenAI4S credential",
                "-w",
            ],
            stdin=f"{secret}\n{secret}\n",
        )
        if proc.returncode != 0:
            raise SecretBrokerError(
                f"keychain write failed: "
                f"{proc.stderr.decode('utf-8', 'replace').strip() or 'no stderr'}"
            )

    def get(self, namespace: str, scope: str, name: str) -> str | None:
        proc = _run(
            [
                "security",
                "find-generic-password",
                "-a",
                self._account(namespace, scope, name),
                "-s",
                _KEYCHAIN_SERVICE,
                "-w",
            ]
        )
        if proc.returncode != 0:
            return None
        # -w prints the password and a trailing newline; only that newline.
        return proc.stdout.decode("utf-8", "replace").rstrip("\n")

    def delete(self, namespace: str, scope: str, name: str) -> None:
        _run(
            [
                "security",
                "delete-generic-password",
                "-a",
                self._account(namespace, scope, name),
                "-s",
                _KEYCHAIN_SERVICE,
            ]
        )


class SecretServiceBackend(_Backend):
    """Linux desktop Secret Service via ``secret-tool`` (libsecret)."""

    name = "secret-service"
    persistent = True
    secure = True

    def available(self) -> bool:
        if not sys.platform.startswith("linux") or not shutil.which("secret-tool"):
            return False
        # A headless box has libsecret installed and no session bus to talk to.
        # Presence of the binary is not availability of the service.
        return bool(
            os.environ.get("DBUS_SESSION_BUS_ADDRESS")
            or os.environ.get("XDG_RUNTIME_DIR")
        )

    @staticmethod
    def _attributes(namespace: str, scope: str, name: str) -> list[str]:
        if not namespace:
            return ["service", _KEYCHAIN_SERVICE, "scope", scope, "name", name]
        return [
            "service",
            _KEYCHAIN_SERVICE,
            "version",
            "v2",
            "namespace",
            namespace,
            "scope",
            scope,
            "name",
            name,
        ]

    def put(self, namespace: str, scope: str, name: str, secret: str) -> None:
        proc = _run(
            [
                "secret-tool",
                "store",
                "--label",
                f"OpenAI4S v2/{namespace}/{scope}/{name}",
                *self._attributes(namespace, scope, name),
            ],
            stdin=secret,
        )
        if proc.returncode != 0:
            raise SecretBrokerError(
                f"secret-service write failed: "
                f"{proc.stderr.decode('utf-8', 'replace').strip() or 'no stderr'}"
            )

    def get(self, namespace: str, scope: str, name: str) -> str | None:
        proc = _run(
            [
                "secret-tool",
                "lookup",
                *self._attributes(namespace, scope, name),
            ]
        )
        if proc.returncode != 0:
            return None
        # secret-tool lookup does NOT append a newline.
        return proc.stdout.decode("utf-8", "replace")

    def delete(self, namespace: str, scope: str, name: str) -> None:
        _run(
            [
                "secret-tool",
                "clear",
                *self._attributes(namespace, scope, name),
            ]
        )


class EnvInjectionBackend(_Backend):
    """Credentials supplied by the operator's environment; never written to disk.

    This is how a server deployment holds secrets: systemd\'s ``EnvironmentFile``,
    a Kubernetes Secret, or whatever the config management already owns. The
    process reads them and stores nothing, so a snapshot of the data directory
    carries no credential at all — which is stronger than the keychain case, not
    a fallback from it.

    Read-only by nature, and that is the point rather than a limitation: if the
    environment owns the secret, the UI must not be able to overwrite it behind
    the operator\'s back. ``put`` therefore fails with the exact variable name to
    set, instead of silently accepting a value that would vanish on restart.

    The preferred variable is
    ``OPENAI4S_SECRET_V2_<NAMESPACE>_<SCOPE>_<NAME>``. Existing
    ``OPENAI4S_SECRET_<SCOPE>_<NAME>`` variables remain an explicit
    process-global fallback so upgrading does not silently change an
    operator-owned systemd/container contract. Names are upper-cased and every
    non-alphanumeric run is collapsed to ``_``.
    """

    name = "env-injection"
    persistent = False
    secure = True
    read_only = True

    PREFIX = "OPENAI4S_SECRET_"
    # Explicit opt-in for a server that has not been given any credential yet:
    # without it a fresh box would look like "no backend" and fail closed before
    # the operator could ever supply one.
    ENABLE = "OPENAI4S_SECRET_ENV"

    @staticmethod
    def var_name(scope: str, name: str) -> str:
        raw = f"{EnvInjectionBackend.PREFIX}{scope}_{name}"
        out = []
        for ch in raw:
            out.append(ch if ch.isalnum() else "_")
        # Collapse runs so "a..b" and "a__b" cannot name different variables.
        collapsed = "_".join(part for part in "".join(out).split("_") if part)
        return collapsed.upper()

    @staticmethod
    def namespaced_var_name(namespace: str, scope: str, name: str) -> str:
        namespace = _sanitize_namespace(namespace)
        raw = f"{EnvInjectionBackend.PREFIX}V2_{namespace}_{scope}_{name}"
        out = [ch if ch.isalnum() else "_" for ch in raw]
        return "_".join(part for part in "".join(out).split("_") if part).upper()

    def available(self) -> bool:
        if os.environ.get(self.ENABLE, "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            return True
        return any(k.startswith(self.PREFIX) for k in os.environ)

    def put(self, namespace: str, scope: str, name: str, secret: str) -> None:
        raise SecretBrokerError(
            f"this deployment takes credentials from the environment, so they "
            f"cannot be set from the app. Set "
            f"{self.namespaced_var_name(namespace, scope, name)} in the daemon's "
            f"environment "
            f"(systemd EnvironmentFile, container secret, …) and restart."
        )

    def get(self, namespace: str, scope: str, name: str) -> str | None:
        if namespace:
            namespaced = os.environ.get(
                self.namespaced_var_name(namespace, scope, name)
            )
            if namespaced:
                return namespaced
        # This fallback is deliberate operator policy: environment injection
        # is process-global by construction. Ambiguous legacy system-keychain
        # slots do not receive the same exception.
        return os.environ.get(self.var_name(scope, name)) or None

    def delete(self, namespace: str, scope: str, name: str) -> None:
        # Nothing of ours to remove; the operator owns the variable.
        return None


class PlaintextBackend(_Backend):
    """Today's behaviour: the value in the Store's ``settings`` table.

    Kept so a working install does not lose its configuration the day the
    broker lands, and so `auto` has somewhere to degrade to. It is not a
    security boundary and never claims to be — ``secure`` is False and the
    posture says so.
    """

    name = "plaintext-db"
    persistent = True
    secure = False

    def __init__(self, store) -> None:
        self._store = store

    def available(self) -> bool:
        return self._store is not None

    def _key(self, namespace: str, scope: str, name: str) -> str:
        if not namespace:
            return f"secret::{scope}::{name}"
        return f"secret::v2::{namespace}::{scope}::{name}"

    def put(self, namespace: str, scope: str, name: str, secret: str) -> None:
        self._store.set_setting(self._key(namespace, scope, name), secret)

    def get(self, namespace: str, scope: str, name: str) -> str | None:
        value = self._store.get_setting(self._key(namespace, scope, name))
        return value if value else None

    def delete(self, namespace: str, scope: str, name: str) -> None:
        self._store.set_setting(self._key(namespace, scope, name), "")


class MemoryBackend(_Backend):
    """Non-persistent. For tests, and for a headless deployment that chooses
    injection over storage: the secret lives for the process and no longer."""

    name = "memory"
    persistent = False
    secure = True

    def __init__(self) -> None:
        self._values: dict[tuple[str, str, str], str] = {}

    def available(self) -> bool:
        return True

    def put(self, namespace: str, scope: str, name: str, secret: str) -> None:
        self._values[(namespace, scope, name)] = secret

    def get(self, namespace: str, scope: str, name: str) -> str | None:
        return self._values.get((namespace, scope, name))

    def delete(self, namespace: str, scope: str, name: str) -> None:
        self._values.pop((namespace, scope, name), None)


# --------------------------------------------------------------------------
# broker
# --------------------------------------------------------------------------


def _mode(value: str | None = None) -> str:
    mode = str(value if value is not None else os.environ.get(_STORE_ENV, "auto"))
    mode = mode.strip().lower() or "auto"
    if mode not in _VALID_MODES:
        raise SecretBrokerError(
            f"{_STORE_ENV} must be one of {', '.join(sorted(_VALID_MODES))}; "
            f"got {mode!r}"
        )
    return mode


def _system_backends() -> list[_Backend]:
    # Ordered by preference. A desktop keychain is interactive and durable; env
    # injection is what a server deployment actually has. Plaintext is never in
    # this list — `auto` must not reach it by falling off the end.
    return [KeychainBackend(), SecretServiceBackend(), EnvInjectionBackend()]


class SecretBroker:
    """Resolve a backend once, then store and fetch secrets through it."""

    def __init__(
        self,
        store=None,
        *,
        mode: str | None = None,
        backends: list[_Backend] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._mode = _mode(mode)
        self._store = store
        self._namespace = store_namespace(store) if store is not None else None
        self._detail = ""
        self._backend = self._resolve(backends)

    def _resolve(self, backends: list[_Backend] | None) -> _Backend:
        if self._mode == "plaintext":
            self._detail = f"explicitly selected by {_STORE_ENV}=plaintext"
            return PlaintextBackend(self._store)

        candidates = backends if backends is not None else _system_backends()
        if self._mode == "keychain":
            candidates = [b for b in candidates if not b.read_only]
        elif self._mode == "env":
            candidates = [b for b in candidates if b.read_only]

        tried: list[str] = []
        for backend in candidates:
            if not backend.available():
                continue
            # A read-only backend cannot round-trip a probe (there is nothing it
            # can write), and its availability was already an explicit operator
            # act — supplying the variable, or setting the enable flag.
            if backend.read_only:
                self._detail = f"{backend.name} supplied by the environment"
                return backend
            ok, why = self._self_test(backend)
            if ok:
                self._detail = f"{backend.name} verified by a round-trip self-test"
                return backend
            tried.append(f"{backend.name} present but unusable: {why}")

        # Fail closed. `auto` used to fall through to plaintext here with a
        # warning, which meant the deployment most likely to need protection —
        # a Linux server, where neither a keychain nor a session bus exists —
        # was exactly the one that silently got none. Storing a credential in
        # the clear is now something an operator has to ask for by name.
        self._detail = "; ".join(tried) or "no secure secret store on this host"
        raise SecretStoreUnavailable(
            f"refusing to handle credentials without a secure store "
            f"({self._detail}).\n"
            f"  desktop: a login keychain (macOS) or libsecret + a session "
            f"keyring (Linux) makes this work with no further configuration.\n"
            f"  server:  supply credentials in the daemon's environment as "
            f"{EnvInjectionBackend.PREFIX}V2_<NAMESPACE>_<SCOPE>_<NAME> "
            f"(legacy process-global {EnvInjectionBackend.PREFIX}<SCOPE>_<NAME> "
            f"also remains supported), and set "
            f"{EnvInjectionBackend.ENABLE}=1 before any are configured); "
            f"nothing is written to disk.\n"
            f"  to accept plaintext storage anyway, set "
            f"{_STORE_ENV}=plaintext explicitly."
        )

    @staticmethod
    def _self_test(backend: _Backend) -> tuple[bool, str]:
        """Prove the backend round-trips before trusting it with a real secret.

        Presence of a CLI is not availability of a keychain: a locked keychain,
        a missing session bus, or a denied prompt all fail only at first use —
        which would otherwise be when the user's key silently fails to save.
        """
        probe = "__selftest__"
        canary = "openai4s-selftest-value"
        probe_namespace = hashlib.sha256(os.urandom(32)).hexdigest()[:32]
        try:
            backend.put(probe_namespace, probe, "probe", canary)
            got = backend.get(probe_namespace, probe, "probe")
        except Exception as e:  # noqa: BLE001 - any failure disqualifies it
            return False, str(e)
        finally:
            try:
                backend.delete(probe_namespace, probe, "probe")
            except Exception:  # noqa: BLE001
                pass
        if got != canary:
            return False, "round-trip returned a different value"
        return True, ""

    # --- the contract ----------------------------------------------------
    def put(self, scope: str, name: str, secret: str) -> str:
        """Store a secret and return the reference to record in its place."""
        scope = _sanitize(scope, "scope")
        name = _sanitize(name, "name")
        value = _validate_secret(secret)
        if self._namespace is None:
            raise SecretBrokerError("a Store is required to write a secret")
        with self._lock:
            self._backend.put(self._namespace, scope, name, value)
        return make_ref(scope, name, self._namespace)

    def get(self, ref: str) -> str | None:
        version, namespace, scope, name = parse_ref(ref)
        with self._lock:
            if version == 2:
                # Reject a copied/foreign Store's reference before invoking a
                # backend. This is both the authorization check and the proof
                # a malicious ref cannot be used as a keychain lookup oracle.
                if namespace != self._namespace:
                    return None
                return self._backend.get(namespace or "", scope, name)
            if isinstance(self._backend, (PlaintextBackend, EnvInjectionBackend)):
                # DB-local plaintext has an owner, and an environment variable
                # is an operator's explicit process-global policy. A legacy
                # Keychain/Secret Service slot has neither and is fail-closed.
                return self._backend.get("", scope, name)
            return None

    def delete(self, ref: str) -> None:
        version, namespace, scope, name = parse_ref(ref)
        with self._lock:
            if version == 2:
                if namespace != self._namespace:
                    return
                self._backend.delete(namespace or "", scope, name)
                return
            if isinstance(self._backend, (PlaintextBackend, EnvInjectionBackend)):
                self._backend.delete("", scope, name)
            # Never delete an ownerless v1 system slot. Another data directory
            # may still be the installation legitimately using it.

    @property
    def read_only(self) -> bool:
        """Whether the operator, not the app, owns what this backend holds.

        A caller needs this to know what an *absent* row means. Behind a
        writable backend an empty row is the app's own answer — the value was
        never set, or was cleared through the UI. Behind a read-only one the
        row was never the answer at all: the app cannot write there, so the
        environment is the only thing that could have supplied a value.
        """
        return bool(self._backend.read_only)

    @property
    def namespace(self) -> str | None:
        """This Store's secret namespace, or None when there is no Store.

        Exposed so a caller that has to build a reference for a row that does
        not exist yet can build the *same* v2 reference `put` would have
        written, rather than the v1 compatibility form — which addresses a
        different variable and would miss the one the app's own error message
        tells an operator to set.
        """
        return self._namespace

    def describe(self, ref: str) -> dict:
        """Metadata for an API response. Never the value."""
        _version, _namespace, scope, name = parse_ref(ref)
        configured = self.get(ref) is not None
        return {
            "ref": ref,
            "scope": scope,
            "name": name,
            "configured": configured,
            "backend": self._backend.name,
            "reentry_required": self.ref_requires_reentry(ref),
        }

    def ref_requires_reentry(self, ref: str) -> bool:
        """Whether this Store must ask the user to save the credential again."""

        version, namespace, _scope, _name = parse_ref(ref)
        if version == 2:
            return namespace != self._namespace
        return bool(
            not isinstance(self._backend, (PlaintextBackend, EnvInjectionBackend))
        )

    # Compatibility name used by the first migration caller.
    legacy_ref_requires_reentry = ref_requires_reentry

    def posture(self) -> dict:
        """Machine-readable report. Says plainly when it is not secure."""
        return {
            "mode": self._mode,
            "backend": self._backend.name,
            "secure": self._backend.secure,
            "persistent": self._backend.persistent,
            "detail": self._detail,
        }


_warned: set[str] = set()


def _warn_degraded(message: str) -> None:
    if message in _warned:
        return
    _warned.add(message)
    print(f"OPENAI4S SECURITY WARNING: {message}", file=sys.stderr)


__all__ = [
    "KeychainBackend",
    "MemoryBackend",
    "PlaintextBackend",
    "SecretBroker",
    "SecretBrokerError",
    "SecretServiceBackend",
    "SecretStoreUnavailable",
    "is_ref",
    "make_ref",
    "parse_ref",
    "split_ref",
    "store_namespace",
]
