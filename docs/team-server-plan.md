# Team Server 实施计划(组服务器模式)

**状态:** Execution Plan(可直接作为自主执行目标)
**版本:** v1.0,2026-08-14 冻结
**上游:** 两轮需求访谈 + 《OpenAI4S 集群控制平面与 Slurm 执行后端形式化规范》v0.1.0(核心结论已采纳,见 §2)
**读者:** 被 set goal 的执行代理(Claude Code / Codex 均可),以及 reviewer

---

## 0. 给执行代理的使用说明(先读这里)

**目标一句话:** 把 OpenAI4S 升级为部署在课题组服务器上的多用户科研平台——纯网页登录、项目制共享、导师全局可见、经 AllocationBackend 对接 Slurm——同时保证单用户模式行为与现有测试**一个字节都不变**。

**执行前提:** 先读根 `CLAUDE.md` 与 `docs/architecture.md`。本计划**补充**而不覆盖它们;冲突时 `CLAUDE.md` 的工程纪律优先。

**推进方式:** 里程碑顺序 M1 → M2 → M3a → M3b → M4,前一个的 DoD 全绿才进下一个。既支持"整计划一个 goal",也支持"每里程碑一个 goal"。建议的 goal 表述:

> 按 `docs/team-server-plan.md` 完成 M1(含全部门禁绿与 DoD),遵守其 §0.1 非阻塞规则;完成后继续 M2,依此类推。

### 0.1 非阻塞规则(必须遵守)

1. **全程不向人提问、不等待批准。** 本计划未定的一切,按第 2 条的优先级自行决定,并在附录 D 追加一行记录。
2. **决策优先级:** 不破坏单用户模式与现有测试 > 安全默认 > §1 冻结决策与 §2 不变量 > 形式化规范契约 > 实现简洁 > 性能。
3. **计划与代码现状冲突:** 以现状为准,调整挂载点(表名、路由名、函数名允许按现状惯例微调),附录 D 记一行(哪条、原状、改法)。**语义与不变量不得因此丢失。**
4. **外部资源不可得**(真实 Slurm 集群、VPS、公网、装不上 Playwright 等):用 fake/harness 验证到接口边界,真实环境测试统一标 `external` marker(默认反选)留待手动;附录 D 记录后**继续推进,不停下**。
5. **门禁红了当场修。** 修不动就最小化复现、定位、再修;禁止 skip/xfail 蒙混。若失败与本工作无关(与 base commit 对照归因确认),附录 D 记录后绕行。
6. **每落地一个工作项立即 commit**(未提交的工作可能被快照恢复类测试或 stash 事故吞掉);进入下一里程碑前 rebase 最新 `origin/next`,冲突按语义合并(以更强的一方为基底、重放真实增量),合并后先跑对方新增的测试。
7. **只有两种终止条件:** 全部目标里程碑 DoD 达成;或出现真正不可绕过的硬阻塞(附录 D 写清阻塞物、已尝试路径、建议)。其余一切情况继续。

### 0.2 分支与提交

- 从最新 `origin/next` 切 `feat/team-server`(分支名 CI 强制 `<prefix>/<name>`)。
- 工作项粒度提交;提交信息用 `feat(team): M1-4 login routes + session cookie` 风格,里程碑收尾提交附 DoD 核对清单。
- 里程碑收尾可开 PR 到 `next`,但**不等待 review**,同分支继续后续里程碑。
- 本计划文件本身:执行代理只允许追加附录 D,不得改写其余章节。

### 0.3 最终报告

收尾时输出:各里程碑 DoD 逐条核对结果、门禁运行记录(命令 + 结果)、附录 D 全部偏差、留待手动的 `external` 测试清单、以及部署一张纸(管理员如何初始化第一个账号、配 cluster.toml、开团队模式)。

---

## 1. 冻结的产品决策(不得重新讨论)

| # | 决策 | 内容 |
|---|---|---|
| D1 | 使用方式 | 纯网页。成员无 Unix 账号、不碰 SSH;管理员建号;文件与会话隔离在应用层 |
| D2 | 角色 | `admin` / `member` / `guest` 三级。admin 全局可见 |
| D3 | Guest | **仅只读回放**,不可发起会话,配额面为零 |
| D4 | 共享 | project 一级实体;项目内默认公开、项目间隔离;成员可将单个会话设为 `private`,**admin 仍可见且每次查看写审计** |
| D5 | 计算 | 对接已有 Slurm,不自建队列;**全组同一 partition**,经 ClusterProfile 抽象,用户与 Agent 永不接触 partition/QoS 名 |
| D6 | 计算模型 | 一个交互 Session ≈ 一个活动 Allocation;**一次 tool call ≠ 一个 Slurm Job**(Slurm 调度常驻 Worker,cell 复用持久 kernel);一次性大任务走 BatchJob → sbatch |
| D7 | LLM key | 默认组 key + 按人配额;个人 key 覆盖后置到 M4 |
| D8 | 数据 | 只读 datasets 区 + 项目工作区 + 个人 scratch;只允许白名单根目录;文件上传/下载是 M1 刚需 |
| D9 | 网络 | 内网/VPN;不开公网;relay 留作后手(M4 仅文档) |
| D10 | 身份提交模式 | Slurm 侧全部以服务账号提交(规范 §30.3 降级模式,仅限本组自管集群),应用层承担配额/计量/账本/隔离/审计/公平六义务(=M2);身份映射抽象保留 native-user 开关 |
| D11 | 规模 | 单机 ≤30 人;SQLite + 单 daemon,不换库不引第三方框架 |

---

## 2. 架构与不变量(binding)

**平面划分:** Team Server daemon = Control Plane(身份/准入/策略/审计);Slurm = Resource Plane(排队/放置/公平性,主权归它);OpenAI4S Worker + 持久 Kernel = Execution Plane;共享文件系统 = Data Plane。

**术语规约(先立后写):** 仓库 kernel 层的 `generation` ≡ 规范的 `executionEpoch`——kernel 层沿用 `generation` 字段名,orchestration 层一律用 `execution_epoch`;规范中表示"声明式配置版本"的 generation 在本仓库一律叫 **`spec_revision`**。

**不变量(测试与代码注释以 INV-n 引用):**

- **INV-1 单用户不变:** `team_mode` 关闭时,行为与主线逐字节一致;现有测试零修改保持绿。
- **INV-2 Backend Opacity:** orchestration 核心模块(models/ports/reconciler)的源码与 import 图不含 `slurm` 字样;`partition`/`qos`/`slurm_job_id` 只存在于 Slurm 子包与 cluster.toml;对外 API 只暴露 `allocation_id`,外部 ID 封装为 ExternalHandle。
- **INV-3 唯一活动 Allocation:** 每 workload 同时至多一个活动 allocation,由 SQLite partial unique index 兜底(附录 A)。
- **INV-4 交互 task 永不隐式新建 Allocation/Slurm Job。**
- **INV-5 SessionRunning ⟺ AllocationGranted ∧ WorkerReady ∧ WorkspaceReady ∧ KernelReady;** 绝不因 Slurm 报 RUNNING 就在 UI 宣称就绪。
- **INV-6 终态单调;恢复 = 新 epoch,不改写历史。**
- **INV-7 Epoch 围栏:** 旧 epoch 的 worker/task/回调一律拒绝(`STALE_EPOCH`)。
- **INV-8 提交幂等:** submission_token 提交前持久化且全局唯一;SubmitResult=Unknown 时必须先按 token 对账(squeue/sacct 查 comment)才允许重提。
- **INV-9 密钥卫生:** 长期密钥不出现在 job name/comment/提交脚本/日志/artifact 元数据;Slurm 参数强类型构造,禁止字符串拼接用户输入。
- **INV-10 LLM 无特权:** 一切资源/权限请求必经确定性准入(认证→授权→校验→配额→策略),拒绝带附录 C 原因码。
- **INV-11 恢复透明:** kernel 内存状态丢失必须以 `KERNEL_STATE_LOST` 明示,绝不静默重建续接。
- **INV-12 审计归因:** 治理敏感操作(登录、admin 读私有、配额变更、workload 提交/取消、用户管理)必录 `(actor, delegated_by, user, project, action, target)`。
- **INV-13 隔离:** 非项目成员不可见他人会话/文件/事件;admin 读私有会话留审计(D4)。
- **INV-14 zero-dep:** 核心 import 图零第三方新增;配置解析用 `tomllib`/JSON,凭据签名用 `hmac`,口令用 `hashlib.pbkdf2_hmac`。

