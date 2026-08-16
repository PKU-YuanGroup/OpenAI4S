"""Every module that can reach the network is named here, on purpose.

Written as a prerequisite for telemetry -- the one feature that deliberately
breaks the loopback-only default -- but it is not about telemetry. Before this,
nothing in the repository recorded which modules can open an outbound
connection. "Off by default, and not a single packet leaves the machine" is a
claim about the whole tree, and it cannot be checked one file at a time.

So the surface is frozen: eleven modules today, each with a stated reason. A new
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
#: Note what is NOT here. `openai4s/share/relay.py` binds a listening socket
#: and is out of scope (see the module docstring), and `server/gateway.py`
#: mentions `import requests` only inside a prompt string shown to the model.
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
    "openai4s/telemetry/sender.py": (
        "opt-in telemetry, and the only module here that is off by default. It "
        "refuses before it resolves: no consent, no redirect, no plain HTTP, "
        "and no payload it did not get from wire.seal. See "
        "tests/test_telemetry_off_by_default.py for the proof."
    ),
    "openai4s/share/ws_client.py": (
        "the outbound tunnel a share opens to the relay. Off unless sharing "
        "is configured."
    ),
    "openai4s/mcp_http.py": (
        "the managed DataPro MCP Streamable HTTP transport. It uses a fixed "
        "endpoint, applies network, egress, and SSRF policy before each POST, "
        "and refuses redirects so authenticated headers stay on one origin."
    ),
    "openai4s/doubao_search.py": (
        "the fixed Doubao Search client. It resolves a brokered Agent Plan Key "
        "only for one bounded POST, enforces network and SSRF policy, refuses "
        "redirects, and never projects the outbound authorization header."
    ),
    "openai4s/kernel/worker.py": (
        "a worker placed on a compute node dialling back to the daemon that "
        "asked for it (M3b-1). The worker is the client because a compute "
        "node is usually reachable from nothing while the daemon usually is. "
        "Off unless the scheduler set OPENAI4S_WORKER_CONNECT, refused "
        "outright without a credential file, and the address it dials is the "
        "one this daemon wrote -- it is never taken from a cell or a model."
    ),
    "openai4s/http_deadline.py": (
        "the shared stdlib HTTP deadline transport. Its custom HTTP(S) "
        "connections register only the live socket so one wall-clock watchdog "
        "can interrupt connect, TLS, response headers, and body reads."
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
    """Twelve modules is reviewable. If this fails, the question is not how to
    raise the bound -- it is why the surface grew.

    It grew once, from eleven, and the answer is recorded rather than left to
    archaeology: `kernel/worker.py` dials back to the daemon when a scheduler
    places it on a compute node (M3b-1). It is a client, not a listener; it is
    off unless the scheduler set the address; it refuses to run at all without
    a credential file; and the address it dials was written by this daemon,
    never taken from a cell or a model."""
    assert len(_DECLARED) <= 12


def test_the_scan_finds_a_planted_call():
    """A gate nobody has watched fail is a gate nobody knows works."""
    source = (
        "import urllib.request\n\n\ndef go():\n    return urllib.request.urlopen('x')\n"
    )
    tree = ast.parse(source)
    hits = [n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)]
    assert "urlopen" in hits


def test_listening_sockets_are_deliberately_out_of_scope():
    """The relay binds a socket. That is a different risk with a different
    answer (bind address), and folding it in here would make this test about
    two things and good at neither.

    `openai4s/server/daemon.py` used to be named here too. It was deleted: an
    unauthenticated `POST /run` onto `Agent.run` that nothing imported. An
    assertion that a nonexistent file is absent passes for the wrong reason,
    so it went with it.
    """
    sites = _egress_sites()
    assert "openai4s/share/relay.py" not in sites


# --------------------------------------------------------------------------
# the same question, asked of the bundled Skills
# --------------------------------------------------------------------------

_SKILLS = Path(__file__).resolve().parent.parent / "skills"

#: Bundled skill sidecars that reach the network directly, and why each is
#: still doing so. This table exists to *freeze* the set, not to bless it.
#:
#: The scanner above has only ever looked at `openai4s/`, and its own docstring
#: names the gap: it "cannot see egress from a subprocess -- a kernel cell
#: running `requests`". A skill sidecar is exactly that. `openai4s/egress.py`
#: is consulted by `webtools`/`web_fetch` and the bash gate; `openai4s/kernel/`
#: mentions egress once, to forward `OPENAI4S_EGRESS` as an environment
#: variable. So these calls do not merely bypass the allowlist by convention --
#: nothing in the analysis kernel enforces it on them at all.
#:
#: Removing an entry is the goal. Adding one needs a reason that survives
#: someone asking why `host.web_fetch` would not do.
#: Bundled skills allowed to reach the network directly, with why.
#:
#: Empty, and that is the point. Three skills were here -- `literature-review`
#: (DOI/OpenAlex lookups), `mineral_spectra_analysis` (the RRUFF archive) and
#: `catalyst_sar_screening` (a model-endpoint probe) -- each because
#: `host.web_fetch` could not express what it needed: a HEAD existence probe
#: that does not follow redirects, a contactable User-Agent, a binary download.
#: So each used raw `urllib`, and a request made that way is subject to neither
#: the egress allowlist nor the SSRF guard. The gap in the Host API was the
#: reason part of the product's own traffic went around the fence built for it.
#:
#: The API grew those three powers (`host.web_fetch(method="HEAD")`,
#: `user_agent=`, and `host.web_download`), all guarded, and the skills moved
#: onto them. A new entry here is a new hole and has to argue for itself.
_SKILL_EGRESS: dict[str, str] = {}


def _skill_egress_sites() -> dict[str, list[tuple[int, str]]]:
    """The same AST scan, rooted at `skills/` instead of the package."""
    sites: dict[str, list[tuple[int, str]]] = {}
    if not _SKILLS.is_dir():
        return sites
    for path in sorted(_SKILLS.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text("utf-8"))
        except (OSError, SyntaxError):  # pragma: no cover - unreadable source
            continue
        rel = path.relative_to(_SKILLS.parent).as_posix()
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


def test_no_undeclared_skill_reaches_the_network():
    """A bundled skill that opens a socket must be named here.

    The frozen surface for `openai4s/` has existed for a while; `skills/` was
    entirely outside it, which is where the unenforced egress actually lives.
    This does not stop the three below from doing it -- it stops a fourth from
    appearing without anyone deciding to allow it.
    """
    undeclared = {
        module: sites
        for module, sites in _skill_egress_sites().items()
        if module not in _SKILL_EGRESS
    }
    assert undeclared == {}, (
        "these bundled skills reach the network and are not declared in "
        f"_SKILL_EGRESS: {undeclared}. Prefer `host.web_fetch`, which is "
        "subject to the egress allowlist and the SSRF guard; if it genuinely "
        "cannot serve, add an entry saying why."
    )


def test_every_declared_skill_egress_entry_is_still_real():
    """A declaration for a skill that no longer reaches the network reads as a
    standing exemption for something already fixed."""
    sites = _skill_egress_sites()
    stale = sorted(module for module in _SKILL_EGRESS if module not in sites)
    assert (
        stale == []
    ), f"these no longer reach the network; drop their _SKILL_EGRESS entry: {stale}"


#: The browser client's own egress surface, which the AST walk above cannot see:
#: it reads Python, and this is JavaScript loaded into a page that holds the
#: session cookie. Frozen the same way and for the same reason.
_WEBUI = _PACKAGE / "server" / "webui"

#: Absolute URLs the client may *name*, each with why it is not a request. A
#: string is not egress; a string handed to something that fetches it is. Both
#: of these are inert, so they are listed rather than removed -- and listed with
#: a reason, so a third entry has to argue for itself.
#:
#: `vendor/` is excluded from the scan entirely: it is upstream minified code,
#: and a URL inside a bundled library is not the client choosing to call it.
_WEBUI_NAMED_HOSTS = {
    "www.w3.org": "the SVG XML namespace passed to createElementNS -- an "
    "identifier, never dereferenced by any browser",
    "api.tavily.com": "displayed as the default search endpoint in Customize. "
    "The call is made by the daemon; the client only renders the string",
}

#: Constructs that turn a URL into a request. An absolute URL on the same line
#: as one of these fails regardless of the table above, because the question
#: there is not which host but whether the client fetches at all.
_WEBUI_REQUEST_SITES = (
    "fetch(",
    ".src",
    ".href",
    "import(",
    "new Worker(",
    "XMLHttpRequest",
    "sendBeacon(",
    "new EventSource(",
    "new WebSocket(",
)


def _webui_sources() -> list[Path]:
    return [
        path
        for path in sorted(_WEBUI.rglob("*"))
        if path.is_file()
        and path.suffix in {".js", ".html", ".css"}
        and "vendor" not in path.relative_to(_WEBUI).parts
    ]


def _webui_absolute_urls() -> list[tuple[str, int, str, str]]:
    """(file, line, host, text) for every absolute URL in live client code."""
    import re

    pattern = re.compile(r"""["'`](https?://[^"'`\s]+)""")
    found: list[tuple[str, int, str, str]] = []
    for path in _webui_sources():
        for lineno, line in enumerate(
            path.read_text("utf-8", errors="replace").splitlines(), 1
        ):
            # A URL in a comment documents what was removed; it is not code.
            stripped = line.lstrip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            for match in pattern.finditer(line):
                url = match.group(1)
                found.append(
                    (str(path.relative_to(_PACKAGE)), lineno, url.split("/")[2], line)
                )
    return found


def test_the_browser_client_fetches_nothing_off_its_own_origin():
    """A CDN fallback is an outbound request the user did not ask for.

    `app.js` used to retry 3Dmol from `https://3Dmol.org/build/3Dmol-min.js`
    when the vendored copy failed to load. Three things were wrong with it at
    once: it is a real outbound request from an application whose premise is
    that it stays on loopback; it executes third-party script in the page
    holding the session cookie; and it is silent, so the one user who would
    care -- an air-gapped or regulated install -- learns nothing. The degraded
    path it was skipping (render the coordinates as text) was already written.

    This asserts the sharp thing: no absolute URL is handed to anything that
    fetches. The host table is the softer companion check below, and neither
    subsumes the other -- an allowlisted host in a `fetch(` still fails here.
    """
    offenders = [
        (path, line, host)
        for path, line, host, text in _webui_absolute_urls()
        if any(site in text for site in _WEBUI_REQUEST_SITES)
    ]
    assert (
        not offenders
    ), "the browser client fetches from an external origin:\n" + "\n".join(
        f"  {path}:{line} -> {host}" for path, line, host in offenders
    )


def test_every_external_host_the_client_names_is_accounted_for():
    """The softer half: a new hostname in client code has to be argued for.

    Kept separate from the fetch check because it catches a different mistake --
    a URL that is inert today and one refactor away from being passed to
    `fetch`. Adding a row here is a decision; typing a hostname into `app.js`
    is a Tuesday.
    """
    unaccounted = sorted(
        {
            (path, line, host)
            for path, line, host, _ in _webui_absolute_urls()
            if host not in _WEBUI_NAMED_HOSTS
        }
    )
    assert not unaccounted, (
        "client code names a host with no recorded reason:\n"
        + "\n".join(f"  {path}:{line} -> {host}" for path, line, host in unaccounted)
        + "\n\nAdd it to _WEBUI_NAMED_HOSTS with why it is not a request, or "
        "remove it."
    )
