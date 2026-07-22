"""Every module that can reach the network is named here, on purpose.

Written as a prerequisite for telemetry -- the one feature that deliberately
breaks the loopback-only default -- but it is not about telemetry. Before this,
nothing in the repository recorded which modules can open an outbound
connection. "Off by default, and not a single packet leaves the machine" is a
claim about the whole tree, and it cannot be checked one file at a time.

So the surface is frozen: seven modules today, each with a stated reason. A new
one fails this test with its file and line, and the fix is to add it here with a
justification a reviewer can weigh -- which is the point. Adding a line to this
table is a decision; adding `urlopen` to a random module is a Tuesday.

Scope, stated plainly. This finds *outbound* primitives. It deliberately does
not flag `http.server` / `socketserver`, which listen: the daemon, the relay and
the recovery listener bind sockets and that is a different risk with a different
answer (bind address, documented in docs/security.md). It also cannot see
egress from a subprocess -- a kernel cell running `requests`, an `ssh` invoked
by the compute manager. Those are outside any in-process guard by construction,
which is why the sandbox and the kernel's own allowlisting exist.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PACKAGE = Path(__file__).resolve().parent.parent / "openai4s"

#: Names that reach the network. Imported from the stdlib modules below, or used
#: as attributes on them.
_EGRESS_NAMES = frozenset(
    {
        "urlopen",
        "urlretrieve",
        "build_opener",
        "install_opener",
        "Request",
        "HTTPConnection",
        "HTTPSConnection",
        "create_connection",
        "getaddrinfo",
        "gethostbyname",
        "gethostbyname_ex",
    }
)

_EGRESS_MODULES = frozenset(
    {"urllib.request", "http.client", "socket", "ftplib", "smtplib", "telnetlib"}
)

#: The frozen surface: module -> why it is allowed to reach the network.
#:
#: Note what is NOT here. `openai4s/share/relay.py` and `server/daemon.py` bind
#: listening sockets and are out of scope (see the module docstring), and
#: `server/gateway.py` mentions `import requests` only inside a prompt string
#: shown to the model.
_DECLARED: dict[str, str] = {
    "openai4s/webtools.py": (
        "the agent's web fetch. Follows redirects manually so the SSRF guard "
        "applies to every hop, and consults the egress allowlist per hop."
    ),
    "openai4s/llm/transport.py": (
        "the LLM client. The whole product is a call to a model provider."
    ),
    "openai4s/host/endpoints.py": (
        "user-registered model endpoints, reached on the user's instruction."
    ),
    "openai4s/server/model_discovery.py": (
        "probes for a local model server (Ollama, LM Studio and friends). "
        "Loopback in practice, but it is a real outbound call and is declared "
        "as one."
    ),
    "openai4s/cli/main.py": (
        "`openai4s share` and the update check, both explicit user actions."
    ),
    "openai4s/share/fetch.py": (
        "downloads a shared session bundle, with its own SSRF hardening."
    ),
    "openai4s/share/ws_client.py": (
        "the outbound tunnel a share opens to the relay. Off unless sharing "
        "is configured."
    ),
}


def _egress_sites() -> dict[str, list[tuple[int, str]]]:
    """Every reference to an outbound primitive, by module."""
    sites: dict[str, list[tuple[int, str]]] = {}
    for path in sorted(_PACKAGE.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text("utf-8"))
        except (OSError, SyntaxError):  # pragma: no cover - unreadable source
            continue
        rel = path.relative_to(_PACKAGE.parent).as_posix()
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.ImportFrom) and node.module in _EGRESS_MODULES:
                for alias in node.names:
                    if alias.name in _EGRESS_NAMES:
                        sites.setdefault(rel, []).append((node.lineno, alias.name))
                continue
            if isinstance(node, ast.Attribute) and node.attr in _EGRESS_NAMES:
                name = node.attr
            elif isinstance(node, ast.Name) and node.id in {
                "urlopen",
                "create_connection",
                "build_opener",
            }:
                name = node.id
            if name:
                sites.setdefault(rel, []).append((node.lineno, name))
    return sites


def test_no_undeclared_module_can_reach_the_network():
    """The gate. A new outbound call fails here with its file and line."""
    sites = _egress_sites()
    undeclared = sorted(set(sites) - set(_DECLARED))
    if undeclared:
        detail = "\n".join(
            f"  {mod}:{sites[mod][0][0]} uses {sites[mod][0][1]}" for mod in undeclared
        )
        pytest.fail(
            "these modules reach the network but are not in the declared egress "
            f"surface:\n{detail}\n\n"
            "If the call is intended, add the module to _DECLARED with a reason "
            "a reviewer can weigh. That is the point of this test: adding a line "
            "here is a decision, adding urlopen to a module is a Tuesday."
        )


def test_the_declared_surface_has_no_stale_entries():
    """A module listed here that no longer reaches out is a licence nobody
    revoked. The list is only meaningful while it is exact."""
    sites = _egress_sites()
    stale = sorted(set(_DECLARED) - set(sites))
    assert stale == [], f"declared but no longer outbound: {stale}"


def test_every_declaration_states_a_reason():
    for module, reason in _DECLARED.items():
        assert len(reason) > 30, f"{module} needs a reason, not a placeholder"


def test_the_surface_is_small_enough_to_review():
    """Seven modules is reviewable. If this fails, the question is not how to
    raise the bound -- it is why the surface grew."""
    assert len(_DECLARED) <= 8


def test_the_scan_finds_a_planted_call():
    """A gate nobody has watched fail is a gate nobody knows works."""
    source = (
        "import urllib.request\n\n\ndef go():\n    return urllib.request.urlopen('x')\n"
    )
    tree = ast.parse(source)
    hits = [n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)]
    assert "urlopen" in hits


def test_listening_sockets_are_deliberately_out_of_scope():
    """The daemon and the relay bind sockets. That is a different risk with a
    different answer (bind address), and folding it in here would make this
    test about two things and good at neither."""
    sites = _egress_sites()
    assert "openai4s/share/relay.py" not in sites
    assert "openai4s/server/daemon.py" not in sites
