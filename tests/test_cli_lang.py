"""`init`, `doctor` and `setup` speak Chinese by locale or OPENAI4S_LANG (CLI-35).

The owner's decision: the first commands a new user runs say what they did in
the user's language, chosen by ``OPENAI4S_LANG`` or else the POSIX locale.
Only prose changes. ``--json`` output, exit codes, flags, command, variable
and package names are the machine interface and must be byte-identical in
both languages, which the second half of this module pins.
"""

from __future__ import annotations

import importlib
import io
import json
import re
import string
import subprocess
import types

import pytest

from openai4s import doctor
from openai4s.cli import lang
from openai4s.config import Config, LLMConfig

_CJK = re.compile(r"[一-鿿]")


def _cli():
    return importlib.import_module("openai4s.cli.main")


def _speak(monkeypatch, *, override=None, **locale):
    """Set the CLI's language inputs exactly: nothing the host set leaks in."""
    for name in ("OPENAI4S_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    if override is not None:
        monkeypatch.setenv("OPENAI4S_LANG", override)
    for name, value in locale.items():
        monkeypatch.setenv(name, value)


# --------------------------------------------------------------------------
# The rule: OPENAI4S_LANG, else LC_ALL -> LC_MESSAGES -> LANG, else English
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, "en"),
        ({"OPENAI4S_LANG": "zh"}, "zh"),
        ({"OPENAI4S_LANG": "zh_CN"}, "zh"),
        ({"OPENAI4S_LANG": "zh-CN"}, "zh"),
        ({"OPENAI4S_LANG": "zh_CN.UTF-8"}, "zh"),
        ({"OPENAI4S_LANG": " ZH-hans "}, "zh"),
        ({"OPENAI4S_LANG": "en"}, "en"),
        ({"OPENAI4S_LANG": "en_US.UTF-8"}, "en"),
        # An explicit choice is never overruled by the locale...
        ({"OPENAI4S_LANG": "en", "LANG": "zh_CN.UTF-8"}, "en"),
        ({"OPENAI4S_LANG": "zh", "LC_ALL": "en_US.UTF-8"}, "zh"),
        # ...and a value naming no translated language is English, not "ask
        # the locale".
        ({"OPENAI4S_LANG": "fr", "LANG": "zh_CN.UTF-8"}, "en"),
        ({"OPENAI4S_LANG": "zhuang", "LANG": "zh_CN.UTF-8"}, "en"),
        # Empty is unset.
        ({"OPENAI4S_LANG": "", "LANG": "zh_CN.UTF-8"}, "zh"),
        ({"LANG": "zh_CN.UTF-8"}, "zh"),
        ({"LANG": "zh_TW.UTF-8"}, "zh"),
        ({"LANG": "zh"}, "zh"),
        ({"LANG": "en_GB.UTF-8"}, "en"),
        ({"LANG": "C.UTF-8"}, "en"),
        ({"LANG": "de_DE.UTF-8"}, "en"),
        # POSIX precedence: the first non-empty of LC_ALL, LC_MESSAGES, LANG.
        ({"LC_MESSAGES": "zh_CN.UTF-8", "LANG": "en_US.UTF-8"}, "zh"),
        ({"LC_ALL": "C", "LC_MESSAGES": "zh_CN.UTF-8", "LANG": "zh_CN"}, "en"),
        ({"LC_ALL": "zh_CN.UTF-8", "LANG": "en_US.UTF-8"}, "zh"),
        ({"LC_ALL": "", "LC_MESSAGES": "", "LANG": "zh_CN.UTF-8"}, "zh"),
        ({"LC_ALL": "POSIX", "LANG": "zh_CN.UTF-8"}, "en"),
    ],
)
def test_the_language_is_decided_by_one_rule(environ, expected):
    assert lang.cli_language(environ) == expected


def test_the_language_reads_the_process_environment_by_default(monkeypatch):
    _speak(monkeypatch, LANG="zh_CN.UTF-8")
    assert lang.cli_language() == "zh"
    _speak(monkeypatch, override="en", LANG="zh_CN.UTF-8")
    assert lang.cli_language() == "en"


# --------------------------------------------------------------------------
# The tables
# --------------------------------------------------------------------------


def _fields(template: str) -> set[str]:
    return {
        name for _, name, _, _ in string.Formatter().parse(template) if name is not None
    }


