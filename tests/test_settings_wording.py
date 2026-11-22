"""Owner decision 29: the settings panel is "Settings", never "Customize".

The workbench has called the panel Settings (设置) for a while, but the
daemon's own messages -- "networking is disabled (enable it in Customize →
Network …)", "connector … is disabled; enable it in Customize → Connectors",
"add one in Customize → Models" -- and the docs that tell people where to
click still sent them to a "Customize" that is nowhere on screen.

This scans what the server says and what the docs say. Code identifiers that
name the frontend module (`components/customize/`, `openCustomize`) are not
navigation and are not matched: only "Customize →" / "Customize ->" (and the
`\\u2192` escape) and "in Customize" are.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_OLD = re.compile(r"Customize ?(?:→|->|\\u2192)|\bin (?:Web )?Customize\b")
#: Frozen or generated trees whose bytes this repository does not author here:
#: the built SPA, vendored libraries, the legacy `app.js` escape hatch (and
#: the dictionaries generated from it), and the pinned bioSkills payloads.
_SKIPPED = (
    "openai4s/server/webui/dist/",
    "openai4s/server/webui/vendor/",
    "openai4s/server/webui/app.js",
    "openai4s/server/webui/index.html",
    "frontend/src/i18n/en.ts",
    "frontend/src/i18n/zh.ts",
    "skills/bioskills/",
)
#: Tests may quote the old wording to prove it is gone.
_QUOTING_ALLOWED = ("tests/",)


def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=_REPO, capture_output=True, text=True, check=True
    ).stdout
    return [
        name
        for name in out.split()
        if name.endswith((".py", ".md", ".sh", ".example", ".ts", ".tsx", ".json"))
        and not name.startswith(_SKIPPED + _QUOTING_ALLOWED)
    ]


def test_server_messages_docs_and_comments_say_settings_not_customize():
    offenders = []
    for name in _tracked():
        path = _REPO / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if _OLD.search(line):
                offenders.append(f"{name}:{number}: {line.strip()[:120]}")
    assert offenders == []


def test_the_messages_a_user_reads_name_the_settings_panel(monkeypatch):
    import pytest

    from openai4s import webtools
    from openai4s.host.mcp import _disabled

    assert "Settings → Connectors" in _disabled("lab-db")
    monkeypatch.setattr(webtools, "network_allowed", lambda: False)
    with pytest.raises(webtools.NetworkDisabled, match="Settings → Network"):
        webtools._require_network()
