"""Credential storage behind an opaque reference.

Business tables should hold a *reference* to a secret, not the secret. Today
they hold the secret: model-profile API keys and ``llm_api_key`` /
``tavily_api_key`` live in ``settings`` as plaintext, connector credentials in
``connectors.env``. The data directory is now owner-only, which removes the
trivial read by another local account — but a file mode is not encryption, and
it does nothing for a backup, an rsync, a container layer, or a support bundle.

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

_REF_PREFIX = "secret://v1/"


class SecretBrokerError(RuntimeError):
    """A secret could not be stored or retrieved."""


class SecretStoreUnavailable(SecretBrokerError):
    """``keychain`` was demanded and no usable keychain exists."""


def make_ref(scope: str, name: str) -> str:
    """The opaque handle a business table stores instead of the secret.

    Deliberately not a URL to anything and deliberately not derived from the
    value: it identifies *which* secret, and leaks nothing about it. Two
    installs with the same key produce the same ref, which is what makes a ref
    safe to log.
    """
    scope = _sanitize(scope, "scope")
    name = _sanitize(name, "name")
    return f"{_REF_PREFIX}{scope}/{name}"


def is_ref(value: object) -> bool:
    return isinstance(value, str) and value.startswith(_REF_PREFIX)


def split_ref(ref: str) -> tuple[str, str]:
    if not is_ref(ref):
        raise SecretBrokerError(f"not a secret reference: {ref!r}")
    scope, _, name = ref[len(_REF_PREFIX) :].partition("/")
    if not scope or not name:
        raise SecretBrokerError(f"malformed secret reference: {ref!r}")
    return scope, name


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

    def put(self, scope: str, name: str, secret: str) -> None:
        raise NotImplementedError

    def get(self, scope: str, name: str) -> str | None:
        raise NotImplementedError

    def delete(self, scope: str, name: str) -> None:
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

    def _account(self, scope: str, name: str) -> str:
        return f"{scope}/{name}"

    def put(self, scope: str, name: str, secret: str) -> None:
        # -w LAST and the value on stdin. `security` prompts twice (enter +
        # confirm), hence the doubled line. Putting it in argv instead would
        # publish it to `ps`.
        proc = _run(
            [
                "security",
                "add-generic-password",
                "-U",
                "-a",
                self._account(scope, name),
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

    def get(self, scope: str, name: str) -> str | None:
        proc = _run(
            [
                "security",
                "find-generic-password",
                "-a",
                self._account(scope, name),
                "-s",
                _KEYCHAIN_SERVICE,
                "-w",
            ]
        )
        if proc.returncode != 0:
            return None
        # -w prints the password and a trailing newline; only that newline.
        return proc.stdout.decode("utf-8", "replace").rstrip("\n")

    def delete(self, scope: str, name: str) -> None:
        _run(
            [
                "security",
                "delete-generic-password",
                "-a",
                self._account(scope, name),
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

    def put(self, scope: str, name: str, secret: str) -> None:
        proc = _run(
            [
                "secret-tool",
                "store",
                "--label",
                f"OpenAI4S {scope}/{name}",
                "service",
                _KEYCHAIN_SERVICE,
                "scope",
                scope,
                "name",
                name,
            ],
            stdin=secret,
        )
        if proc.returncode != 0:
            raise SecretBrokerError(
                f"secret-service write failed: "
                f"{proc.stderr.decode('utf-8', 'replace').strip() or 'no stderr'}"
            )

    def get(self, scope: str, name: str) -> str | None:
        proc = _run(
            [
                "secret-tool",
                "lookup",
                "service",
                _KEYCHAIN_SERVICE,
                "scope",
                scope,
                "name",
                name,
            ]
        )
        if proc.returncode != 0:
            return None
        # secret-tool lookup does NOT append a newline.
        return proc.stdout.decode("utf-8", "replace")

    def delete(self, scope: str, name: str) -> None:
        _run(
            [
                "secret-tool",
                "clear",
                "service",
                _KEYCHAIN_SERVICE,
                "scope",
                scope,
                "name",
                name,
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

    A variable is named ``OPENAI4S_SECRET_<SCOPE>_<NAME>``, upper-cased with
    every non-alphanumeric run collapsed to ``_`` — so the llm api key is
    ``OPENAI4S_SECRET_LLM_LLM_API_KEY`` and a connector env value is
    ``OPENAI4S_SECRET_CONNECTOR_ENV_LAB_LAB_TOKEN``.
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

    def available(self) -> bool:
        if os.environ.get(self.ENABLE, "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            return True
        return any(k.startswith(self.PREFIX) for k in os.environ)

    def put(self, scope: str, name: str, secret: str) -> None:
        raise SecretBrokerError(
            f"this deployment takes credentials from the environment, so they "
            f"cannot be set from the app. Set "
            f"{self.var_name(scope, name)} in the daemon's environment "
            f"(systemd EnvironmentFile, container secret, …) and restart."
        )

    def get(self, scope: str, name: str) -> str | None:
        return os.environ.get(self.var_name(scope, name)) or None

    def delete(self, scope: str, name: str) -> None:
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

    def _key(self, scope: str, name: str) -> str:
        return f"secret::{scope}::{name}"

    def put(self, scope: str, name: str, secret: str) -> None:
        self._store.set_setting(self._key(scope, name), secret)

    def get(self, scope: str, name: str) -> str | None:
        value = self._store.get_setting(self._key(scope, name))
        return value if value else None

    def delete(self, scope: str, name: str) -> None:
        self._store.set_setting(self._key(scope, name), "")


class MemoryBackend(_Backend):
    """Non-persistent. For tests, and for a headless deployment that chooses
    injection over storage: the secret lives for the process and no longer."""

    name = "memory"
    persistent = False
    secure = True

    def __init__(self) -> None:
        self._values: dict[tuple[str, str], str] = {}

    def available(self) -> bool:
        return True

    def put(self, scope: str, name: str, secret: str) -> None:
        self._values[(scope, name)] = secret

    def get(self, scope: str, name: str) -> str | None:
        return self._values.get((scope, name))

    def delete(self, scope: str, name: str) -> None:
        self._values.pop((scope, name), None)


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
            f"{EnvInjectionBackend.PREFIX}<SCOPE>_<NAME> (and set "
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
        try:
            backend.put(probe, "probe", canary)
            got = backend.get(probe, "probe")
        except Exception as e:  # noqa: BLE001 - any failure disqualifies it
            return False, str(e)
        finally:
            try:
                backend.delete(probe, "probe")
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
        with self._lock:
            self._backend.put(scope, name, value)
        return make_ref(scope, name)

    def get(self, ref: str) -> str | None:
        scope, name = split_ref(ref)
        with self._lock:
            return self._backend.get(scope, name)

    def delete(self, ref: str) -> None:
        scope, name = split_ref(ref)
        with self._lock:
            self._backend.delete(scope, name)

    def describe(self, ref: str) -> dict:
        """Metadata for an API response. Never the value."""
        scope, name = split_ref(ref)
        with self._lock:
            configured = self._backend.get(scope, name) is not None
        return {
            "ref": ref,
            "scope": scope,
            "name": name,
            "configured": configured,
            "backend": self._backend.name,
        }

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
    "split_ref",
]
