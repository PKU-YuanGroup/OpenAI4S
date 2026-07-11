"""Tests for the opencode-style tool-call permission gate: rule resolution
(store) + the blocking broker round-trip."""
import json
import threading
import time

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.permissions import PermissionBroker, broker, suggest_patterns
from openai4s.store import get_store


def _store(tmp_path):
    cfg = Config(data_dir=tmp_path, llm=LLMConfig(provider="deepseek", api_key="k"))
    st = get_store(cfg.db_path)
    st.seed_default_permission_rules()
    return st


# --- rule resolution ------------------------------------------------------
def test_seed_defaults_and_fallback(tmp_path):
    st = _store(tmp_path)
    assert st.resolve_permission(tool="read_file", pattern_input="data.csv") == "allow"
    assert st.resolve_permission(tool="glob", pattern_input="**/*.py") == "allow"
    # gentle default: safe in-workspace / SSRF-guarded research tools allow
    assert st.resolve_permission(tool="write_file", pattern_input="out.txt") == "allow"
    assert st.resolve_permission(tool="edit_file", pattern_input="out.txt") == "allow"
    assert st.resolve_permission(tool="web_search", pattern_input="x") == "allow"
    assert st.resolve_permission(tool="env_setup", pattern_input="numpy") == "allow"
    # genuinely risky ones still ask
    assert st.resolve_permission(tool="bash", pattern_input="ls -la") == "ask"
    assert st.resolve_permission(tool="mcp_call", pattern_input="x") == "ask"
    # a tool with no rule at all falls back to ask (security-first)
    assert st.resolve_permission(tool="totally_unknown", pattern_input="x") == "ask"


def test_env_read_denied_even_over_conversation_allow(tmp_path):
    st = _store(tmp_path)
    # broad conversation allow for reads
    st.set_permission_rule(
        scope="conversation",
        scope_id="f3",
        tool="read_file",
        pattern="*",
        decision="allow",
    )
    # the more-specific global *.env deny still wins
    assert (
        st.resolve_permission(
            root_frame_id="f3", tool="read_file", pattern_input="cfg/.env"
        )
        == "deny"
    )
    # a normal read under the conversation allow is fine
    assert (
        st.resolve_permission(
            root_frame_id="f3", tool="read_file", pattern_input="cfg/data.csv"
        )
        == "allow"
    )


def test_conversation_allow_overrides_global_ask(tmp_path):
    st = _store(tmp_path)
    st.set_permission_rule(
        scope="conversation", scope_id="f1", tool="bash", pattern="*", decision="allow"
    )
    assert (
        st.resolve_permission(root_frame_id="f1", tool="bash", pattern_input="ls")
        == "allow"
    )
    # a different conversation is unaffected
    assert (
        st.resolve_permission(root_frame_id="other", tool="bash", pattern_input="ls")
        == "ask"
    )


def test_pattern_specificity(tmp_path):
    st = _store(tmp_path)
    st.set_permission_rule(
        scope="conversation",
        scope_id="f2",
        tool="bash",
        pattern="git *",
        decision="allow",
    )
    assert (
        st.resolve_permission(
            root_frame_id="f2", tool="bash", pattern_input="git push origin main"
        )
        == "allow"
    )
    # non-matching command still hits the global bash ask
    assert (
        st.resolve_permission(root_frame_id="f2", tool="bash", pattern_input="rm -rf /")
        == "ask"
    )


def test_project_scope(tmp_path):
    st = _store(tmp_path)
    # a project rule applies only within that project (use a non-default decision
    # so the isolation is observable against the gentle web_search=allow default)
    st.set_permission_rule(
        scope="project",
        scope_id="proj-x",
        tool="web_search",
        pattern="*",
        decision="deny",
    )
    assert (
        st.resolve_permission(
            project_id="proj-x", tool="web_search", pattern_input="caffeine"
        )
        == "deny"
    )
    # a different project falls back to the gentle default (web_search allow)
    assert (
        st.resolve_permission(
            project_id="proj-y", tool="web_search", pattern_input="caffeine"
        )
        == "allow"
    )


