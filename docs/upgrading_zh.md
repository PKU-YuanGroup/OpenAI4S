# 从 0.2.x 升级到 0.3.0

[English](upgrading.md)

首次启动 0.3.0 之前请先读完本页。有两项变化无法靠重装旧版本撤销：0.3.0 会原地升级数据库，并且移除了让本地守护进程不带访问令牌运行的开关。

## 1. 先备份数据库

0.3.0 中第一个打开数据库的命令会把 `<data_dir>/openai4s.db` 从 schema **27** 迁移到 schema **32**。启动守护进程和运行 `openai4s run` 都会触发迁移。`openai4s doctor` 不会：它不以写方式打开数据库，只读取 schema 版本，把待进行的升级报告为警告（因此退出码不为 0），数据库保持不变。`openai4s diagnostics` 也不会：它生成的诊断包在 `report.json` 里记录待进行的升级，数据库同样保持不变。数据目录默认是 `~/.openai4s`，除非用 `OPENAI4S_DATA_DIR` 指定了别的位置。`pip` 安装、Linux tarball 和 macOS 应用都使用这个默认位置。

迁移开始前会先把数据库复制为 `openai4s.db.v27.bak`。迁移失败时保留这份副本，**迁移成功后会删除它**。所以一次正常的升级之后，不会留下任何 0.2.x 数据库的副本。

如果迁移失败，数据库会回滚并保持在 schema 27，这份副本会保留，命令以一行 `error:` 信息停止，并给出副本所在位置。此时 `openai4s serve`（无论是否带 `--detached`）和 `openai4s run` 的退出码为 2。在升级成功之前，`openai4s doctor` 的 data 检查会失败（退出码 2），并给出保留的副本位置。

如果你之后可能想退回 0.2.x，请先自己备份：

1. 停止 OpenAI4S：运行 `openai4s stop` 或退出应用，再用 `openai4s status` 确认没有守护进程仍在运行。
2. 复制整个数据目录。里面不只有数据库，还有 Artifact、日志和访问令牌：

   ```bash
   cp -a ~/.openai4s ~/.openai4s-0.2-backup
   ```

   如果目录太大无法整体复制，至少要把 `openai4s.db` 连同旁边的 `openai4s.db-wal` 或 `openai4s.db-journal`（如果存在）一起复制，并且在没有任何进程运行时复制。
3. 安装 0.3.0 并启动。

容器镜像把数据目录放在 `/data`（`OPENAI4S_DATA_DIR=/data`），也就是 `compose.yaml` 里名为 `openai4s-data` 的命名卷，或 `deploy/kubernetes.yaml` 里名为 `openai4s-data` 的 PersistentVolumeClaim。0.3.0 镜像会以同样的方式迁移其中的数据库。使用 Docker 或 Kubernetes 时，请先停止容器（或把 Deployment 缩容到 0），备份这个卷或 PVC，再启动 0.3.0 镜像。

迁移 28 到 32 只新增表、列和索引，不删除任何东西：

| 版本 | 新增内容 |
| --- | --- |
| 28 | 执行记录上的 generation id，以及委派子任务的任务状态 |
| 29 | Auto Mode 预算准入 |
| 30 | 委派请求与尝试 |
| 31 | 模型能力回执 |
| 32 | 用于浏览 Artifact 的索引 |

## 2. 不支持退回 0.2.x

0.2.0 不检查它打开的数据库的 schema 版本：只要库里记录的版本不低于它所知道的版本，它就跳过迁移。如果让它打开一个已经被 0.3.0 升级过的数据目录，它会不给任何警告地启动并写入这个数据库，但不会维护迁移 28 到 32 新增的任何内容。这些迁移只新增表、列和索引，但这并不让这种组合变成受支持的：没有任何机制检查两个版本对它们都会写的那些行理解一致。

要退回，请停止 0.3.0，再恢复升级前自己做的那份备份：

```bash
mv ~/.openai4s ~/.openai4s-0.3
cp -a ~/.openai4s-0.2-backup ~/.openai4s
```

在 0.3.0 下创建的内容都不在那份备份里。

