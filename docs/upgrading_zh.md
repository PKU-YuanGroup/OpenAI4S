# 升级

[English](upgrading.md)

已经装了 1.0.1、要升到 1.0.2 的，读「升级到 1.0.2」。已经装了 1.0.0、要升到 1.0.1 的，读「升级到 1.0.1」；从 1.0.0 直接升到 1.0.2 的，「升级到 1.0.1」和「升级到 1.0.2」两节都要读，第一次打开数据库时会把 schema 从 36 一次迁到 45。已经装了 0.3.1、要升到 1.0.0 的，读「升级到 1.0.0」；从 0.3.1 直接升到 1.0.1 的，「升级到 1.0.0」和「升级到 1.0.1」两节都要读，第一次打开数据库时会把 schema 从 35 一次迁到 43。已经装了 0.3.0、要升到 0.3.1 的，读「升级到 0.3.1」。数据库仍在 0.2.x 的，0.3.1 会在一次打开里把它一直迁到 schema 35：到 schema 32 为止的步骤读「从 0.2.x 升级到 0.3.0」，33 到 35 读「升级到 0.3.1」。跨多个版本升级也是一样：第一次打开会跑完中间所有的迁移，所以要从你所装版本的下一个版本那一节读起，一直读到要安装的版本。从 0.3.0 直接升到 1.0.1 的，也会经过改写已落盘 judge 行的 schema 33（见「升级到 0.3.1」）。1.0.1 之后的每个版本发布前都会实测一条升级路径；实测了哪些数据、哪个通道，以及如何回退，见本页末尾的「发版前如何实测升级」。

## 升级到 1.0.2（schema 43 → 45）

先停守护进程（`openai4s stop`，再用 `openai4s status` 确认），在没有任何进程运行时复制整个数据目录，包括 `openai4s.db-wal` 或 `openai4s.db-journal`：

```bash
cp -a ~/.openai4s ~/.openai4s-before-1.0.2
```

数据目录在哪里、容器要备份什么，与下面「升级到 1.0.1」一节相同。已有的 PyPI 安装：激活原来的环境后运行 `python -m pip install --upgrade "openai4s[science]==1.0.2"`。源码检出：`git fetch --tags && git checkout v1.0.2 && git submodule update --init third_party/OpenMHS`，然后运行 `./setup.sh`。

第一个打开数据库的命令会把它从 schema 43 迁到 45。迁移 44 新增一个可为空的列，迁移 45 新建一张表（一开始是空的），两者各在 `schema_migrations` 里记一行；1.0.1 存下的任何行都不会被改写或删除：

| 版本 | 新增内容 |
| --- | --- |
| 44 | `execution_log_inlined_script`：`execution_log` 的 `inlined_script` 列。代码只是运行一个工作区脚本的单元（`exec(open("x.py").read())`、`runpy.run_path`、`%run`、R 的 `source("x.R")`）会记下该脚本运行时的内容（最多 256 KB），所以文件之后被改写或删除，Notebook 和两种 `.ipynb` 导出仍能显示并重新运行它。升级前记录的单元保持 NULL |
| 45 | `message_admissions`：普通消息的提交回执。`POST /frames/{fid}/message` 的应答丢失时，浏览器据此查询结果，而不是把这一轮再发一次 |

这条升级路径——1.0.1 的 Linux x86_64 安装包写入数据，1.0.2 的安装包迁移并回读，再把迁移前的副本恢复、由 1.0.1 重新读取——就是发布前实测的那一条，见本页末尾的「发版前如何实测升级」。

和每次升级一样，迁移会先把数据库复制成一个以旧 schema 命名的文件（1.0.1 的数据库是 `openai4s.db.v43.bak`），迁移成功后会删除这份副本，所以不能靠它回退。不支持降级：1.0.1 遇到 schema 45 的数据库会在写入任何内容之前以 `future_schema` 拒绝打开。要回到 1.0.1：先停掉 1.0.2，再恢复你复制的那份数据目录：

```bash
mv ~/.openai4s ~/.openai4s-1.0.2
cp -a ~/.openai4s-before-1.0.2 ~/.openai4s
```

在任何程序打开恢复出来的副本之前，先装回 1.0.1：PyPI 用 `python -m pip install "openai4s[science]==1.0.1"`。在 1.0.2 下新建的内容不在这份副本里。

API 客户端能看到的变化：