def test_upsert_and_delete_rule(tmp_path):
    st = _store(tmp_path)
    rid = st.set_permission_rule(
        scope="global", scope_id="", tool="bash", pattern="rm *", decision="deny"
    )
    assert st.resolve_permission(tool="bash", pattern_input="rm x") == "deny"
    # upsert same key flips the decision, does not duplicate
    rid2 = st.set_permission_rule(
        scope="global", scope_id="", tool="bash", pattern="rm *", decision="ask"
    )
    assert rid2 == rid
    assert st.resolve_permission(tool="bash", pattern_input="rm x") == "ask"
    st.delete_permission_rule(rid)
    # back to the seeded bash * -> ask
    assert st.resolve_permission(tool="bash", pattern_input="rm x") == "ask"


# --- broker round-trip ----------------------------------------------------
def test_broker_headless_fails_closed_unless_operator_explicitly_allows(
    tmp_path, monkeypatch
):
    st = _store(tmp_path)
    b = PermissionBroker()
    # No UI channel registered: an ask action is auditable and denied by
    # default instead of silently escalating to allow.
    denied = b.gate(store=st, frame_id=None, method="bash", target="ls")
    assert denied["allow"] is False
    assert st.list_permission_requests(state="denied")[-1]["tool"] == "bash"
    monkeypatch.setenv("OPENAI4S_UNATTENDED_APPROVAL", "allow")
    assert b.gate(store=st, frame_id=None, method="bash", target="pwd")["allow"] is True
    assert st.list_permission_requests(state="allowed")[-1]["target"] == "pwd"
    # deny rules still bite even without a channel
    res = b.gate(store=st, frame_id=None, method="read_file", target="a/.env")
    assert res["allow"] is False


def test_broker_blocks_until_allowed_and_persists(tmp_path):
    st = _store(tmp_path)
    b = PermissionBroker()
    events = []
    b.register_channel("root1", lambda ev: events.append(ev))
    out = {}

    def run():
        out["res"] = b.gate(
            store=st, frame_id="root1", method="bash", target="pytest -q"
        )

    t = threading.Thread(target=run)
    t.start()
    # wait for the await_permission emit
    for _ in range(200):
        if any(e.get("type") == "await_permission" for e in events):
            break
        time.sleep(0.01)
    ask = next(e for e in events if e.get("type") == "await_permission")
    assert ask["tool"] == "bash" and ask["scopes"][0] == "once"
    assert b.resolve(
        ask["decision_id"], allow=True, scope="conversation", pattern="pytest *"
    )
    t.join(timeout=5)
    assert out["res"]["allow"] is True
    durable = st.get_permission_request(ask["decision_id"])
    assert durable["state"] == "allowed"
    assert durable["scope"] == "conversation"
    # a resolved event was emitted to clear the card
    assert any(e.get("type") == "permission_resolved" for e in events)
    # the conversation rule was persisted, so a matching call no longer asks
    assert (
        st.resolve_permission(
            root_frame_id="root1", tool="bash", pattern_input="pytest -q"
        )
        == "allow"
    )


def test_broker_deny_returns_soft_fail(tmp_path):
    st = _store(tmp_path)
    b = PermissionBroker()
    events = []
    b.register_channel("root2", lambda ev: events.append(ev))
    out = {}

    def run():
        # use a still-gated tool (bash) so the ask→deny round-trip actually prompts
        out["res"] = b.gate(
            store=st, frame_id="root2", method="bash", target="rm -rf /tmp/x"
        )

    t = threading.Thread(target=run)
    t.start()
    for _ in range(200):
        if any(e.get("type") == "await_permission" for e in events):
            break
        time.sleep(0.01)
    did = next(e for e in events if e.get("type") == "await_permission")["decision_id"]
    assert b.resolve(did, allow=False, scope="once", message="not now")
    t.join(timeout=5)
    assert out["res"]["allow"] is False
    assert "not now" in (out["res"].get("message") or "")


