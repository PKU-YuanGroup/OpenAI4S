# 工具

[English](README.md)

不属于 Python 包的开发者与用户侧工具。

`openai4s/` 下的一切都会打进 wheel 并在运行时被导入；这里的东西不会。它通过
另一条渠道分发——目前是 npm——并刻意不写进
`[tool.setuptools.packages.find]`，因此 `pip install openai4s` 不会带上任何
JavaScript。

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`skills-installer/`](skills-installer/) | `npx openai4s-skills` 背后的 `openai4s-skills` 命令。它把本仓库自带的 Skill 库复制到 Claude Code、OpenAI4S 数据目录，或用户指定的任意目录——并写下清单，使卸载精确、覆盖默认拒绝。 |

## 它处在什么位置

仓库根目录的 `package.json` 声明了这个 npm 包：`bin` 指向
`skills-installer/cli.mjs`，`files` 列表把该目录连同 `skills/` 一起发布。把清单
放在根目录，正是 `npx github:PKU-YuanGroup/OpenAI4S` 无需先发布任何东西即可
工作的原因——这是从"我们的地址"到一次可用安装的最短路径。

这里的任何东西都不会被 daemon、kernel 或任何 Python 测试导入——语言边界让双向导入
都不可能。安装器自己的关卡是 `node tools/skills-installer/selftest.mjs` 和
`node tools/skills-installer/check_package.mjs`，各自作为独立的 CI job 运行，好让其中
一个变红时挡不住另一个：「安装器行为是否正确」和「发布出去的包里到底有没有东西可装」
是两个不同的问题。契约中位于 Python 一侧的那一半——`skills/` 的目录形态，以及决定
发布包内容的 `package.json` 清单——由 `tests/test_skills_installer_contract.py`
断言；它刻意不去调用 `node`：一个在没装 Node 的机器上直接跳过的测试，是以错误的
理由汇报成功。
