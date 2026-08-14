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