def test_broker_cancel_denies_pending(tmp_path):
    st = _store(tmp_path)
    b = PermissionBroker()
    events = []
    b.register_channel("root3", lambda ev: events.append(ev))
    out = {}

    def run():
        out["res"] = b.gate(
            store=st, frame_id="root3", method="bash", target="sleep 999"
        )

    t = threading.Thread(target=run)
    t.start()
    for _ in range(200):
        if any(e.get("type") == "await_permission" for e in events):
            break
        time.sleep(0.01)
    b.cancel_root("root3")
    t.join(timeout=5)
    assert out["res"]["allow"] is False
    decision_id = next(
        event["decision_id"]
        for event in events
        if event.get("type") == "await_permission"
    )
    assert st.get_permission_request(decision_id)["state"] == "cancelled"


def test_durable_pending_request_survives_broker_restart_and_can_be_resolved(tmp_path):
    st = _store(tmp_path)
    payload = {
        "type": "await_permission",
        "frame_id": "root-durable",
        "decision_id": "perm-durable",
        "tool": "mcp_call",
        "target": "server/send",
    }
    st.create_permission_request(
        decision_id="perm-durable",
        root_frame_id="root-durable",
        frame_id="root-durable",
        project_id="default",
        tool="mcp_call",
        target="server/send",
        payload=payload,
    )

    restarted = PermissionBroker()
    restarted.register_channel(
        "root-durable", lambda event: None, store=st
    )
    assert restarted.pending_events("root-durable") == [payload]
    assert restarted.resolve("perm-durable", allow=False, message="reviewed")
    row = st.get_permission_request("perm-durable")
    assert row["state"] == "denied"
    assert row["message"] == "reviewed"
    assert restarted.pending_events("root-durable") == []


def test_suggest_patterns_generalizes():
    ps = suggest_patterns("bash", "git push origin main")
    assert ps[0] == "git push origin main"
    assert "git push *" in ps and "git *" in ps and ps[-1] == "*"
    ps2 = suggest_patterns("write_file", "results/out.csv")
    assert "results/*" in ps2 and "*.csv" in ps2


# --- end-to-end through the real HostDispatcher.__call__ ------------------
def _dispatcher(tmp_path):
    from openai4s.host_dispatch import build_dispatcher

    cfg = Config(data_dir=tmp_path, llm=LLMConfig(provider="deepseek", api_key="k"))
    st = get_store(cfg.db_path)
    st.seed_default_permission_rules()
    frame = st.new_frame(kind="turn")  # frame_id == its own root_frame_id
    disp = build_dispatcher(cfg, frame_id=frame)
    return disp, frame, st


def _wait_ask(events):
    for _ in range(300):
        for e in events:
            if e.get("type") == "await_permission":
                return e
        time.sleep(0.01)
    raise AssertionError("no await_permission emitted")


def test_dispatcher_gate_denies_write_file_soft_fail(tmp_path):
    # bash is no longer a host method (shell runs kernel-local); write_file —
    # pinned to 'ask' for this conversation — exercises the same deny path.
    disp, frame, st = _dispatcher(tmp_path)
    st.set_permission_rule(
        scope="conversation",
        scope_id=frame,
        tool="write_file",
        pattern="*",
        decision="ask",
    )
    events = []
    broker().register_channel(frame, lambda ev: events.append(ev))
    try:
        out = {}
        t = threading.Thread(
            target=lambda: out.__setitem__(
                "r",
                disp("write_file", [{"path": "gate.txt", "content": "nope"}]),
            )
        )
        t.start()
        ask = _wait_ask(events)
        broker().resolve(ask["decision_id"], allow=False, scope="once")
        t.join(timeout=8)
        # denied call returns the single-key soft-fail dict the worker raises
        assert set(out["r"].keys()) == {"error"}
        assert "Permission denied" in out["r"]["error"]
        assert not (disp._workspace() / "gate.txt").exists()
    finally:
        broker().unregister_channel(frame)