- `GET /frames/{fid}/execution-log` 的每个条目和每个 `notebook_cell_finished` 事件都带 `inlined_script`，其他单元为 null。
- `POST /frames/{fid}/message` 可选接收 `client_request_id`（32 位小写十六进制，须 `wait:false`，且不带 `annotation_ids`），此时 202 会带上 `admission_state`；`GET /frames/{fid}/message-admissions/{client_request_id}` 回答它后来怎样了。不带这个字段的请求得到与 1.0.1 完全相同的响应。
- 模型服务拒绝请求时，公开失败信息的 `code` 改为按能证实的原因命名：`llm_authentication_failed`（HTTP 401）、`llm_access_denied`（403）、`llm_model_unavailable`（404 且服务的机器码为 `model_not_found`）、`llm_request_target_not_found`（其他 404）、`llm_endpoint_unreachable`（请求发出前连接被拒绝或域名无法解析）。1.0.1 里 401 和 403 都是 `llm_auth_rejected`，404 是 `llm_model_not_found`；这两个旧码只在同样的服务机器码出现在其他状态码上时保留。1.0.1 已存下的失败记录保持原来的码。
- `GET /onboarding` 新增 `completed_explicitly`；`complete` 的含义不变。
- 安装软件包改为每次单独批准。1.0.1 存下的「始终允许安装」规则不再生效，`POST /permissions` 新建这类规则会得到 400 `install_rule_refused`。

## 升级到 1.0.1（schema 36 → 43）

先停守护进程（`openai4s stop`，再用 `openai4s status` 确认），在没有任何进程运行时复制整个数据目录，包括 `openai4s.db` 旁边的 `openai4s.db-wal` 或 `openai4s.db-journal`：

```bash
cp -a ~/.openai4s ~/.openai4s-before-1.0.1
```

数据目录默认是 `~/.openai4s`，除非 `OPENAI4S_DATA_DIR` 指定了别的位置。容器镜像把数据放在 `/data`（`OPENAI4S_DATA_DIR=/data`），也就是 `compose.yaml` 里名为 `openai4s-data` 的命名卷，或 `deploy/kubernetes.yaml` 里名为 `openai4s-data` 的 PersistentVolumeClaim。请先停止容器（或把 Deployment 缩容到 0），备份这个卷或 PVC，再启动 1.0.1 的镜像。

已有的 PyPI 安装：激活原来的环境后运行 `python -m pip install --upgrade "openai4s[science]==1.0.1"`。源码检出：`git fetch --tags && git checkout v1.0.1 && git submodule update --init third_party/OpenMHS`，然后运行 `./setup.sh`。

第一个打开数据库的命令会把它从 schema 36 迁到 43。`doctor` 和 `diagnostics` 只检查 schema，不会升级它：`doctor` 把待执行的升级报告为警告，`diagnostics` 把它记进支持包。迁移 37 到 43 属于 Agentic RL 运行时（见 [rl/README_zh.md](rl/README_zh.md)），只创建 `rl_*` 表和索引（这些表一开始是空的），并在 `schema_migrations` 里各记一行；1.0.0 存下的任何表、索引或行都不会被改动或删除。每一步新增的内容：

| 版本 | 新增内容 |
| --- | --- |
| 37 | `rl_runtime`：trajectory 相关的表（episode、observation、decision、segment、执行事件、reward 标注、成本记录）和 run receipt 表 |
| 38 | `rl_dataset_snapshots`：数据集 snapshot 元数据 |
| 39 | `rl_segment_parent`：`rl_segments` 的 `parent_segment_id` 列及其索引，只在缺失时添加；迁移 37 已经建好了这两者，所以这次升级里它什么也不加 |
| 40 | `rl_budget_ledger`：预算账本及其 allocation |
| 41 | `rl_resource_admission`：resource admission 及其 allocation |
| 42 | `rl_phase_bindings`：带 owner 校验（owner-fenced）的阶段绑定 |
| 43 | `rl_run_results`：run request、冻结的 run 结果和 step receipt |

其中唯一的 `ALTER TABLE` 在迁移 39，只在 `rl_segments` 缺少 `parent_segment_id` 时执行。迁移 37 在同一次升级里创建的 `rl_segments` 已经带着这一列，所以这条 `ALTER TABLE` 不会执行。表定义在 `openai4s/storage/rl.py` 和 `openai4s/storage/rl_runs.py`。

迁移改动任何东西之前，会先把数据库复制成以正在离开的 schema 命名的文件（1.0.0 的库是 `openai4s.db.v36.bak`）。迁移成功后会删除这份副本，所以它不是退路。某一步失败时，数据库回滚到升级前的 schema（1.0.0 的库是 36），副本保留，命令以一行 `error:` 停止，并给出副本位置；此时 `openai4s serve` 和 `openai4s run` 的退出码是 2，重新跑升级是安全的。升级成功之前，`doctor` 的 data 检查会失败（退出码 2），并指出保留下来的副本。

不支持降级。把 1.0.0 的守护进程指到已升级的数据库上时，它会在写入之前停下，并报告 `future_schema` 和两个版本号。这次停下不会恢复以前的行。先停掉 1.0.1。要回到升级前的数据，只能用你自己在升级前复制的那份数据目录：

```bash
mv ~/.openai4s ~/.openai4s-1.0.1
cp -a ~/.openai4s-before-1.0.1 ~/.openai4s
```