@pytest.mark.parametrize("table", [lang._COPY, doctor._COPY], ids=["cli", "doctor"])
def test_both_languages_say_every_sentence_with_the_same_blanks(table):
    """A key missing on one side is a KeyError mid-command; a blank missing
    on one side drops a path or a value from one language's report."""
    assert set(table["en"]) == set(table["zh"])
    for key, english in table["en"].items():
        assert _fields(english) == _fields(table["zh"][key]), key


@pytest.mark.parametrize("table", [lang._COPY, doctor._COPY], ids=["cli", "doctor"])
def test_the_chinese_uses_the_products_terms(table):
    """Owner decision 10: 产物 not 制品, 单元 not Cell/代码单元, 标注 not 批注;
    the settings panel is 设置 (decision 29), never Customize."""
    for key, text in table["zh"].items():
        for wrong in (
            "制品",
            "代码单元",
            "批注",
            "Customize",
            "自定义",
            "Cell",
            "cell",
        ):
            assert wrong not in text, (key, wrong)


def test_every_refusal_the_cli_translates_is_one_the_services_raise(tmp_path):
    """`init` says the onboarding and search services' English refusals in
    its own language by their exact text. A refusal reworded at its source
    would silently fall back to English; this holds the two together."""
    from openai4s import search_settings
    from openai4s.llm import PROVIDERS
    from openai4s.onboarding import OnboardingService
    from openai4s.store import get_store

    cfg = Config(data_dir=tmp_path, llm=LLMConfig(provider="ark"))
    cfg.ensure_dirs()
    store = get_store(cfg.db_path)
    service = OnboardingService(cfg, store, PROVIDERS)
    raised = set()
    for kwargs in (
        {"provider": "ark", "model": " "},
        {"provider": "ark", "base_url": "ftp://example.test"},
        {"provider": "ark", "base_url": "https://user:pw@example.test"},
        {"provider": "ark", "base_url": "https://example.test/v1?x=1"},
        {"provider": "ark", "api_key": " "},
    ):
        with pytest.raises(ValueError) as caught:
            service.configure(**kwargs)
        raised.add(str(caught.value))
    for choice in ("doubao", "tavily"):
        with pytest.raises(ValueError) as caught:
            search_settings.apply_setup_choice(store, choice, "")
        raised.add(str(caught.value))
    with pytest.raises(ValueError) as caught:
        service.configure(provider="deepmind")
    unknown = str(caught.value)

    assert raised == set(lang._REFUSALS)
    for english, key in lang._REFUSALS.items():
        assert lang._COPY["en"][key] == english
        assert _CJK.search(lang.service_refusal(english, lang.phrases("zh")))
    # The one refusal with blanks: the English side reproduces it exactly.
    assert lang.service_refusal(unknown, lang.phrases("en")) == unknown
    zh = lang.service_refusal(unknown, lang.phrases("zh"))
    assert zh.startswith("未知的服务商 'deepmind'") and "ark" in zh
    # Anything else is printed as raised.
    assert lang.service_refusal("disk full", lang.phrases("zh")) == "disk full"


# --------------------------------------------------------------------------
# `openai4s doctor`
# --------------------------------------------------------------------------


@pytest.fixture
def fixed_disk(monkeypatch):
    """The free space doctor reports, held still so two runs can be compared."""
    usage = types.SimpleNamespace(total=500e9, used=100e9, free=400e9)
    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda _path: usage)


def _doctor(monkeypatch, capsys, *argv, **speak):
    _speak(monkeypatch, **speak)
    rc = _cli().main(["doctor", *argv])
    captured = capsys.readouterr()
    return rc, captured.out