**取消屏障固定顺序:** fence 新任务 → 取消活动任务 → drain worker → 取消 backend allocation → 观察终态 → 标记终态。

---

## 3. 全局工程规则

- **兼容开关:** `OPENAI4S_TEAM_MODE`(默认关)。关 = 现状;开 = 登录强制 + 归属过滤。所有新表 additive,不改既有表结构。
- **facade 外科手术:** `server/gateway.py`、`host_dispatch.py`、`store.py`、`sdk/host.py`、`webui/app.js`、`kernel/worker.py`、`kernel/manager.py` 只做定点插入;新算法放进各自的 service/repository 模块。
- **新目录义务:** 每个新目录(如 `openai4s/orchestration/`)需要 `README.md` + `README_zh.md` 双语对并列出直接文件;本文件登记进 `docs/README*.md` 已完成。
- **mypy 严格圈:** 把 `orchestration/models.py`、`orchestration/ports.py` 加入 pyproject 的 mypy 文件清单(契约模块从严)。
- **测试卫生:** 假密码/假 token 用明显假值(`test-password-not-real`);需要真实外部资源的测试用已注册 marker(`external`/`ssh`/`browser`),不发明新 marker;stub 掉服务的路由测试必须标 `stubbed_backend`。
- **webui:** 工作树静态直出,无构建步骤;改完刷新即生效,收尾跑浏览器冒烟。

---

## 4. 验证矩阵(改动类型 → 门禁)

| 改了什么 | 必跑 |
|---|---|
| 任何提交前 | `uv run pre-commit run --all-files`(不是 `--files`,两者结论可能不同) |
| 里程碑收尾 | `uv run pytest` 全套(先 `uv sync --extra science`;不许只跑子模块——全局 Popen patch 会与新真实子进程冲突) |
| gateway 路由/serializer | `uv run python scripts/capture_response_schemas.py --check` + `uv run python scripts/capture_response_contract.py --check`;**新增路由:** 先写驱动真实 handler 的测试(直接方法调用测不出 HTTP 状态码),再跑两个 capture 脚本再生,然后审查 diff——若 `/environments` 两条路由出现本机 conda 环境列表增量,revert 该部分(已知的机器本地漂移),只保留新路由条目 |
| agent core / `host_dispatch.py` | `uv run mypy` |
| 场景/故障/trace | `uv run python -m harness.cli run --tier pr --offline` |
| 新目录 | `uv run python scripts/check_directory_readmes.py`(bash fence 内的 `#` 会被当标题计数,写 README 时注意) |
| 涉密路径 | `python scripts/source_secret_scan.py` |
| webui / kernel / gateway 流式 | `node tests/browser_smoke.mjs`(需先 `npm install --no-save --ignore-scripts playwright@1.54.1 && npx playwright install chromium`,daemon 必须**免凭据**——配了真 key 冒烟会超时;本地需 `OPENAI4S_NOTEBOOK_REPL=1`;8760 被占时用 `OPENAI4S_BROWSER_URL` + 独立 `OPENAI4S_DATA_DIR` 起副本) |
| sandbox / subprocess / 平台探测 | 本地强制走 Linux 分支(mac 绿 ≠ CI 绿:sh exec、无 Seatbelt 有 bwrap) |
| R interrupt 类测试偶发红 | 先单独、安静复跑定性,再决定是否与本工作有关 |

---

## 5. M1 多租户地基

**范围:** 账号、登录、路由鉴权、会话归属、事件流权限化、文件区。**非目标:** 项目治理(M2)、任何 Slurm(M3)。

| # | 工作项 | 要点 |
|---|---|---|
| M1-0 | 现状侦察(有界) | 确认:会话标识与存储位置、WS 广播与 Timeline 投影的全部扇出点、静态文件服务方式、现有 Bearer 鉴权路径。产出 ≤20 行纪要进提交信息。发现与本计划描述不符→按 §0.1-3 处理,不停下 |
| M1-1 | 配置开关 | `config.py` 增 `team_mode`(env `OPENAI4S_TEAM_MODE`)与 `data_roots`(env `OPENAI4S_DATA_ROOTS`,冒号分隔;空 = 沿用现状)。关闭态零行为变化(INV-1) |
| M1-2 | 用户存储 | 新 repository + 迁移(走现有 `schema_migrations`):`users`/`auth_sessions`/`team_audit_log`(DDL 附录 A)。口令 `pbkdf2_hmac('sha256', …, 600_000)` + 独立盐;比较用 `secrets.compare_digest` |
| M1-3 | 用户管理 CLI | `openai4s user add|list|disable|reset-password`;密码经 `--password-stdin` 或自动生成打印一次,绝不进 argv/日志。团队模式首启无用户:打印引导命令后正常启动(不交互不阻塞) |
| M1-4 | 登录路由 | `POST /api/auth/login`(限速:同用户名+IP 令牌桶 5 次/分)、`POST /api/auth/logout`、`GET /api/auth/me`。HttpOnly cookie,`SameSite=Lax`;服务端只存 token 的 sha256。保留 loopback Bearer 通路给服务器上的管理 CLI |
| M1-5 | 路由鉴权中间层 | `_route` 内、现有 Host/Origin 守卫之后插入 team 守卫:未认证 → API 401 / 页面跳登录;admin-only 路由表集中声明。定点插入,不重排现有逻辑 |
| M1-6 | 会话归属 | 新表 `session_owners`(不改既有表)。创建会话时写入;一切会话枚举/读取/操作按归属过滤,admin 除外(INV-13) |
| M1-7 | **事件流权限化(最大项)** | WS 升级时鉴权并绑定 user 上下文;每个广播/投影扇出点按归属+角色过滤。先列全扇出点清单(M1-0 产出)再动手——"守卫只接了一个调用点"是本仓库的惯性缺陷,数完再信 |
| M1-8 | 文件区 | `GET /api/files`(列目录)、`GET /api/files/download`、`POST /api/files/upload`(流式,默认 512 MiB 上限);路径 `resolve()` 后必须落在 data_roots 前缀内(防穿越);webui 最小文件面板 |
| M1-9 | 登录页与前端 | webui 静态登录页;`app.js` 启动查 `/api/auth/me`,401 跳转;显示当前用户/登出。团队模式关闭时前端行为不变 |

**M1 DoD:**
- [ ] pytest 新增:鉴权矩阵(A 不可见/不可操作 B 的会话与文件,真实 handler 驱动)、路径穿越、限速、cookie 过期、WS 事件不跨用户泄漏
- [ ] `OPENAI4S_TEAM_MODE` 关闭:现有全套件零修改绿(INV-1)
- [ ] 新路由契约完成(§4 流程);全部门禁绿

