"""openai4s CLI: serve / status / stop / url / run / init / setup.

openai4s serve    start the daemon (supports --port/--no-browser/--detached)
openai4s status   is the daemon up? (reads pidfile + /health)
openai4s stop     stop the running daemon
openai4s url      print the local web UI url
openai4s run "<task>"   run one Code-as-Action task (in-process, no daemon)
openai4s init     guided first-run model configuration
openai4s setup    create/update conda envs from envs/*.yml
openai4s jupyter  describe/export/install the optional Jupyter bridge
"""

from __future__ import annotations

import argparse
import errno
import getpass
import ipaddress
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from openai4s import __version__
from openai4s.config import get_config
from openai4s.execution.process_group import TERM_GRACE_S


def _statefile_payload(cfg) -> str:
    return json.dumps(
        {
            "pid": os.getpid(),
            "pid_start": _process_start_token(os.getpid()),
            "host": cfg.host,
            "port": cfg.port,
            "started_at": int(time.time()),
        }
    )


def _acquire_singleton(cfg) -> bool:
    """Atomically claim the daemon pidfile. True iff we now own the singleton.

    ``O_CREAT|O_EXCL`` makes "create the pidfile or fail" a single atomic step,
    closing the check-then-write race where two concurrent ``serve`` runs each
    passed a separate liveness check and then both booted the same data dir. A
    stale pidfile (its recorded pid is gone) is reclaimed once; a live one
    means another daemon holds the slot. The statefile is a non-authoritative
    sidecar written after — the pidfile is the lock.
    """
    for reclaim in (True, False):
        try:
            fd = os.open(cfg.pidfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            existing = _read_pid(cfg)
            if existing and _daemon_alive(cfg, existing):
                return False
            if not reclaim:
                return False  # a concurrent booter won the reclaim
            try:
                cfg.pidfile.unlink()
            except FileNotFoundError:
                pass
            continue
        try:
            # The statefile first, then the pid — the reverse of the obvious
            # order, and load-bearing since the statefile started carrying the
            # identity `_daemon_alive` compares against.
            #
            # Written second, there was a window holding a readable *new* pid
            # beside the *previous* generation's record. When the two happen to
            # name the same pid — negligible on a desktop, the ordinary case in
            # a container where the daemon lands on the same low pid every boot
            # — a reader in that window compares this process against its
            # predecessor's start token, concludes stale, and acts on it: a
            # second `serve` reclaims a live pidfile, and `stop` reports "not
            # running" and then deletes the live daemon's state.
            #
            # This way round there is no such state. The pidfile is empty until
            # the record describing it is already on disk, and an empty pidfile
            # is a case every reader already handles as "no daemon".
            cfg.statefile.write_text(_statefile_payload(cfg), "utf-8")
            with os.fdopen(fd, "w") as handle:
                handle.write(str(os.getpid()))
        except OSError:
            _clear_state(cfg, only_if_owned_by=os.getpid())
            raise
        return True
    return False


def _clear_state(cfg, *, only_if_owned_by: int | None = None) -> None:
    """Remove the daemon state files.

    ``only_if_owned_by`` guards the serve path: on a startup failure a booter
    must delete only the pidfile it actually wrote, never one a concurrent
    winner now owns (deleting that stranded a live daemon whose port stayed
    bound while ``status`` and ``stop`` read "not running"). ``stop`` clears
    unconditionally — it is deliberately removing another process's state after
    confirming that process is gone.
    """
    if only_if_owned_by is not None:
        current = _read_pid(cfg)
        if current is not None and current != only_if_owned_by:
            return
    for p in (cfg.pidfile, cfg.statefile):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def _read_pid(cfg) -> int | None:
    try:
        return int(cfg.pidfile.read_text("utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _process_start_token(pid: int) -> str | None:
    """An identity for *this* incarnation of ``pid``, or None where unknowable.

    ``os.kill(pid, 0)`` answers "some process has this pid", which is not the
    question the singleton is asking — "is the daemon we recorded still
    running". The two come apart wherever pids are reused quickly, and a
    container is the worst case rather than a corner one: every restart starts
    a fresh pid namespace at 1, so a pidfile persisted on a volume names a low
    pid that the *new* container has almost certainly handed to something else
    — often to the init that just launched this very daemon. `serve` then
    reports "daemon already running (pid 1)" and exits 1, every time, on the
    volume whose whole purpose was to make restarts safe.

    Linux exposes the distinguishing fact: field 22 of ``/proc/<pid>/stat`` is
    the process's start time in clock ticks since boot. Two processes may share
    a pid, but a process that started at a different moment is a different
    process. Elsewhere — macOS has no procfs — there is nothing cheap and
    correct to read, so this returns None and the caller keeps the older,
    weaker answer instead of guessing.
    """
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    # comm (field 2) is the one field that may contain spaces and parentheses,
    # and it is always parenthesised — so split after its *last* ')' rather
    # than on whitespace, which a process named "(x) 1 2 3" would otherwise
    # shift by four fields.
    close = raw.rfind(b")")
    if close == -1:
        return None
    fields = raw[close + 2 :].split()
    # Field 22 overall. Fields 1 and 2 are behind us, so it is index 19 here.
    if len(fields) < 20:
        return None
    return fields[19].decode("ascii", "replace")


def _recorded_state(cfg) -> dict[str, object] | None:
    """Return the daemon sidecar when it is a JSON object, otherwise ``None``."""
    path = getattr(cfg, "statefile", None)
    if path is None:
        return None
    try:
        payload = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _recorded_identity(cfg) -> tuple[int | None, str | None]:
    """The (pid, start token) the running daemon wrote, as far as it is known."""
    payload = _recorded_state(cfg)
    if payload is None:
        return (None, None)
    pid = payload.get("pid")
    start = payload.get("pid_start")
    return (
        pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
        start if isinstance(start, str) and start else None,
    )


def _valid_recorded_host(value: object) -> bool:
    """Whether a sidecar value is a bind host, rather than URL-shaped input."""
    if not isinstance(value, str):
        return False
    # The empty string is Python's IPv4 wildcard bind and is rendered as
    # localhost by `_reachable_host`. Preserve that established meaning.
    if value == "":
        return True
    if value != value.strip() or any(char.isspace() for char in value):
        return False
    if any(char in value for char in "/?#@\\[]"):
        return False

    if ":" in value:
        try:
            ipaddress.IPv6Address(value)
        except ValueError:
            return False
        return True

    try:
        ascii_host = value.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    if len(ascii_host) > 253:
        return False
    candidate = ascii_host[:-1] if ascii_host.endswith(".") else ascii_host
    if not candidate:
        return False
    if all(char in "0123456789." for char in candidate):
        try:
            ipaddress.IPv4Address(candidate)
        except ValueError:
            return False
        return True
    for label in candidate.split("."):
        if (
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not all(char.isalnum() or char == "-" for char in label)
        ):
            return False
    return True


def _recorded_endpoint(cfg, expected_pid: int) -> tuple[str, int] | None:
    """The live daemon endpoint, only for the exact recorded process generation.

    ``daemon.json`` is a non-authoritative sidecar. A stale generation, a
    partially written file, or a malformed host/port must therefore fall back
    to the caller's current config rather than steering a local control request.
    A pid alone is not an identity: after reuse, a stale sidecar could otherwise
    redirect the local access-token URL to an unrelated host that happens to
    hold the same pid.  Linux provides the process start token needed to bind
    the endpoint to one generation.  On platforms where that token is
    unavailable, callers safely fall back to their current configuration.
    """
    payload = _recorded_state(cfg)
    if payload is None:
        return None
    recorded_pid = payload.get("pid")
    if (
        not isinstance(recorded_pid, int)
        or isinstance(recorded_pid, bool)
        or recorded_pid != expected_pid
    ):
        return None
    recorded_start = payload.get("pid_start")
    if not isinstance(recorded_start, str) or not recorded_start:
        return None
    current_start = _process_start_token(expected_pid)
    if current_start is None or current_start != recorded_start:
        return None
    host = payload.get("host")
    port = payload.get("port")
    if not isinstance(host, str) or not _valid_recorded_host(host):
        return None
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        return None
    return (host, port)


def _daemon_alive(cfg, pid: int) -> bool:
    """Is the daemon that wrote the pidfile still the process holding ``pid``?

    Liveness first, because it is the cheap half and the only half available
    off Linux. When the statefile corroborates the pidfile — same pid, and a
    start token to compare — a mismatched token means the pid was reused and
    the pidfile is stale.

    A statefile naming a *different* pid is deliberately treated as no
    information rather than as evidence of staleness. It is written just after
    the pidfile is claimed, so during that window it still describes the
    previous generation; reading it as proof would let a second booter declare
    the live winner stale and reclaim its pidfile — reopening the double-boot
    race ``O_EXCL`` exists to close.
    """
    if not _pid_alive(pid):
        return False
    recorded_pid, recorded_start = _recorded_identity(cfg)
    if recorded_pid != pid or recorded_start is None:
        return True
    current = _process_start_token(pid)
    if current is None:
        return True
    return current == recorded_start


def _live_endpoint(cfg) -> tuple[str, int] | None:
    """The endpoint of the daemon that currently owns this data dir, if known.

    Every command that dials the local daemon needs the same answer, and
    getting it in only some of them is how one CLI comes to disagree with
    itself: under the WSL NAT fallback the launcher starts `serve` with an
    explicit `OPENAI4S_HOST`, while a later `openai4s <cmd>` has only the
    default. ``None`` means "no better information than the caller's config".
    """

    if getattr(cfg, "pidfile", None) is None:
        return None
    pid = _read_pid(cfg)
    if not pid or not _daemon_alive(cfg, pid):
        return None
    return _recorded_endpoint(cfg, pid)


def _reachable_host(host: str) -> str:
    """A bind address, rendered as somewhere a client can actually connect.

    A wildcard bind is a statement about which interfaces to listen on, not an
    address: nothing dials `http://0.0.0.0:8760/`. Every caller of `_url` is
    either handing the string to a person to open or opening it itself, and
    both were being given a URL that is wrong on macOS and merely peculiar on
    Linux. Containers made it the common case rather than the exotic one —
    `OPENAI4S_HOST=0.0.0.0` is the only way a published port reaches the
    daemon — so the startup banner a container operator reads, and the
    `openai4s url` they run to recover their token, both printed an address
    they then had to know to translate.

    Loopback is the honest rendering: it is reachable from inside the
    namespace that is listening, and it is what the operator's own port
    publishing or `kubectl port-forward` puts on the other end.
    """
    return "localhost" if host in ("0.0.0.0", "::", "") else host


def _url(
    cfg,
    *,
    with_token: bool = True,
    endpoint: tuple[str, int] | None = None,
) -> str:
    """The URL a person can actually open.

    This returned the bare origin, and every human-facing caller used it: the
    browser auto-open on `serve`, the `status` line, the `url` command, and the
    macOS .app. Since the access token became required by default on loopback,
    that URL answers 401 — and so does `/static/app.js`, so the SPA never loads
    and cannot offer a way in. The one working URL went to stderr, which the
    .app redirects into a log file nobody is looking at on first launch.

    `with_token=False` is for anywhere the string is not being handed to a
    person to open — a credential does not belong in a log line or a title.
    """
    host, port = endpoint if endpoint is not None else (cfg.host, cfg.port)
    reachable_host = _reachable_host(host)
    authority = f"[{reachable_host}]" if ":" in reachable_host else reachable_host
    base = f"http://{authority}:{port}/"
    if not with_token:
        return base
    try:
        from openai4s.server import local_auth

        token = local_auth.read_token(cfg.data_dir)
    except Exception:  # noqa: BLE001 — never let this break `serve`
        token = None
    return f"{base}?token={token}" if token else base


def _sigterm_to_keyboard_interrupt(signum, frame):
    """Turn `openai4s stop`'s SIGTERM into the Ctrl-C path — nothing more.

    The state files must outlive the process they describe: clearing them
    here, at signal arrival, deleted the pidfile before the (possibly slow)
    runner/kernel teardown ran, so a `stop` that timed out told the user to
    retry against a pidfile the daemon had already removed — the retry and
    `stop --force` both saw "not running" while the port stayed bound.  The
    serve loop's finally clears state after teardown completes.
    """
    raise KeyboardInterrupt


def _foreground_cell_interrupt(agent):
    """Restore what Ctrl-C did before the worker had its own session.

    Until the kernel worker was moved into its own session, a terminal Ctrl-C
    was delivered to the whole foreground process group -- so it reached both
    this process, where the default handler raised KeyboardInterrupt out of
    `Agent.run`, and the worker, whose handler ended the running cell. Session
    isolation is deliberate (a stray Ctrl-C must not end every cell a daemon is
    running) and it takes the second half away from the CLI, where `Agent.run`
    executes cells on this very thread.

    So this restores exactly that pair and invents nothing: interrupt this
    Agent's own workers, then raise, which is what the two handlers did between
    them. Returns a context manager; off the main thread, or where the
    disposition cannot be set, it is a no-op and the old behaviour stands.
    """

    import contextlib
    import threading

    @contextlib.contextmanager
    def _installed():
        if threading.current_thread() is not threading.main_thread():
            yield
            return

        def _handler(signum, frame):  # noqa: ANN001, ARG001
            try:
                agent.interrupt_foreground()
            finally:
                # Unconditionally: the CLI has always exited on Ctrl-C, and a
                # run that survived because no worker happened to be up would
                # be a new behaviour arriving through a signal handler.
                raise KeyboardInterrupt

        try:
            previous = signal.signal(signal.SIGINT, _handler)
        except (ValueError, OSError):  # pragma: no cover - unsupported platform
            yield
            return
        try:
            yield
        finally:
            try:
                signal.signal(signal.SIGINT, previous)
            except (ValueError, OSError):  # pragma: no cover
                pass

    return _installed()


def _bind_failure_message(exc: OSError, cfg) -> str | None:
    """One clear line naming the env var to fix, or ``None`` to re-raise.

    ``build_server`` does more than bind, so only bind-shaped errnos map to
    the port: EADDRINUSE and EADDRNOTAVAIL cannot come from anywhere else,
    while EACCES is blamed on the port only when the port is actually
    privileged — a read-only data dir raises EACCES too, and that one must
    stay a traceback pointing at the real path.
    """
    prefix = f"error: cannot listen on {cfg.host}:{cfg.port} — "
    if exc.errno == errno.EADDRINUSE:
        return prefix + (
            "address already in use. A previous daemon may still be "
            "shutting down, or another process holds the port; free it or "
            "change OPENAI4S_PORT."
        )
    if exc.errno == errno.EACCES and cfg.port < 1024:
        return prefix + (
            "permission denied. Ports below 1024 are privileged; pick an "
            "unprivileged one via OPENAI4S_PORT (default 8760)."
        )
    if exc.errno == errno.EADDRNOTAVAIL:
        return prefix + (
            "this machine has no such address. Check OPENAI4S_HOST "
            "(127.0.0.1 serves locally)."
        )
    return None


def _apply_serve_overrides(args, cfg) -> None:
    """Apply command-line listen overrides to config and the child environment.

    Host and port historically came only from environment variables.  Config's
    dataclass defaults are evaluated when its module is imported, so changing
    the environment alone here would be too late for the foreground process.
    Updating both keeps this process and a detached child on the same address.
    """

    host = getattr(args, "host", None)
    port = getattr(args, "port", None)
    if host:
        cfg.host = str(host)
        os.environ["OPENAI4S_HOST"] = str(host)
    if port is not None:
        cfg.port = int(port)
        os.environ["OPENAI4S_PORT"] = str(port)


def _tcp_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("port must be an integer") from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _open_daemon(request, *, timeout: float):
    """Open a local daemon URL without consulting environment proxies.

    Under WSL2 the daemon can be reached through its NAT address rather than
    loopback.  It is still a local control-plane request, so sending it through
    an inherited HTTP(S) proxy is both incorrect and a source of false startup
    failures.
    """

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(request, timeout=timeout)


def _health_ready(cfg) -> bool:
    """True only when the listener identifies itself as an OpenAI4S daemon."""

    try:
        with _open_daemon(
            _url(cfg, with_token=False) + "health", timeout=1
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        # The occupant may be any local service: a JSON body that is not an
        # object is "not our daemon", never a crash.
        return (
            response.status == 200
            and isinstance(payload, dict)
            and payload.get("status") == "ok"
        )
    except (OSError, ValueError):
        return False


def _cleanup_failed_detached_child(process) -> None:
    """Stop and reap a detached child whose readiness contract failed."""

    if process.poll() is not None:
        return
    try:
        process.terminate()
    except (ProcessLookupError, OSError):
        pass
    try:
        process.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    except (ProcessLookupError, OSError):
        return

    try:
        process.kill()
    except (ProcessLookupError, OSError):
        pass
    try:
        process.wait(timeout=5)
    except (ProcessLookupError, OSError, subprocess.TimeoutExpired):
        pass


def _cmd_serve_detached(args, cfg) -> int:
    """Start the same foreground server in a new POSIX session.

    The detached child owns the pid/state files and all shutdown handling.  The
    parent only redirects its descriptors, waits until the child-owned pidfile
    and the real ``/health`` response agree, and then returns.  This is
    intentionally a CLI convenience for Linux/macOS (including WSL2), not a
    native-Windows kernel path.
    """

    if os.name != "posix":
        print(
            "error: --detached is supported on Linux/macOS; on Windows run "
            "OpenAI4S inside WSL2.",
            file=sys.stderr,
        )
        return 2

    cfg.ensure_dirs()
    log_path = cfg.logs_dir / "app.out"
    command = [
        sys.executable,
        "-m",
        "openai4s",
        "serve",
        "--host",
        str(cfg.host),
        "--port",
        str(cfg.port),
        "--no-browser",
    ]
    with log_path.open("ab", buffering=0) as log:
        process = subprocess.Popen(
            command,
            cwd=cfg.data_dir,
            env=dict(os.environ),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
        )

    # A packaged bundle's first start can spend most of a minute on imports and
    # Store migrations on a slow disk; 30s produced false "did not become
    # ready" failures for a daemon that was seconds from healthy.
    ready_timeout = 60.0
    raw_timeout = os.environ.get("OPENAI4S_DETACHED_READY_TIMEOUT", "")
    if raw_timeout:
        try:
            ready_timeout = max(1.0, float(raw_timeout))
        except ValueError:
            pass
    deadline = time.monotonic() + ready_timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        if _read_pid(cfg) == process.pid and _health_ready(cfg):
            # Re-check both identities after the request.  Another daemon on
            # the same address can answer /health, and a child that loses the
            # bind race can exit while that request is in flight.
            if process.poll() is not None or _read_pid(cfg) != process.pid:
                break
            app_url = _url(cfg)
            print(f"daemon started (pid {process.pid}) at {app_url}")
            print(f"log: {log_path}")
            if not os.environ.get("OPENAI4S_NO_OPEN") and not getattr(
                args, "no_open", False
            ):
                try:
                    import webbrowser

                    webbrowser.open(app_url)
                except Exception:
                    pass
            return 0
        time.sleep(0.25)

    _cleanup_failed_detached_child(process)
    print(
        f"error: detached daemon did not become ready within {ready_timeout:.0f}s "
        f"(OPENAI4S_DETACHED_READY_TIMEOUT overrides); inspect {log_path}",
        file=sys.stderr,
    )
    return 1


def cmd_serve(args) -> int:
    from openai4s.server import build_server, run_server

    cfg = get_config()
    _apply_serve_overrides(args, cfg)
    if getattr(args, "detached", False):
        # The parent never claims the singleton: the detached child re-runs
        # this command and acquires the pidfile under its own pid, which is
        # exactly the identity the readiness wait checks. A quick liveness
        # peek keeps the common "already running" answer immediate.
        existing = _read_pid(cfg)
        if existing and _daemon_alive(cfg, existing):
            print(
                f"daemon already running (pid {existing}) at "
                f"{_url(cfg, endpoint=_live_endpoint(cfg))}"
            )
            return 1
        return _cmd_serve_detached(args, cfg)
    # Atomically claim the singleton, covering the whole boot. A plain
    # read-then-write let a second `serve` racing a slow store open/migration
    # pass a separate liveness check and boot the same data dir concurrently;
    # O_EXCL makes the claim one step so exactly one booter wins. Every
    # build-failure path below clears only the state this process owns.
    if not _acquire_singleton(cfg):
        existing = _read_pid(cfg)
        if existing and _daemon_alive(cfg, existing):
            print(
                f"daemon already running (pid {existing}) at "
                f"{_url(cfg, endpoint=_live_endpoint(cfg))}"
            )
        else:
            print(
                "another `openai4s serve` is starting on this data dir; "
                "retry in a moment",
                file=sys.stderr,
            )
        return 1
    my_pid = os.getpid()
    # Arm the SIGTERM handler before the (possibly slow) build_server: a signal
    # arriving mid-build must run teardown, not the interpreter's default
    # terminate, which would orphan the pidfile just claimed above. A startup
    # that fails puts back the disposition it found, so an in-process caller
    # does not inherit this one from a `serve` that never served.
    previous_sigterm = signal.signal(signal.SIGTERM, _sigterm_to_keyboard_interrupt)
    # Bind before the banner: "listening" printed ahead of the actual bind made
    # a port collision look like a crash after a successful start. Binding also
    # mints the access token, so the URL printed below actually opens.
    try:
        httpd = build_server(cfg)
    except OSError as exc:
        signal.signal(signal.SIGTERM, previous_sigterm)
        _clear_state(cfg, only_if_owned_by=my_pid)
        message = _bind_failure_message(exc, cfg)
        if message is None:
            raise
        print(message, file=sys.stderr)
        return 1
    except BaseException:
        signal.signal(signal.SIGTERM, previous_sigterm)
        _clear_state(cfg, only_if_owned_by=my_pid)
        raise
    print(f"openai4s listening at {_url(cfg)} (model={cfg.llm.model})")
    print("web UI ready. Ctrl-C to stop.")
    if cfg.team_mode:
        # First boot of team mode with no accounts: print the bootstrap
        # command and start normally — never prompt, never block (M1-3).
        try:
            from openai4s.store import get_store

            if get_store(cfg.db_path).team.count_users() == 0:
                print(
                    "team mode is ON but no users exist yet; create the "
                    "first admin with:\n"
                    "  openai4s user add <name> --role admin"
                )
        except Exception:
            pass
    if not os.environ.get("OPENAI4S_NO_OPEN") and not getattr(args, "no_open", False):

        def _open():
            time.sleep(1.0)
            try:
                import webbrowser

                webbrowser.open(_url(cfg))
            except Exception:
                pass

        import threading

        threading.Thread(target=_open, daemon=True).start()
    try:
        # The shared service loop (gateway.run_server): serve_app and this
        # command must not carry two copies of serve/teardown that can drift.
        run_server(httpd)
    finally:
        _clear_state(cfg, only_if_owned_by=my_pid)
    return 0


def _doctor_config():
    """A config for doctor that does not have to create anything to exist.

    ``get_config()`` calls ``ensure_dirs()``, and that is exactly what fails
    when ``OPENAI4S_DATA_DIR`` names a file, or a directory this user cannot
    write to, or one that cannot be created — the startup failure doctor is
    meant to diagnose. Raising here handed back a traceback instead of the
    report and its documented exit code 2, in the one situation the command
    exists for. The plain ``Config`` reads env and defaults and touches
    nothing; ``doctor``'s data check then reports what is wrong with it.
    """
    from openai4s.config import Config

    try:
        return get_config()
    except Exception:  # noqa: BLE001 - the bootstrap failure is the diagnosis
        return Config()


def cmd_doctor(args) -> int:
    """Check whether this installation can actually do the work.

    Deliberately needs no daemon: the situation that motivates running it is
    usually one where the daemon will not start.

    Exit code is the verdict — 0 for ok, 1 for degraded-but-usable, 2 when a
    check failed outright — so a setup script can branch on it rather than
    grepping prose.
    """
    from openai4s import doctor

    result = doctor.report(_doctor_config())
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(doctor.render(result))
    return {doctor.OK: 0, doctor.WARN: 1, doctor.FAIL: 2}[result["status"]]


def cmd_verify_package(args) -> int:
    """Verify an exported session/evidence package in a clean environment."""
    from openai4s.evidence import EvidenceError, verify_package

    try:
        report = verify_package(args.package)
    except EvidenceError as e:
        print(f"cannot verify: {e}")
        return 2
    print(f"package: {report['path']}")
    print(f"  format: {report['format']} (schema {report['schema_version']})")
    print(f"  archive sha256: {report['archive_sha256']}")
    print(f"  files verified: {len(report['files_verified'])}")
    if report["ok"]:
        print("  OK — every listed file matches its recorded hash, and the")
        print("       manifest matches its own digest.")
        print(f"  note: {report['verifies']}")
        return 0
    print(f"  FAILED — {len(report['problems'])} problem(s):")
    for problem in report["problems"]:
        print(f"    - {problem}")
    return 1


def cmd_diagnostics(args) -> int:
    """Write a redacted diagnostic bundle for a bug report."""
    from openai4s.diagnostics import build_bundle

    cfg = get_config()
    target = (
        Path(args.output) if args.output else Path.cwd() / "openai4s-diagnostics.zip"
    )
    result = build_bundle(cfg, target)
    print(f"wrote {result['path']}")
    print(f"  included: {', '.join(result['included']) or 'nothing'}")
    for item in result["excluded"]:
        print(f"  excluded: {item['path']} ({item['reason']})")
    print(
        "\nLog lines and report fields are redacted, but review the file before "
        "sharing it — only you know what your own output contains."
    )
    return 0


def cmd_status(args) -> int:
    cfg = get_config()
    pid = _read_pid(cfg)
    if not pid or not _daemon_alive(cfg, pid):
        print("daemon: not running")
        return 1
    endpoint = _recorded_endpoint(cfg, pid)
    # confirm via /health
    try:
        with _open_daemon(
            _url(cfg, with_token=False, endpoint=endpoint) + "health", timeout=3
        ) as r:
            health = json.loads(r.read().decode("utf-8"))
        print(f"daemon: running (pid {pid}) at {_url(cfg, endpoint=endpoint)}")
        print(f"  model    : {health.get('model')}")
        # The loopback health response is intentionally a minimal public
        # projection.  The CLI already owns the local configuration, so it can
        # report the data directory without publishing an absolute host path
        # over HTTP.
        print(f"  data_dir : {cfg.data_dir}")
        return 0
    except urllib.error.URLError:
        print(f"daemon: pid {pid} alive but /health unreachable")
        return 2


def _wait_pid_exit(
    pid: int, *, attempts: int | None = None, interval: float = 0.1
) -> bool:
    """Poll until ``pid`` is gone; True means it actually exited.

    The default grace period is ``TERM_GRACE_S`` — the same budget
    ``execution/process_group.py`` gives a job's SIGTERM — so daemon stops
    and job stops cannot quietly drift apart.  The pid-shaped ladder itself
    stays local on purpose: the daemon is not this CLI's child (there is no
    ``Popen`` to reap, which the group helpers require) and it is not
    reliably its own process-group leader (under nohup/launchd the pgid may
    be shared with siblings), so ``killpg`` semantics do not transfer.
    Leader-only signalling is safe here: kernel workers exit on their
    manager pipe's EOF when the daemon dies.
    """
    if attempts is None:
        attempts = max(1, round(TERM_GRACE_S / interval))
    for _ in range(attempts):
        if not _pid_alive(pid):
            return True
        time.sleep(interval)
    return not _pid_alive(pid)


def cmd_stop(args) -> int:
    cfg = get_config()
    pid = _read_pid(cfg)
    if not pid or not _daemon_alive(cfg, pid):
        print("daemon: not running")
        _clear_state(cfg)
        return 1
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass  # exited between the aliveness check and the signal
    stopped = _wait_pid_exit(pid)
    if not stopped and getattr(args, "force", False):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stopped = _wait_pid_exit(pid)
    if not stopped:
        # An in-flight cell can hold shutdown past the grace period. The state
        # files must outlive the process they describe: clearing them here left
        # a live daemon on a bound port that `status` and a second `stop` both
        # called "not running", and the next `serve` crashed into.
        hint = (
            "it ignored SIGKILL"
            if getattr(args, "force", False)
            else "retry `openai4s stop`, or `openai4s stop --force` to SIGKILL it"
        )
        print(
            f"error: daemon (pid {pid}) is still shutting down — {hint}",
            file=sys.stderr,
        )
        return 2
    _clear_state(cfg)
    print(f"daemon stopped (pid {pid})")
    return 0


def cmd_url(args) -> int:
    print(_url(cfg := get_config(), endpoint=_live_endpoint(cfg)))
    return 0


def cmd_run(args) -> int:
    from openai4s.agent import Agent
    from openai4s.agent.loop import enable_auto_run_environment, review_cli_result
    from openai4s.kernel.readiness import EnvironmentReadinessError

    auto_applied: dict[str, str] = {}
    if getattr(args, "auto", False):
        # Before get_config(), which reads these at construction.
        auto_applied = enable_auto_run_environment()
    cfg = get_config()
    agent = Agent(cfg=cfg, verbose=args.verbose, task_mode=getattr(args, "mode", None))
    try:
        with _foreground_cell_interrupt(agent):
            result = agent.run(args.task)
    except EnvironmentReadinessError as error:
        # A standard-profile refusal is raised only at the first Code Cell.
        # Keeping this adapter typed lets native tools and structured
        # finalization run with zero kernel while retaining the existing CLI
        # JSON/text failure contract for a scientific execution attempt.
        payload = {
            "error": str(error),
            "code": error.error_code,
            "standard_profile_readiness": error.readiness,
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"error: {payload['error']}", file=sys.stderr)
        return 2
    if getattr(args, "auto", False):
        # A machine-readable terminal is the point of --auto: CI needs to tell
        # "ran and was verified" from "ran and nobody checked".
        review = review_cli_result(args.task, result, cfg=cfg)
        result = dict(result)
        result["auto_mode"] = {
            "preset": "autonomous",
            "enabled_environment": auto_applied,
            **review,
        }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("\n=== stop_reason:", result["stop_reason"], "===")
        if result.get("submitted_output"):
            print(
                "submitted_output:",
                json.dumps(result["submitted_output"], ensure_ascii=False, indent=2),
            )
        if result.get("final_message"):
            print("final:", result["final_message"])
        auto = result.get("auto_mode")
        if auto:
            print(f"=== review: {auto['terminal']} — {auto['user_truth']} ===")
            for item in auto.get("findings") or []:
                print(
                    f"  - {item.get('severity')} {item.get('category')}: "
                    f"{str(item.get('claim_ref'))[:80]}"
                )
    return 0


# --------------------------------------------------------------------------- #
#  init — guided first-run configuration without checkout-local files
# --------------------------------------------------------------------------- #


def _onboarding_service():
    from openai4s.llm import PROVIDERS
    from openai4s.onboarding import OnboardingService
    from openai4s.store import get_store

    cfg = get_config()
    cfg.ensure_dirs()
    store = get_store(cfg.db_path)
    return OnboardingService(cfg, store, PROVIDERS), store


def _prompt_value(label: str, default: str) -> str:
    suffix = f" [{default}]" if default else ""
    return input(f"{label}{suffix}: ").strip() or default


def cmd_init(args) -> int:
    service, store = _onboarding_service()
    try:
        defaults = service.defaults(args.provider)
        interactive = (
            not args.non_interactive and not args.api_key_stdin and sys.stdin.isatty()
        )
        provider = args.provider or defaults["provider"]
        model = args.model
        base_url = args.base_url
        api_key = None

        if interactive:
            known = ", ".join(sorted(service.providers))
            print("OpenAI4S first-run setup")
            print(f"Available providers: {known}")
            provider = _prompt_value("Provider", provider).lower()
            defaults = service.defaults(provider)
            model = model or _prompt_value("Model", defaults["model"])
            base_url = base_url or _prompt_value("Base URL", defaults["base_url"])
            if not args.clear_api_key:
                answer = input("Configure an API key now? [y/N]: ").strip().lower()
                if answer in {"y", "yes"}:
                    api_key = getpass.getpass("API key (input hidden): ")
        elif args.api_key_stdin:
            api_key = sys.stdin.readline().rstrip("\r\n")

        result = service.configure(
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            clear_api_key=args.clear_api_key,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()

    payload = result.as_dict()
    if args.json:
        # OnboardingResult.as_dict() is an explicit secret-free projection:
        # has_api_key is a boolean and the credential value never reaches it,
        # which tests/test_onboarding.py asserts against a live secret. The
        # suppression has to ride the alert's own line -- a CodeQL suppression
        # scope is that one line, so a comment above it suppresses nothing.
        rendered = json.dumps(payload, ensure_ascii=False, indent=2)
        print(rendered)  # codeql[py/clear-text-logging-sensitive-data]
    else:
        print(f"Configured {result.provider} / {result.model}")
        print(f"Settings stored in {result.data_dir}")
        if not result.has_api_key:
            print("No API key stored; add one in Customize → Models after launch.")
        if not result.native_runtime_supported:
            print("Native Windows kernels are unsupported; run OpenAI4S under WSL2.")
        print("Next: openai4s serve")
    return 0


# --------------------------------------------------------------------------- #
#  optional Jupyter adapter — stdlib KernelSpec operations, lazy wire import
# --------------------------------------------------------------------------- #


def cmd_jupyter_describe(args) -> int:
    from openai4s.adapters.jupyter import adapter_status

    status = adapter_status()
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0
    bridge = "available" if status["bridge_available"] else "not installed"
    print(f"Jupyter bridge: {bridge}")
    print("  scope      : standalone (not a Web-session attachment)")
    print("  host RPC   : unavailable")
    print("  protocol   : Jupyter wire adapter -> hardened OpenAI4S JSON-line worker")
    for kernel in status["kernels"]:
        print(f"  kernelspec : {kernel['name']} ({kernel['language']})")
    if not status["bridge_available"]:
        print("  install    : python -m pip install 'ipykernel>=7,<8'")
    return 0


def _print_kernelspec_writes(written: list[dict], action: str) -> None:
    for item in written:
        print(f"{action} {item['name']}: {item['kernel_json']}")


def cmd_jupyter_export(args) -> int:
    from openai4s.adapters.jupyter import write_kernelspecs
    from openai4s.adapters.jupyter.kernelspec import KernelSpecError

    try:
        written = write_kernelspecs(
            args.output,
            languages=args.language,
            replace=args.replace,
        )
    except (KernelSpecError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print_kernelspec_writes(written, "exported")
    return 0


def cmd_jupyter_install(args) -> int:
    from openai4s.adapters.jupyter import install_kernelspecs
    from openai4s.adapters.jupyter.kernelspec import KernelSpecError

    try:
        written = install_kernelspecs(
            prefix=args.prefix,
            languages=args.language,
            replace=args.replace,
        )
    except (KernelSpecError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print_kernelspec_writes(written, "installed")
    return 0


# --------------------------------------------------------------------------- #
#  setup — create the four default conda environments from envs/*.yml
# --------------------------------------------------------------------------- #
# The four default envs, in the order we create them (python first: it's the
# default kernel env). Names must match the `name:` in each envs/<name>.yml.
_DEFAULT_ENVS = ["python", "phylo", "r", "struct"]

# Named setup profiles. The standard profile is the broad, everyday Python/R
# stack used by setup.sh; full preserves the historical four-env setup.
_ENV_PROFILES = {
    "standard": ["python", "r"],
    "full": list(_DEFAULT_ENVS),
}

# Conda-family tools we know how to drive, fastest first.
_CONDA_TOOLS = ["micromamba", "mamba", "conda"]


def _envs_dir() -> Path:
    """The repo's ``envs/`` directory (sibling of the ``openai4s`` package)."""
    return Path(__file__).resolve().parents[2] / "envs"


def _find_conda_tool() -> str | None:
    """First available of micromamba / mamba / conda on PATH, or None."""
    for tool in _CONDA_TOOLS:
        if shutil.which(tool):
            return tool
    return None


def _existing_envs() -> dict[str, Path]:
    """Existing conda envs, mapped name → prefix.

    Prefers the daemon's own discovery (:mod:`openai4s.kernel.environments`,
    which honours ``OPENAI4S_ENV_ROOTS`` and the reference-daemon envs dir);
    falls back to ``conda env list`` parsing if that import isn't available.

    The prefix matters: we decide create-vs-update from *these* roots, so an
    update has to name the very prefix we found. Passing only the spec file
    would make the conda tool re-resolve the yml's ``name:`` inside its own
    root prefix, which is a different namespace — see :func:`_update_cmd`."""
    try:
        from openai4s.kernel.environments import discover_environments

        return {e.name: e.root for e in discover_environments(force=True) if e.is_conda}
    except Exception:  # noqa: BLE001 — fall back to CLI probing
        pass
    tool = _find_conda_tool()
    if not tool:
        return {}
    try:
        out = subprocess.run(
            [tool, "env", "list"], capture_output=True, text=True, timeout=30
        )
    except Exception:  # noqa: BLE001
        return {}
    envs: dict[str, Path] = {}
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # rows look like:  "python   *  /path/to/envs/python"
        fields = line.split()
        path = fields[-1]
        if os.sep not in path:
            continue
        prefix = Path(path)
        envs.setdefault(prefix.name, prefix)
        first = fields[0]
        if first and first != "*":
            envs.setdefault(first, prefix)
    return envs


def _create_cmd(tool: str, name: str, yml: Path) -> list[str]:
    """The env-creation argv for ``tool`` from spec file ``yml``.

    micromamba/mamba/conda all accept ``env create -f <file>``; conda derives
    the env name from the file's ``name:`` field."""
    return [tool, "env", "create", "-f", str(yml)]


def _update_cmd(tool: str, prefix: Path, yml: Path) -> list[str]:
    """Non-destructively update the env at ``prefix`` from ``yml``.

    ``-p`` is not optional. Without it, micromamba/mamba/conda resolve the
    yml's ``name:`` against *their own* root prefix — but we chose "update"
    because :func:`_existing_envs` found the env somewhere else (a second conda
    root, ``OPENAI4S_ENV_ROOTS``, …). conda would then happily build a brand-new
    env under its own root and report success while the env the agent actually
    runs in stays untouched; micromamba would abort with "Prefix does not exist".

    ``--prune`` is deliberately omitted so setup never removes packages the user
    installed after the initial environment creation.
    """
    return [tool, "env", "update", "-p", str(prefix), "-f", str(yml)]


def cmd_setup(args) -> int:
    tool = _find_conda_tool()
    if not tool:
        print("error: no conda/mamba/micromamba found on PATH.", file=sys.stderr)
        print(
            "       install one (e.g. micromamba) and re-run `openai4s setup`.",
            file=sys.stderr,
        )
        return 1

    envs_dir = _envs_dir()
    if not envs_dir.is_dir():
        print(f"error: envs directory not found: {envs_dir}", file=sys.stderr)
        return 1

    if args.only:
        if args.only not in _DEFAULT_ENVS:
            print(
                f"error: unknown env '{args.only}' "
                f"(choices: {', '.join(_DEFAULT_ENVS)})",
                file=sys.stderr,
            )
            return 1
        wanted = [args.only]
    elif getattr(args, "profile", None):
        wanted = list(_ENV_PROFILES[args.profile])
    else:
        wanted = list(_DEFAULT_ENVS)

    existing = _existing_envs()
    update_existing = bool(getattr(args, "update", False))

    print(
        f"using '{tool}' to manage envs from {envs_dir}"
        + (" (dry-run)" if args.dry_run else "")
    )
    created = 0
    updated = 0
    skipped = 0
    failed = 0
    for name in wanted:
        yml = envs_dir / f"{name}.yml"
        if not yml.is_file():
            print(f"  [{name}] skip: spec file missing ({yml})")
            failed += 1
            continue
        prefix = existing.get(name)
        if prefix is not None and not update_existing:
            print(f"  [{name}] already exists — skipping (use --update to sync)")
            skipped += 1
            continue
        cmd = (
            _update_cmd(tool, prefix, yml)
            if prefix is not None
            else _create_cmd(tool, name, yml)
        )
        action = "update" if prefix is not None else "create"
        if args.dry_run:
            print(f"  [{name}] would {action}: {' '.join(cmd)}")
            continue
        print(f"  [{name}] {action}… ({' '.join(cmd)})")
        try:
            rc = subprocess.run(cmd).returncode
        except Exception as exc:  # noqa: BLE001
            print(f"  [{name}] error: {exc}", file=sys.stderr)
            failed += 1
            continue
        if rc == 0:
            print(f"  [{name}] {action}d")
            if prefix is not None:
                updated += 1
            else:
                created += 1
        else:
            print(f"  [{name}] FAILED (exit {rc})", file=sys.stderr)
            failed += 1

    if args.dry_run:
        return 0
    print(
        f"done: {created} created, {updated} updated, "
        f"{skipped} skipped, {failed} failed"
    )
    return 1 if failed else 0


# --------------------------------------------------------------------------
# environments as a transaction: plan / apply / rollback
# --------------------------------------------------------------------------


def _env_store(cfg, runner=None):
    from openai4s.kernel.env_generations import EnvironmentStore

    return EnvironmentStore(Path(cfg.data_dir) / "environments", runner=runner)


def _env_spec(name: str) -> Path:
    return _envs_dir() / f"{name}.yml"


def _env_verify(prefix: Path, *, name: str | None = None) -> tuple[str, list[str]]:
    """Prove the generation runs and satisfies standard before it is current.

    A build that exits 0 having produced nothing usable is the false success
    this step exists to catch, and it is the same rule the compute manager
    applies to a job that exits 0 having written no outputs. The check used to
    stop at "a file exists at that path"; it now *starts the interpreter*, in
    both languages, because a file is not an environment.  The standard Python
    and R generations additionally require every direct package in their
    shipped manifests; a runnable but partial prefix is not ready.
    """
    from openai4s.kernel.env_generations import (
        probe_interpreter,
        verify_standard_environment,
    )

    if name in ("python", "r"):
        return verify_standard_environment(prefix, name)
    return probe_interpreter(prefix)


def cmd_env_plan(args) -> int:
    cfg = get_config()
    tool = _find_conda_tool() or "conda"
    store = _env_store(cfg)
    plans = [
        store.plan(
            name,
            _env_spec(name),
            tool=tool,
            force_replace=bool(getattr(args, "repair", False)),
        )
        for name in args.names
    ]
    if args.json:
        print(json.dumps([p.public() for p in plans], indent=2, sort_keys=True))
    else:
        for plan in plans:
            print(f"  [{plan.name}] {plan.action}: {plan.reason}")
    return 0


def cmd_env_apply(args) -> int:
    from openai4s.kernel.env_generations import EnvironmentError_

    cfg = get_config()
    tool = _find_conda_tool()
    if not tool:
        print("error: no conda/mamba/micromamba found on PATH.", file=sys.stderr)
        return 1
    store = _env_store(cfg)
    failed = 0
    for name in args.names:
        spec = _env_spec(name)
        plan = store.plan(
            name,
            spec,
            tool=tool,
            force_replace=bool(getattr(args, "repair", False)),
        )
        if args.dry_run:
            if plan.changes:
                print(f"  [{name}] would {plan.action}: {plan.reason}")
            else:
                print(f"  [{name}] up to date ({plan.reason})")
            continue
        # A real (non-dry-run) no-op still goes through `store.apply`, not a
        # short-circuit here: its locked validation is what catches the pointer
        # or spec moving between plan and apply, which a bare "up to date" would
        # report success over.

        def build(prefix: Path, staged_spec: Path, _tool=tool):
            # The *staged* spec, never the live one: the manifest records the
            # hash taken under the apply lock, and building from a file that
            # can still be edited would make that hash describe something else.
            return [
                _tool,
                "env",
                "create",
                "--yes",
                "--prefix",
                str(prefix),
                "-f",
                str(staged_spec),
            ]

        try:
            result = store.apply(
                plan,
                spec,
                tool=tool,
                build=build,
                verify=lambda prefix, _name=name: _env_verify(prefix, name=_name),
            )
        except EnvironmentError_ as e:
            failed += 1
            print(f"  [{name}] FAILED: {e}", file=sys.stderr)
            continue
        if result.ok and not plan.changes:
            print(f"  [{name}] up to date ({result.detail or 'no change'})")
        elif result.ok:
            print(f"  [{name}] now generation {result.generation.id}")
        else:
            failed += 1
            print(f"  [{name}] FAILED: {result.detail}", file=sys.stderr)
            print(
                f"  [{name}] the current environment is unchanged "
                f"({result.previous or 'none'})",
                file=sys.stderr,
            )
    return 1 if failed else 0


def cmd_env_list(args) -> int:
    store = _env_store(get_config())
    names = args.names or store.environments()
    payload = {
        name: {
            "current": store.current_id(name),
            "generations": [g.public() for g in store.list(name)],
        }
        for name in names
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for name, info in payload.items():
            print(f"  {name}: current={info['current'] or '-'}")
            for generation in info["generations"]:
                mark = "*" if generation["generation_id"] == info["current"] else " "
                print(
                    f"   {mark} {generation['generation_id']} "
                    f"{generation['state']} "
                    f"({generation['package_count']} packages)"
                )
    return 0


def cmd_env_rollback(args) -> int:
    from openai4s.kernel.env_generations import EnvironmentError_

    store = _env_store(get_config())
    try:
        result = store.rollback(args.name, args.generation)
    except EnvironmentError_ as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"  [{args.name}] now generation {result.generation.id} (pointer moved)")
    return 0


def cmd_env_recover(args) -> int:
    store = _env_store(get_config())
    names = args.names or store.environments()
    report = {name: store.recover(name) for name in names}
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    for name, info in report.items():
        print(f"  {name}: current={info['current'] or '-'}")
        for item in info["abandoned"]:
            print(f"    abandoned {item['state']}: {item['path']}")
        if info.get("apply_in_progress"):
            print("    an apply is currently in progress (holding the lock)")
    return 0


def cmd_benchmark(args) -> int:
    """Run the versioned workflow benchmark against the real subsystems."""
    from openai4s.benchmark import load_workflows, run_acceptance_pack, run_all

    if getattr(args, "acceptance", False):
        report = run_acceptance_pack()
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            for item in report["field_paths"]:
                if not item["pass"]:
                    mark = "FAIL"
                elif item["claim"] == "capability":
                    mark = "CAPABILITY"
                else:
                    mark = "BASELINE"
                status = item["observed"].get("status", "unknown")
                print(f"  [{mark:10}] {item['id']} — {status}")
            for item in report["safety_actions"]:
                mark = "ok" if item["pass"] else "FAIL"
                decision = item["observed"].get("effective_decision", "unknown")
                print(f"  [{mark:10}] safety:{item['id']} — {decision}")
            summary = report["summary"]
            print(
                "\n"
                f"{summary['capability_passes']} current capability path(s), "
                f"{summary['baseline_observations_reproduced']} baseline gap/behavior "
                "observation(s) reproduced, "
                f"{summary['field_path_failures']} field failure(s), "
                f"{summary['safety_action_failures']} safety failure(s)"
            )
            print(
                "A BASELINE match reproduces current behavior; it does not claim "
                "that an incomplete capability works."
            )
        return 0 if report["pass"] else 1

    if args.list:
        for workflow in load_workflows():
            print(f"  {workflow.id} v{workflow.version} — {workflow.title}")
            for case in workflow.cases:
                print(f"    {case.id} [{case.outcome}] {case.title}")
        return 0
    report = run_all()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for item in report["results"]:
            mark = "skip" if item["skipped"] else ("ok  " if item["passed"] else "FAIL")
            line = f"  [{mark}] {item['case_id']} ({item['expected_outcome']})"
            if item["detail"]:
                line += f" — {item['detail']}"
            print(line)
        print(
            f"\n{report['passed']} passed, {report['failed']} failed, "
            f"{report['skipped']} skipped "
            f"across {report['workflows']} workflow(s)"
        )
    # Zero workflows is a failure, not a pass. An installed wheel that did not
    # ship the manifests would otherwise report "0 failed" and exit 0 — a
    # silent green on the suite that is supposed to decide whether a release is
    # good. There is always at least one workflow in a correct install.
    if report["workflows"] == 0:
        print(
            "error: no benchmark workflows were found; the manifests are "
            "missing from this installation",
            file=sys.stderr,
        )
        return 1
    return 1 if report["failed"] else 0


def _daemon_token(cfg) -> str | None:
    """The credential this CLI presents, or None when there is none to find.

    Two sources, in this order. `OPENAI4S_TOKEN` exists because the token file
    is owner-only: a daemon running under another account (a systemd unit, say)
    writes a file this user cannot read, and without an override the CLI would
    be unusable without changing permissions or switching user.

    Never a query parameter. A URL carrying a credential is logged by proxies
    and kept in history, and the daemon refuses query tokens on mutations for
    that reason.
    """
    override = (os.environ.get("OPENAI4S_TOKEN") or "").strip()
    if override:
        return override
    from openai4s.server import local_auth

    return local_auth.read_token(cfg.data_dir)


def _daemon_credential_hint(cfg) -> str:
    """Why the CLI has no token, phrased so the reader can act on it."""
    from openai4s.server import local_auth

    path = local_auth.token_path(cfg.data_dir)
    if path.exists():
        return (
            f"error: cannot read the daemon's access token at {path} "
            "(it is owner-only). Run this as the user the daemon runs as, or "
            "set OPENAI4S_TOKEN to the token that daemon printed at startup."
        )
    return (
        f"error: no daemon access token at {path}. Start the daemon with "
        "`openai4s serve`, or set OPENAI4S_TOKEN if it runs elsewhere."
    )


def _daemon_request(cfg, method: str, path: str, body: dict | None = None):
    """Call the running daemon's REST API; returns (status, parsed_json).

    `path` is relative to the API root -- "/shares", not "/api/shares". Every
    `openai4s share` subcommand passed the latter, and the daemon serves the
    API only under `/api/v1`, so all nine requests answered with the daemon's
    own "the API is versioned" 404 -- nine and not eight because `share create
    latest` resolves the session with a `GET /frames` of its own before it
    posts. The whole feature had never reached a route, including the
    `openai4s share import <url>` line the generated share page tells a
    recipient to run.

    The version is joined from `contract.API_ROOT`, the constant the gateway
    routes on, so the two cannot drift.
    """
    from openai4s.server import contract

    if path.startswith("/api/"):
        # A caller supplying its own prefix is the bug this signature exists to
        # prevent, and papering over it would be wrong: a merely-wrong path
        # produces a 404 that nobody reads as a defect.
        raise ValueError(
            f"path must be relative to the API root, not {path!r} "
            f"(it is joined with {contract.API_ROOT})"
        )
    base = _url(cfg, with_token=False, endpoint=_live_endpoint(cfg))
    url = base.rstrip("/") + contract.API_ROOT + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    # The gate is required by default now, so every daemon-backed subcommand
    # has to present a credential. Sent as a header rather than `?token=`,
    # which the daemon refuses on mutations anyway.
    from openai4s.server import local_auth

    token = _daemon_token(cfg)
    if token:
        req.add_header(local_auth.TOKEN_HEADER, token)
    # The daemon's CSRF guard passes non-browser clients (no Origin header).
    try:
        with _open_daemon(req, timeout=300) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", "replace")
        if error.code == 401 and not token:
            # A bare 401 tells the reader nothing they can act on. Say which
            # file could not be read and what to do instead.
            print(_daemon_credential_hint(cfg), file=sys.stderr)
        try:
            return error.code, json.loads(raw)
        except ValueError:
            return error.code, {"error": raw}


def _require_daemon(cfg) -> bool:
    pid = _read_pid(cfg)
    if not pid or not _daemon_alive(cfg, pid):
        print(
            "error: daemon is not running — start it with `openai4s serve`",
            file=sys.stderr,
        )
        return False
    return True


def _parse_duration(text: str) -> int:
    """Parse '30m' / '24h' / '7d' / '3600' into seconds. Raises SystemExit on error."""

    text = str(text).strip().lower()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    try:
        if text and text[-1] in units:
            return int(float(text[:-1]) * units[text[-1]])
        return int(text)
    except (ValueError, IndexError):
        print(
            f"error: invalid duration {text!r} (use e.g. 30m, 24h, 7d)", file=sys.stderr
        )
        raise SystemExit(2) from None


def cmd_share(args) -> int:
    cfg = get_config()
    action = args.share_action
    if action in (
        "create",
        "update",
        "list",
        "revoke",
        "enable",
        "disable",
        "status",
        "import",
    ):
        if not _require_daemon(cfg):
            return 1
    try:
        if action == "create":
            root = args.session
            if root == "latest":
                _, frames = _daemon_request(cfg, "GET", "/frames")
                items = frames.get("frames") if isinstance(frames, dict) else frames
                if not items:
                    print("error: no sessions found", file=sys.stderr)
                    return 2
                root = items[0].get("frame_id") or items[0].get("id")
            body: dict = {}
            if args.title:
                body["title"] = args.title
            if args.expires:
                body["expires_in"] = _parse_duration(args.expires)
            status, rec = _daemon_request(cfg, "POST", f"/frames/{root}/shares", body)
        elif action == "update":
            ubody: dict = {}
            if getattr(args, "no_expiry", False):
                ubody["expires_in"] = 0
            elif args.expires:
                ubody["expires_in"] = _parse_duration(args.expires)
            status, rec = _daemon_request(
                cfg, "PUT", f"/shares/{args.share_id}", ubody or None
            )
        elif action == "list":
            status, rec = _daemon_request(cfg, "GET", "/shares")
        elif action == "revoke":
            status, rec = _daemon_request(cfg, "DELETE", f"/shares/{args.share_id}")
        elif action == "enable":
            status, rec = _daemon_request(
                cfg, "PUT", "/share/settings", {"enabled": True}
            )
        elif action == "disable":
            status, rec = _daemon_request(
                cfg, "PUT", "/share/settings", {"enabled": False}
            )
        elif action == "status":
            status, rec = _daemon_request(cfg, "GET", "/share/status")
        elif action == "import":
            status, rec = _daemon_request(
                cfg, "POST", "/sessions/import-url", {"url": args.url}
            )
        else:  # pragma: no cover
            print("error: unknown share action", file=sys.stderr)
            return 2
    except urllib.error.URLError as error:
        print(f"error: could not reach daemon: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(rec, ensure_ascii=False, indent=2))
    elif status >= 400:
        print(f"error: {rec.get('error') or rec}", file=sys.stderr)
    elif action == "create" or action == "update":
        print(rec.get("url") or json.dumps(rec))
    elif action == "list":
        for item in rec.get("shares", []):
            print(f"{item['share_id']}\t{item['status']}\t{item.get('url', '')}")
    elif action == "import":
        rid = rec.get("root_frame_id")
        print(
            f"imported session {rid} (view-only). Open the web UI and use "
            "“Restart fresh” to continue."
        )
    else:
        print(json.dumps(rec, ensure_ascii=False))
    return 0 if status < 400 else 2


def cmd_relay_serve(args) -> int:
    from openai4s.share.relay import RelayConfig, serve_relay

    base_domain = args.base_domain or os.environ.get("OPENAI4S_RELAY_BASE_DOMAIN", "")
    if not base_domain:
        print(
            "error: --base-domain (or OPENAI4S_RELAY_BASE_DOMAIN) is required",
            file=sys.stderr,
        )
        return 1
    listen = args.listen or os.environ.get("OPENAI4S_RELAY_LISTEN", "127.0.0.1:8770")
    host, _, port_s = listen.rpartition(":")
    host = host or "127.0.0.1"
    try:
        port = int(port_s)
    except ValueError:
        print(f"error: invalid --listen {listen!r}", file=sys.stderr)
        return 1
    tokens_file = args.tokens_file or os.environ.get("OPENAI4S_RELAY_TOKENS_FILE")
    single = os.environ.get("OPENAI4S_RELAY_AUTH_TOKEN")
    tokens = {"env": single} if single else None
    if not tokens_file and not tokens:
        print(
            "error: provide --tokens-file or OPENAI4S_RELAY_AUTH_TOKEN", file=sys.stderr
        )
        return 1
    trust_proxy = args.trust_proxy or os.environ.get(
        "OPENAI4S_RELAY_TRUST_PROXY", ""
    ) in ("1", "true", "yes")
    config = RelayConfig(
        base_domain=base_domain,
        tunnel_host=args.tunnel_host,
        tokens=tokens,
        tokens_file=tokens_file,
        trust_proxy=trust_proxy,
    )
    print(f"openai4s relay listening on {host}:{port} for *.{base_domain}")
    print("front this with TLS (Caddy/nginx) — see docs/webshare.md")
    try:
        serve_relay(host=host, port=port, config=config, block=True)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_relay_gen_token(args) -> int:
    import secrets as _secrets

    print(f"openai4s_pub_{_secrets.token_urlsafe(32)}")
    return 0


def cmd_cluster(args) -> int:
    """`openai4s cluster …` — batch jobs, through the daemon.

    Through the daemon rather than the store directly, unlike `user`: a
    submission has to reach the reconciler that will act on it, and a second
    process writing workload rows behind the daemon's back is how two
    reconcilers end up disagreeing about one job.
    """
    cfg = get_config()
    action = args.cluster_action
    if not _require_daemon(cfg):
        return 1
    try:
        if action == "submit":
            body = {
                "command": list(args.command),
                "profile": args.profile,
            }
            if args.backend:
                body["backend"] = args.backend
            if args.workdir:
                body["workdir"] = args.workdir
            status, rec = _daemon_request(cfg, "POST", "/orchestration/jobs", body)
        elif action == "list":
            status, rec = _daemon_request(cfg, "GET", "/orchestration/jobs")
        elif action == "cancel":
            status, rec = _daemon_request(
                cfg, "POST", f"/orchestration/jobs/{args.job_id}/cancel", {}
            )
        elif action == "logs":
            status, rec = _daemon_request(
                cfg, "GET", f"/orchestration/jobs/{args.job_id}/logs"
            )
        elif action == "profiles":
            status, rec = _daemon_request(cfg, "GET", "/orchestration/profiles")
        else:  # pragma: no cover - argparse enforces choices
            return 2
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(rec, indent=2))
    elif action == "list":
        jobs = (rec or {}).get("jobs") or []
        for job in jobs:
            print(
                f"{job['id']}  {job['phase']:<10} {job['profile']:<16} "
                f"{' '.join(job['command'])[:48]}"
            )
        if not jobs:
            print("no jobs")
    elif action == "submit":
        print(f"submitted {rec.get('id')} ({rec.get('phase')})")
    elif action == "logs":
        if rec.get("stdout"):
            print(rec["stdout"], end="")
        if rec.get("stderr"):
            print(rec["stderr"], end="", file=sys.stderr)
    elif action == "profiles":
        for profile in (rec or {}).get("profiles") or []:
            print(
                f"{profile['name']:<20} cpus={profile['cpus']} "
                f"gpus={profile['gpus']} walltime={profile['walltime_s']}s"
            )
        if not (rec or {}).get("configured"):
            print("(no cluster.toml configured; local backend only)")
    else:
        print(json.dumps(rec))
    return 0 if 200 <= int(status) < 300 else 2


def _team_store():
    """Direct-store access for offline account management on the server
    (the same template as _onboarding_service: no daemon required)."""
    from openai4s.store import get_store

    cfg = get_config()
    cfg.ensure_dirs()
    return get_store(cfg.db_path)


def _read_new_password(args) -> tuple[str, str | None]:
    """(password, generated) per M1-3: --password-stdin reads one line from
    stdin; otherwise a random password is generated and returned for a
    single print. The password never appears in argv or logs."""
    import secrets as _secrets

    if getattr(args, "password_stdin", False):
        # `\r\n` as well as `\n`: a CRLF pipe (a Windows-authored secrets
        # file, a CI runner, `printf 'pw\r\n'`) otherwise stores the hash of
        # `pw\r`, and the account is then permanently unloginnable with the
        # password that was supplied -- behind a login error that is
        # deliberately indistinguishable from a wrong one, so nothing points
        # at the cause. Docker's own `--password-stdin` trims `\r` for the
        # same reason.
        pw = sys.stdin.readline().rstrip("\r\n")
        if not pw:
            raise ValueError("empty password on stdin")
        return pw, None
    generated = _secrets.token_urlsafe(12)
    return generated, generated


def cmd_user(args) -> int:
    action = args.user_action
    store = _team_store()
    try:
        if action == "add":
            try:
                password, generated = _read_new_password(args)
            except ValueError as e:
                print(f"error: {e}", file=sys.stderr)
                return 2
            user = store.team.create_user(
                username=args.username,
                password=password,
                role=args.role,
                display_name=args.display_name,
            )
            store.team.audit(
                actor="cli", action="user_add", user_id=user["id"], target=args.username
            )
            print(f"created {user['role']} {user['username']} ({user['id']})")
            if generated is not None:
                # printed exactly once, never stored or logged
                print(f"initial password: {generated}")
        elif action == "list":
            users = store.team.list_users()
            if getattr(args, "json", False):
                print(json.dumps(users, indent=2))
            else:
                for u in users:
                    flag = " [disabled]" if u["disabled"] else ""
                    print(f"{u['username']:<20} {u['role']:<7} {u['id']}{flag}")
                if not users:
                    print(
                        "no users; create one with: openai4s user add <name> --role admin"
                    )
        elif action == "disable":
            user = store.team.get_user_by_username(args.username)
            if user is None:
                print(f"error: no such user {args.username!r}", file=sys.stderr)
                return 2
            store.team.set_disabled(user["id"], True)
            # What the HTTP route does, on the path an operator uses when the
            # daemon is down. Leaving the row behind made the credential
            # unreachable *and* unrevocable: nothing in the product would
            # ever name that keychain slot again, while a turn in one of this
            # user's sessions still resolves it by owner and bills their
            # personal provider key.
            cleared = store.user_keys.delete_all_for_user(
                user["id"], secrets=store.secrets
            )
            store.team.audit(
                actor="cli",
                action="user_disable",
                user_id=user["id"],
                target=args.username,
            )
            keys = f", {cleared} LLM key(s) cleared" if cleared else ""
            print(f"disabled {args.username} (live sessions revoked{keys})")
        elif action == "reset-password":
            user = store.team.get_user_by_username(args.username)
            if user is None:
                print(f"error: no such user {args.username!r}", file=sys.stderr)
                return 2
            try:
                password, generated = _read_new_password(args)
            except ValueError as e:
                print(f"error: {e}", file=sys.stderr)
                return 2
            store.team.set_password(user["id"], password)
            store.team.audit(
                actor="cli",
                action="user_reset_password",
                user_id=user["id"],
                target=args.username,
            )
            print(f"password reset for {args.username} (live sessions revoked)")
            if generated is not None:
                print(f"new password: {generated}")
        else:  # pragma: no cover - argparse enforces choices
            return 2
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="openai4s", description="openai4s CLI")
    p.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("serve", help="start the daemon")
    ps.add_argument("--host", help="listen host (default: OPENAI4S_HOST or 127.0.0.1)")
    ps.add_argument(
        "--port",
        type=_tcp_port,
        help="listen port (default: OPENAI4S_PORT or 8760)",
    )
    ps.add_argument(
        "--no-open",
        "--no-browser",
        dest="no_open",
        action="store_true",
        help="don't open a browser",
    )
    ps.add_argument(
        "--detached",
        action="store_true",
        help="run in the background (Linux/macOS, including WSL2)",
    )
    ps.set_defaults(fn=cmd_serve)
    sub.add_parser("status", help="check daemon status").set_defaults(fn=cmd_status)
    pdoc = sub.add_parser(
        "doctor",
        help="check model, runtime, isolation, disk, connectors and remote "
        "compute (no daemon needed)",
    )
    pdoc.add_argument("--json", action="store_true", help="machine-readable report")
    pdoc.set_defaults(fn=cmd_doctor)
    pv = sub.add_parser(
        "verify-package",
        help="verify an exported session/evidence package (no daemon needed)",
    )
    pv.add_argument("package", help="path to the .openai4s-session.zip")
    pv.set_defaults(fn=cmd_verify_package)
    pd = sub.add_parser(
        "diagnostics", help="write a redacted diagnostic bundle for a bug report"
    )
    pd.add_argument(
        "-o", "--output", help="destination zip (default ./openai4s-diagnostics.zip)"
    )
    pd.set_defaults(fn=cmd_diagnostics)
    pstop = sub.add_parser("stop", help="stop the daemon")
    pstop.add_argument(
        "--force",
        action="store_true",
        help="escalate to SIGKILL if the daemon does not exit in time",
    )
    pstop.set_defaults(fn=cmd_stop)
    sub.add_parser("url", help="print the web UI url").set_defaults(fn=cmd_url)

    pr = sub.add_parser("run", help="run one Code-as-Action task in-process")
    pr.add_argument("task", help="the task description")
    pr.add_argument("--json", action="store_true", help="emit full JSON result")
    pr.add_argument("-v", "--verbose", action="store_true", help="stream turns")
    from openai4s.agent.task_modes import TaskMode

    pr.add_argument(
        "--mode",
        choices=[mode.value for mode in TaskMode],
        default=None,
        help=(
            "task mode. Omitted, it is detected conservatively from the task "
            "text (guidance only — detection never gates completion) and "
            "defaults to analysis_run. Selecting reusable_pipeline or "
            "codebase_change explicitly requires the run to save source "
            "files, keep a thin entry point, and back its completion with "
            "verified source/entry-point/test evidence"
        ),
    )
    pr.add_argument(
        "--auto",
        action="store_true",
        help=(
            "autonomous Auto Mode: boundary actions go to the Guardian instead "
            "of failing closed, and the result is reviewed before the run "
            "reports a terminal. NOT full access -- the Guardian's active "
            "surface is a read-only allowlist bound to a verified action digest"
        ),
    )
    pr.set_defaults(fn=cmd_run)

    pi = sub.add_parser("init", help="guided first-run model configuration")
    pi.add_argument("--provider", help="provider id (default: current provider)")
    pi.add_argument("--model", help="model id (default: provider default)")
    pi.add_argument("--base-url", help="provider API base URL")
    pi.add_argument(
        "--api-key-stdin",
        action="store_true",
        help="read one API-key line from stdin (never from command arguments)",
    )
    pi.add_argument(
        "--clear-api-key",
        action="store_true",
        help="remove the stored API key for the selected profile",
    )
    pi.add_argument(
        "--non-interactive",
        action="store_true",
        help="accept supplied options and provider defaults without prompting",
    )
    pi.add_argument("--json", action="store_true", help="emit secret-free JSON")
    pi.set_defaults(fn=cmd_init)

    pu = sub.add_parser("setup", help="create or update conda envs from envs/*.yml")
    setup_selection = pu.add_mutually_exclusive_group()
    setup_selection.add_argument(
        "--only",
        metavar="NAME",
        choices=_DEFAULT_ENVS,
        help="create just one env (%(choices)s)",
    )
    setup_selection.add_argument(
        "--profile",
        choices=tuple(_ENV_PROFILES),
        help="environment profile: standard=python+r, full=all four",
    )
    pu.add_argument(
        "--dry-run",
        action="store_true",
        help="print the commands that would run, without executing",
    )
    pu.add_argument(
        "--update",
        action="store_true",
        help="update existing envs without pruning user-installed packages",
    )
    pu.set_defaults(fn=cmd_setup)

    pb = sub.add_parser(
        "benchmark",
        help="run the versioned workflow benchmark against the real subsystems",
    )
    pb.add_argument("--json", action="store_true", help="machine-readable report")
    benchmark_mode = pb.add_mutually_exclusive_group()
    benchmark_mode.add_argument(
        "--list", action="store_true", help="list workflows and cases"
    )
    benchmark_mode.add_argument(
        "--acceptance",
        action="store_true",
        help="replay the Stage 0 field-and-safety baseline pack",
    )
    pb.set_defaults(fn=cmd_benchmark)

    pe = sub.add_parser(
        "env",
        help="environments as a transaction: plan, apply, roll back",
    )
    esub = pe.add_subparsers(dest="env_action", required=True)
    ep = esub.add_parser("plan", help="what would change; touches nothing")
    ep.add_argument("names", nargs="*", default=list(_DEFAULT_ENVS))
    ep.add_argument(
        "--repair",
        action="store_true",
        help="plan a fresh verified generation even when the spec is unchanged",
    )
    ep.add_argument("--json", action="store_true")
    ep.set_defaults(fn=cmd_env_plan)
    ea = esub.add_parser(
        "apply", help="build a new generation and switch to it if it verifies"
    )
    ea.add_argument("names", nargs="*", default=list(_DEFAULT_ENVS))
    ea.add_argument(
        "--repair",
        action="store_true",
        help="build a fresh verified generation even when the spec is unchanged",
    )
    ea.add_argument("--dry-run", action="store_true")
    ea.set_defaults(fn=cmd_env_apply)
    el = esub.add_parser("list", help="generations, and which one is current")
    el.add_argument("names", nargs="*")
    el.add_argument("--json", action="store_true")
    el.set_defaults(fn=cmd_env_list)
    er = esub.add_parser("rollback", help="point at a generation already on disk")
    er.add_argument("name")
    er.add_argument("generation")
    er.set_defaults(fn=cmd_env_rollback)
    ev = esub.add_parser("recover", help="what a restart should know")
    ev.add_argument("names", nargs="*")
    ev.add_argument("--json", action="store_true")
    ev.set_defaults(fn=cmd_env_recover)

    pj = sub.add_parser(
        "jupyter",
        help="describe/export/install the optional Jupyter adapter",
    )
    jsub = pj.add_subparsers(dest="jupyter_action", required=True)
    jd = jsub.add_parser("describe", help="show adapter capabilities and limits")
    jd.add_argument("--json", action="store_true", help="emit JSON")
    jd.set_defaults(fn=cmd_jupyter_describe)
    je = jsub.add_parser("export", help="export standard KernelSpec directories")
    je.add_argument("output", type=Path, help="destination kernels directory")
    je.add_argument(
        "--language",
        choices=("all", "python", "r"),
        default="all",
    )
    je.add_argument(
        "--replace",
        action="store_true",
        help="replace kernel.json in an existing spec directory",
    )
    je.set_defaults(fn=cmd_jupyter_export)
    ji = jsub.add_parser("install", help="install KernelSpecs for Jupyter clients")
    ji.add_argument(
        "--prefix",
        type=Path,
        help="install below PREFIX/share/jupyter/kernels (default: user data dir)",
    )
    ji.add_argument(
        "--language",
        choices=("all", "python", "r"),
        default="all",
    )
    ji.add_argument(
        "--replace",
        action="store_true",
        help="replace kernel.json in an existing spec directory",
    )
    ji.set_defaults(fn=cmd_jupyter_install)

    psh = sub.add_parser("share", help="publish / manage read-only session shares")
    ssub = psh.add_subparsers(dest="share_action", required=True)

    def _share_sub(name: str, help_text: str):
        sp = ssub.add_parser(name, help=help_text)
        sp.add_argument("--json", action="store_true", help="emit JSON")
        sp.set_defaults(fn=cmd_share)
        return sp

    sc = _share_sub("create", "publish a session as a share")
    sc.add_argument("session", help="root frame id, or 'latest'")
    sc.add_argument("--title", help="optional share title")
    sc.add_argument("--expires", help="auto-revoke after this long, e.g. 30m/24h/7d")
    su = _share_sub("update", "refresh a share snapshot")
    su.add_argument("share_id")
    su.add_argument("--expires", help="reset the expiry, e.g. 30m/24h/7d")
    su.add_argument("--no-expiry", action="store_true", help="clear any expiry")
    _share_sub("list", "list shares")
    _share_sub("revoke", "revoke a share").add_argument("share_id")
    _share_sub("enable", "enable sharing")
    _share_sub("disable", "disable sharing (keeps shares offline)")
    _share_sub("status", "show tunnel status")
    _share_sub("import", "import a shared session by URL").add_argument("url")

    pcl = sub.add_parser("cluster", help="submit and manage batch jobs")
    clsub = pcl.add_subparsers(dest="cluster_action", required=True)
    cls = clsub.add_parser("submit", help="submit a batch job")
    cls.add_argument(
        "command",
        nargs="+",
        help="the command to run, as separate arguments (never one string: "
        "splitting a command line is where quoting bugs become injection)",
    )
    cls.add_argument("--profile", default="cpu-interactive")
    cls.add_argument("--backend", help="local | cluster (default: local)")
    cls.add_argument("--workdir")
    cls.add_argument("--json", action="store_true")
    cls.set_defaults(fn=cmd_cluster)
    cll = clsub.add_parser("list", help="list batch jobs")
    cll.add_argument("--json", action="store_true")
    cll.set_defaults(fn=cmd_cluster)
    clc = clsub.add_parser("cancel", help="ask for a job to be cancelled")
    clc.add_argument("job_id")
    clc.add_argument("--json", action="store_true")
    clc.set_defaults(fn=cmd_cluster)
    clg = clsub.add_parser("logs", help="tail a job's output")
    clg.add_argument("job_id")
    clg.add_argument("--json", action="store_true")
    clg.set_defaults(fn=cmd_cluster)
    clp = clsub.add_parser("profiles", help="show the configured cluster profiles")
    clp.add_argument("--json", action="store_true")
    clp.set_defaults(fn=cmd_cluster)

    puser = sub.add_parser(
        "user",
        help="manage team-mode accounts (direct database access, no daemon needed)",
    )
    usub = puser.add_subparsers(dest="user_action", required=True)
    ua = usub.add_parser("add", help="create an account")
    ua.add_argument("username")
    ua.add_argument("--role", choices=("admin", "member", "guest"), default="member")
    ua.add_argument("--display-name", dest="display_name")
    ua.add_argument(
        "--password-stdin",
        dest="password_stdin",
        action="store_true",
        help="read the password from stdin; otherwise one is generated and "
        "printed once (a password never goes on the command line)",
    )
    ua.set_defaults(fn=cmd_user)
    ul = usub.add_parser("list", help="list accounts")
    ul.add_argument("--json", action="store_true")
    ul.set_defaults(fn=cmd_user)
    ud = usub.add_parser(
        "disable", help="disable an account and revoke its live sessions"
    )
    ud.add_argument("username")
    ud.set_defaults(fn=cmd_user)
    ur = usub.add_parser(
        "reset-password", help="set a new password and revoke live sessions"
    )
    ur.add_argument("username")
    ur.add_argument(
        "--password-stdin",
        dest="password_stdin",
        action="store_true",
        help="read the new password from stdin; otherwise one is generated "
        "and printed once",
    )
    ur.set_defaults(fn=cmd_user)

    prelay = sub.add_parser("relay", help="run the public share relay (on a VPS)")
    rsub = prelay.add_subparsers(dest="relay_action", required=True)
    rs = rsub.add_parser("serve", help="serve the relay (front with TLS)")
    rs.add_argument("--listen", help="host:port (default 127.0.0.1:8770)")
    rs.add_argument("--base-domain", help="wildcard base domain, e.g. openai4s.org")
    rs.add_argument("--tunnel-host", help="host for the /tunnel endpoint (optional)")
    rs.add_argument("--tokens-file", help="publisher tokens file (one per line)")
    rs.add_argument(
        "--trust-proxy",
        action="store_true",
        help="read X-Forwarded-For only when the direct peer is loopback",
    )
    rs.set_defaults(fn=cmd_relay_serve)
    rg = rsub.add_parser("gen-token", help="print a fresh publisher token")
    rg.set_defaults(fn=cmd_relay_gen_token)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
