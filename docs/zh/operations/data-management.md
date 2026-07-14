---
title: 数据、备份与恢复
description: OpenAI4S 数据目录内容、一致性备份流程、恢复验证与可移植性边界。
canonical: true
last_verified: 2026-07-14
verification: code-and-tests
status: current
audience: [operators, contributors]
verified_commit: a92e736
owner: OpenAI4S maintainers
---

# 数据、备份与恢复

一个 OpenAI4S 实例不仅仅是 SQLite 数据库。持久状态分别存放在 `openai4s.db` 和 `OPENAI4S_DATA_DIR` 下的文件中。可恢复的备份必须保存**守护进程已停止这一时间点上的整个目录**。

默认目录是 `~/.openai4s`；由 supervisor 管理的安装应设置一个明确路径，例如 `/var/lib/openai4s`。同一时刻只能有一个守护进程拥有该目录。

## 数据分类

将整个目录视为机密研究数据。根据具体用途，其中可能包含：

| 位置 | 内容 |
|---|---|
| `openai4s.db` | Session、message、Action Ledger、Cell 记录、设置与已保存的模型 profile、connector 配置、权限状态、Artifact metadata、plan、review、memory、checkpoint、branch 与 recovery 记录 |
| `agent-workspaces/` | 每个 Session 和 branch 的活动工作文件 |
| `artifact-versions/` 与 `artifacts/` | 不可变或旧版 Artifact 快照 |
| `workspace-cas/` | checkpoint 引用的内容寻址 workspace blob 与 tree |
| `uploads/` | 尚未限制到 Session workspace 内的上传文件 |
| `user-skills/` 与 `dynamic-tools/` | 用户编写的可执行 recipe，以及 Session/project/global 动态工具状态 |
| `session-imports/` | 从可移植 Session package 导入并通过验证的文件 |
| `compaction-history/`、`tool-results/` 与 `logs/` | 历史 context slice、tool material 与运维日志 |
| `compute-jobs/` 与 `hpc/` | 使用相关功能时由 command 创建的本地 working file 与已收取的远程计算输出；本地 job metadata/output buffer 本身位于进程内存中 |
| `remote_compute.json` | 已注册的 SSH host 与远程 capability metadata，但不含 SSH private key |
| `openai4s_tape.json` | 启用录制时的可选 replay material |
| `openai4s.pid` 与 `daemon.json` | 临时进程 metadata；保存在归档中没有问题，但恢复后已失效 |

SQLite 可能包含已保存的 API key。即使专用凭据字段经过脱敏，日志、message、Cell 源码、tool output、文件与导出也可能包含 secret 或受监管研究数据。对完整备份实施保留、加密与访问控制策略。

## 数据目录之外的状态

即使备份整个目录，仍不会捕获所有依赖：

- 已部署的代码修订版与发行版专属虚拟环境；
- checkout 本地的 `.env` 或 supervisor 环境文件；
- 操作系统 keychain 与仅在内存中的 `host.credentials` vault；
- `~/.ssh/config`、SSH private key 与 ssh-agent 状态；
- conda 环境和外部 R/Python 安装；
- provider/cloud 凭据、Docker image 与 container；
- 有意保留在远程计算 host 上的文件；
- 未在受管部署中明确配置的服务用户默认计算安装身份。

在每份备份旁记录源码修订版、Python/R 版本、所选环境、相关的非 secret 配置与外部服务依赖。使用独立的 secret management system 存储凭据，并测试重新关联，而不是把明文写入备份 manifest。

## 文件权限

首次启动前设置私有 umask，并让目录归服务账户所有：

```bash
umask 077
chmod 0700 "$OPENAI4S_DATA_DIR"
chmod -R go-rwx "$OPENAI4S_DATA_DIR"
```

应用会使用进程 umask 创建若干文件，但不会对所有已有文件追溯统一权限。恢复、手动复制文件以及导入包/工具后，应重新检查权限。不要将数据目录设为静态 Web 服务器的 document root。

## 一致性实例备份

停止守护进程是基准流程。这样会关闭 kernel、共享 SQLite 连接与活跃 HTTP thread，并可防止文件复制期间数据库状态与 workspace、Artifact snapshot 和 workspace CAS 发生偏移。

1. 阻止新任务进入，并等待活动中的本地/远程任务完成，或明确取消它们。
2. 使用进程 supervisor，或在服务环境下运行 `openai4s stop`，干净地停止守护进程。
3. 确认进程已经退出。不要只相信可能过期的 pidfile。
4. 使用能够保留 mode、owner、timestamp、symlink 和 filename 的工具复制完整数据目录。
5. 对生成的归档做 hash 和加密，且仅在快照完成后重启。