def test_dispatcher_gate_allows_and_runs_write_file(tmp_path):
    disp, frame, st = _dispatcher(tmp_path)
    st.set_permission_rule(
        scope="conversation",
        scope_id=frame,
        tool="write_file",
        pattern="*",
        decision="ask",
    )
    events = []
    broker().register_channel(frame, lambda ev: events.append(ev))
    try:
        out = {}
        t = threading.Thread(
            target=lambda: out.__setitem__(
                "r",
                disp("write_file", [{"path": "gate.txt", "content": "gate-ok"}]),
            )
        )
        t.start()
        ask = _wait_ask(events)
        broker().resolve(ask["decision_id"], allow=True, scope="once")
        t.join(timeout=8)
        # allow → the real _m_write_file ran and the file exists
        assert out["r"].get("path")
        assert (disp._workspace() / "gate.txt").read_text() == "gate-ok"
    finally:
        broker().unregister_channel(frame)


def test_new_control_tool_class_auto_routes_and_defaults_to_approval(tmp_path):
    from openai4s.tools import registry as registry_mod
    from openai4s.tools.base import Tool

    calls = []

    class ExtensionProbeTool(Tool):
        name = "extension_probe"
        host_method = "extension_probe"
        description = "Test the class-based extension path."
        parameters = {
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        }

        def execute(self, context, arguments):
            calls.append((context, arguments))
            return {"value": arguments.get("value")}

    tool = ExtensionProbeTool()
    registry_mod.register_tool(tool)
    try:
        disp, frame, store = _dispatcher(tmp_path)
        store.set_permission_rule(
            scope="conversation",
            scope_id=frame,
            tool=tool.host_method,
            pattern="*",
            decision="deny",
        )

        denied = disp(tool.host_method, [{"value": "blocked"}])

        assert set(denied) == {"error"}
        assert calls == []

        store.set_permission_rule(
            scope="conversation",
            scope_id=frame,
            tool=tool.host_method,
            pattern="*",
            decision="allow",
        )
        allowed = disp(tool.host_method, [{"value": "ran"}])

        assert allowed == {"value": "ran"}
        assert calls == [(disp._tool_context, {"value": "ran"})]
        logged = store._conn.execute(
            "SELECT ok FROM host_call_log WHERE method=? ORDER BY rowid",
            (tool.host_method,),
        ).fetchall()
        assert [row["ok"] for row in logged] == [0, 1]
    finally:
        registry_mod._unregister_tool(tool.name)


def test_control_tool_secret_guard_is_independent_of_approval(tmp_path):
    from openai4s.tools import registry as registry_mod
    from openai4s.tools.base import Tool

    calls = []

    class UngatedSecretProbeTool(Tool):
        name = "ungated_secret_probe"
        host_method = "ungated_secret_probe"
        description = "Exercise the absolute secret-path veto."
        parameters = {
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }
        requires_approval = False
        secret_path_key = "path"

        def execute(self, context, arguments):
            calls.append(arguments)
            return {"ok": True}

    tool = registry_mod.register_tool(UngatedSecretProbeTool())
    try:
        disp, _frame, _store = _dispatcher(tmp_path)

        result = disp(tool.host_method, [{"path": "config/.env"}])

        assert set(result) == {"error"}
        assert "secret" in result["error"].lower()
        assert calls == []
    finally:
        registry_mod._unregister_tool(tool.name)


def test_plugin_tool_cannot_shadow_existing_non_control_host_method(tmp_path):
    from openai4s.tools import registry as registry_mod
    from openai4s.tools.base import Tool

    class CredentialShadowTool(Tool):
        name = "credential_shadow"
        host_method = "credentials_set"
        description = "Must not replace a built-in host capability."
        parameters = {"properties": {}, "required": []}

        def execute(self, context, arguments):
            return {"ok": True}

    tool = registry_mod.register_tool(CredentialShadowTool())
    try:
        disp, _frame, _store = _dispatcher(tmp_path)

        with pytest.raises(ValueError, match="conflicts with existing host method"):
            disp(tool.host_method, [{}])
    finally:
        registry_mod._unregister_tool(tool.name)


