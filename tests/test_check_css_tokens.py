"""Contracts for ``scripts/check_css_tokens.py`` and the F-21 stylesheet fixes."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STYLE = ROOT / "openai4s" / "server" / "webui" / "style.css"


def _load():
    path = ROOT / "scripts" / "check_css_tokens.py"
    spec = importlib.util.spec_from_file_location(
        "openai4s_test_check_css_tokens", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_undefined_var_without_a_declaration_is_a_finding(tmp_path):
    css = tmp_path / "hole.css"
    css.write_text(":root{--ink:#111}.x{color:var(--text-100)}\n", encoding="utf-8")
    checker = _load()

    missing = checker.undefined(
        checker._strip_comments(css.read_text(encoding="utf-8"))
    )

    assert [ref.name for ref in missing] == ["--text-100"]
    assert missing[0].line == 1


def test_declared_token_is_not_a_finding(tmp_path):
    css = tmp_path / "ok.css"
    css.write_text(
        ":root{--text-100:#111}.x{color:var(--text-100)}\n", encoding="utf-8"
    )
    checker = _load()

    assert (
        checker.undefined(checker._strip_comments(css.read_text(encoding="utf-8")))
        == []
    )


def test_nested_fallback_still_counts_as_a_reference(tmp_path):
    css = tmp_path / "nested.css"
    css.write_text(
        ":root{--text-200:#333}.x{color:var(--text-200,var(--text-300))}\n",
        encoding="utf-8",
    )
    checker = _load()
    src = checker._strip_comments(css.read_text(encoding="utf-8"))
    missing = checker.undefined(src)

    assert [ref.name for ref in missing] == ["--text-300"]
    assert missing[0].nested_fallback is True
    assert checker.audit_subset(missing) == []


def test_hard_text_100_and_text_300_make_the_audit_subset():
    checker = _load()
    refs = [
        checker.Ref("--text-100", 1, False, False),
        checker.Ref("--text-300", 2, False, False),
        checker.Ref("--text-300", 3, False, True),
        checker.Ref("--warn", 4, True, False),
    ]

    subset = checker.audit_subset(refs)

    assert [ref.line for ref in subset] == [1, 2]


def test_check_file_returns_nonzero_on_a_hole(tmp_path, capsys):
    css = tmp_path / "hole.css"
    css.write_text(".x{color:var(--missing)}\n", encoding="utf-8")
    checker = _load()

    assert checker.check_file(css) == 1
    err = capsys.readouterr().err
    assert "--missing" in err
    assert "1 undefined" in err


def test_shipped_style_css_has_an_empty_undefined_set():
    checker = _load()
    src = checker._strip_comments(STYLE.read_text(encoding="utf-8"))

    assert checker.undefined(src) == []
    assert checker.check_file(STYLE) == 0


def test_root_and_dark_declare_the_four_f21_tokens():
    css = STYLE.read_text(encoding="utf-8")
    root = css.split('html[data-theme="dark"]', 1)[0]
    dark = css.split('html[data-theme="dark"]', 1)[1].split("}", 1)[0]
    for name in ("--text-100", "--text-300", "--surface-0", "--warn"):
        assert f"{name}:" in root
        assert f"{name}:" in dark
    assert "--text-400:#6f6d68" in root


def test_dark_comma_selectors_do_not_leak_onto_light():
    css = STYLE.read_text(encoding="utf-8")
    assert (
        'html[data-theme="dark"] .lang-btn.active,'
        'html[data-theme="dark"] .seg-btn.active{'
    ) in css
    assert 'html[data-theme="dark"] .lang-btn.active,.seg-btn.active{' not in css
    for leaked in (
        'html[data-theme="dark"] .thumb .molmini,.a-thumb .molmini',
        'html[data-theme="dark"] .thumb .txt,.a-thumb .txt',
        'html[data-theme="dark"] .tile:hover,.art:hover',
    ):
        assert leaked not in css


def test_markdown_wide_tables_scroll_inside_a_wrapper():
    css = STYLE.read_text(encoding="utf-8")
    assert ".md-table-wrap{" in css
    assert (
        "overflow-x:auto"
        in css[css.index(".md-table-wrap{") : css.index(".md-table-wrap{") + 80]
    )
    app = (ROOT / "openai4s" / "server" / "webui" / "app.js").read_text(
        encoding="utf-8"
    )
    assert 'class="md-table-wrap"' in app
    render = (ROOT / "frontend" / "src" / "features" / "md" / "render.ts").read_text(
        encoding="utf-8"
    )
    assert 'class="md-table-wrap"' in render


def test_narrow_viewport_touch_targets_are_at_least_40px():
    css = STYLE.read_text(encoding="utf-8")
    assert "@media (max-width:900px)" in css
    assert "min-height:40px" in css
    assert (
        ".nb-icon{width:40px;height:40px" in css
        or ".nb-icon{width:40px;height:40px;min-width:40px;min-height:40px}" in css
    )


def test_eight_dead_rules_are_gone():
    css = STYLE.read_text(encoding="utf-8")
    for selector in (
        ".files-view{",
        ".folder-tools{",
        ".side-mini{",
        ".nb-repl-prompt{",
        ".nbc-error-msg{",
        ".nbc-toggle{",
        ".prov-file-h{",
    ):
        assert selector not in css
    assert ".pmt{" not in css
