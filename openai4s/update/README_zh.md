# 在线更新

[English](README.md)

本阶段提供安装渠道识别、版本发现和载荷校验。apply 事务、CLI 与 HTTP 路由属于后续工作，当前尚未提供。

`openai4s update` 负责替换这个 daemon 正在跑的代码，同时不丢它攒下来的历史与配置。后半句才是难的，而难在哪里这个仓库早就写下来过：[`../storage/migrations.py`](../storage/migrations.py) 里的 migration 只能向前、没有反向步骤，旧的二进制在每一个入口都会拒绝更新过的数据库，而 migration runner 在升级提交的那一刻就删掉了自己的升级前副本。这个包里的一切，都是围着一个原语做的管道：一份由更新器自己拿、并且自己留着的、独立的升级前快照。

这里没有任何东西能从一个 turn 到达。任何 `Tool` 子类、任何 `host.*` 能力、任何 Skill 都不得 import 这个包：一个能从模型 turn 到达的安装器，会把每一次 prompt 注入都变成持久化的代码执行。这个包的 import 也被构造成很便宜——[`__init__.py`](__init__.py) 用 PEP 562 的模块级 `__getattr__` 解析全部公开名字、模块层不 import 任何东西，于是 `import openai4s.update` 既不会拉进事务，也不会打开 socket。

## 它在整体里的位置

1. [`channel.py`](channel.py) 回答「这是哪一种安装」。它是纯的：只读环境变量和文件系统，不联网、不起子进程、不碰 Store。识别不出来的安装就是 `unknown`——那是一次拒绝，不是一次猜测。
2. [`discovery.py`](discovery.py) 回答「有没有更新的版本」。一个版本判据（pypi.org 的 project 文档）加两个摘要证人（按版本的文档，以及 release 的 `SHA256SUMS`）。每一种失败都归到 `unknown`；没有任何一种会归到「已是最新」。
3. [`verify.py`](verify.py) 回答「在停任何东西、换任何东西之前，这份载荷必须满足什么」。摘要文法、双证人一致、压缩包成员校验、wheel 结构门，以及把新代码放到进程外真的跑一遍——全部发生在 daemon 被静默之前，因为一次坏构建绝不该换来一次重启。
4. 之后才是 apply 事务：停 daemon、快照、提交、重启、写 journal。它落在 `store.py`、`preserve.py`、`apply.py`、`commit.py` 和 `applier.py` 里。

出站面是**靠机制**冻住的：这个包里没有任何模块写出 `urlopen`、`Request`、`build_opener` 或 `create_connection`。每一个字节都走 [`../webtools.py`](../webtools.py)——它是全树唯一手工跟随重定向、并在每一跳都重新套用出站白名单与 SSRF 保护的代码。两个来源都会重定向，所以一个在内部自动跟随重定向的客户端，检查的是第一跳，信任的是其余各跳。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 惰性的公开面（`detect`、`check`、`plan`、`apply_update`、`rollback`、`recover`、`prune`、`Channel`）以及两个异常类型。异常在这里定义而不是再导出，因为惰性解析出来的异常类是抓不住的。注意 docstring 点明的那一处名字撞车：`openai4s.update.apply` 既是模块也是可调用对象，所以要用 `apply_update` 来调。 |
| [`channel.py`](channel.py) | `detect(cfg) -> Channel`，按顺序解析：显式的 `OPENAI4S_CHANNEL`、容器、macOS `.app`、WSL 托管 bundle、Linux 可重定位 bundle、源码 checkout、venv、unknown。容器探针看四个正向信号而不是两个：containerd 和 CRI-O——也就是 Kubernetes 真正跑的东西——既不写 `/.dockerenv` 也不写 `/run/.containerenv`，而一个以 root 跑的 Pod 里 site-packages 是可写的，于是 venv 探针会命中，更新会落进一个下次重启就丢掉的镜像层。每一个分支都把命中的探针和做出判断的绝对路径记进 `Channel.evidence`；它是管理员可见的，`as_dict()` 默认不带它，除非调用方明确要。`require_self_update` 是拒绝一个被拒绝渠道的唯一地方。 |
| [`discovery.py`](discovery.py) | `check(cfg)` 及其周边词汇：`STATUSES`（三个成员）、`REASONS`（冻结的失败码）、`ReleaseSource` 接缝、`SHA256SUMS` 解析、不依赖 `packaging` 的版本比较、`OPENAI4S_UPDATE_INDEX` 的校验，以及 `<data_dir>/updates/check.json` 的 6 小时缓存（失败后的重试间隔要短得多）。失败会覆盖缓存——`retry_after_at` 正是那个阻止「网断了就每次都重拨」的东西——所以失败文档里会带上最近一次**真是答案**的答案，放在 `previous` 下；这就是界面能打出「上次已知 0.4.0，三小时前查的」的依据。`OPENAI4S_UPDATE_SOURCE=offline` 会在读缓存之前、解析 source 之前就短路。 |
| [`verify.py`](verify.py) | `REFUSAL_CODES`、摘要文法、`Witnesses`/`agree`/`digest_for`、`validate_zip`/`validate_tar`、`wheel_structure`、`extract_wheel`、`rehash`，以及 `probe_installation`。`Witnesses` 被封了口，只有 `agree()` 能造出来——这正是让那条顺序规则变成结构性的原因：`SHA256SUMS` 里某个 bundle 的摘要，只有在 wheel 已经和 PyPI 对上之后才会被采信。 |