---

## 6. M2 共享与治理(= D10 六义务)

| # | 工作项 | 要点 |
|---|---|---|
| M2-1 | 项目与成员 | 复用现有 `projects` 表可用则用之,不可用则新建 `team_projects`(§0.1-3);`project_members(project_id,user_id,role)`;admin CRUD 路由 |
| M2-2 | 可见性 | `session_owners.visibility ∈ {project, private}`,默认 project(无项目 → private);owner 可切换;非成员不可见;admin 读 private 会话时写 `team_audit_log(action='admin_read_private')`(D4/INV-12) |
| M2-3 | 只读回放 | 内部复用 webshare 快照渲染:`GET /api/sessions/{id}/replay`,按可见性授权,不经公网 |
| M2-4 | Guest | `invites` 表(token 只存 sha256,限定 project、限期);guest 角色仅回放路由可用(D3) |
| M2-5 | 用量账本 | `usage_ledger`;挂接:LLM 归一化回复的 usage 字段、kernel `getrusage` 汇报。按 user/project 聚合查询 |
| M2-6 | 配额 | `quotas` 表;执行点:LLM 调用前 + 会话创建前;超限 → `QUOTA_EXCEEDED`(附录 C)。**决策(已定):配额检查自身故障时放行并记审计**——可用性优先,不因记账 bug 卡住科研 |
| M2-7 | 治理面板 | admin 路由聚合用量/会话/审计;webui 管理页最小可用 |

**M2 DoD:**
- [ ] admin 账号可见全组会话与用量报表;member 看不见别的项目;private 对同项目成员隐藏;admin 读 private 产生审计行;guest 仅能回放
- [ ] D10 六义务(配额/计量/账本/隔离/审计/公平)逐条在提交信息中给出落点
- [ ] 全部门禁绿

---

## 7. M3a Backend 抽象与 Slurm BatchJob(规范 Phase 1–2)

| # | 工作项 | 要点 |
|---|---|---|
| M3a-1 | 新包 `openai4s/orchestration/` | `models.py`(Workload kind∈{SESSION,BATCH}、Allocation、ResourceProfile、phase 枚举、附录 C 原因码)、`ports.py`(`AllocationBackend` Protocol:submit/observe/cancel/diagnostics;`SubmitResult = Created|Existing|Rejected|Unknown`)。放 server/ 之外(与 `execution/` 同摆位) |
| M3a-2 | 泄漏守卫先行 | 在写任何 Slurm 代码**之前**提交测试:orchestration 核心模块源码与 import 图不含 `slurm`(白名单其 slurm 子包)(INV-2) |
| M3a-3 | LocalBackend | 现有本地执行收编为默认 backend;行为不变,只是套上 Workload/Allocation 对象形态 |
| M3a-4 | SlurmBroker | 独立模块收敛 `sbatch/squeue/sacct/scancel` 子进程调用;参数强类型构造(INV-9);文本解析只在 broker;submission_token 写入 `--comment='openai4s:tok=<t>;user=<uid>'` |
| M3a-5 | SlurmBackend | 实现 ports 协议;状态映射表(Pending→PENDING、Running→GRANTED/ACTIVE、Timeout→FAILED/TIME_LIMIT_EXCEEDED、NodeFail→LOST、Preempted→LOST/PREEMPTED…);原始状态存 diagnostics,核心只见规范化状态;Unknown → 按 token 对账后才可重提(INV-8) |
| M3a-6 | Reconciler | daemon 内单线程周期(默认 5s):比对 desired/observed 驱动状态机;幂等;取消屏障固定顺序(§2);就绪检查放在 backend RUNNING 分支内(规范 §35.1 伪代码的遮蔽问题,实现时修正) |
| M3a-7 | ClusterProfile | `<data_dir>/cluster.toml`(`tomllib`);profile→partition/QoS/资源映射只在此文件(D5);示例含 `cpu-interactive`/`gpu-interactive`/`gpu-batch`;admin 只读路由展示 |
| M3a-8 | BatchJob 端到端 | 路由:提交/列表/详情/取消/日志尾部;CLI `openai4s cluster submit|list|cancel`;结束后 stage-out 产物 digest 校验通过才 COMMITTED 进 artifacts |

**测试策略(关键,保证离线可测):** 测试内用 `tmp_path` 生成假 `sbatch`/`squeue`/`sacct`/`scancel` 可执行脚本注入 PATH(状态序列可编程;**不放进 `tests/fixtures/`**,那里是字节精确的捕获数据)。真实集群测试标 `external`。**本机无 Slurm 不构成阻塞。**

**M3a DoD:**
- [ ] 假 Slurm 全链路绿:提交→PENDING→RUNNING→COMPLETED→产物 COMMITTED;取消;超时;NodeFail
- [ ] 提交响应丢失 → 按 token 对账 → 不重复提交(INV-8 测试)
- [ ] 泄漏守卫绿(INV-2);新路由契约完成;全部门禁绿

---

## 8. M3b Slurm 上的持久会话(规范 Phase 3)

| # | 工作项 | 要点 |
|---|---|---|
| M3b-1 | 帧协议 TCP 传输 | kernel manager 增加出站 TCP 传输变体:daemon 起 worker 控制监听(env `OPENAI4S_WORKER_LISTEN`,默认关闭);**单帧读取循环、id 路由的 host_response、`_HOST_CALL_LOCK` 事务纪律原样保留**;本地管道路径零改动。改完必跑 `tests/test_kernel.py` 全量 + 整套 |
| M3b-2 | Bootstrap 凭据 | per-daemon secret(数据目录 0600 文件)HMAC 签 `(allocation_id, epoch, rank, expires, nonce)`;凭据写入会话工作区 0600 文件,sbatch 环境只带**路径**;注册即消费 nonce;过期/旧 epoch 拒绝(INV-7/9) |
| M3b-3 | SESSION workload | ComputeSession 经 SlurmBackend 取 Allocation;sbatch 引导 worker 出站连回;四条件合取才置 RUNNING(INV-5) |
| M3b-4 | Lease | `leases` 表 + 回收线程;idleTTL/maxLifetime 取自 profile(默认 2h/48h);到期 → 取消屏障 → scancel,原因 `SESSION_IDLE_TIMEOUT`/`SESSION_MAX_LIFETIME_EXCEEDED`;**worker 存活心跳不算用户活跃**,用户执行/显式续期才算 |
| M3b-5 | 恢复 | WORKSPACE_ONLY:allocation LOST → 新 epoch 重拉 worker;时间线与 UI 明示 `KERNEL_STATE_LOST`(INV-6/11) |
| M3b-6 | UI | 新建会话选运行位置(本机 \| profile);集群会话显示 allocation 状态徽标;恢复横幅 |
| M3b-7 | 测试 | 假 sbatch 直接本机 `Popen` 拉起**真 worker** 连回 daemon → 全链路离线可测;场景:跨多轮变量存活、中断、取消、租约到期、凭据过期、旧 epoch 拒绝 |

**M3b DoD:**
- [ ] 离线假 Slurm 下:同一会话跨多轮复用同一 kernel 变量;idle 到期资源确实释放;恢复后 UI 明示状态丢失
- [ ] `tests/test_kernel.py` 全量 + 整套绿;浏览器冒烟绿;全部门禁绿

---

## 9. M4 外延(每项独立可裁)