@pytest.mark.parametrize(
    "speak",
    [{"override": "zh"}, {"override": "zh_CN.UTF-8"}, {"LANG": "zh_CN.UTF-8"}],
    ids=["OPENAI4S_LANG=zh", "OPENAI4S_LANG=zh_CN.UTF-8", "LANG=zh_CN.UTF-8"],
)
def test_doctor_speaks_chinese_by_override_or_locale(
    monkeypatch, capsys, fixed_disk, speak
):
    rc, out = _doctor(monkeypatch, capsys, **speak)
    rc_en, out_en = _doctor(monkeypatch, capsys, override="en", LANG="zh_CN.UTF-8")

    assert rc == rc_en  # the verdict is the exit code, in every language
    verdict = out.rstrip().splitlines()[-1]
    assert verdict in {
        "所有检查都已通过。",
        "可以使用，但有上面列出的降级。",
        "尚未就绪：至少有一项检查失败。",
    }
    # Every check line is said in Chinese; marks and check names are not.
    lines = [line for line in out.splitlines() if line.startswith("[")]
    assert [line.split("]")[1].split()[0] for line in lines] == [
        name for name, _probe in doctor._CHECKS
    ]
    for line in lines:
        assert line[:6] in {"[ok  ]", "[warn]", "[FAIL]"}
        assert _CJK.search(line), line
    assert out_en.rstrip().splitlines()[-1] in {
        "All checks passed.",
        "Usable, with degradations noted above.",
        "Not ready: at least one check failed.",
    }
    assert not _CJK.search(out_en)


def test_doctor_json_is_byte_identical_in_both_languages(
    monkeypatch, capsys, fixed_disk
):
    rc_zh, zh = _doctor(monkeypatch, capsys, "--json", override="zh")
    rc_locale, locale = _doctor(monkeypatch, capsys, "--json", LANG="zh_CN.UTF-8")
    rc_en, en = _doctor(monkeypatch, capsys, "--json", override="en")

    assert zh == locale == en
    assert rc_zh == rc_locale == rc_en
    assert not _CJK.search(en)
    assert json.loads(en)["checks"]


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "skills").mkdir()
    return Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="chatgpt", api_key="test-key", model="test-model"),
    )


def _refusing(status):
    def send(_llm):
        from openai4s.llm.models import TransportError

        raise TransportError("LLM HTTP error", provider="chatgpt", status=status)

    return send


@pytest.mark.parametrize(
    ("live_model", "status", "zh_phrase", "en_phrase"),
    [
        (None, doctor.OK, "未验证：没有向服务商发送请求", "not verified: no request"),
        (lambda _llm: None, doctor.OK, "凭据已验证", "credential verified"),
        (
            _refusing(401),
            doctor.FAIL,
            "拒绝了已配置的 API Key",
            "rejected the configured",
        ),
        (_refusing(404), doctor.FAIL, "没有模型 'test-model'", "has no model"),
        (_refusing(500), doctor.WARN, "返回 HTTP 500", "answered HTTP 500"),
        (_refusing(None), doctor.WARN, "无法连接", "could not reach"),
    ],
)
def test_each_model_verdict_is_said_in_both_languages(
    cfg, live_model, status, zh_phrase, en_phrase
):
    """The model line is rebuilt after the test request; the Chinese form
    must follow it there too, not keep the "not verified" it started with."""
    check = doctor._model(cfg)
    if live_model is not None:
        check = doctor._verify_model(cfg, check, live_model)
    zh, en = check.public("zh"), check.public()

    assert zh["status"] == en["status"] == status
    assert zh["facts"] == en["facts"]
    assert zh_phrase in zh["detail"] and en_phrase in en["detail"]
    assert not _CJK.search(en["detail"] + en["remedy"])
    if en["remedy"]:
        assert _CJK.search(zh["remedy"])