def test_dispatcher_readonly_tool_not_gated_by_default(tmp_path):
    # glob is seeded 'allow', so a read-only tool must NOT emit a prompt.
    disp, frame, _ = _dispatcher(tmp_path)
    events = []
    broker().register_channel(frame, lambda ev: events.append(ev))
    try:
        # runs inline (no thread) — if it blocked on a prompt this would hang
        disp("glob", [{"pattern": "*.py"}])
        assert not any(e.get("type") == "await_permission" for e in events)
    finally:
        broker().unregister_channel(frame)


# --- review-fix regression tests -----------------------------------------
def test_deny_is_absolute_over_broader_scope_allow(tmp_path):
    # a conversation 'deny bash *' must beat a broader-scope specific 'allow git *'
    st = _store(tmp_path)
    st.set_permission_rule(
        scope="global", scope_id="", tool="bash", pattern="git *", decision="allow"
    )
    st.set_permission_rule(
        scope="conversation", scope_id="fD", tool="bash", pattern="*", decision="deny"
    )
    assert (
        st.resolve_permission(root_frame_id="fD", tool="bash", pattern_input="git push")
        == "deny"
    )
    # without the conversation deny, the specific global allow applies
    assert (
        st.resolve_permission(
            root_frame_id="other", tool="bash", pattern_input="git push"
        )
        == "allow"
    )


def test_exact_literal_pattern_with_metachars_matches_itself(tmp_path):
    from openai4s.store import _perm_match

    assert _perm_match("grep [a-z] file.txt", "grep [a-z] file.txt")  # exact literal
    st = _store(tmp_path)
    st.set_permission_rule(
        scope="conversation",
        scope_id="fM",
        tool="bash",
        pattern="ls a[1].txt",
        decision="allow",
    )
    assert (
        st.resolve_permission(
            root_frame_id="fM", tool="bash", pattern_input="ls a[1].txt"
        )
        == "allow"
    )


def test_reset_restores_modified_default_decision(tmp_path):
    st = _store(tmp_path)
    st.set_permission_rule(
        scope="global", scope_id="", tool="mcp_call", pattern="*", decision="allow"
    )  # user loosens the default
    assert st.resolve_permission(tool="mcp_call", pattern_input="srv/tool") == "allow"
    st.seed_default_permission_rules(force=True)  # reset
    assert st.resolve_permission(tool="mcp_call", pattern_input="srv/tool") == "ask"


def test_exec_background_gate_target_is_the_code():
    from openai4s.host_dispatch import _gate_target

    assert _gate_target("exec_background", [{"code": "print(1)"}]) == "print(1)"


def test_control_tool_gate_targets_preserve_missing_argument_defaults():
    from openai4s.host_dispatch import _gate_target

    assert _gate_target("read_file", [{}]) == ""
    assert _gate_target("glob", [{}]) == ""
    assert _gate_target("web_search", [{}]) == ""
    assert _gate_target("list_dir", [{}]) == "."
    assert _gate_target("env_setup", [{"packages": []}]) == ""


def test_is_secret_path_case_insensitive():
    from openai4s.host_dispatch import _is_secret_path

    assert _is_secret_path(".env") and _is_secret_path("cfg/.ENV")
    assert _is_secret_path("deploy/prod.env") and _is_secret_path("id_rsa")
    assert not _is_secret_path("notes.txt") and not _is_secret_path("main.py")


def test_secret_file_read_hard_denied_without_prompt(tmp_path):
    # read_file .env is blocked by the hard guard BEFORE the rule engine / prompt
    disp, frame, _ = _dispatcher(tmp_path)
    events = []
    broker().register_channel(frame, lambda ev: events.append(ev))
    try:
        r = disp("read_file", [{"path": "config/.ENV"}])  # case-insensitive
        assert set(r.keys()) == {"error"} and "secret" in r["error"].lower()
        assert not any(e.get("type") == "await_permission" for e in events)
    finally:
        broker().unregister_channel(frame)


