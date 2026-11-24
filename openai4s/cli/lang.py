"""The language the CLI speaks to a person: Chinese or English.

``openai4s init``, ``openai4s doctor`` and ``openai4s setup`` print their human
prose in Chinese or English; every other subcommand stays English for now.
Only prose changes. ``--json`` output, exit codes, command names, flags,
environment variable names, paths and package names are the same in both.

The language is decided here, once, by one rule:

1. ``OPENAI4S_LANG`` when it is set: ``zh`` (``zh_CN``, ``zh-CN``,
   ``zh_CN.UTF-8``, ...) or ``en``. Any other non-empty value means English;
   an explicit choice is never overruled by the locale.
2. Otherwise the POSIX locale variables in their own precedence: the first
   non-empty one of ``LC_ALL``, ``LC_MESSAGES``, ``LANG`` decides.
3. Nothing set, ``C``/``POSIX``, or a language without a translation: English.

Plain environment reads only. On macOS a shell that exports no ``LANG`` gets
English rather than a ``defaults read`` subprocess for the system language.

The words themselves are in `_COPY` below, one key per sentence, with the
English and the Chinese side by side so neither can drift out of the other's
sight. ``doctor``'s check sentences live in `openai4s.doctor`'s own table, next
to the checks that say them.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping

#: A tag names Chinese or English when it is the language itself, or the
#: language followed by a territory, codeset or modifier (``zh_TW``,
#: ``zh-Hans``, ``en_US.UTF-8``, ``zh_CN@pinyin``). Same rule as the
#: workbench's browser-language pick.
_ZH = re.compile(r"zh(?:[-_.@]|\Z)", re.IGNORECASE)
_EN = re.compile(r"en(?:[-_.@]|\Z)", re.IGNORECASE)


def _named_language(value: str) -> str | None:
    """``zh`` or ``en`` when ``value`` names one of them, else None."""
    text = value.strip()
    if _ZH.match(text):
        return "zh"
    if _EN.match(text):
        return "en"
    return None


def cli_language(environ: Mapping[str, str] | None = None) -> str:
    """``"zh"`` or ``"en"``: the language this process speaks to a person."""
    env = os.environ if environ is None else environ
    override = str(env.get("OPENAI4S_LANG") or "").strip()
    if override:
        return _named_language(override) or "en"
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = str(env.get(name) or "").strip()
        if value:
            return _named_language(value) or "en"
    return "en"


#: Every sentence `init` and `setup` print, keyed, in both languages. The two
#: sides hold the same keys (`tests/test_cli_lang.py` holds them to it).
_COPY: dict[str, dict[str, str]] = {
    "en": {
        "error": "error: {message}",
        # init
        "init.one_key_per_run": (
            "stdin carries one key per run: use --api-key-stdin or "
            "--search-key-stdin, not both"
        ),
        "init.search_key_needs_engine": (
            "--search-key-stdin needs --search {engines} to say which engine "
            "the key is for"
        ),
        "init.search_takes_no_key": " (--search {choice} takes no key)",
        "init.not_a_terminal": (
            "note: stdin is not a terminal, so no questions are asked; saving "
            "the options given and the provider defaults, as --non-interactive "
            "does"
        ),
        "init.title": "OpenAI4S first-run setup",
        "init.providers": (
            "Providers (type the id on the left, or the name beside it):"
        ),
        "init.provider_line": "  {provider:<17} {name} ({what})",
        "init.what.ark": "Volcengine Ark: Doubao, GLM, Kimi, DeepSeek",
        "init.what.chatgpt": (
            "/chat/completions: OpenAI, DeepSeek, vLLM, OpenRouter, ..."
        ),
        "init.what.openai_responses": "OpenAI /responses",
        "init.what.claude": "Anthropic /v1/messages",
        "init.what.gemini": "Google generateContent",
        "init.what.ollama": "cloud or local models",
        "init.prompt": "{label}{default}: ",
        "init.prompt_default": " [{default}]",
        "init.label.provider": "Provider",
        "init.label.model": "Model",
        "init.label.base_url": "Base URL",
        "init.unknown_provider": "  unknown provider {typed!r}; enter one of: {known}",
        "init.using_provider": "  using provider {provider}",
        "init.ask_api_key": "Configure an API key now? [y/N]: ",
        "init.api_key": "API key (input hidden): ",
        "init.answer_y_or_n": "  answer y or n",
        "init.search.title": (
            "Web search (Doubao is tried before the backups whenever an Agent "
            "Plan key is stored, an Ark Agent Plan model key included; 2-5 "
            "choose the backups):"
        ),
        "init.search.menu1": (
            "  1) Doubao (Agent Plan key)  2) Tavily first (API key)  "
            "3) You.com first (free; key optional)"
        ),
        "init.search.menu2": (
            "  4) backups: built-in keyless engines only (Doubao, if "
            "configured, is still tried first)  5) Parallel first (keyless)"
        ),
        "init.search.choose": (
            "Choose 1-5, or press Enter to keep the current setup: "
        ),
        "init.search.again": (
            "  enter a number from 1 to 5, or press Enter to keep the current " "setup"
        ),
        "init.search.key.doubao": "Agent Plan key (input hidden): ",
        "init.search.key.tavily": "Tavily API key (input hidden): ",
        "init.search.key.you": (
            "You.com API key (optional; Enter for the free tier, input hidden): "
        ),
        "init.input_ended": "input ended before setup was finished",
        "init.configured": "Configured {provider} / {model}",
        "init.stored_in": "Settings stored in {data_dir}",
        "init.no_api_key": (
            "No API key stored; add one in Settings → Models after launch."
        ),
        "init.web_search": "Web search: {lead}{order}",
        "init.web_search_doubao_first": "Doubao, then ",
        "init.no_native_windows": (
            "Native Windows kernels are unsupported; run OpenAI4S under WSL2."
        ),
        "init.next": "Next: openai4s serve (or ./start.sh in the OpenAI4S folder)",
        # what the services `init` calls refuse with (see `service_error`)
        "init.refused.model_empty": "model must not be empty",
        "init.refused.base_url_not_absolute": (
            "base URL must be an absolute http(s) URL"
        ),
        "init.refused.base_url_credentials": "base URL must not contain credentials",
        "init.refused.base_url_query": (
            "base URL must not contain a query or fragment"
        ),
        "init.refused.api_key_placeholder": "API key is empty or a placeholder",
        "init.refused.doubao_key": "Doubao search needs an Agent Plan key",
        "init.refused.tavily_key": "Tavily needs an API key",
        "init.refused.unknown_provider": (
            "unknown provider {provider}; choose one of: {known}"
        ),
        # setup
        "setup.no_conda": "error: no conda/mamba/micromamba found on PATH.",
        "setup.install_conda": (
            "       install one (e.g. micromamba) and re-run `openai4s setup`."
        ),
        "setup.no_envs_dir": "error: envs directory not found: {path}",
        "setup.unknown_env": "error: unknown env '{name}' (choices: {choices})",
        "setup.using": "using '{tool}' to manage envs from {envs_dir}{dry_run}",
        "setup.dry_run": " (dry-run)",
        "setup.spec_missing": "  [{name}] skip: spec file missing ({yml})",
        "setup.exists": (
            "  [{name}] conda env '{name}' already exists at {prefix} — "
            "skipping (use --update to sync it)"
        ),
        "setup.would_create": (
            "  [{name}] would create conda env '{name}' in {tool}'s own envs "
            "directory: {cmd}"
        ),
        "setup.would_update": (
            "  [{name}] would update conda env '{name}' at {prefix}: {cmd}"
        ),
        "setup.creating": "  [{name}] create… ({cmd})",
        "setup.updating": "  [{name}] update… ({cmd})",
        "setup.error": "  [{name}] error: {error}",
        "setup.created": "  [{name}] created",
        "setup.updated": "  [{name}] updated",
        "setup.failed": "  [{name}] FAILED (exit {rc})",
        "setup.done": (
            "done: {created} created, {updated} updated, {skipped} skipped, "
            "{failed} failed"
        ),
    },
    "zh": {
        "error": "错误：{message}",
        # init
        "init.one_key_per_run": (
            "stdin 每次只能传入一个 Key：请用 --api-key-stdin 或 "
            "--search-key-stdin，不要同时使用"
        ),
        "init.search_key_needs_engine": (
            "--search-key-stdin 需要配合 --search {engines}，说明这个 Key "
            "属于哪个搜索引擎"
        ),
        "init.search_takes_no_key": "（--search {choice} 不需要 Key）",
        "init.not_a_terminal": (
            "提示：stdin 不是终端，所以不会提问；与 --non-interactive 一样，"
            "只保存给出的选项和服务商的默认值"
        ),
        "init.title": "OpenAI4S 首次设置",
        "init.providers": "服务商（输入左侧的 id，或它旁边的名称）：",
        "init.provider_line": "  {provider:<17} {name}（{what}）",
        "init.what.ark": "火山方舟 Ark：豆包、GLM、Kimi、DeepSeek",
        "init.what.chatgpt": (
            "/chat/completions：OpenAI、DeepSeek、vLLM、OpenRouter 等"
        ),
        "init.what.openai_responses": "OpenAI /responses",
        "init.what.claude": "Anthropic /v1/messages",
        "init.what.gemini": "Google generateContent",
        "init.what.ollama": "云端或本机模型",
        "init.prompt": "{label}{default}：",
        "init.prompt_default": " [{default}]",
        "init.label.provider": "服务商",
        "init.label.model": "模型",
        "init.label.base_url": "Base URL",
        "init.unknown_provider": "  未知的服务商 {typed!r}；请输入其中之一：{known}",
        "init.using_provider": "  使用服务商 {provider}",
        "init.ask_api_key": "现在配置 API Key 吗？[y/N]：",
        "init.api_key": "API Key（输入时不显示）：",
        "init.answer_y_or_n": "  请输入 y 或 n",
        "init.search.title": (
            "网页搜索（只要保存了 Agent Plan Key，包括方舟 Agent Plan 的模型 "
            "Key，就会先用豆包搜索，再用备用引擎；2-5 选择备用引擎）："
        ),
        "init.search.menu1": (
            "  1) 豆包搜索（需要 Agent Plan Key）  2) Tavily 优先（需要 API Key）  "
            "3) You.com 优先（免费，Key 可选）"
        ),
        "init.search.menu2": (
            "  4) 备用引擎只用内置免 Key 引擎（若已配置豆包搜索，仍会先用它）  "
            "5) Parallel 优先（免 Key）"
        ),
        "init.search.choose": "输入 1-5 选择，或直接按回车保留当前设置：",
        "init.search.again": "  请输入 1 到 5 之间的数字，或直接按回车保留当前设置",
        "init.search.key.doubao": "Agent Plan Key（输入时不显示）：",
        "init.search.key.tavily": "Tavily API Key（输入时不显示）：",
        "init.search.key.you": (
            "You.com API Key（可选；直接按回车使用免费额度，输入时不显示）："
        ),
        "init.input_ended": "设置完成之前输入就结束了",
        "init.configured": "已配置 {provider} / {model}",
        "init.stored_in": "设置已保存在 {data_dir}",
        "init.no_api_key": "没有保存 API Key；启动后可在 设置 → 模型 里添加。",
        "init.web_search": "网页搜索：{lead}{order}",
        "init.web_search_doubao_first": "豆包搜索，然后 ",
        "init.no_native_windows": "不支持原生 Windows 内核；请在 WSL2 中运行 OpenAI4S。",
        "init.next": "下一步：openai4s serve（或在 OpenAI4S 文件夹里运行 ./start.sh）",
        "init.refused.model_empty": "模型名称不能为空",
        "init.refused.base_url_not_absolute": "Base URL 必须是完整的 http(s) 地址",
        "init.refused.base_url_credentials": "Base URL 不能包含凭据",
        "init.refused.base_url_query": "Base URL 不能包含查询参数或片段（? 或 #）",
        "init.refused.api_key_placeholder": "API Key 为空，或只是占位符",
        "init.refused.doubao_key": "豆包搜索需要 Agent Plan Key",
        "init.refused.tavily_key": "Tavily 需要 API Key",
        "init.refused.unknown_provider": "未知的服务商 {provider}；可选：{known}",
        # setup
        "setup.no_conda": "错误：PATH 上找不到 conda/mamba/micromamba。",
        "setup.install_conda": (
            "      请安装其中一个（例如 micromamba），然后重新运行 `openai4s setup`。"
        ),
        "setup.no_envs_dir": "错误：找不到环境定义目录：{path}",
        "setup.unknown_env": "错误：未知的环境 '{name}'（可选：{choices}）",
        "setup.using": "使用 '{tool}' 按 {envs_dir} 中的定义管理环境{dry_run}",
        "setup.dry_run": "（仅预演，不做改动）",
        "setup.spec_missing": "  [{name}] 跳过：缺少定义文件（{yml}）",
        "setup.exists": (
            "  [{name}] conda 环境 '{name}' 已存在于 {prefix}，跳过"
            "（加 --update 可同步它）"
        ),
        "setup.would_create": (
            "  [{name}] 将在 {tool} 自己的环境目录中创建 conda 环境 '{name}'：{cmd}"
        ),
        "setup.would_update": "  [{name}] 将更新位于 {prefix} 的 conda 环境 '{name}'：{cmd}",
        "setup.creating": "  [{name}] 正在创建…（{cmd}）",
        "setup.updating": "  [{name}] 正在更新…（{cmd}）",
        "setup.error": "  [{name}] 错误：{error}",
        "setup.created": "  [{name}] 已创建",
        "setup.updated": "  [{name}] 已更新",
        "setup.failed": "  [{name}] 失败（退出码 {rc}）",
        "setup.done": (
            "完成：创建 {created} 个，更新 {updated} 个，跳过 {skipped} 个，"
            "失败 {failed} 个"
        ),
    },
}


def phrases(language: str | None = None) -> Callable[..., str]:
    """``say(key, **params)``: the CLI's sentence ``key`` in one language.

    ``language`` defaults to `cli_language()`. Resolve it once per command
    and pass the result down, so one run never mixes the two.
    """
    table = _COPY["zh" if (language or cli_language()) == "zh" else "en"]

    def say(key: str, **params: object) -> str:
        return table[key].format(**params)

    return say


#: The refusals `OnboardingService` and `search_settings` raise for `init`,
#: by their exact English text. Those services also answer the Web wizard, so
#: they keep raising English; `init` says the same refusal in its own
#: language. A message not listed here is printed as raised.
_REFUSALS = {
    "model must not be empty": "init.refused.model_empty",
    "base URL must be an absolute http(s) URL": "init.refused.base_url_not_absolute",
    "base URL must not contain credentials": "init.refused.base_url_credentials",
    "base URL must not contain a query or fragment": "init.refused.base_url_query",
    "API key is empty or a placeholder": "init.refused.api_key_placeholder",
    "Doubao search needs an Agent Plan key": "init.refused.doubao_key",
    "Tavily needs an API key": "init.refused.tavily_key",
}
_UNKNOWN_PROVIDER = re.compile(
    r"unknown provider (?P<provider>.+); choose one of: (?P<known>.*)\Z", re.DOTALL
)


def service_refusal(message: str, say: Callable[..., str]) -> str:
    """A service's English refusal ``message``, said with ``say``."""
    key = _REFUSALS.get(message)
    if key is not None:
        return say(key)
    unknown = _UNKNOWN_PROVIDER.match(message)
    if unknown is not None:
        return say("init.refused.unknown_provider", **unknown.groupdict())
    return message


__all__ = ["cli_language", "phrases", "service_refusal"]