从 0.3.0 起才有这道拒绝：0.3.x 版本打开 schema 更新的数据库时，会在写入任何东西之前停下，并报告 `future_schema` 以及两个版本号。

## 3. 访问令牌总是必需

0.2.x 在设置 `OPENAI4S_REQUIRE_TOKEN=0` 时允许 loopback 上的守护进程不带凭据运行，并提示这个开关会在下一个小版本移除。0.3.0 移除了它：该变量会被忽略，每个守护进程都要求访问令牌，包括绑定在 `127.0.0.1` 上的守护进程。

* 通过 `openai4s serve` 打开并打印出来的 URL 进入工作台，或用 `openai4s url` 再打印一次。直接访问 `http://127.0.0.1:8760/` 会得到「需要访问令牌」的页面。
* `openai4s status` 不再打印带令牌的 URL，请改用 `openai4s url`。
* 令牌保存在 `<data_dir>/access-token`，重启后不变。脚本可以用 `Authorization: Bearer <token>` 或 `X-OpenAI4S-Token: <token>` 发送它。会改变状态的请求如果在查询参数里带 `token`，会被拒绝。

## 4. 其他可能会注意到的变化

* **工作台是新的。** 默认 UI 是 Preact/TypeScript 工作台。`OPENAI4S_WEBUI=legacy` 仍可打开旧的 `app.js` UI，但它不再增加新功能。0.2.x 写进聊天消息里的 Artifact 链接（`/api/artifacts/<id>`）只在默认工作台里能打开，工作台会把它们改写到 `/api/v1`；旧 UI 按原样使用这些链接，服务器会返回 404。
* **0.2.x 生成的 Artifact。** 它们的环境溯源现在显示为 Python kernel，包列表未知。0.2.x 用 kernel 的模式 `repl` 记录 Python kernel，也没有读取它的包。0.3.0 在读取这条记录时更正这个标签，但不会重新测量环境，也不会编造包列表。
* **`openai4s run` 的退出码。** 只有当运行提交了结果时，命令才以 `0` 退出。因其他原因停止的运行（例如达到轮数上限、没有进展或被取消）以 `3` 退出。`--json` 输出里仍然带有 `stop_reason`。把「运行结束」一律当作成功的脚本应改为检查退出码。运行开始之前就被拒绝时同样以 `2` 退出：任务为空、`--allow-test-command` 无效、显式代码模式下没有任何途径能授权它的测试命令，或它不会打开的数据库，即比当前版本新的数据库（`future_schema`）或升级失败的数据库（`migration_failed`，见第 1 节）。加 `--json` 时，错误和 code 会打印在 stdout。
* **`openai4s stop` 等得更久。** 0.2.x 只等守护进程大约 5 秒，之后就报告失败，或在加了 `--force` 时发送 SIGKILL。0.3.0 最多会先等 `--timeout`（默认 30 秒）。守护进程过了最初 5 秒仍未退出时，才会在 stderr 打印一行 `shutting down…` 并继续等待；在那之前就退出的，只打印 `daemon stopped`。依赖短等待的脚本可以传 `--timeout 5`（旧的 SIGKILL 行为再加 `--force`）。
* **容器镜像。** 镜像使用 Python 3.14，而 0.2.0 镜像使用 3.12。如果你扩展过镜像，或在运行中的容器里安装过包，请针对 3.14 重新构建或重新安装。
* **macOS 磁盘镜像是预览版。** v0.3.0 附带一个面向 Apple Silicon、只做了 ad-hoc 签名且未经公证的 `.dmg`；Intel Mac 请从 PyPI 安装（见 [上手指南](startup-guide.md#zh)）。用它替换 v0.2.0 应用后，首次启动就会升级数据目录，所以请先备份。v0.2.0 应用仍能打开 0.2.x 的数据目录，但第 2 节同样适用于它：不要让它打开已被 0.3.0 升级过的数据目录。
* **Skill 安装器。** npm 包 `@pku-yuangroup/openai4s-skills@0.2.0` 仍可安装，包含 603 个 Skill；仓库和 0.3.0 wheel 带有 604 个。