def test_grep_and_glob_skip_secret_files(tmp_path):
    disp, frame, _ = _dispatcher(tmp_path)
    ws = disp._workspace()
    (ws / ".env").write_text("API_KEY=NEEDLE123\n", encoding="utf-8")
    (ws / "notes.txt").write_text("nothing here\n", encoding="utf-8")
    grep = disp("grep", [{"pattern": "NEEDLE123"}])
    assert not any(".env" in (m.get("file") or "") for m in grep.get("matches", []))
    glob = disp("glob", [{"pattern": "*"}])
    assert not any(m.endswith(".env") for m in glob.get("matches", []))


# --- secret reads/logs through the real dispatcher (PR 01) ----------------
_SYNTH_SECRET = "sk-SYNTHETIC-SECRET-DO-NOT-LEAK-4f2a9c"


def test_agent_query_cannot_read_settings_secret(tmp_path):
    # A secret persisted under `settings` (the gateway stores the live API key
    # there) must not be reachable through host.query. The handler raises
    # PermissionError, which the worker turns into the soft-fail RuntimeError the
    # agent sees; the secret never appears in the error.
    disp, _frame, st = _dispatcher(tmp_path)
    st.set_setting("llm_api_key", _SYNTH_SECRET)
    with pytest.raises(PermissionError) as exc:
        disp("query", [{"sql": "SELECT value FROM settings"}])
    assert _SYNTH_SECRET not in str(exc.value)
    # schema introspection also hides the secret-bearing table.
    schema = disp("query_schema", [])
    assert "settings" not in schema and "connectors" not in schema


def test_credentials_set_secret_never_in_host_call_log(tmp_path):
    # Explicitly authorize this synthetic credential write; headless `ask`
    # now fails closed. Its plaintext must never reach the host_call_log.
    disp, _frame, st = _dispatcher(tmp_path)
    st.set_permission_rule(
        scope="global",
        scope_id="",
        tool="credentials_set",
        pattern="*",
        decision="allow",
    )
    out = disp("credentials_set", [{"name": "HF_TOKEN", "value": _SYNTH_SECRET}])
    assert out.get("ok") is True
    # the value round-trips in-process…
    got = disp("credentials_get", ["HF_TOKEN"])
    assert got["value"] == _SYNTH_SECRET
    # …but is nowhere in the persisted audit log.
    rows = st._conn.execute("SELECT method, args_preview FROM host_call_log").fetchall()
    assert not any(_SYNTH_SECRET in (r["args_preview"] or "") for r in rows)
    # credentials_get is not logged at all; credentials_set is logged, redacted.
    methods = {r["method"] for r in rows}
    assert "credentials_get" not in methods


def test_recorder_never_tapes_credentials_set(tmp_path):
    # The replay-tape recorder must skip SECRET_ARG_HOST_CALLS: an exported
    # notebook tape must never carry a plaintext credential.
    from openai4s.replay import TapeRecorder

    disp, _frame, _st = _dispatcher(tmp_path)
    _st.set_permission_rule(
        scope="global",
        scope_id="",
        tool="credentials_set",
        pattern="*",
        decision="allow",
    )
    rec = TapeRecorder(tmp_path / "openai4s_tape.json")
    disp.recorder = rec

    # a benign successful call IS taped — proves the recorder is live…
    disp("glob", [{"pattern": "*.py"}])
    assert any(r["method"] == "glob" for r in rec.records)

    # …but a successful credentials_set never reaches the tape.
    out = disp("credentials_set", [{"name": "HF_TOKEN", "value": _SYNTH_SECRET}])
    assert out.get("ok") is True
    assert not any(r["method"] == "credentials_set" for r in rec.records)
    # and the plaintext secret appears nowhere in the tape, in memory or on disk.
    assert _SYNTH_SECRET not in json.dumps(rec.records, ensure_ascii=False)
    tape_file = rec.flush()
    assert _SYNTH_SECRET not in tape_file.read_text()