在任何命令打开恢复后的副本之前，先重新装回 1.0.0：PyPI 安装运行 `python -m pip install "openai4s[science]==1.0.0"`；源码检出按下一节的 `v1.0.0` 步骤操作；容器用恢复后的卷启动 1.0.0 镜像。1.0.1 的命令一打开这份副本，就会再次把它迁到 schema 43。在 1.0.1 里创建的内容都不在那份副本里。

## 升级到 1.0.0（schema 35 → 36）

先停守护进程（`openai4s stop`，再用 `openai4s status` 确认），在没有任何进程运行时复制整个数据目录，
包括 `openai4s.db` 旁边的 `openai4s.db-wal` 或 `openai4s.db-journal`：

```bash
cp -a ~/.openai4s ~/.openai4s-before-1.0.0
```

已有的 PyPI 安装：激活原来的环境后运行 `python -m pip install --upgrade "openai4s[science]==1.0.0"`。
源码检出：`git fetch --tags && git checkout v1.0.0 && git submodule update --init third_party/OpenMHS`，
然后运行 `./setup.sh`。

第一个打开数据库的命令会把它从 schema 35 迁到 36。schema 36 新增 `dataset_verification_receipts` 表：
只有绑定到确切版本、来源和字节的 Host 回执，才会显示为数据集已校验。历史制品来源保持不变，也不会为它们
推断回执，所以它们的校验状态显示为未知。`doctor` 和 `diagnostics` 只检查 schema，不会升级它。不支持降级：
0.3.1 会拒绝 schema 36，你自己复制的那份数据目录才是退路。

OpenMHS 实验室需要主动启用。从不运行 `openai4s lab` 的安装不会有其他变化；`OPENAI4S_OPENMHS=off` 可以关闭
OpenMHS 查找。详见 [openmhs_zh.md](openmhs_zh.md)。

## 升级到 0.3.1（schema 32 → 35）

schema 33 会改写已经落盘的 judge 审计行，迁移成功之后还会删掉它自己的升级前副本。那份副本不是退回上一版本的办法。升级前请自己复制整个数据目录。

### 先停守护进程，并自己复制数据目录

安装或首次启动 0.3.1 之前，先停掉守护进程。运行 `openai4s stop`，或退出应用，再用 `openai4s status` 确认没有守护进程仍在运行。如果还有进程在跑带 `host.judge`、但没有本版审计投影的构建（例如 0.3.1 开发期间版本号仍显示 0.3.0 的早期源码构建），它可能在这次升级提交之后才写入一条带着原始参数的 `judge` 行，而第 33 步不会再跑。已发布的 0.3.0 安装包没有 `host.judge`，不写 `judge` 行。提交后的 `user_version` 是 35。

然后在没有任何进程运行时复制整个数据目录。里面有 Artifact、日志和访问令牌，不只有数据库。请把 `openai4s.db` 旁边的 `openai4s.db-wal` 或 `openai4s.db-journal`（如果存在）一起复制：

```bash
cp -a ~/.openai4s ~/.openai4s-before-0.3.1
```

数据目录默认是 `~/.openai4s`，除非 `OPENAI4S_DATA_DIR` 指定了别的位置。

容器镜像把数据放在 `/data`（`OPENAI4S_DATA_DIR=/data`），也就是 `compose.yaml` 里名为 `openai4s-data` 的命名卷，或 `deploy/kubernetes.yaml` 里名为 `openai4s-data` 的 PersistentVolumeClaim。请先停止容器（或把 Deployment 缩容到 0），备份这个卷或 PVC，再启动 0.3.1 的镜像。

本版止于 schema 35。后续版本继续往上：1.0.0 打开的是 schema **36**，1.0.1 打开的是 schema **43**。第 36 步新增 `dataset_verification_receipts` 表（见上文 1.0.0 一节）；第 37 到 43 步新增 Agentic RL 运行时的 `rl_*` 表（见上文 1.0.1 一节）。它们都是加法迁移，接在 0.3.1 的 schema 35 边界之后执行。

### 备份后安装 0.3.1