| # | 工作项 | 验证方式 |
|---|---|---|
| M4-1 | 个人 LLM key 覆盖(D7 后半):per-user 密钥走现有 secret store,调用链按 user 解析 | pytest(密钥隔离、回落组 key) |
| M4-2 | DISTRIBUTED task = 既有 allocation 内 `srun` job step(INV-4 不破) | 假 srun |
| M4-3 | 多节点 gang 就绪:`registered == expected` 才 Ready | 假 Slurm 多 rank |
| M4-4 | harness 场景库:规范 §50 二十条中可离线复现的 ≥12 条(声明失败的用例在成功时判负) | `harness.cli --tier pr --offline` |
| M4-5 | relay 公网后手 | **仅文档 + 配置样例;deploy-only,不做 E2E,不阻塞收尾** |
| M4-6 | CHECKPOINT 恢复策略占位:接口 + UI 明示"暂不支持"(真 checkpoint 后续版本) | pytest(拒绝语义) |

---

## 10. 全局完成定义

- [ ] M1–M3b 全部 DoD 达成;M4 各项按其验证方式达成或在附录 D 说明裁掉的理由
- [ ] `uv run pytest` 全套 + `pre-commit --all-files` + mypy + 两个 capture `--check` + harness pr tier + README 检查 + secret scan 全绿
- [ ] 团队模式关闭:与 `origin/next` 基线行为一致(INV-1 回归测试)
- [ ] 部署一张纸(§0.3)已写入 PR 描述或 `docs/team-server-plan.md` 附录 D 之后

---

## 附录 A:数据模型 DDL 草案

Additive-only;字段可按现状惯例微调,**约束语义不可丢**。全部走现有 `schema_migrations` 机制。

```sql
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT,
  role TEXT NOT NULL CHECK (role IN ('admin','member','guest')),
  password_hash BLOB NOT NULL, password_salt BLOB NOT NULL, iterations INTEGER NOT NULL,
  disabled INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);

CREATE TABLE IF NOT EXISTS auth_sessions (
  token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  created_at REAL NOT NULL, expires_at REAL NOT NULL, last_seen_at REAL);

CREATE TABLE IF NOT EXISTS team_audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
  actor TEXT NOT NULL, delegated_by TEXT, user_id TEXT, project_id TEXT,
  action TEXT NOT NULL, target TEXT, detail TEXT);

CREATE TABLE IF NOT EXISTS session_owners (
  session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, project_id TEXT,
  visibility TEXT NOT NULL DEFAULT 'project' CHECK (visibility IN ('project','private')));

CREATE TABLE IF NOT EXISTS project_members (
  project_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'member',
  UNIQUE (project_id, user_id));

CREATE TABLE IF NOT EXISTS invites (
  token_hash TEXT PRIMARY KEY, project_id TEXT NOT NULL, created_by TEXT NOT NULL,
  expires_at REAL NOT NULL, used_at REAL);

CREATE TABLE IF NOT EXISTS usage_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
  user_id TEXT NOT NULL, project_id TEXT, kind TEXT NOT NULL, amount REAL NOT NULL, ref TEXT);

CREATE TABLE IF NOT EXISTS quotas (
  scope TEXT NOT NULL CHECK (scope IN ('user','project')), scope_id TEXT NOT NULL,
  kind TEXT NOT NULL, limit_amount REAL NOT NULL, window TEXT NOT NULL,
  UNIQUE (scope, scope_id, kind, window));

CREATE TABLE IF NOT EXISTS workloads (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('SESSION','BATCH')),
  owner_user_id TEXT NOT NULL, project_id TEXT, spec_json TEXT NOT NULL,
  spec_revision INTEGER NOT NULL DEFAULT 1, desired_state TEXT NOT NULL,
  phase TEXT NOT NULL, execution_epoch INTEGER NOT NULL DEFAULT 0,
  reason TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);

CREATE TABLE IF NOT EXISTS allocations (
  id TEXT PRIMARY KEY, workload_id TEXT NOT NULL, epoch INTEGER NOT NULL,
  phase TEXT NOT NULL, external_backend TEXT, external_ns TEXT, external_id TEXT,
  submission_token TEXT UNIQUE NOT NULL, observed_json TEXT,
  created_at REAL NOT NULL, released_at REAL,
  UNIQUE (workload_id, epoch));
-- INV-3 兜底:
CREATE UNIQUE INDEX IF NOT EXISTS ux_active_allocation ON allocations(workload_id)
  WHERE phase IN ('SUBMITTING','PENDING','GRANTED','ACTIVE');

CREATE TABLE IF NOT EXISTS leases (
  workload_id TEXT PRIMARY KEY, created_at REAL NOT NULL, last_active_at REAL NOT NULL,
  idle_ttl_s INTEGER NOT NULL, max_lifetime_s INTEGER NOT NULL);
```

## 附录 B:新增路由清单(全部需要契约,§4 流程)

- 认证:`POST /api/auth/login` · `POST /api/auth/logout` · `GET /api/auth/me`
- 用户管理(admin):`GET/POST /api/team/users` · `POST /api/team/users/{id}/disable` · `POST /api/team/users/{id}/reset-password`
- 文件区:`GET /api/files` · `GET /api/files/download` · `POST /api/files/upload`
- 回放:`GET /api/sessions/{id}/replay`
- 治理(admin):`GET /api/team/usage` · `GET /api/team/audit` · 项目 CRUD · 邀请 CRUD
- 编排:`POST/GET /api/orchestration/jobs` · `GET /api/orchestration/jobs/{id}` · `POST /api/orchestration/jobs/{id}/cancel` · `GET /api/orchestration/jobs/{id}/logs` · `GET /api/orchestration/profiles`

路由命名可按 gateway 现状惯例微调(附录 D 记录)。

## 附录 C:标准原因码(裁剪自规范 §40)

```
AUTHENTICATION_FAILED  AUTHORIZATION_DENIED  QUOTA_EXCEEDED  POLICY_REJECTED
INVALID_SPEC  BACKEND_UNAVAILABLE  BACKEND_SUBMISSION_UNKNOWN  BACKEND_REJECTED
UNSCHEDULABLE  BOOTSTRAP_FAILED  WORKER_REGISTRATION_TIMEOUT  WORKER_LOST
NODE_FAILED  OUT_OF_MEMORY  TIME_LIMIT_EXCEEDED  PREEMPTED
USER_CANCELLED  ADMIN_CANCELLED  SESSION_IDLE_TIMEOUT  SESSION_MAX_LIFETIME_EXCEEDED
STALE_EPOCH  STALE_SPEC_REVISION  DUPLICATE_SUBMISSION  KERNEL_STATE_LOST
```

## 附录 D:执行偏差记录(执行代理追加,每行:日期 · 条目 · 原状 · 改法与理由)