def test_the_chinese_report_is_the_english_one_in_other_words(cfg, monkeypatch):
    for var in ("OPENAI4S_LLM_API_KEY", "OPENAI4S_DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    cfg.llm.api_key = ""
    monkeypatch.setenv("OPENAI4S_KERNEL_SANDBOX", "off")

    en = doctor.report(cfg)
    zh = doctor.report(cfg, language="zh")

    assert en["status"] == zh["status"]
    for english, chinese in zip(en["checks"], zh["checks"]):
        assert english["name"] == chinese["name"]
        assert english["status"] == chinese["status"]
        assert english["facts"] == chinese["facts"]
        assert _CJK.search(chinese["detail"]), chinese
        assert bool(english["remedy"]) == bool(chinese["remedy"])
        if chinese["remedy"]:
            assert _CJK.search(chinese["remedy"]), chinese
    # The default stays English: the web route and `--json` read it.
    assert not _CJK.search(json.dumps(en, ensure_ascii=False))
    rendered = doctor.render(zh, language="zh")
    assert "尚未就绪" in rendered and "-> 设置 OPENAI4S_CHATGPT_API_KEY" in rendered


def test_a_timed_out_host_is_said_in_chinese_without_changing_its_facts():
    answer = {
        "host": "rest.uniprot.org",
        "reachable": False,
        "attempts": 2,
        "timed_out": True,
        "error": "timed out: no answer within 10 s, on 2 tries",
    }
    result = {
        "status": doctor.WARN,
        "checks": [
            {
                "name": "connectors",
                "status": doctor.WARN,
                "detail": "d",
                "remedy": "r",
                "facts": {"reachability": {"uniprot": answer}},
            }
        ],
    }

    assert "FAIL  timed out: no answer within 10 s" in doctor.render(result)
    assert "超时：两次尝试都没有在 10 秒内得到回应" in doctor.render(
        result, language="zh"
    )
    assert answer["error"].startswith("timed out")


# --------------------------------------------------------------------------
# `openai4s init`
# --------------------------------------------------------------------------


class _Terminal:
    def isatty(self) -> bool:
        return True


@pytest.fixture
def ark_onboarding(monkeypatch, tmp_path):
    """`init` against a real Store whose current provider is one this build
    has (the suite's fake `deepseek` is not), as tests/test_cli_setup_usability
    drives it."""
    from openai4s.llm import PROVIDERS
    from openai4s.onboarding import OnboardingService
    from openai4s.store import get_store

    cfg = Config(data_dir=tmp_path / "init-data", llm=LLMConfig(provider="ark"))
    cfg.ensure_dirs()

    def service():
        store = get_store(cfg.db_path)
        return OnboardingService(cfg, store, PROVIDERS), store

    monkeypatch.setattr(_cli(), "_onboarding_service", service)


def _init(monkeypatch, capsys, *argv, answers=(), secrets=(), piped=None, **speak):
    _speak(monkeypatch, **speak)
    cli = _cli()
    prompts: list[str] = []
    typed, hidden = iter(answers), iter(secrets)

    def fake_input(prompt=""):
        prompts.append(prompt)
        try:
            return next(typed)
        except StopIteration:
            raise EOFError from None

    def fake_getpass(prompt=""):
        prompts.append(prompt)
        return next(hidden)

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(cli.getpass, "getpass", fake_getpass)
    monkeypatch.setattr(
        cli.sys, "stdin", io.StringIO(piped) if piped is not None else _Terminal()
    )
    rc = cli.main(["init", *argv])
    captured = capsys.readouterr()
    return types.SimpleNamespace(
        rc=rc, out=captured.out, err=captured.err, prompts=prompts
    )


def test_the_init_wizard_asks_and_answers_in_chinese(
    monkeypatch, capsys, ark_onboarding
):
    run = _init(
        monkeypatch,
        capsys,
        answers=["opena1", "openai", "", "", "maybe", "y", "9", "5"],
        secrets=["sk-test-cli35"],
        LANG="zh_CN.UTF-8",
    )

    assert run.rc == 0, run.err
    assert "OpenAI4S 首次设置" in run.out
    assert "服务商（输入左侧的 id，或它旁边的名称）：" in run.out
    # The names a person types stay as typed; what speaks them is translated.
    assert "OpenAI-compatible（/chat/completions：OpenAI、DeepSeek" in run.out
    assert "未知的服务商 'opena1'" in run.out
    assert "使用服务商 chatgpt" in run.out
    assert "请输入 y 或 n" in run.out
    assert "请输入 1 到 5 之间的数字" in run.out
    assert run.prompts[0].startswith("服务商 [")
    assert run.prompts[0].endswith("：")
    assert "现在配置 API Key 吗？[y/N]：" in run.prompts
    assert "API Key（输入时不显示）：" in run.prompts
    assert "已配置 chatgpt /" in run.out
    assert "网页搜索：" in run.out and "parallel" in run.out
    assert "下一步：openai4s serve" in run.out
    assert "Configured" not in run.out and "Provider" not in "".join(run.prompts)


def test_init_says_its_refusals_in_chinese_with_the_same_exit_code(
    monkeypatch, capsys, ark_onboarding
):
    for argv, phrase in (
        (
            ["--search-key-stdin"],
            "错误：--search-key-stdin 需要配合 --search doubao|tavily|you",
        ),
        (
            ["--base-url", "ftp://example.test"],
            "错误：Base URL 必须是完整的 http(s) 地址",
        ),
        (["--provider", "deepmind"], "错误：未知的服务商 'deepmind'"),
    ):
        zh = _init(
            monkeypatch, capsys, "--non-interactive", *argv, piped="k\n", override="zh"
        )
        en = _init(
            monkeypatch, capsys, "--non-interactive", *argv, piped="k\n", override="en"
        )
        assert zh.rc == en.rc == 1
        assert phrase in zh.err
        assert en.err.startswith("error: ") and not _CJK.search(en.err)

    ended = _init(monkeypatch, capsys, answers=["nonsense"], override="zh")
    assert ended.rc == 1
    assert "错误：设置完成之前输入就结束了" in ended.err


def test_init_without_a_terminal_says_so_in_chinese(
    monkeypatch, capsys, ark_onboarding
):
    run = _init(monkeypatch, capsys, piped="", LANG="zh_CN.UTF-8")

    assert run.rc == 0
    assert run.err.startswith("提示：stdin 不是终端，所以不会提问")
    assert "已配置 ark /" in run.out


def test_init_json_is_byte_identical_in_both_languages(
    monkeypatch, capsys, ark_onboarding
):
    argv = (
        "--non-interactive",
        "--provider",
        "claude",
        "--search",
        "builtin",
        "--json",
    )
    zh = _init(monkeypatch, capsys, *argv, piped="", override="zh")
    locale = _init(monkeypatch, capsys, *argv, piped="", LANG="zh_CN.UTF-8")
    en = _init(monkeypatch, capsys, *argv, piped="", override="en")

    assert zh.out == locale.out == en.out
    assert zh.rc == locale.rc == en.rc == 0
    assert json.loads(en.out)["provider"] == "claude"
    assert not _CJK.search(en.out)


# --------------------------------------------------------------------------
# `openai4s setup`
# --------------------------------------------------------------------------


def _setup(monkeypatch, capsys, tmp_path, *argv, tool="micromamba", rc=0, **speak):
    _speak(monkeypatch, **speak)
    cli = _cli()
    for name in ("python", "r"):
        (tmp_path / f"{name}.yml").write_text(f"name: {name}\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_find_conda_tool", lambda: tool)
    monkeypatch.setattr(cli, "_envs_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "_existing_envs", lambda: {"r": tmp_path / "envs" / "r"})
    ran = []

    def fake_run(cmd, *args, **kwargs):
        ran.append(cmd)
        return subprocess.CompletedProcess(cmd, rc)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    code = cli.main(["setup", *argv])
    captured = capsys.readouterr()
    return types.SimpleNamespace(rc=code, out=captured.out, err=captured.err, ran=ran)


def test_setup_speaks_chinese_and_keeps_commands_and_exit_codes(
    monkeypatch, capsys, tmp_path
):
    dry = _setup(
        monkeypatch,
        capsys,
        tmp_path,
        "--profile",
        "standard",
        "--dry-run",
        LANG="zh_CN.UTF-8",
    )
    assert dry.rc == 0
    assert "（仅预演，不做改动）" in dry.out
    assert (
        "[python] 将在 micromamba 自己的环境目录中创建 conda 环境 'python'："
        "micromamba env create -f" in dry.out
    )
    assert "[r] conda 环境 'r' 已存在于" in dry.out

    for rc, phrase, stream in ((0, "已更新", "out"), (1, "失败（退出码 1）", "err")):
        zh = _setup(
            monkeypatch,
            capsys,
            tmp_path,
            "--only",
            "r",
            "--update",
            rc=rc,
            override="zh",
        )
        en = _setup(
            monkeypatch,
            capsys,
            tmp_path,
            "--only",
            "r",
            "--update",
            rc=rc,
            override="en",
        )
        assert zh.rc == en.rc == (1 if rc else 0)
        assert zh.ran == en.ran  # the same conda command, whatever is said
        assert phrase in getattr(zh, stream)
        assert "完成：创建 0 个，更新" in zh.out
        assert "done: 0 created" in en.out
        assert not _CJK.search(en.out + en.err)


def test_setup_without_conda_says_so_in_chinese(monkeypatch, capsys, tmp_path):
    run = _setup(monkeypatch, capsys, tmp_path, tool=None, override="zh_CN")

    assert run.rc == 1
    assert run.err.splitlines()[0] == "错误：PATH 上找不到 conda/mamba/micromamba。"
    assert "`openai4s setup`" in run.err
