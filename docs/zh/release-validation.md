---
title: 发布验证
description: 源码、归档、wheel 与隔离安装的验证门槛。
outline: deep
status: current
audience: [contributors, operators]
verified_commit: a92e736
last_verified: 2026-07-14
owner: OpenAI4S maintainers
---

# 发布验证

> 已于 2026-07-14 对仓库 revision `a92e736` 完成核验。

OpenAI4S 把可安装 artifact 视为独立于源码 checkout 的契约。源码树测试通过仍不够：wheel 必须包含 Web 工作台、R worker、compute template、内置 Skills 和 conda 环境规范，并且在未安装可选科学包时仍能导入。

## 本地门槛

构建前运行源码扫描。它检查 Git-tracked 和 non-ignored 文件，不在输出中暴露匹配到的值，并为解包后的 source archive 提供确定性的文件系统 fallback。

```bash
python scripts/source_secret_scan.py
uv build --no-sources --out-dir dist --clear
python scripts/verify_release_artifacts.py dist
```

随后在全新环境中安装 wheel，不解析或下载 runtime dependency。从 checkout 外运行 smoke script，防止 editable/source import 造成假阳性。

```bash
python -m venv /tmp/openai4s-release-venv
/tmp/openai4s-release-venv/bin/python -m pip install \
  --no-index --no-deps dist/openai4s-*.whl
(cd /tmp && env -u PYTHONPATH \
  /tmp/openai4s-release-venv/bin/python \
  "$OLDPWD/scripts/release_import_smoke.py")
```

Build backend 由 `pyproject.toml` 声明，在全新机器上可能需要由 `uv` bootstrap。Artifact verification、wheel 安装及 import/CLI smoke 均不使用 package index 或应用凭据。

## 强制契约

`.github/workflows/ci.yml` 中的 release job 在 Pull Request、推送到 `main`/`next`、nightly schedule 和 manual dispatch 时运行，并强制检查：

- release source 中没有 credential-shaped token 或 private-key material；
- 恰好一个 wheel 和一个 sdist，archive path 安全；
- 两种 archive 中都没有 `.env`、VCS metadata、cache directory 或 bytecode；
- `Requires-Python >=3.10`、`py3-none-any` wheel 及 `openai4s` console entry point；
- 没有非 extra 的 `Requires-Dist` metadata（core 保持零依赖）；
- 包含 Web UI、R、compute、Skills、environment、provider SDK 与 worker runtime 资源；
- 通过 `pip --no-index --no-deps` 安装、代表性架构 import、installed-resource 检查和隔离的 `python -m openai4s --help`。

常规 CI browser smoke 和 nightly macOS Seatbelt smoke 保持独立，因为它们验证的是 runtime/browser 与操作系统边界，而非 archive 完整性。

## 有意保留的外部门槛

本仓库不声称离线 CI 会执行 package publication、release signing/notarization、vulnerability-database lookup，或 live provider、GPU、SSH 和 laboratory validation。这些操作需要明确的 release identity、网络服务、凭据或硬件，必须留在单独授权的 release workflow 中，并在公开分发前由 maintainer 完成。