已有 PyPI 安装时，先激活其虚拟环境，再安装 [0.3.1 包](https://pypi.org/project/openai4s/0.3.1/)：

```bash
source ~/.venvs/openai4s/bin/activate   # 请使用已有环境的实际路径
python -m pip install --upgrade "openai4s[science]==0.3.1"
openai4s --version
```

新建环境或从旧 macOS 应用迁移时，请按[上手指南](startup-guide.md#zh)操作。仅安装核心时使用 `openai4s==0.3.1`；还需要 RDKit 时使用 `openai4s[science,chemistry]==0.3.1`。

Linux 与 Windows/WSL2 安装包、`SHA256SUMS` 和验证资料归档均在 [GitHub release](https://github.com/PKU-YuanGroup/OpenAI4S/releases/tag/v0.3.1)。停止旧守护进程后，按 [Linux 安装说明](../README_zh.md#linux-应用无需任何工具链)或 [Windows/WSL2 指南](windows-wsl.md#中文)操作。发布版 ZIP 已完整，无需合并分卷。macOS 请使用 PyPI，因为 0.3.1 不含 DMG。

容器使用公开的 [`ghcr.io/pku-yuangroup/openai4s:0.3.1`](https://github.com/orgs/PKU-YuanGroup/packages/container/package/openai4s) 镜像（`linux/amd64`）。使用已备份的同一数据卷重新创建已停止的容器；配置见 [Docker 指南](docker.md#zh)。需要固定版本时，请使用 `0.3.1` 标签，而不是 `latest`。

安装后，`openai4s --version` 应显示 `0.3.1`。完成备份后才启动新守护进程；第一次打开数据库时会执行下述迁移。

### schema 33 到 35 改了什么

0.3.1 里第一个打开数据库的命令会把 `<data_dir>/openai4s.db` 从 schema 32 迁到 schema **35**。启动守护进程和运行 `openai4s run` 都会触发。`openai4s doctor` 和 `openai4s diagnostics` 仍然只读取 schema 版本。这次升级在一个事务里提交新 schema。失败时数据库回滚到升级前的 schema。

数据库旁边的副本，文件名是正在离开的那个 schema。schema 32 的库会被复制为 `openai4s.db.v32.bak`。迁移成功后会删除 `openai4s.db.v32.bak`。仍低于 schema 32 的库使用 0.2.x 那一节里的文件名（离开 schema 27 时仍是 `openai4s.db.v27.bak`），并在同一次打开里一直迁到新 schema。失败时数据库留在原来的 schema，副本保留，命令以一行 `error:` 停止，并给出副本位置。此时 `openai4s serve` 和 `openai4s run` 的退出码是 2。重新跑升级是安全的。

这份副本里有未脱敏的 judge 原文。升级成功之后它就不在了。如果还需要旧数据，请保留上一节里你自己复制的那份数据目录。

| 版本 | 变化 |
| --- | --- |
| 33 | `redact_judge_host_call_args`：改写 `host_call_log` 里已经落盘的 `judge` 行 |
| 34 | `background_exec_receipts`：新增表；已有行保持原样 |
| 35 | `project_source_folder`：新增可为空的 `projects.folder_path` 列；已有项目默认不绑定文件夹 |

改写期间连接会执行 `PRAGMA secure_delete = ON`，并在事务提交前按名字恢复原先的模式（`OFF`、`ON` 或 `FAST`）。开着 `secure_delete` 时，SQLite 会用零覆盖这次 `UPDATE` 换下来的旧字节。

与新写入逐字节相同的预览保持不变，包括 `<invalid template>`、`<unknown template>` 和 params 标记。写着模板 id 的预览，只有注册表仍能解析这个 id 时才算投影；否则按原文处理，记为 `<unknown template>`，并丢掉 params 标记。原文预览若以注册表能解析的模板 id 开头，保留该 id，state 换成 `<redacted judge state>`，并丢掉 params。原文预览若以匹配 `^[A-Za-z0-9_.:-]{1,100}$`、但注册表解析不了的 id 开头，记为 `<unknown template>`。其余原文变成只含 state 标记的 `[{"state": "<redacted judge state>"}]`。

升级前已删除的行，旧字节可能还在空闲页里。这次迁移不读那些页。若必须清掉这些残留字节，先停守护进程，再对数据库执行 `VACUUM`：

```bash
sqlite3 ~/.openai4s/openai4s.db 'VACUUM'
```

容器镜像里没有 `sqlite3` 命令。请在服务停止时，从一个挂载同一个卷的临时容器里，用 Python 的 `sqlite3` 模块对 `/data/openai4s.db` 执行 `VACUUM`。

### 降级

不支持降级到上一版本。

把 0.3.0 的守护进程指到已升级的数据库上时，它会在写入之前停下，并报告 `future_schema` 和两个版本号。这次停下不会恢复以前的行。0.2.0 不检查 schema 版本，所以指到这个数据库时它会打开并写入。先停掉 0.3.1。要回到升级前的数据，只能用你自己在升级前复制的那份数据目录：

```bash
mv ~/.openai4s ~/.openai4s-0.3.1
cp -a ~/.openai4s-before-0.3.1 ~/.openai4s
```

在 0.3.1 里创建的内容都不在那份副本里。迁移失败时留下来的 `.v32.bak` 只是那次失败尝试之前的文件。不要把它当作回滚副本：迁移成功后它会被删除，在那之前它装着未脱敏的 judge 原文。

### 本地项目源文件夹

单机回环地址运行的 daemon 现在可以给项目绑定一个本地源文件夹。已有项目在你选择文件夹之前保持未绑定。授权为只读：可以浏览和读取子文件夹，再把选中的文件导入独立的会话工作区进行 Python/R 分析。删除项目不会移动或删除源文件夹。团队模式与远程部署不能绑定主机文件夹。操作见[工作台指南](webapp.md)。

### 出口 allowlist

`OPENAI4S_EGRESS=allowlist` 仍把宿主侧的 `web_fetch`、`web_search` 和已授权的 `host.bash` 预检留在域名 allowlist 上。新的 Python 或 R Cell 只有在该 Cell 的 kernel 已经测到原始网络被阻断时才放行：`enforced` 为真、`self_test_passed` 为真，且 `network_policy` 为 `blocked`。否则这个 Cell 以 `egress_boundary_unavailable` 结束。

`OPENAI4S_KERNEL_SANDBOX=auto` 在自测通过且仍报告 `network_policy=blocked` 时放行。`enforce` 会拒绝启动 kernel，而不是降级后继续。远端 kernel 报告的 `network_policy` 是 `unproven`，只要 allowlist 开着就会被拒绝。默认的 `OPENAI4S_EGRESS=off` 不加这道关卡。已经在跑的 Cell 不受影响；下一次 Cell 会重新读这个模式。

在非特权容器里，bubblewrap 拿不到命名空间，于是 `auto` 会降级，allowlist 会拒绝每一个新的 Python 和 R Cell。那里若要放行 Cell，把 `OPENAI4S_EGRESS` 设为 `off`，或者给容器足够的权限，让 `enforce` 能建立这道边界。见 [docker.md](docker.md)。

Web Agent Cell 的结果里带 `egress_boundary`。Notebook REPL 路由只返回以 `egress_boundary_unavailable:` 开头的 `error`。在 daemon 内部，Python 在发布 worker 之前失败时抛出的错误以 `kernel bootstrap failed: egress_boundary_unavailable:` 开头；新 R worker 的拒绝以 `R kernel unavailable: R kernel bootstrap failed: egress_boundary_unavailable:` 交给 Cell 服务。allowlist 开着时，两者都让这个 Cell 以 `egress_boundary_refused` 结束。

下面这些限制与 [security.md](security.md) 一致，留到以后的版本：

- 生命周期入口不投影 `egress_boundary_refused`。`start_kernel` 和 `set_env` 遇到这道边界拒绝时回答一个普通的 HTTP 500。对仍在运行的 worker 调用 `restart_kernel` 会报告成功，下一个 Cell 才被拒绝。Agent 回合开始时若有待应用的环境变更，这一回合会失败。
- 恢复回放、Jupyter bridge 和 benchmark 步骤把 `EgressBoundaryUnavailable` 当作普通错误传出去。
- 某个 Cell 第一次因这道边界被拒绝时，集群分配会被释放。
- Web daemon 里的委派子代理可能把拒绝行写到 stderr。
- 这道关卡按 Cell 生效。解释器在 worker 进程启动期间运行的代码，包括 `.pth` 和 `sitecustomize`，不在 Cell 准入检查之内。

### 日志和启动器里的访问令牌

启动日志和容器日志不再包含访问令牌。监听行、已经在运行的提示，以及 daemon 日志，打印的都是不带令牌的源地址。这一行下面，单人模式会让你去运行 `openai4s url`，团队模式会打印 `/login`。

单人模式下，`openai4s url` 会打印登录地址。这个地址带有 `?token=`。团队模式打印 `/login`。在容器里运行 `docker exec <container> openai4s url`。以前从日志里解析令牌的脚本，要改成调用 `openai4s url`。

除非设置了 `OPENAI4S_NO_OPEN` 或传了 `--no-open`，`openai4s serve` 仍会打开本地浏览器。单人模式打开带令牌的地址。团队模式打开 `/login`。

桌面应用再次打开、且端口上已经有进程在听时，只有 `openai4s status` 验证通过这个数据目录的 daemon（pidfile、进程启动令牌，以及 `/health`），才会打开 `openai4s url` 给出的登录地址（单人模式带令牌，团队模式是 `/login`）。否则打开的是裸地址。

### judge 审计行

新写入的 `method="judge"` 的 `host_call_log` 行，在落盘前由 `HostCallRepository.log` 投影。模板 id 只在判断注册表能解析时保留。匹配 `^[A-Za-z0-9_.:-]{1,100}$` 但未注册的字符串记为 `<unknown template>`。其他模板值（包括没有模板）记为 `<invalid template>`。state 总是 `<redacted judge state>`。调用带了 params 时，记为 `<redacted judge params>`。参数不是「只含一个对象的列表」时，只存 state 标记。

`result_preview` 不变。judge 的 soft-fail 错误结果不再记 `result_digest`：错误文本可能复述调用方的模板 id 或 params，短 id 能从 SHA-256 枚举出来。其他结果仍记摘要。

原文仍可能留在这一行之外。[security.md](security.md) 列出的位置是：回滚日志或 WAL 文件、空闲页、你自己做的备份、在设置了 `OPENAI4S_RECORD_TAPE` 时写下的 `openai4s_tape.json`、升级前导出的会话包，以及在迁移成功并删除之前的 `openai4s.db.v32.bak`。这个构建把 `journal_mode` 留在回滚日志上。空闲页里的字节若必须清掉，按上面 schema 一节的做法停掉守护进程再跑 `VACUUM`。

### 后台 Cell 收据

Web 会话的后台 Cell 在 worker 启动之前把一行写进 `background_exec_receipts`。这一行只留代码的 SHA-256 和字符数，不存源码。失败 Cell 存下的 traceback，以及它打印的输出，仍可能引用源码行。

每个 job 的 stdout 头部最多 256 KiB。任务持续输出时，这段头部至少每秒落盘一次。

某个会话 runtime 在 daemon 进程里第一次用到后台执行（`exec_background`、`exec_peek`、`exec_list` 或 `exec_interrupt`）时跑一次清理；之后每写成一条终态收据还会再跑一次，同一进程里最多每十分钟一次。每次清理先把其他 daemon 进程留下的未结束行落盘为 `outcome_unknown`，`ended_at` 记为这次清理的时间，此后它们和其他终态行一样处理；然后按所属用户分别清掉最老的终态行的输出，直到该用户存下的输出不超过 128 MiB，并删除结束时间早于七天的终态行。输出配额按所属用户计算：团队模式下，一个成员的后台任务不会清掉另一个成员存下的输出。清理不会删除未结束的行。两次清理之间，表可以暂时超过这两个上限。

尚未终态的行在两种情况下读作 `outcome_unknown`：这一行是另一个 daemon 记下的（重启之后就是这样），或者本进程已经不再持有这个 job。daemon 不会重放它，也不会接回仍在运行的 worker。

CLI 和子代理的后台 job 不入库，结果里是 `persistent: false`。Web 的 `exec_*` 结果带有 `persistent`。只从收据读出的结果还有 `source: "receipt"`、`output_truncated`、`code_sha256` 和 `created_at`。收据写入失败时，`exec_peek` 带 `receipt_degraded: true`。这个键只在那种情况下出现。

`exec_interrupt` 只找到收据时，`interrupt_undelivered` 和 `reason` 是同一句话：收据由另一个 daemon 进程记下时是 `the daemon has restarted; delivery cannot be confirmed`；job 仍在本进程的另一个会话 runtime 里运行时是 `another session runtime in this process holds this job; this session cannot deliver the stop`；其他情况是 `this process no longer has a handle for this job; delivery cannot be confirmed`。活着的 worker 没有收到停止请求时，`interrupt_undelivered` 是这次投递的 reason，或者是 `the stop request did not reach the worker`。

这张表在 `QUERY_DENYLIST` 上。删除会话时会一并删掉这些行。数据目录的模式是 `0700`，数据库文件的模式是 `0600`。

### REST 客户端

`GET /frames/{fid}/delegations` 的形状变了。子代理对象不再包含 `result` 和 `output` 这两个键。这两个键是缺省，不是 `null`：按 `child["result"]` 取值或用 `=== null` 判断的客户端，要改成判断键是否存在。`artifact_refs` 去掉 `durable_path`，也去掉绝对的 `path` 或 `filename`。存储的结果里有证据时，子代理带上 `artifact_evidence`，其中包含 `checked_at`。结果里没有证据时，这个键不出现。

`POST /frames/{fid}/delegations/{child}/stop` 和 `POST /frames/{fid}/delegations/{child}/continue` 返回同一个投影。continue 返回的是存储里的子代理，而不是这次运行的结果信封。

子代理的步骤卡（WebSocket 事件 `step_update` 和 `GET /frames/{fid}/steps`）键不变。其中的 `raw` 字符串现在先去掉主机路径再截断：`environment` 里的绝对路径（解释器、`env_root`）、所有 `durable_path`，以及绝对的 `path` 或 `filename`。子代理的输出正文、完成要点和结论按子代理写的原样保留，所以其中提到的主机路径也会保留。升级前已存下的步骤不会被改写。

`docs/response-schemas.json` 已按真实响应重新采集。

### 工作台

从用户消息创建分支的按钮，只在会话能力 `fork_from_message` 为真时显示。工作台把 `documentElement.dataset.forkFromMessage` 设为 `on` 或 `off`，`.msg-fork` 规则按这个标志显示按钮。请求是 `POST /frames/{fid}/branches/fork`，正文恰好是 `{from_message_id}`。没有检查点的消息返回 409。按钮保持不可用，并显示服务器原句；消息为空时显示「无法从此问题创建分支。」新分支有自己的工作区，在你激活之前保持不激活。

`@` 补全按文件名向项目的 artifact-index 要一页（20 行），再并上本会话的文件。`priority` 低于 0 的隐藏产物不会出现。弹出列表最多 8 条。搜索在途时，Enter、Tab 和发送按钮都会等这一页，再补全按下时所在的那个 token。请求超过 8 秒时显示「项目文件搜索失败，仅显示本会话文件」。本会话自己的文件仍在列表里。

子代理证据面板是子代理结束时做的记录一致性核对。它对照版本记录、校验和、快照文件是否存在及其大小，以及产出它的 Cell。宿主把产出它的 Cell 记为读到这次调用时正在执行的 Cell，前提是 worker 自己声称的 Cell 为空或与之相同；声称不一致时记为 `unattributed`，永远不会被核对通过。它不授权这个子代理，之后再打开面板看到的是当时存下的记录（`checked_at`），不会在面板开着的时候重算。条目最多 12 条。超出时，面板会标明只显示前 12 条。

### Auto Mode

这一版只带规格。[auto-mode.md](auto-mode.md) 末尾的「Workbench status surface (specification — not shipped)」写的是：这一节规定默认工作台将如何显示 Auto Mode。它还没有实现。这一版不增加控件，不增加菜单行，也不调用 `/auto-mode` 或 `/auto-audits`。

### 留给以后版本的已知限制

- Windows 启动器在 `openai4s url` 的查询串为空时会提示这个地址没有登录令牌。团队模式的 `/login` 正是这种空查询。
- 出口关卡按 Cell 生效。worker 启动期间运行的 `.pth` 和 `sitecustomize` 不受它约束，`start_kernel`、`set_env` 和 `restart_kernel` 也不投影 `egress_boundary_refused`。
- 在 `OPENAI4S_KERNEL_SANDBOX=enforce` 下，R 没有一项自动化测试能把原始网络阻断，和未加沙箱的 Rscript 在连接被拒绝时打印的那句话区分开。

## 从 0.2.x 升级到 0.3.0

首次启动 0.3.0 之前请先读完本页。有两项变化无法靠重装旧版本撤销：0.3.0 会原地升级数据库，并且移除了让本地守护进程不带访问令牌运行的开关。

### 1. 先备份数据库

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

### 2. 不支持退回 0.2.x

0.2.0 不检查它打开的数据库的 schema 版本：只要库里记录的版本不低于它所知道的版本，它就跳过迁移。如果让它打开一个已经被 0.3.0 升级过的数据目录，它会不给任何警告地启动并写入这个数据库，但不会维护迁移 28 到 32 新增的任何内容。这些迁移只新增表、列和索引，但这并不让这种组合变成受支持的：没有任何机制检查两个版本对它们都会写的那些行理解一致。

要退回，请停止 0.3.0，再恢复升级前自己做的那份备份：

```bash
mv ~/.openai4s ~/.openai4s-0.3
cp -a ~/.openai4s-0.2-backup ~/.openai4s
```

在 0.3.0 下创建的内容都不在那份备份里。

从 0.3.0 起才有这道拒绝：0.3.x 版本打开 schema 更新的数据库时，会在写入任何东西之前停下，并报告 `future_schema` 以及两个版本号。

### 3. 访问令牌总是必需

0.2.x 在设置 `OPENAI4S_REQUIRE_TOKEN=0` 时允许 loopback 上的守护进程不带凭据运行，并提示这个开关会在下一个小版本移除。0.3.0 移除了它：该变量会被忽略，每个守护进程都要求访问令牌，包括绑定在 `127.0.0.1` 上的守护进程。

* 通过 `openai4s serve` 打开并打印出来的 URL 进入工作台，或用 `openai4s url` 再打印一次。直接访问 `http://127.0.0.1:8760/` 会得到「需要访问令牌」的页面。
* `openai4s status` 不再打印带令牌的 URL，请改用 `openai4s url`。
* 令牌保存在 `<data_dir>/access-token`，重启后不变。脚本可以用 `Authorization: Bearer <token>` 或 `X-OpenAI4S-Token: <token>` 发送它。会改变状态的请求如果在查询参数里带 `token`，会被拒绝。

### 4. 其他可能会注意到的变化

* **工作台是新的。** 默认 UI 是 Preact/TypeScript 工作台。`OPENAI4S_WEBUI=legacy` 仍可打开旧的 `app.js` UI，但它不再增加新功能。0.2.x 写进聊天消息里的 Artifact 链接（`/api/artifacts/<id>`）只在默认工作台里能打开，工作台会把它们改写到 `/api/v1`；旧 UI 按原样使用这些链接，服务器会返回 404。
* **0.2.x 生成的 Artifact。** 它们的环境溯源现在显示为 Python kernel，包列表未知。0.2.x 用 kernel 的模式 `repl` 记录 Python kernel，也没有读取它的包。0.3.0 在读取这条记录时更正这个标签，但不会重新测量环境，也不会编造包列表。
* **`openai4s run` 的退出码。** 只有当运行提交了结果时，命令才以 `0` 退出。因其他原因停止的运行（例如达到轮数上限、没有进展或被取消）以 `3` 退出。`--json` 输出里仍然带有 `stop_reason`。把「运行结束」一律当作成功的脚本应改为检查退出码。运行开始之前就被拒绝时同样以 `2` 退出：任务为空、`--allow-test-command` 无效、显式代码模式下没有任何途径能授权它的测试命令，或它不会打开的数据库，即比当前版本新的数据库（`future_schema`）或升级失败的数据库（`migration_failed`，见第 1 节）。加 `--json` 时，错误和 code 会打印在 stdout。
* **`openai4s stop` 等得更久。** 0.2.x 只等守护进程大约 5 秒，之后就报告失败，或在加了 `--force` 时发送 SIGKILL。0.3.0 最多会先等 `--timeout`（默认 30 秒）。守护进程过了最初 5 秒仍未退出时，才会在 stderr 打印一行 `shutting down…` 并继续等待；在那之前就退出的，只打印 `daemon stopped`。依赖短等待的脚本可以传 `--timeout 5`（旧的 SIGKILL 行为再加 `--force`）。
* **容器镜像。** 镜像使用 Python 3.14，而 0.2.0 镜像使用 3.12。如果你扩展过镜像，或在运行中的容器里安装过包，请针对 3.14 重新构建或重新安装。
* **macOS 磁盘镜像是预览版。** v0.3.0 附带一个面向 Apple Silicon、只做了 ad-hoc 签名且未经公证的 `.dmg`；Intel Mac 请从 PyPI 安装（见 [上手指南](startup-guide.md#zh)）。用它替换 v0.2.0 应用后，首次启动就会升级数据目录，所以请先备份。v0.2.0 应用仍能打开 0.2.x 的数据目录，但第 2 节同样适用于它：不要让它打开已被 0.3.0 升级过的数据目录。
* **Skill 安装器。** npm 包 `@pku-yuangroup/openai4s-skills@0.2.0` 仍可安装，包含 603 个 Skill；仓库和 0.3.0 wheel 带有 604 个。

## 发版前如何实测升级

1.0.1 之后的版本在发布之前，发布工作流会用新版本打开上一个稳定版写下的数据。这一步在工作流的 Linux x86_64 任务 `linux-runtime-smoke` 里执行：

1. 上一个稳定版的 Linux x86_64 安装包在 `.github/workflows/release.yml` 里按文件名、版本和 SHA-256 固定。任务把它下载到 runner 的临时目录，不放进存放本次发布文件的目录。smoke 先把它复制到自己的目录，核对 SHA-256 与固定值一致之后才解包。旧包和新包分别解包到各自的目录。
2. 旧包在一个空的数据目录上启动，使用与新装检验相同的隔离 `HOME`、环境和回环端口。它创建一个项目和一个会话，运行三个 Cell，写出同一个 CSV 文件的两个版本，然后通过 `openai4s stop` 停止，检验会一直等到它的进程全部退出。
3. 复制整个数据目录。这份副本不会被任何进程打开。
4. 新包在同一个数据目录路径上启动并完成迁移。迁移前后都用 `preflight_schema` 读取 schema。项目和会话的 ID、Cell 记录以及两个 CSV 版本都必须原样读回，逐字节一致。新包重启之后，必须报告相同的执行历史和相同的字节。
5. 新包停止后，用副本替换已迁移的目录（路径不变），旧包必须能从中读回同一个项目、会话、Cell 和两个 CSV 版本。

结果以 `upgrade_readback` 记在 Linux 安装包的构建回执里：两个包的文件名、版本和 SHA-256，两个 schema，每一项检查，以及项目、会话、Cell、Artifact 和 CSV 版本的 ID 与每个版本原始字节的 SHA-256。回执里没有这项记录的 Linux 安装包不会发布。旧包下载不到、SHA-256 与固定值不符，或者任何一项检查失败，同样不会发布，而且每种情况各有自己的错误信息。

实测的数据是固定的上一版（1.0.1，schema 43）写下、由待发布版本打开的数据。实测的通道是 Linux x86_64 安装包。这项检验不覆盖 PyPI wheel、容器镜像、Windows/WSL2 安装包和 macOS 应用，不覆盖比固定版本更早的版本写下的数据，也不覆盖移到其他路径的数据目录。跨多个版本的升级会依次跑完中间的所有迁移，这些步骤由 `tests/test_schema_migrations.py` 单独测试。

按检验中的做法回退 Linux 安装包：先停掉新版本，再把升级前复制的那份数据目录放回原来的路径：

```bash
openai4s stop                                       # 再用 `openai4s status` 确认
mv ~/.openai4s ~/.openai4s-after-upgrade
cp -a ~/.openai4s-before-<新版本> ~/.openai4s
```

请使用数据目录原来的路径（默认是 `~/.openai4s`，除非 `OPENAI4S_DATA_DIR` 指定了别的位置）：检验只验证了恢复到原路径。然后用旧版本自己的启动器启动它，也就是解包目录里的 `bin/openai4s`；或者重新运行旧版本的 `install.sh`，让 `PATH` 上的 `openai4s` 重新指向它。升级之后新建的内容都不在副本里；更新的版本一打开这份副本，就会再次迁移它。