严格发现模式（`allow_single_witness=False`）始终重新获取摘要证人，即使已有普通检查的缓存。每个缓存写入者使用独立、仅所有者可读写的临时文件。只有元数据指向实际加载的包时，才能据此识别可写的安装渠道。

归档校验解析 tar 链接链，并以归档根目录解释硬链接目标；拒绝重复路径、循环链接和位于链接下的成员。解压必须使用空暂存目录，wheel 解压函数会自行执行这项检查。wheel 元数据必须声明目标版本，依赖条件即使包含布尔表达式，也必须以选择非空 extra 为前提。

执行探针兼容 Python 3.10 及以上，排除调用者工作目录和用户 site，并确认实际导入了指定的暂存包。探针使用无需凭据的回环模型配置并禁用联网。有效诊断报告中的离线或可选运行环境警告可以通过；失败检查和临时数据库错误仍会拒绝载荷。

## 计划中的后续模块（当前尚未提供）

| 文件 | 计划职责 |
| --- | --- |
| `store.py` | `<data_dir>/updates/` 下的持久化更新仓：flock、暂存的载荷、各代、journal，以及 `prune`。 |
| `preserve.py` | 换代时要活下来的东西——升级前的数据库快照、配置、Skill，以及有多少恢复检查点处于风险中。 |
| `apply.py` | `plan`、`apply_update`、`rollback` 和 `recover`：事务的阶段顺序、它那套分三种情况的回滚判定过程，以及它的各种拒绝。 |
| `commit.py` | 按渠道的提交。venv 这一支是这个仓库交过学费的：daemon 自己的 `.venv` 不带 `pip` 模块，所以提交时先探 `importlib.util.find_spec("pip")`，否则走 `uv pip install --python sys.executable`，两者都没有时则拒绝，并把两种补救都说清楚。 |
| `applier.py` | daemon 以固定 argv、detached 方式拉起的进程外 applier，参数只有一个它自己写出来的 plan 文件。HTTP 请求体里的任何东西都不会进到 argv，而 applier 在动任何东西之前会重新校验 plan 里的每一个字段——包括重新计算载荷的哈希。 |

## 这个包刻意拒绝的事

- **不做 re-exec。** 在进程启动时读一个指针、然后 `execve` 进 `<data_dir>` 下的某一代，等于把「能在数据目录里写一个文件」变成「从此以后都以 daemon 身份执行」——而且恰好发生在那些 daemon uid 根本写不动安装树的渠道上。
- **不做数据库降级迁移，也不自动恢复快照。** 快照会被留下并被点名；放回去是操作者的决定。
- **不声称作者身份。** 两个独立的摘要证人确立的是完整性。它们通过 Trusted Publishing 从同一个仓库发布，共享同一个信任根，而且 release 里没有任何东西是用客户端持有的密钥签的。信任那一行两半都要说，而通常丢掉的正是第二半。
- **不在启动时检查，不轮询，不起后台线程。** 检查只在用户的显式动作下发生。

## 修改规则

- 只用标准库。`pyproject.toml` 声明了 `dependencies = []`，发布门会拒绝带非 extra `Requires-Dist` 的 wheel。特别地，这里没有 `packaging`：release tag 文法是固定且无预发布的，版本就按整数三元组比较。
- 绝不从响应体、设置行、工具参数或模型消息里拿主机名、路径或版本，除非先对照这个包里已经决定好的东西校验过。
- 失败永远不等于「已是最新」。出现新的失败模式就给它一个 `REASONS` 条目；不要并进已有的一项，也不要让它以成功的形式到达调用方。
- 不要在这里加出站原语。如果 `webtools` 表达不了某次读取需要的东西，那就去扩 `webtools`。