- 2026-08-14 · §0.2 分支基点 · "从最新 origin/next 切" · 从本地 next(= origin/next + 本计划文档提交 6867582)切出 feat/team-server——计划文件本身尚未推到 origin,分支上必须携带它才能追加本附录。
- 2026-08-14 · 附录 A 时间戳 · `created_at REAL` · 按仓库 storage 惯例改为 INTEGER 毫秒(注入的 `clock_ms`);列名与约束语义不变。
- 2026-08-14 · 附录 B 路由名 · `/api/auth/login` 等 · 网关 API 带版本前缀(contract.API_ROOT = /api/v1),落地为 `/api/v1/auth/*`,与既有 `/auth/status` 同侧;文件区同理为 `/api/v1/files*`。
- 2026-08-14 · M1-4 loopback Bearer · "保留 loopback Bearer 通路给管理 CLI" · service 身份仅接受来自 127.0.0.1/::1 对等端的 header 令牌(X-OpenAI4S-Token / Bearer);团队模式下 `?token=` 换 cookie 的 bootstrap 流程停用——浏览器一律走 /login,机器令牌不再能变成已登录浏览器。
- 2026-08-14 · M1-6 无归属会话 · 计划未定义(团队模式前历史、demo 播种、CLI 直跑产生的根 frame 无 owner 行) · 判为 admin-only(fail closed,决策优先级"安全默认");M2 项目可见性落地后可再放宽。
- 2026-08-14 · M1-6 拒绝形态 · 计划未定义 · 越权按址访问一律 404 而非 403——"哪些会话存在"本身是被保护的信息(INV-13);WS 侧 view_denied/cancel 拒绝用同一句 "session not found"。
- 2026-08-14 · M1-4 限速语义 · "同用户名+IP 令牌桶 5 次/分" · 桶空时连正确口令也拒绝(429),且在 PBKDF2 计算之前扣桶——限速同时约束攻击者能诱发的哈希算力;桶内存态,重启即忘(每次猜测的 PBKDF2 成本远大于此)。
- 2026-08-14 · M1-8 guest 与文件区 · D3 只说"仅只读回放" · guest 对 /files 三条路由一律 403(读也不给):文件区不属于回放面,回放路由在 M2-3 落地。
- 2026-08-14 · M1-7 已建立连接的吊销 · 计划未定义 · 禁用用户/登出后,其已打开的 WS 连接的既有订阅在连接存续期内不被回收(订阅时一次性授权);新订阅与新 HTTP 请求立即失效。M2 治理面可加周期性重校验。
- 2026-08-14 · 守卫正则与契约扫描 · 契约扫描器把内联 `re.fullmatch(r"...", sub)` 识别为路由 · 团队范围守卫的两个匹配器改为模块级预编译常量,避免把守卫模式发布成端点(`/artifacts/([^/]+)(?:/.*)?` 曾被误捕)。
- 2026-08-14 · M2-2 "每次查看写审计"粒度 · 计划未定义粒度 · 审计单位 = 每个命中 private 会话的 admin GET 请求 + 每次 WS view_session 订阅;同一页面打开会产生多行(messages/timeline 各一)——宁多勿漏。
- 2026-08-14 · M2-4 guest 入口 · 计划只说 invites 表与"guest 仅回放路由可用" · 落地为 `POST /api/v1/auth/redeem-invite {token,username,password}`:兑换即建 guest 账号 + project_members(role=guest) + 登录 cookie;用户名冲突在消费 token 之前拒绝(单次 token 不被打字错误烧掉);guest 的 API 面收敛为 auth/* + `GET /sessions/{id}/replay`,页面面为 /login、/replay。
- 2026-08-14 · M2-5 LLM 计量盲区 · "挂接:LLM 归一化回复的 usage 字段" · 台账挂在 RuntimeActionLedger(覆盖主 turn 与 delegation 子代理);titles/compaction/host.llm/安全三门四条旁路 LLM 调用不经 ledger,其 usage 暂不入账(占比小、无 frame 上下文可归因);留待后续下传 context。
- 2026-08-14 · M2-6 配额执行点覆盖 · "执行点:LLM 调用前 + 会话创建前" · LLM 门挂在 Web ChatModel(每次 provider 请求前查 user+project 两个 scope);delegation 子代理的 ChatModel 由 agent/loop 构造、暂无门——子代理超额消耗仍被记账,主 turn 的下一次调用即被拒,窗口有界;会话创建门在 SessionRunner.create_session,会话导入路径(session_package)不经此门(导入不"创建"计费会话,但 _team_claim_imported 已记归属)。
- 2026-08-14 · 附录 A allocations 活动 phase 集合 · `WHERE phase IN ('SUBMITTING','PENDING','GRANTED','ACTIVE')` · 增加 `RELEASING`。语义理由:INV-3 的目的是"不得同时有两个 allocation 持有资源",而正在拆除的 allocation **仍持有**一个真实作业;排除它会让取消进行中就能提交新 allocation(正是该不变量禁止的双分配),并且会让取消屏障在第二遍找不到自己的 allocation,从而在作业仍在运行时把 workload 标成已取消(测试已抓到该缺陷)。
- 2026-08-14 · M3a 收尾发现的三个真缺陷(全套里红、单跑绿的那条测试查出来的,均已修+回归) · (a) `save_workload` 会把**陈旧的 `desired_state`** 写回,一个在取消到达前载入 workload 的 tick 会把 STOPPED 覆盖回 RUNNING——用户的取消被静默丢弃、作业继续跑、且没有任何地方记录该请求被抹掉;修法是规则而非加锁:desired_state 是用户的意图,reconciler 是执行者不是作者,该列移出 UPDATE。(b) 同一形状在隔壁一列:陈旧的 `reason=NULL` 会抹掉 `USER_CANCELLED`,于是被取消的作业在审计里"无缘无故"结束;改为 COALESCE——写 NULL 意为"我没有要报告的",绝非"忘掉你已知的"。(c) 从未拿到 handle 的 allocation 无法被取消:backend 对没见过的 allocation 答 SUBMITTING(非终态),屏障于是每 tick 重入、永不完成;修法要点是那个例外——带 `BACKEND_SUBMISSION_UNKNOWN` 的 allocation 无 handle **却可能已落地**,必须先 `find_by_token` 再下结论,否则会把仍在跑的作业标成已结束。
- 2026-08-14 · M3a-8 stage-out 产物 digest 校验 · "结束后 stage-out 产物 digest 校验通过才 COMMITTED 进 artifacts" · **本次未实现,明确留待**。理由:stage-out 需要 M3b 的共享工作区语义(作业在哪写、daemon 从哪读)才有确定含义;在没有共享文件系统约定的情况下实现它,等于给"产物已校验"这个说法一个无法验证的实现。当前 BatchJob 的产物面是 `GET /orchestration/jobs/{id}/logs`(尾部输出)。M3b 落地工作区后补齐,届时 digest 校验才有真实对象。
- 2026-08-14 · M3a reconciler 周期 · "daemon 内单线程周期(默认 5s)" · 默认仍为 5s,但增加 `OPENAI4S_RECONCILE_INTERVAL` 可调。理由:端到端测试在 5s 周期下要花 75 秒等 tick,而慢到会被跳过的套件是最贵的那种慢;生产默认不变。
- 2026-08-14 · M3a-7 cluster.toml 解析 · "配置解析用 `tomllib`"(INV-14) · `tomllib` 是 3.11+ 而本仓 `requires-python` 为 3.10 且 CI 矩阵真的跑 3.10——只用 tomllib 会让整个集群功能在下限版本上不可用。改为:有 tomllib 用之,否则用自带的受限子集解析器(表/字符串/整数/布尔),遇到数组、浮点等不认识的语法**带行号拒绝**而非猜测。仍是零依赖(不引入 tomli),语义"配置解析不引第三方"未丢。
- 2026-08-14 · M2 加固 · 对抗审查(6 视角 × 2 反驳者)在自测全绿的分支上查出隔离漏洞,已全部修复并加回归 · (a) artifact 字节:守卫原先只按路径在 `_api` 内检查,`/preview/`(更早分发)与 version/filename 寻址完全绕过——检查下沉到 `_serve_artifact` 字节 chokepoint,且会话不可判定时 fail closed;(b) `/projects/*` 原先零鉴权(任意成员可读/改名/删除任意项目,列表泄漏全部项目名)——新增参与者守卫(成员行 **或** 在该项目拥有会话),`GET /projects` 按参与者过滤,建项目即成为成员;(c) WS 身份原为握手时一次捕获,禁用/改密后旧连接仍有权——改为每条消息重新解析;(d) `host.query` 裸子串预检因新增 `users`/`invites`/`quotas` 误伤单用户查询(INV-1 回归)——改词边界正则,授权器仍是真正防线;(e) `revoke_invite` 的 `LIKE` 前缀可用 `%` 一次吊销全部邀请——改 `substr` 字面比较;(f) 兑换邀请时用户名竞态会烧掉单次 token——失败即 `reinstate_invite` 归还;(g) reviewer 经独立 port 触达 provider、绕开 LLM 配额门——抽出 `SessionRunner.enforce_llm_quota` 供两处共用;(h) 登录令牌桶裁剪判据永不成立、字典无界增长——改按空闲时长裁剪;(i) `set_quota` 允许无执行点的 kind(形同虚设的限额)——限制为 `ENFORCED_QUOTA_KINDS`。
- 2026-08-14 · M2-3 回放实现 · "内部复用 webshare 快照渲染" · 直接调用 ShareProjectionBuilder.build + serialize_view 现场生成(FIFO ticket 内),不落 shares 行、不占"唯一活跃 share"名额、不需 relay 配置;脱敏语义原样保留(guest 只能触达已脱敏投影);查看器为独立 /replay 静态页(share viewer 资产的路径与 credentials:"omit" 不适配登录态)。
- 2026-08-15 · M3b-1 传输抽象 · 计划只要求"kernel manager 增加出站 TCP 传输变体" · 落地为 `kernel/transport.py` 的 `KernelTransport` Protocol（`PipeTransport` + `OutboundTcpTransport`），manager 的单帧读取/id 路由/事务锁一字未动。搬迁过程中暴露两个真缺陷：(a) manager.py 存在整块方法重复，且**运行时胜出的是改造前的 `restart()`**——它直接摸本地子进程的 `.stdin`，在远端路径上那是一个从不存在的 None；我的替换版是死代码且丢了每次 respawn 都欠看门狗与租约的 `generation += 1`。(b) 修好之后，远端 worker 的 `restart()` 没有诚实答案：别处放置的 worker 不归本进程重生，改为明确拒绝并说明，而不是返回一个"看起来重启了、下一个 cell 才炸"的 Kernel——调用方正确的动作是恢复（新 epoch、状态丢失），而它只有被告知才做得出这个动作。
- 2026-08-15 · M3b-3 每次尝试的 spec · 计划未定义凭据如何进入提交 · Reconciler 增加 `prepare_attempt(workload, allocation) -> WorkloadSpec` 钩子（默认恒等，BATCH 与既有调用方零改动）。理由：凭据绑定 (allocation, epoch)，在一次尝试存在之前它无法存在；由此持久 spec_json 永不含签名（INV-9），且一次恢复是真正的新尝试而非丢失那次身份的重放（INV-7）。
- 2026-08-15 · M3b-4 leases 表 · 附录 A DDL 无 session↔workload 映射 · 新增 `session_workloads(session_id PK, workload_id UNIQUE)`。理由：集群内核必须能从聊天会话双向找到（正向答"我的内核好了吗"，反向答"该给谁的时间线写状态丢失"）；把 session_id 塞进 spec_json 会让两个方向都变成 JSON 表扫描，且配对无法被约束强制。
- 2026-08-15 · M3b-5 恢复次数 · 计划未定义上限 · `DEFAULT_MAX_RECOVERIES = 3`（可注入）。理由：一个把每个交给它的 worker 都弄死的节点，否则会被无限重投；无限重试在有人去读账单之前，与一个正常工作的系统完全无法区分。
- 2026-08-15 · M3b-7 发现的真缺陷:取消原因被 backend 覆盖 · `workload.reason = observed.reason or reason` · 改为 `workload.reason = reason`（allocation 仍记 `observed.reason or reason`）。两行记的是两件事：allocation 记资源平面说这次尝试**遭遇了什么**（被抢占就是被抢占，那是它的历史），workload 记**我们为何**结束它——而后者只有我们知道：调度器被问到一个按我们指令取消的作业，只能答"cancelled"。让它的答案胜出，会使租约回收、管理员取消与用户取消统统上报 USER_CANCELLED：对用户谎称是他自己取消了系统收回的会话，对审计 GPU 释放的运维给出一个纯属虚构的原因。M3a 的用例恰好都用 USER_CANCELLED 请求取消，于是这个缺陷在那时不可见。
- 2026-08-15 · M3b-6 契约扫描器再次误捕 · `sub.startswith("/sessions/")` 内联 · 同 M1 的守卫正则一样提为模块常量；顺带修了 `server/README.md` 里两行表格被并成一行（kernel_routes 的描述落在 orchestration_routes 那一行里，kernel_routes 自己那格是空的）——README 门禁只查"文件被提到"，所以它一直是绿的。
- 2026-08-15 · M4-1 密钥槽位名 · 计划未定义 · broker 的引用会变成 keychain account 名并进日志，因此只允许 `[A-Za-z0-9-_.]`；`user_id:provider` 里的冒号会在 `put` 处被拒，表现为一个本身没有任何问题的请求返回 503。改用 `.` 连接。
- 2026-08-15 · M4-1 读不出来的个人密钥 · 计划未定义 · 判为**拒绝该轮**（409 `user_key_unreadable`）而不是静默回落到组 key：用户明确要求用自己的凭据，悄悄改记到组里是一个他没有做过的决定。区别对待"查找本身出故障"（回落，可用性优先，与配额门一致）与"配置了但槽位是空的"（拒绝）。
- 2026-08-15 · M4-1 账号禁用与密钥 · 计划未定义 · 禁用用户即清空其全部个人密钥行。理由：数据库里那一行是唯一还会指向该槽位的东西，留着它等于让凭据既不可达又不可撤销。
- 2026-08-15 · M4-3 发现的真缺陷:多 rank 注册被覆盖 · `WorkerGateway._arrived[key] = registration` · 改为按 (allocation, epoch) 存**列表**。多节点作业每个 rank 都持一份同 (allocation, epoch) 的凭据拨回来，原实现里 rank 1 会静默顶掉 rank 0——一个两节点会话看起来就是一个跑得好好的单节点会话，而 gang 就绪要数的正是这些注册。同时新增 `await_workers(expected=…)`：超时返回**已到的那部分**而不是空，好让界面能说"4 个到了 3 个"而不是"没就绪"——那是诊断与转圈图的区别；也因为丢掉那 3 个注册会让它们连着、等着、且无法释放。
- 2026-08-15 · M4-3 gang 与 INV-5 的关系 · 计划未定义 · `gang_complete` 是 `worker_registered` 的**细化**而非并列的第五个条件：单节点是绝大多数情形，expected ≤ 1 时它就等于那个布尔。否则每一处手工构造的 readiness 都得带上一个谁也没有的计数，而默认的 0 会把一个已就绪的会话变成卡住的会话。
- 2026-08-15 · M4-2 srun 的 `--jobid` · 规范只说"既有 allocation 内的 job step" · 落地时把它当作被断言的不变量而非注释：不带 `--jobid` 的 `srun` 会自己去申请资源，正确行为与昂贵行为之间只差这一个参数。没有 handle 的 workload 直接**拒绝**并说明（INV-4），而不是退回去提交一个——那正是该不变量禁止的。
- 2026-08-15 · M4-6 拒绝的 HTTP 语义与检查顺序 · 计划只说"接口 + UI 明示暂不支持" · 落地为 `501` 而非 `400`：请求本身是良构的、策略也是本产品命名过的真实策略——是**这个版本**honour 不了它，用 400 等于告诉用户是他错了。且该检查放在 profile 校验**之前**：本版本能不能 honour 一个恢复策略与站点怎么配置无关，用户选了 CHECKPOINT 就该被告知这件事，不管他填的是哪个 profile;答"未知 profile"会把他支去改一个改了也没用的东西。
- 2026-08-15 · M4-5 relay 公网后手 · "仅文档 + 配置样例;deploy-only" · 写在新增的 `docs/team-server.md`（中英双份）里，并**明确说明 relay 不是访问实验室服务器的第三条路**:`openai4s share`/`relay` 发布的是单个会话的只读脱敏投影，不带 cookie、无写路由、无活内核;推荐路径是 SSH 隧道或带 TLS 的反向代理。把这一点写清楚，比给一份"能用但会被误用"的样例更重要。
- 2026-08-15 · M4-4 harness 场景的形态 · "规范 §50 二十条中可离线复现的 ≥12 条" · 规范本身不在仓库内，§50 的二十条无法逐条引用；按 §0.1 决策优先级，从**在仓库内**的东西推导：附录 C 的原因码与 §2 的不变量。落地 12 条，覆盖 INV-3/4/6/8/11 与取消屏障。**关键取舍：** 这些场景驱动的是**真的** `Reconciler`（backend 被脚本化），而不是在 harness 里把 reconciler 的规则重写一遍——后者是拿模型验模型，会在模型与代码共有的每一个缺陷上保持绿色，而那正是"把问题理解错了"所产生的全部缺陷。这与 harness README 里"generic runner 不导入生产运行时"是相容的：那里已有先例（action-routing eval 就在录制输入上调用生产函数），判据是"这条边界有没有活的东西需要替身去顶"，而 reconciler 的输入是一行 workload 和一个 observation，都是数据。
- 2026-08-15 · M4-4 "声明失败的用例在成功时判负"是被验证过的 · 计划要求 · 把 `recovery.is_bounded_rather_than_endless` 的观测改成每次都成功，该场景确实变红（`terminal_reason: expected 'LOST:NODE_FAILED', got 'PENDING:KERNEL_STATE_LOST'`），随后原样还原并以 sha256 相同确认。写下来是因为"绿了"本身不能证明一个门禁真的会红。
- 2026-08-15 · M4-4 写场景时被真实轨迹纠正 · 我最初写的 4 条期望是错的 · 其中 3 条错在 `allocation_draining` 出现了两次——那是屏障在资源平面尚未跟上时**可重入**的真实轨迹（每 tick 一次 drain，直到它承认资源已消失）。改的是期望而不是脚本，并把这一点写进那条场景的 task 描述：这正是场景库该记录下来的东西。

---

## 附录 E:M3b / M4 收尾核对(执行代理填写,2026-08-15)

**M3b DoD**

- [x] 离线假 Slurm 下同一会话跨多轮复用同一 kernel 变量:`tests/test_cluster_session_e2e.py::test_variables_survive_across_turns`——假 sbatch 真的把作业跑起来，真 worker 经真 socket 拨回，`import math` 与列表跨三次 `execute` 存活。
- [x] idle 到期资源确实释放:同文件 `test_a_lapsed_lease_takes_the_resource_back`——内核在整段时间里都在应答（那恰恰**不算**用户活跃），到期后 reconciler 走完屏障，假调度器的 `in_queue` 归 0。
- [x] 恢复后 UI 明示状态丢失:`test_recovery_tells_the_session_its_kernel_memory_is_gone` + `/sessions/{id}/compute` 的 `state_lost_epochs` + 前端 `#compute-lost` 横幅（按丢失 epoch 集合去重，再丢一次会再抬起来）。
- [x] `tests/test_kernel.py` 全量 + 整套绿:整套 exit 0（静置树上跑，见下条）。
- [x] 浏览器冒烟绿:`node tests/browser_smoke.mjs` 与 `node tests/browser_admission_fault.mjs` 均通过（免凭据 daemon + `OPENAI4S_NOTEBOOK_REPL=1`，独立 data dir，跑完即清）。

**M4**

- [x] M4-1 个人 LLM key(`tests/test_user_llm_keys.py`,11 例)
- [x] M4-2 DISTRIBUTED = allocation 内 srun job step(`tests/test_orchestration_distributed.py`)
- [x] M4-3 gang 就绪 `registered == expected`(同文件，真 socket 多 rank)
- [x] M4-4 harness 场景 12 条(`harness/scenarios/orchestration/`,`--tier pr --offline` 15/15)
- [x] M4-5 relay 公网后手:文档 `docs/team-server.md`(中英)——并明确它**不是**访问实验室服务器的路
- [x] M4-6 CHECKPOINT 占位:接口 + 501 拒绝语义(`tests/test_orchestration_session.py`、`tests/test_compute_session_routes.py`)

**§10 全局门禁(最终树 2a42baf)**

| 门禁 | 结果 |
|---|---|
| `uv run pytest` 全套 | exit 0 |
| `uv run pre-commit run --all-files` | 全项 Passed |
| `uv run mypy` | Success（8 files） |
| `capture_response_contract.py --check` | 183/183 |
| `capture_response_schemas.py` 再生 + 两条覆盖测试 | 绿；diff 纯增量、仅新路由，无 `/environments` 本机漂移 |
| `harness.cli run --tier pr --offline` | 15/15 |
| `check_directory_readmes.py` | 107 目录、883 文件，双语齐全 |
| `source_secret_scan.py` | 通过（1127 文件） |
| 浏览器 smoke + admission-fault | 通过 |

**一条值得记下来的排查:** 两次全量各有 3 条相同的红——`test_biosecurity_web_parity`、`test_model_binding_recovery`、`test_model_revision_binding`——单跑全绿。三条都基于 `inspect.getsource(gateway_mod.SessionRunner.*)`，而两次运行期间我都在编辑 `gateway.py`：`getsource` 在**调用时**按代码对象记下的行号去读磁盘，文件行数一变，读回来的就是错位的切片。在临时副本上复现确认了该机制（编辑后 `getsource` 确实返回了错位内容），随后在**静置树**上重跑全量，三条全绿。教训比结论更有用：源码内省型断言的"绿"只对读取那一刻的磁盘成立，所以里程碑收尾的那一遍全量必须在不动树的前提下跑。

## 附录 F:外部审查(2026-08-15)确认并修复的 13 项

一次外部审查在自测与 CI 全绿的分支上提了 13 条。**13 条全部经独立复核为真**(12 条并行核实 + 1 条我自验),按严重度修复如下。每条的守卫都用"拆掉它、看对应测试变红"验证过。

| # | 缺陷 | 修法要点 |
|---|---|---|
| 1 | `POST /frames` 带别人的 project_id 即入伙,而入伙就是 `DELETE /projects/{id}` 的全部授权 | 建会话需授权(未被认领的播种项目仍开放,"认领"= 有成员行,刻意不含"有谁的会话",否则第一次越权入伙反而把项目锁死);破坏性动词改为要求**真成员行** |
| 2 | INV-8 只覆盖 `BACKEND_SUBMISSION_UNKNOWN` 这个原因码 | 谓词改为持久**状态**:`SUBMITTING` 且无 handle 就是"结果没被记录下来的提交";提交前行已落库,所以"submit 成功但 save 前崩"这个最常见窗口原先根本不进 INV-8 分支,而无 handle 的 observe 永远答 SUBMITTING,取消屏障于是判定"什么都没放上去"并把仍在跑的作业标成已取消 |
| 3 | `--export` 的**值**未校验,`{"X":"1,ALL"}` 让作业继承 daemon 环境(即 LLM 密钥所在) | 在校验 key 的同一收口校验 value(逗号/换行/NUL/关键字);拼装后再查一遍,防止将来新字段绕过 dataclass |
| 4 | `host.query` 在团队模式下可读全员 `messages`/`execution_log`/`frames` | 会话/执行一族按团队模式纳入受限视图(`my_messages` 等);团队模式关闭时表集合与从前逐字节相同(INV-1) |
| 5 | `/shares` 全局:任何成员可列全组分享 URL、撤销任何人的快照 | share 继承其所投影会话的可见性判定,放在守卫而非两个 handler 里;列表在 handler 侧过滤 |
| 6 | `/memory?project_id=` 绕过项目守卫(守卫匹配路径,scope 在参数里) | 增加按 scope 的守卫并在五个 handler 调用;`all`/`global` 两个实例级层收归管理员 |
| 7 | 文件区只判"路径在 data_roots 内",`overwrite=1` 可覆盖他人文件 | 按构造分区:成员写入 `<root>/<用户名>/`。事后判权不可能——所有文件都是 daemon 的 uid 写的 |
| 8 | 恢复的两次写之间崩溃 → 下一 tick 撞 `UNIQUE (workload_id, epoch)`,**永远**恢复不了 | 退休旧 allocation 与开启新 epoch 合并为一次事务。注意:仅调换顺序不行,那只是把窗口从 UNIQUE 移到 INV-3 的部分唯一索引 |
| 9 | M3b 从未接线:无生产 `attach_worker`、无 `kernel_factory`、`touch()` 从不被调用,且 `ensure_reconciler()` 每次提交都把监听器关掉 | 在 `_spawn_kernel`(会话内核唯一创建点)路由远端内核;在 Cell 边界续租;teardown 挪回 `close()` |
| 10 | nonce 与 epoch 栅栏只在内存——"重启即失效"的理由是错的 | 栅栏落盘(0600、原子写、在告知调用方之前写)。重启带走的是 worker 的**连接**,不是那份还在共享文件系统上、还在 24h TTL 内的凭据文件 |
| 11 | 任何成员可 `POST /config/llm` 改 `llm_base_url`,把全员 LLM 流量导向自己 | 实例级配置的写操作收归管理员(读保留,UI 要显示当前模型) |
| 12 | gang 只签 rank 0 的凭据;`attach_worker` 用 `arrivals[0]` 而非 rank 0 | 每 rank 一份凭据(文件名带 rank,否则互相覆盖);驱动内核的固定为 rank 0——由网络时序决定谁是 driver 的分布式作业,其结果也由网络时序决定 |
| 13 | 删会话只删归属行,集群作业继续跑 | 删除服务增加独立的 `release_compute` 协作者(不是塞进 `drop_runtime`——那正是本仓"守卫只接了几个调用点之一"的老毛病);记录持久停止请求而非当场执行,于是调度器不可达时删除也不会留下无人取消的作业 |

**审查的方法论批评也是对的**,并已回应:鉴权原先是"这个路径匹不匹配那两条正则",于是凡不是 `/frames/{id}` 或 `/projects/{id}` 的面都还是单用户 API——1/5/6/7/11 是同一个形状的五个实例。决策现在集中在 `openai4s/server/team_policy.py`,写成关于资源的谓词。调用点仍须主动去问(没有机制能强迫一条新路由去问),但至少只有一个模型要读、一处规则要改。

**两处过程教训,记下来比结论有用:**
- 我的 denylist 改动一度**丢失**:一次为证伪而做的 `git stash`/`pop` 把它带走了,而我当时只检查了**另一个**文件就认定"已还原"。还原必须按内容验,不能按假设。
- 两个修复里各有一个 bug,都是被"属主仍能访问"这半边断言抓住的:身份字典的键写成了 `user_id`(仓库读的是 `id`),以及 `my_messages` join 到了可空的 `frame_id`。**永远为空的受限视图和拒绝一切的守卫,从外面看与正常工作的守卫一模一样。**

## 附录 G:第二次外部审查(Codex,基线 `23a20b4...eabecbb`)8 项的处置

基线早于附录 F 的修复,所以 8 项里 3 项已在 F 中闭合。其余 5 项全部核实为真并修复;每道守卫都用"拆掉它、对应测试变红"验证过(只读根有两道检查,两道都拆才红——那是纵深,不是测试弱)。

| # | 结论 | 处置 |
|---|---|---|
| 1 项目 CRUD | 已由 F-#1 闭合(建会话需授权;破坏性动词需真成员行)。Codex 建议"项目 CRUD 一律管理员",**未采纳**:D-决策与 M2-1 让创建者成为成员并管理自己的项目;成员制已闭合升权路径。记录于此供审阅者权衡 | 无新改动 |
| 2 host.query | 已由 F-#4 闭合(会话/执行一族纳入团队模式受限视图;三张新表进 denylist) | 无新改动 |
| 3 `/compute/jobs` RCE | **属实,critical**:成员经旧 JobManager 以 daemon uid 跑 `bash -c`。顺带同类:`/permissions` 缺省 `scope="global"`——成员可给全员植入常设放行;`/kernel/install` 往共享 venv 装包;`/compute/remote`、`/connectors` 配置带凭据;`/skills`、`/skills/import` 往全员 agent 都加载的目录发布 recipe | 策略表新增 `DAEMON_OPERATION_*`(全动词管理员)与 `INSTANCE_MUTATION_*`(写动词管理员;连接器的 `/call`、`/probe` 是"使用"不是"配置",保留给成员);`/permissions` 按 scope 判:global 仅管理员,project/conversation 需可达 |
| 4 文件区 | 写半边已由 F-#7 闭合;**读半边与只读根属实**:D8 明写"只读 datasets 区 + 个人 scratch",而根无策略、Bob 仍能下载 Alice 的上传 | `OPENAI4S_DATA_ROOTS` 支持 `path=ro`(对所有人只读,含管理员);个人区命名空间化为 `<root>/users/<name>/`,他人个人区读即 404,共享区仍共享 |
| 5 分享 | 已由 F-#5 闭合 | 无新改动 |
| 6 `/search` datapro | **属实**:三族结果只过滤了两族 | 第三族按同一 `session_visible_to` 过滤(命中项自带 `root_frame_id`,已验证) |
| 7 reviewer 台账 | **属实**:M2 加固把配额**检查**接到了 reviewer,却没接**用量**,台账永不前进 | 抽出唯一记账函数 `record_session_llm_usage`,turn ledger 与 reviewer 共用——同一事实两个写者会漂,一个函数两处调用不会 |
| 8 个人密钥残留 | **属实**:清除只删引用行,broker 里的值留着 | 仓储层拥有整个生命周期:先删 broker 值再删行(顺序刻意:崩在中间留下"指向空槽的行"会被 `user_key_unreadable` 可见地拒绝,而"没有行的活密钥"没人再找得到);禁用账号同理 |

**审阅者应知的一处判断:** Codex 拒绝了"管理员读私有会话的全局视图缺审计"这一项(判为正确性问题而非安全问题)。我同意其定性,本轮**未处理**,留待后续。