代表性的 Linux 命令如下：

```bash
sudo systemctl stop openai4s
if sudo systemctl is-active --quiet openai4s; then
  echo "OpenAI4S is still active; refusing to copy live state" >&2
  exit 1
fi

sudo install -d -m 0700 /var/backups/openai4s
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
sudo tar -C /var/lib -cpf "/var/backups/openai4s/data-${stamp}.tar" openai4s
sudo sha256sum "/var/backups/openai4s/data-${stamp}.tar" \
  > "/var/backups/openai4s/data-${stamp}.tar.sha256"

sudo systemctl start openai4s
```

请根据实际安装调整服务名和路径。不要把备份放在 `OPENAI4S_DATA_DIR` 内，否则会递归包含先前备份。限制归档与 checksum 的所有权；checksum 只能保护完整性，不能保护机密性。

### 为什么只备份数据库或在线复制都不够

SQLite `.backup` 能产生一致的数据库镜像，但无法原子捕获 workspace、不可变 Artifact bytes、checkpoint CAS object、Skill file 或远程输出。因此，在线复制目录可能把某次数据库事务与更早或更晚的文件配在一起。只有在运维人员已经验证文件系统能原子冻结整个数据目录，并在写入负载下测试过恢复时，filesystem snapshot 才可能成为可接受的 hot-backup 机制。

## 备份验证

验证应在生产路径之外完成：

1. 解压前验证归档 hash。
2. 以服务用户身份解压到新的私有目录。
3. 检查所有路径均位于 restore root 下，并且没有引入意外的 owner 或 group/other 权限。
4. 对解压出的数据库执行 SQLite integrity check。
5. 使用匹配的代码发行版，在另一个回环端口与恢复目录上启动实例。
6. 检查一个旧 Session、Notebook Cell、Artifact version、branch/checkpoint 列表、用户 Skill 与权限状态。只有被动数据检查成功后，才启动 Python/R。

SQLite 检查仅使用 Python 标准库：

```bash
python -c \
  'import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); print(db.execute("PRAGMA integrity_check").fetchone()[0])' \
  /restore/openai4s/openai4s.db
```

`ok` 结果只验证数据库文件，不验证它是否与 Artifact 和 CAS 文件一致；仍须进行隔离的应用检查。

## 完整恢复

1. 停止守护进程，并把当前目录换名保留。绝不要向正在运行或只复制了一部分的目录树上恢复。
2. 尽可能将归档恢复到同一本地文件系统上的新目录中。
3. 把 owner 设为专用账户，并移除所有 group/other 权限。
4. 仅在确认没有进程拥有该实例后，从恢复副本中移除过期的 `openai4s.pid` 与 `daemon.json`。
5. 选择备份时记录的代码发行版。不要先用较新版本打开数据库“检查”；打开可能执行前向迁移。
6. 在回环地址上启动，并先验证只读视图，再运行新 Cell 或远程作业。
7. 保留被替换的目录，直到恢复实例通过有意义的工作负载测试并完成一次新备份。

系统不提供通用数据库 down-migration 契约。应用回滚时，应同时恢复升级前的数据快照与旧代码发行版。

## 保留与删除

删除 Session 或 project 会触发引用感知清理，包括数据库行、自有 workspace、version snapshot、dynamic state、import 及无引用 CAS object。它不保证能从 SSD、copy-on-write 文件系统、备份、远程 host 或外部 provider 中安全擦除数据。

请分别为以下数据定义保留策略：

- 活动实例数据；
- 加密的实例备份；
- 导出的 Session package 和 Notebook/Artifact ZIP；
- 远程作业目录与 provider log；
- 已撤销凭据和审计证据。

## 可移植性功能不是实例备份

- **Artifact version restore**会验证旧快照并追加一个新的当前版本。它不会恢复数据库或 Session namespace。
- **Checkpoint/recovery action**可以重建选定的 workspace 与 runtime state。任意 Python/R 内存对象不属于备份契约。
- **Notebook export**保留 Cell 视图，但不保留 Host RPC、权限、持久 ledger state 或活动 namespace identity。
- **Session package export**是一种确定性、已清理 secret 的交换格式。导入会刻意创建新 identity、降低 authority，并以隔离的 ended/view-only 状态打开，直到确认全新重启。它适合共享与检查，而不是逐字节恢复实例。
