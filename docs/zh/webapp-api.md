---
title: Web App API
description: 本地工作台 Gateway 所实现的 HTTP 与 WebSocket 描述性契约。
outline: deep
status: current
audience: [contributors, operators]
verified_commit: a92e736
last_verified: 2026-07-14
owner: OpenAI4S maintainers
---

# Web App API 契约（以实际实现为准）

> 已于 2026-07-14 对照仓库修订版本 `a92e736` 验证。这是一份
> 描述性实现契约，而非稳定的公共互联网 API。

本文记录 `openai4s/server/gateway.py`（后端）与
`openai4s/server/webui/app.js`（前端）之间**实际存在的** HTTP/WebSocket
契约，包括已知瑕疵与缺口。它描述现状，而非愿景：下文每项陈述都能映射到
Gateway/前端，或映射到它们组合的某个专门服务（尤其是执行协调器、会话域、
工作台状态和权限服务）。如果你修改了这一公共表面，请同步更新本文档。

范围说明：本文覆盖由 `openai4s serve` / `./start.sh` 启动的 **gateway**。
精简版 `openai4s/server/daemon.py` 单页 UI 及其 `/run` 端点属于另一个更小的
接口表面，不在本文档范围内。

## 1. 传输与一般行为

- 服务器：标准库 `http.server.BaseHTTPRequestHandler`、`HTTP/1.1`
  （`protocol_version = "HTTP/1.1"`），并在 `/api/ws` 上手写 WebSocket
  升级逻辑。默认绑定 `127.0.0.1:8760`。
- REST 位于 `/api/*` 下。处理器会移除 `/api` 前缀，并在
  `Handler._api` 中通过一长串 `if`/`re.fullmatch` 匹配余下部分（`sub`）——
  不存在路由表或 OpenAPI 规范。
- 前端是单页应用；source checkout 直接从工作树提供资源，installed wheel 则从 package-local asset 提供（`/`、`/index.html`、`/static/*`）。
  任何未知的非 API `GET` 都会返回 SPA 外壳（`index.html`），以支持深层链接。
  未知的非 GET、非 API 路径返回 `404 {"error": "not found"}`。
- 所有 JSON 响应均为 `application/json; charset=utf-8`，带有
  `Cache-Control: no-cache` 和明确的 `Content-Length`。
- 除明确记录的 Session 包导入路由外，请求体均为 JSON；该导入路由消费原始
  ZIP 字节。`Handler._body()` 对空或无法解析的请求体会返回 `{}`——格式错误的
  JSON 请求体会被**静默视为空对象**，而不是以 400 拒绝。
- 查询字符串使用 `parse_qs` 解析（每个值都是列表；处理器通过
  `q.get("x", [default])[0]` 读取）。

### 身份认证与 CSRF

- **CSRF/来源保护：** 对 `/api/*` 的每个变更型请求
  （`POST`/`PUT`/`PATCH`/`DELETE`），如果存在 `Origin` 标头且其 netloc 与
  `Host` 标头不同，则以 `403 {"error": "cross-origin request refused"}`
  拒绝。没有 `Origin` 标头的请求（curl、同源 fetch）可通过。
- **令牌门禁**（仅在绑定到非回环地址或设置 `OPENAI4S_REQUIRE_TOKEN=1` 时
  启用）：除 `/health` 外，所有路径都要求 `os_token` cookie 或
  `?token=<hex>`。携带有效 `?token=` 的 `GET` 响应
  `303 Location: /` 并设置 cookie；有效的非 GET 请求继续执行。其他请求获得
  `401 {"error": "unauthorized — append ?token=… to the URL"}`。在默认回环
  绑定下**完全没有身份认证**。

### 错误封装

- 后端错误形状始终为 **`{"error": "<message>"}`**，并带有 HTTP 状态码：
  抛出的 `GatewayError(code, message)` → 以对应状态码返回
  `{"error": message}`；任何未处理异常 → `500 {"error": str(e)}`；
  `_api` 的兜底分支 →
  `404 {"error": "not found", "path": sub, "method": …}`。
- 前端 `api()` 辅助函数读取 `j.error || j.detail`，因此会展示 Gateway 的
  错误文本。为兼容外部适配器，仍接受 `detail`。
- 某些处理器会把错误放在 **200 响应体内部**，而不是返回错误状态：
  `POST /api/connectors/{id}/call` 在异常时以 HTTP 200 返回
  `{"error": str(e)}`；`POST /api/artifacts/{aid}/versions/{vid}/restore`
  会把软错误 `{"error": …}` 映射为 404，但其他处理器会直接以 200 透传
  软错误。不要假设“2xx ⇒ 不含 `error` 键”。

### JSON 路由与原始字节路由

大多数路由返回 JSON。以下例外会返回**原始字节**，并附带猜测或已存储的
`Content-Type`：

| 路由 | 响应体 | 说明 |
| --- | --- | --- |
| `GET /`、`GET /index.html`、未知的非 API GET | HTML | 来自 `webui/index.html` 的 SPA 外壳。 |
| `GET /static/<rel>` | 文件字节 | 有路径遍历保护；404/403 仍以 JSON 返回。 |
| `GET /api/artifacts/{ident}` | Artifact 字节 | `ident` 可以是 **version_id、artifact_id 或 filename**（按此顺序解析：`store.resolve_artifact_path` 先尝试 `artifact_versions.version_id`，再尝试 `artifacts.artifact_id` → 其最新版本；处理器最后回退到 filename 查询）。`Content-Type` 来自已存储的元数据，否则根据文件名猜测。 |
| `GET /api/frames/{fid}/artifacts.zip` | ZIP 字节 | 一个会话的当前 Artifact 版本。 |
| `GET /api/projects/{pid}/artifacts.zip` | ZIP 字节 | 一个项目中所有当前 Artifact 版本。 |
| `GET /api/frames/{fid}/notebook/export?language=` | `.ipynb` 或 ZIP 字节 | `python`/`r` 返回一个 Notebook；省略参数或使用 `bundle` 时返回二者及一个清单。 |
| `GET /api/frames/{fid}/session/export` | Session ZIP 字节 | 确定性的 `application/vnd.openai4s.session+zip`；携带 schema 与 SHA-256 标头。 |
| `GET /preview/{ident}` | Artifact 字节 | 解析方式相同，但 `Content-Type` 被**强制**设为 `text/html; charset=utf-8`（用于沙箱化 iframe 预览）。不在 `/api` 下。 |
| `GET /ketcher` | HTML | 静态占位页面。 |

**瑕疵：** 当原始字节路由失败（Artifact 不存在）时，它会返回 *JSON* 响应体
`404 {"error": "artifact not found"}`——直接把响应流写入磁盘的使用者将得到
一份 JSON 文档。

注意 `GET /api/artifacts/…` 上的重叠：先尝试具体匹配器
（`/lineage`、`/environment`、`/versions` 等）；最后的
`re.fullmatch(r"/artifacts/(.+)")` + GET 兜底会提供字节。由于它匹配
`.+`（包括斜杠），它也会捕获 `/api/artifacts/` 下所有其他未匹配的 GET。

## 2. REST 路由

除非另有说明，以下所有路径都位于 `/api` 下。“→”描述成功响应体。序列化器
形状见 §4。

### 身份 / 配置 / 元信息

| 方法与路径 | 行为 |
| --- | --- |
| `GET /health`（不在 `/api` 下） | 最小公开投影 `{"status":"ok","model"}`。不受令牌门禁约束，并刻意省略主机文件系统路径。 |
| `GET /me` | 硬编码的本地身份：`{"user_id":"local-dev","email":null,"provider","has_api_key","shared_api_key":false,"auth_mode":"none"}`。 |
| `GET /auth/status` | `{"authenticated":true,"auth_mode":"none"}`（始终如此）。 |
| `GET /csrf` | `{"csrf_token":"local"}`（占位实现；真正的 CSRF 防御是 Origin 检查）。 |
| `GET|POST|PUT|PATCH /config/llm` | GET → `{provider,model,base_url,has_api_key}`。写入 → 持久化 `provider`/`model`/`base_url`；仅当 `api_key` 非空时覆盖；`clear_api_key:true` 会清空它 → `{"ok":true,"has_api_key"}`。绝不返回原始密钥。 |
| `GET /search?q=` | `{sessions:[{id,project_id,name,task_summary}], artifacts:[{id,filename,content_type,root_frame_id,project_id}]}`；空 `q` → 空列表。 |
| `GET /`（即 `/api` 或 `/api/`） | `{"service":"openai4s","ok":true}`。 |

### 模型与模型配置档

| 方法与路径 | 行为 |
| --- | --- |
| `GET /models` | `{"models":{"default":[{id,name,description}…]},"default_model_id"}`——依次为当前模型、配置档模型、提供商默认值，并去重。 |
| `GET /models/default` | `{"default_model_id"}`。 |
| `POST /models/default`（任何非 GET） | 请求体 `{model_id}` → 持久化为 `llm_model` 设置 → `{"default_model_id"}`。 |
| `GET /model-endpoints/discover?force=1` | 明确探测固定回环目录中的 Ollama、LM Studio、vLLM 和 llama.cpp，并禁用环境代理。返回经过净化的配置档建议以及 `mutated_settings:false`；它绝不接受调用方提供的 URL，也绝不创建或激活配置档。`force=1` 会绕过短时的进程内缓存。发现的端点无需密钥，但不会推断厂商能力：在存在明确覆盖前，它采用保守的 Code-as-Action（不继承任何视觉/工具/schema 能力声明）。 |
| `GET /model-profiles` | 首次调用时填充内置预设，然后返回 `{"profiles":[masked…],"active_id","known_providers"}`。配置档会被**脱敏**：`{id,name,provider,base_url,model,has_api_key}`——绝不回显 API key。 |
| `POST /model-profiles` | 请求体 `{name,provider?,base_url?,model?,api_key?}`；缺少 `name` → `400 {"error":"name required"}`；成功 → `201` 脱敏配置档。 |
| `POST /model-profiles/{id}/activate` | 将配置档字段复制到当前 `llm_*` 设置，并将其移到列表首位 → `{"ok":true,"active_id","has_api_key"}`；未知 id → 404。 |
| `PUT|PATCH /model-profiles/{id}` | 局部编辑；仅当 `api_key` 非空时覆盖；`clear_api_key:true` 会清空。编辑当前配置档也会同步当前设置 → 脱敏配置档；未知 id → 404。 |
| `DELETE /model-profiles/{id}` | 删除配置档（若它处于激活状态，还会清除 `active_model_profile`）→ `{"ok":true}`。删除不存在的 id 仍返回 `{"ok":true}`。 |

### 项目、笔记与文件夹

| 方法与路径 | 行为 |
| --- | --- |
| `GET /projects` | `{"projects":[project…],"total":n}`。**没有分页：** 前端会发送 `?limit=100&offset=0`，但处理器忽略这两个参数，并始终返回*全部*项目；`total` 只是 `len(projects)`。不要记录或依赖 offset 语义——它不存在。 |
| `POST /projects` | 请求体 `{name?,description?,context?}` → 项目 JSON（带 `conversation_count: 0`）。 |
| `GET /projects/{pid}` | 项目 JSON；不存在时返回 `{}`（**不是** 404）。 |
| `GET /projects/{pid}/action-timeline?limit=` | 有边界的跨会话安全 Timeline 投影，带会话标签。 |
| `GET /projects/{pid}/lineage?limit=` | 项目范围的 Artifact/版本沿袭图，节点和边有数量上限。 |
| `PUT|PATCH /projects/{pid}` | 更新 `name`/`description`/`context` → 项目 JSON。 |
| `DELETE /projects/{pid}` | 删除项目及 frames，并解除 Artifact 文件和会话工作区的链接 → `{"ok":true,"freed_files","freed_sessions"}`。 |
| `GET /projects/{pid}/notes` | `{"notes":[note…]}`。 |
| `POST /projects/{pid}/notes` | 请求体 `{content}` → 笔记 JSON。 |
| `DELETE /notes/{note_id}` | `{"ok":true}`。 |
| `GET /projects/{pid}/folders` | `{"folders":[…]}`。 |
| `POST /projects/{pid}/folders` | 请求体 `{name}` → 文件夹行。 |
| `PUT|PATCH /folders/{fid}` | 重命名 → `{"ok":true}`。 |
| `DELETE /folders/{fid}` | `{"ok":true}`。 |
| `POST|PUT|PATCH /frames/{fid}/folder` | 请求体 `{folder_id}`（或 null）→ `{"ok":true}`。 |

### Frames（会话）与轮次

| 方法与路径 | 行为 |
| --- | --- |
| `GET /frames?project_id=&limit=` | **裸 JSON 数组**形式的 frame JSON（无外层封装）。`limit` 默认为 100；处理器会超额读取 `limit*2` 个根 frame，丢弃“被遗弃的空”会话（无消息、无 Cell、无标题），为每个条目标注实时 `running` 和 `kernel_alive` 布尔值，然后截断至 `limit`。没有 `offset`。 |
| `POST /frames` | 请求体 `{project_id?,model?}` → 新根 frame 的 JSON。 |
| `GET /frames/{fid}` | Frame JSON；不存在时返回 `{}`。 |
| `PATCH /frames/{fid}` | 更新 `name`/`task_summary`，广播 `frame_update` → frame JSON。 |
| `DELETE /frames/{fid}` | `{"ok":true}`。 |
| `GET /frames/{fid}/messages?from=&limit=&branch_id=` | 按分支投影的 `{"messages":[{message_id,role,content,created_at,fork_checkpoint_id}…]}`。省略 `branch_id` 时选择持久化的活动分支；其继承前缀和 Revert 后的延续都会包含在内，而同级/废弃行只保留在审计源中。`from`（默认 0）与 `limit`（默认 300）是真实的切片参数。 |
| `GET /frames/{fid}/steps` | `{"steps":[…]}`（持久化的语义步骤）。 |
| `POST /frames/{fid}/message` | 开始一个轮次。请求体 `{request}`（或 `{input_data:{request}}`），可选 `model`、`plan`、`explore`、`annotation_ids`。使用 `wait:false` → `202 {"status":"accepted","frame_id","job_id","execution_id","owner":{"kind","id"},"queue_position"}`；默认（省略 `wait` 或为 true）会阻塞等待轮次结果。有效且唯一的 `finalize_response` 是一次 Engine 完成（即使较早步骤运行过 Cell）；`host.submit_output(...)` 是从 Python Cell 内部发出的唯一完成信号。普通文本/结果和达到最大轮次都不算成功。 |
| `GET /frames/{fid}/execution` | 权威 FIFO 快照：`{root_frame_id,owner,queue,queued_count,active_count,closed,close_reason}`。已知时，owner/queue 条目包括 `execution_id`、`{kind,id}` owner、状态、位置、分支/语言/generation 以及资源键。 |
| `POST /frames/{fid}/cancel` | 作用域受限的取消。请求体 `{execution_id,owner:{kind,id}}`（或 `owner_kind` + `owner_id`）以及可选 `reason` → `{ok,execution_id,owner,scope,…}`。缺少身份返回带 `error` 的 HTTP 400；过期/不匹配身份返回 `ok:false`。取消排队中的任务不会影响活动 owner。 |
| `GET /frames/{fid}/status` | `{"frame_id","running",kernel:{…kernel status…}}`。 |
| `POST /frames/{fid}/feedback` | 请求体 `{key,rating}` → `{"ok":true}`。 |
| `GET /frames/{fid}/feedback` | `{"feedback":[…]}`。 |
| `GET /frames/{fid}/session/export` | 原始确定性 Session 包 ZIP，带 `X-Content-SHA256` 与 `X-OpenAI4S-Session-Schema`。其中包含分支所有的消息、完整且已净化的提供商组/wire 状态、Notebook 与 Artifact/沿袭记录、Revert 游标、证据审阅和 checkpoint 的计划/审阅/记忆快照；含有秘密材料时会被拒绝。 |
| `POST /sessions/import` | 原始 Session ZIP 请求体（非 JSON，归档最大 128 MiB）→ HTTP 201，返回新的 `{project_id,root_frame_id,active_branch_id,kernel_state:"ended",view_only:true,trust_state:"quarantined",explicit_recovery_required:true,…}`。整个归档会先作为不可信输入进行预检，所有身份都会重新映射，权限会被降级，审阅自动化会被禁用，而且不会启动任何 Kernel/hook/包代码。隔离状态是持久的：在用户调用 `POST /frames/{fid}/recovery/actions/restart_fresh` 并传入 `{"confirm":true}` 前，frame 作用域的变更返回 HTTP 423；读取/导出/删除仍然可用。 |

### Plan 模式

| 方法与路径 | 行为 |
| --- | --- |
| `GET /frames/{fid}/plan` | `{"frame_id","plan_id","status","plan"}`（无计划时各值为 null）。 |
| `POST /frames/{fid}/plan/approve` | `202 {"status":"accepted","frame_id","job_id"}`——自动执行在后台运行。 |
| `POST /frames/{fid}/plan/revise` | 请求体 `{changes}`（或 `{feedback}`）；为空 → `400 {"error":"changes required"}`；否则返回 `202` accepted。 |
| `POST /frames/{fid}/plan/discard` | `runner.discard_plan` 的结果（同步）。 |

### 权限

| 方法与路径 | 行为 |
| --- | --- |
| `POST /frames/{fid}/decision` | 回答待处理的 `await_permission` 提示。请求体 `{decision_id,allow,scope?("once"),pattern?,message?}`。实时决策返回 `{ok,decision_id,allow,scope,resolution_context:"live_thread",requires_continue:false,original_action_executed:null}`，并唤醒被准确阻塞的调用。守护进程重启后，返回 `resolution_context:"after_restart"`、`original_action_executed:false`，且批准时 `requires_continue:true`；不会重放已存储的参数。重启后的 `once` 批准还会返回其精确授权的 `continuation_expires_at` 和 `continuation_authorization`；更宽范围则持久化为常驻规则。未知、跨 frame、冲突或过期的决策返回带 `error` 的 `ok:false`。 |
| `GET /frames/{fid}/permissions` | `{"root_frame_id","project_id","rules":[…]}`——对该会话生效的规则。 |
| `POST /permissions` | Upsert 一条规则。请求体 `{scope("global"),scope_id?,frame_id?,tool("*"),pattern("*"),decision("ask")}`；省略 `scope_id` 但给出 `frame_id` 时，scope id 从 frame 推导 → `{"ok":true,"rule_id"}`。 |
| `POST /permissions/reset` | 重新填充默认值 → `{"ok":true,"rules":[…]}`。 |
| `DELETE /permissions/{rule_id}` | `{"ok":true}`。 |

### 图像标注（图形审阅）

| 方法与路径 | 行为 |
| --- | --- |
| `GET /frames/{fid}/annotations?artifact_id=` | `{"annotations":[annotation…]}`。 |
| `POST /frames/{fid}/annotations` | 请求体 `{artifact_id,body`（或 `text`）`,artifact_name?,x?,y?}`（`x`/`y` 为 0–1 的比例；也接受 `rel_x`/`rel_y` 别名）。缺少 artifact_id/body → 400 → 否则 `201 {"annotation":…}`。 |
| `PATCH|POST|PUT /annotations/{aid}` | 请求体 `{body?,status?}` → `{"annotation":…}` 或 `404 {"annotation":null}`。 |
| `DELETE /annotations/{aid}` | `{"ok":true}`。 |

### Kernel / Notebook（每会话）

Kernel 状态与执行日志读取是惰性的：它们绝不会启动 Python 或 R。第一个
Agent/用户 Cell 仅启动所选语言；只包含原生工具或 `FinalizeAction` 的轮次可以
在没有 kernel 进程的情况下完成。

| 方法与路径 | 行为 |
| --- | --- |
| `GET /frames/{fid}/execution-log` | `{"kernels":[id…],"entries":[cell…]}`；条目包括稳定的 `producing_cell_id`、`cell_index`、会话内单调递增的 `state_revision`、由尝试派生的 `generation_id`（旧行或未获取 worker 时可为 null）、`kernel_id`、`language`、`origin`、源码/输出/错误、文件/图形、用量，以及记录时不可变的重试元数据。 |
| `POST /frames/{fid}/kernel/execute` | 请求体 `{code,language?,execution_id?,wait?}`，其中 language 为 `python`（默认）或 `r`；随附 UI 会提供可移植的 execution ID。默认/`wait:false` 会立即返回 HTTP 202 `{status:"accepted",job_id,execution_id,owner,queue_position}`，因此排队中的 Cell 仍可寻址。`wait:true` 阻塞等待由 FIFO owner 完成的 Cell 结果。执行始终追加，从不编辑历史。 |
| `POST /frames/{fid}/kernel/restart` | → `{"ok":true,"status":"restarted","generation","generation_id","frame_id"}` + `kernel_status` WS 事件。 |
| `POST /frames/{fid}/kernel/stop` | → `{"ok":true,"state":"stopped"|"none","frame_id"}`。 |
| `POST /frames/{fid}/kernel/start` | → `{"ok":true,"state":"running","generation","frame_id",…}`。 |
| `POST /frames/{fid}/kernel/interrupt` | 精确 ticket 停止。请求体 `{execution_id,owner:{kind,id}}`（或 owner 别名）标识一个 ticket：排队 ticket 会被取消而不触及活动 writer；活动 ticket 只针对其冻结 lease 请求信号。结果中的 `interrupted` 标志说明 lease 是否实际收到信号。缺少身份返回 HTTP 400；过期/错误 owner 请求返回 `ok:false`。随附 Notebook 的 Stop 控件只选择 `user_repl` ticket。 |
| `GET /frames/{fid}/kernel` | Kernel 状态：`{frame_id,state("none"|"running"|"stopped"|"ended"),alive,generation,generation_id,generation_ordinal,last_activity_at,ended_reason,turn_running,cell_count,manual_stop,repl_enabled,env:{name,language,python_version,pending,kernel_id}}`。`repl_enabled` 映射 `OPENAI4S_NOTEBOOK_REPL`。 |
| `POST /frames/{fid}/kernel/install` | 请求体 `{packages:[…]}` 或 `{package}`（另有 `restart`，默认 true）→ pip-install 报告（`{ok,installed,…,restarted}`）。 |
| `GET /frames/{fid}/environments` | `{"environments":[…],"current","default","pending"}`。 |
| `POST /frames/{fid}/kernel/env` | 请求体 `{env}`（或 `{name}`）——将 kernel 切换到预构建 env（重启）→ `{"ok":true,"state","env","generation","language","python_version","frame_id"}`。 |

**Notebook REPL 门禁：** Notebook 默认是**只读执行轨迹**。变更型
`kernel/*` 路由——`execute`、`env`、`restart`、`stop`、`start`、
`interrupt`——会返回 `403 {"error":…}`，除非设置了
`OPENAI4S_NOTEBOOK_REPL`。`kernel/install` 被有意排除在门禁外：它服务于
Customize → Compute，而不是任意 Notebook 执行。只读的
`GET /frames/{fid}/kernel` 与 `GET /frames/{fid}/execution-log` 始终可用。
`GET /frames/{fid}/kernel` 通过 `repl_enabled` 报告当前状态。启用后，随附 UI
提供多行 Python/R 输入与 Shift+Enter；每次提交都通过 Agent 和生命周期任务
共用的 FIFO 协调器追加一个 Cell。

**`kernel_id` 运行时分段：** kernel 与执行日志路由返回的 `kernel_id` 现在
包含运行时分段——默认 env 为 `python`，agent 切换 conda env 后为
`python — struct` / `python — phylo` 等——因此每个 Cell 行都会标注其运行的
环境。`state_revision` 当前复用持久化的会话 Cell 序号。它是用于过期/只读 UI
标记的状态变更游标，不是序列化的变量状态，也不能证明较旧的内存命名空间仍
可恢复。`generation_id` 是绑定到执行尝试的 UUID，而不是从该展示标签重建的值。

### 科学会话工作台

这些路由是 `SessionDomainService` 与 `SessionWorkbenchStateService` 之上的
轻量 Gateway 适配器：

| 方法与路径 | 行为 |
|---|---|
| `GET /frames/{fid}/action-timeline?branch_id=&before_ordinal=&after_ordinal=&limit=` | 面向研究者的 Action Ledger 投影。`limit` 默认为 500，且必须在 1–500 之间。没有游标时返回最新窗口；`before_ordinal` 向更早移动，`after_ordinal` 向更新移动。游标必须非负且互斥（无效值 → 400）。字段有边界且经过脱敏，原始参数/提供商 wire 状态会被省略。包含规范用量；仅当记录了明确的部署价格元数据时，`cost` 才非 null。响应元数据包括 `count`、`total_count`、`truncated`、`has_earlier`、`has_more`、`first_ordinal` 和 `last_ordinal`。 |
| `GET /frames/{fid}/execution-queue` | 权威执行快照（`/execution`）的别名。 |
| `GET /frames/{fid}/context` | 安全的 token 组成投影：总量/上限、消息数、handoff/compaction 状态，以及 text/image/tool/wire token 层；不含消息内容。 |
| `GET /frames/{fid}/security` | 聚合的沙箱自检投影，以及每种语言的 `sandbox.runtimes[]`、持久权限待处理数量和 Notebook 交互标志。只有 Python 或只有 R 的会话会报告实际运行过的 worker；在任一 worker 启动前，状态如实为 `not_started`，而非推断结果。 |
| `GET /frames/{fid}/delegations` | 安全、持久化的子 agent 树，共享 spawn 预算、进度/终态、强制覆盖摘要和 steering 投递计数器。结果/输出正文与 steering 文本不会出现在浏览器投影中。 |
| `GET /frames/{fid}/branches` | 分支树、checkpoints 与能力描述符。GET 不会创建初始 branch/checkpoint。 |
| `GET|POST /frames/{fid}/checkpoints` | 列出或创建不可变 checkpoints。`/branches/checkpoints` 是别名。POST 接受 `branch_id`、`reason`、`expected_head`。 |
| `POST /frames/{fid}/branches/fork` | 请求体必须且只能选择 `from_checkpoint_id`、`from_cell_id` 或 `from_message_id` 之一；可选 `name`。Cell/message 源只能通过该根会话中的精确边界 checkpoint 解析。缺少该 checkpoint 的旧历史返回 409。新分支拥有独立工作区，并保持非活动/只读。 |
| `POST /frames/{fid}/branches/{branch_id}/activate` | 精确的 FIFO 生命周期变更。停止旧分支 runtime，以原子方式选择所请求分支/checkpoint 的旁路状态，并返回 `status: active|partial|failed` 以及各维度的应用/恢复详情。绝不修改旧分支历史。 |
| `POST /frames/{fid}/revert/preview` | 请求体 `{target_checkpoint_id,branch_id?}` → `{preview}`，其中包括工作区/message/action/Notebook/artifact/env/permission 差异和冲突。`/branches/revert-preview` 是别名。 |
| `POST /frames/{fid}/revert/apply` | 经过冲突检查的追加式 revert；使实时 kernels 失效；无法安全应用时返回 409。`/branches/revert` 是别名。 |
| `POST /frames/{fid}/revert/undo` | 请求体 `{revert_checkpoint_id,branch_id?}`——revert 到已记录的 revert 前 checkpoint。 |
| `GET /frames/{fid}/revert/operations` | 持久化的 revert 操作历史。 |
| `GET /frames/{fid}/recovery` | 安全的 Recovery Journal 状态投影。 |
| `GET /frames/{fid}/recovery/actions` | 描述当前根分支上的五种选择：带 ticket 的变更操作 `restore`、`retry` 和 `restart_fresh`，以及非变更型指导选项 `inspect_log` 和 `continue_view_only`。 |
| `POST /frames/{fid}/recovery/actions/{restore\|retry\|restart_fresh}` | 在精确的 recovery execution ticket 下运行所宣告的验证恢复操作。`restart_fresh` 要求 `{"confirm":true}`，且绝不声称恢复了命名空间。 |
| `GET /frames/{fid}/kernel/variables?language=python|r` | 有边界、仅在空闲时可用的 Variable Inspector 投影。它绝不会启动已停止的语言 worker，并返回明确的 Busy/Restoring/Ended/Not Started 状态。 |
| `GET /frames/{fid}/notebook/export?language=` | `python`/`r` 返回原始确定性 `.ipynb`；省略参数或使用 `bundle` 时返回稳定 ZIP，其中包含二者及一份清单。包括 `Content-Disposition` 和 `X-Content-SHA256`。 |
| `GET /frames/{fid}/session/export` | 原始确定性、经清单哈希校验的 Session 包。 |
| `GET /renderers` | 安全的科学 renderer 描述符目录。 |
| `GET /artifacts/{aid}/renderer?version=&root_frame_id=` | 选择绑定到版本的 renderer 描述符，以及不可变的 checksum/size/provenance 元数据；绝不执行 Artifact 内容。 |

Timeline UI 首先请求最新 500 条记录。当 `has_earlier` 为 true 时，它会显示
一个明确控件，请求 `before_ordinal=<first_ordinal>&limit=500`，按持久化 group
身份合并，并在不丢弃最新窗口的前提下最多保留 2,000 条记录。

Notebook 标头和 provenance 执行视图会链接到 Notebook 导出路由的 bundle
形式。仍可通过查询参数直接获得特定语言的 Python/R 文件。

### Artifacts

| 方法与路径 | 行为 |
| --- | --- |
| `GET /frames/{fid}/artifacts` | Artifact JSON 的**裸数组**。 |
| `GET /projects/{pid}/artifacts` | **裸数组**——项目所有会话中的每个 Artifact。 |
| `GET /frames/{fid}/artifacts.zip` | 会话当前 Artifact 版本的原始 ZIP。 |
| `GET /projects/{pid}/artifacts.zip` | 项目范围内当前 Artifact 版本的原始 ZIP。 |
| `GET /artifacts/{aid}/lineage` | `{"artifact_id","filename","interactions":[{kind:"cell",…}|{kind:"save",at}],"dependency_mappings":{"inputs":[…]}}`。未知 artifact → 同样的形状，但值为 null/空，HTTP 200（**不是** 404）。 |
| `GET /artifacts/{aid}/environment?version=` | 为产出该版本的运行所捕获的 env 快照，`{"source":"captured",…}`；没有记录时回退到实时 freeze `{"source":"live",…}`。 |
| `POST|PUT|PATCH /artifacts/{aid}/priority` | 请求体 `{priority:int}` → `{"ok":true,"artifact":…|null}`。 |
| `GET /artifacts/{aid}/versions` | `{"versions":[{version_id,ordinal,is_latest,size_bytes,content_type,checksum?,producing_cell_id?,created_at}…]}`。 |
| `POST /artifacts/{aid}/versions/{vid}/restore` | 还原实时文件和 latest 指针 → `{"ok":true,"artifact":…}` 或 `404 {"error":…}`；广播一个*裸* `artifact_created`（见 §3）。 |
| `POST|PUT|PATCH /artifacts/{aid}/edit` | 请求体 `{content}`（文本）。非文本 artifact → `415`；未知 → `404`（两者均通过 `GatewayError`）→ `{"ok":true,"artifact_id","version_id","size_bytes"}`。 |
| `POST|PUT|PATCH /artifacts/{aid}/rename` | 请求体 `{filename}`；缺少 → `400`；未知 → `404` → `{"ok":true,"artifact_id","filename"}`。 |
| `DELETE /artifacts/{aid}` | 删除行和快照文件 → `{"ok":true}`；广播一个*裸* `artifact_created`。 |
| `GET /artifacts/{ident}` | **原始字节**（见 §1）。 |
| `POST /uploads` | **Base64 JSON 上传——不是 multipart。** 请求体 `{filename?,content_base64`（或 `content`）`,frame_id?,project_id?}`。无效 base64 不一定报错（两层瑕疵）：解码调用 `base64.b64decode` 时没有 `validate=True`，所以会在解码前**静默丢弃非字母表字符**；只有结果仍存在错误的长度/填充（`binascii.Error`/`ValueError`）时，才会回退为按原样存储原始字符串的 UTF-8 字节。文件落在会话工作区（没有 `frame_id` 时落在 `data_dir/uploads`），并注册为版本化 artifact（`is_user_upload`）；在同一 frame 中重新上传同名文件会创建新版本 → `{"artifact_id","id","filename"}`。 |

### Skills / agents / specialists / connectors

| 方法与路径 | 行为 |
| --- | --- |
| `GET /skills/catalog` | `{"skills":[{…,enabled}…]}`。 |
| `PUT|PATCH /skills/catalog/{name}/enabled` | 请求体 `{enabled}` → `{"ok":true}`。Skill 启用状态通过作用域能力状态持久化，并在发现/prompt/runtime 加载时强制执行。 |
| `POST /skills` | 在 `<data_dir>/user-skills` 下创建 Web 编写的 `user` Skill：`{name,description?,body|content}`。拒绝与 bundled 名称冲突和不安全路径。 |
| `POST /skills/import` | 接受 `content` 中原始的 `SKILL.md`（解析 frontmatter）或显式字段，然后写入规范化的 `user` 文档；导入的 frontmatter 不能声明 bundled trust。 |
| `GET|PUT|PATCH|DELETE /skills/{name}` | 读取 / 更新 / 删除用户 Skill（URL 编码名称）。Bundled `openai4s` Skills 仍不可编辑/删除。 |
| `GET /skills/{name}/versions` | 个人不可变版本/事件历史以及安全的活动 manifest；绝不返回已存储的源字节。 |
| `POST /skills/{name}/rollback` | 请求体 `{version_id}`，以原子方式激活一个保留的个人版本。 |
| `GET /projects/{project_id}/skills/catalog` | 仅返回项目所有的 Skill overlays；省略个人 fallback 与 bundled 条目。 |
| `GET /projects/{project_id}/skills/{name}/versions` | 精确的项目作用域不可变历史。未知项目 fail closed。 |
| `POST /projects/{project_id}/skills/{name}/rollback` | 请求体 `{version_id}`，仅在该项目中激活一个保留版本。 |
| `GET /agents` | 内置 agent 描述符的裸数组（含 `enabled`）。 |
| `PUT|PATCH /agents/{name}/enabled` | `{"ok":true}`。这个旧式内置 agent roster 开关仍然只在进程内生效；持久化的 Specialist 能力策略在 delegation 中另行执行。 |
| `GET /agents/{name}` | Agent 描述符或 `404 {"error":"unknown agent"}`。 |
| `GET /specialists` | `{"builtin":[…],"specialists":[…]}`。 |
| `POST /specialists` | 按 `name` upsert（缺少时 400）→ agent 行。 |
| `GET|PUT|PATCH|DELETE /specialists/{name}` | CRUD；GET 不存在时返回 404 `{"error":"not found"}`。 |
| `GET /connectors` | `{"connectors":[…]}`（MCP servers）。 |
| `POST /connectors` | 必须包含 `{name,command}`（否则 400）→ connector 行。 |
| `GET /connectors/directory` | `{"directory":[…]}`——精选安装列表。 |
| `PUT|PATCH /connectors/{id}/enabled` | `{"ok":true}`。 |
| `POST /connectors/{id}/probe` | 生成 server 进程并列出工具；未知 id → 404。 |
| `POST /connectors/{id}/call` | 请求体 `{tool,args}` → 工具结果；**异常以 HTTP 200 的 `{"error":…}` 返回**。 |
| `DELETE /connectors/{id}` | 断开并删除 → `{"ok":true}`。 |

### Compute / environments / kernel packages

| 方法与路径 | 行为 |
| --- | --- |
| `GET /compute/gpu` | 本地 GPU 探测报告。 |
| `GET /compute/ssh-aliases` | 来自 `~/.ssh/config` 的 `{"aliases":[…]}`。 |
| `GET /compute/remote` | 已注册远程主机信息。 |
| `POST /compute/remote` | 请求体 `{alias,label?}`；alias 必须存在于 `~/.ssh/config`（否则 400）；通过 SSH 探测 GPU → `{"ok":true,"alias",…,"info"}`。 |
| `DELETE /compute/remote/{alias}` | `{"ok":bool}`。 |
| `GET /compute/providers` | `{"providers":[…]}`。 |
| `GET /compute/local/hostinfo` | 主机信息快照。 |
| `GET /compute/jobs` | `{"jobs":[…]}`。 |
| `POST /compute/jobs` | 请求体 `{command|code,kind("bash"),cwd?}` → job 行。**本地代码执行端点**——仅受 Origin 检查和回环绑定保护。 |
| `POST /compute/jobs/{id}/cancel` | 取消结果。 |
| `GET /compute/jobs/{id}` | Job 行。 |
| `GET /environments/status` | `{"environments":[{language,status,python_version,package_count,packages,preinstall}]}`。 |
| `GET /environments` | 与 `GET /frames/{fid}/environments` 形状相同，但不属于会话。 |
| `GET /kernel/packages` | `{"packages":[…],"preinstall":{…}}`。 |
| `GET /kernel/environment` | 用于 Provenance → Environment 的完整 env freeze。 |
| `POST /kernel/install` | 请求体 `{packages}` 或 `{package}` → 安装报告（不重启 kernel）。 |

### Memory / network / web-search 配置

| 方法与路径 | 行为 |
| --- | --- |
| `GET /memory/enabled` | `{"enabled":bool,"override":null}`。 |
| `PUT|PATCH|POST /memory/enabled` | 请求体 `{enabled}` → `{"enabled"}`。 |
| `GET /memory?project_id=` | `{"enabled","memories":[…]}`（`project_id` 默认为 `all`）。 |
| `POST /memory` | 请求体 `{content,block?("general"),project_id?}` → memory 行。 |
| `GET /memory/categories?project_id=` | `{"categories":[…]}`。 |
| `GET /memory/context?project_id=` | `{"context":"- …\n- …"}`。 |
| `DELETE /memory/{id}` | `{"ok":true}`。 |
| `GET|PUT|PATCH|POST /network/status` | 写入会切换 `OPENAI4S_ALLOW_NETWORK`（进程 env + 设置）；始终返回 `{"enabled":bool}`。 |
| `GET /preferences/builtin-allowlist` | `{"enabled","egress_mode","granted":[domains],"groups"}`。 |
| `GET|PUT|PATCH|POST /search/config` | Tavily key 配置；写入接受 `{api_key}` 或 `{clear_api_key}`；始终返回 `{"endpoint":"https://api.tavily.com/search","api_key_configured":bool}`——绝不回显密钥本身。 |

## 3. WebSocket 契约（`/api/ws`）

标准 RFC-6455 升级（手写：计算 `Sec-WebSocket-Accept`，不支持扩展/子协议）。
双向消息都是 JSON 文本帧。协议 `ping` 帧（opcode 0x9）以 `pong` 帧响应；
JSON `{"type":"ping"}` 以 `{"type":"pong"}` 响应（前端每 25 秒发送一次
JSON 形式）。

### 客户端 → 服务器消息

| 消息 | 效果 |
| --- | --- |
| `{"type":"ping"}` | → `{"type":"pong"}`。 |
| `{"type":"view_session","root_frame_id":fid}` | 让此连接订阅 `fid` 的事件。如果轮次正在进行，会重放当前轮次的缓冲事件（`replay_begin` … events … `replay_end`）；即使重启后尚未重建会话 runtime，任何待处理的 `await_permission` prompt 也会从持久存储中重新发送。接受 `frame_id` 作为别名。 |
| `{"type":"unview_session","root_frame_id":fid}` | 取消订阅。 |
| `{"type":"cancel_execution","root_frame_id":fid,"execution_id", "owner":kind,"owner_id":id}` | 请求精确 ticket 取消并接收 `execution_cancel_result`。为兼容也接受 `cancel` 类型，但缺失/过期/不匹配的身份会 fail closed。 |

事件只发送给订阅了该事件 `root_frame_id` 的连接（`root_frame_id=None` 的
广播会发送给所有人，但 gateway 目前不会发出这样的事件）。

### 服务器 → 客户端事件

每个事件都有 `type`，并且（通过 hub emitter）带有 `root_frame_id`；大多数还
带有冗余的 `frame_id`。前端以 `m.root_frame_id || m.frame_id` 为键。

| 事件 `type` | 字段（除 `root_frame_id` 外） | 含义 |
| --- | --- | --- |
| `replay_begin` / `replay_end` | — | 在轮次中途执行 `view_session` 后，框住缓冲事件重放。 |
| `text_reset` | `frame_id` | 新流式 assistant 消息的开端（清除实时气泡）。 |
| `text_chunk` | `frame_id`、`block_type`（文本为 `"text"`，代码 Cell 回显/stdout/错误为 `"tool"`）、`chunk`；代码 Cell 开始时还携带 `cell_index`、规范 `kernel_id` 和 `language` | 增量流。前端直接使用开始元数据，使实时 Notebook 分组与持久化执行日志一致，且不受状态缓存竞争影响。 |
| `notebook_cell_start` | `frame_id`、`producing_cell_id`、`cell_index`、`state_revision`、`generation_id`、`kernel_id`、`language`、`origin`、`source`、`status` | 使用精确的、绑定到尝试的 runtime generation 开始/upsert 一个不可变 Cell 身份。 |
| `notebook_cell_chunk` | `frame_id`、`producing_cell_id`、`stream`、`chunk` | 将输出追加到该精确实时 Cell。可容忍未知/重放字段。 |
| `notebook_cell_finished` | start 身份（包括不变的 `state_revision` 和 `generation_id`）以及完整的 source/output/error、figures/files 与 usage | 用权威的完成修订替换实时投影。 |
| `step` | `frame_id`、`step_id`、`kind`、`title`、`input`、`status:"running"` | 一个语义步骤开始（host call、artifact save 等）。 |
| `step_update` | `frame_id`、`step_id`、`status`、`output`、`summary` | 步骤完成/修补。Artifact-save 步骤会连续发出 `step` + `step_update`。 |
| `plan_ready` | `frame_id`、`plan_id`、`status`、`plan`、`artifact_id` | Plan 模式轮次产出结构化计划。 |
| `plan_progress` | `frame_id`、`plan_id`、`step_id`、`status`、`note` | 自动执行期间一个 plan 步骤发生更新。 |
| `await_permission` | `frame_id`、`decision_id`、`tool`、`kind`、`title`、`input`、`target`、`suggested_patterns`、`scopes`、`sub_agent` | 工具调用被阻塞并等待用户批准（通过 `POST /api/frames/{fid}/decision` 回答）。由 `openai4s/permissions.py` 发出。 |
| `permission_resolved` | `frame_id`、`decision_id`、`allow`、`scope`，以及重启后：`resolution_context`、`requires_continue`、`original_action_executed`、`continuation_expires_at`、`continuation_authorization` | 待处理 prompt 已被回答/超时。重启后的事件会明确说明旧操作未执行，以及用户是否必须发起新的 continuation。 |
| `frame_update` | `frame_id`、`status`、`task_summary`（仅当 `status:"titled"` 时） | 轮次/会话生命周期。发出的状态：`processing`、`completed`、`failed`、`cancelled`、`success`（REPL cell）、`updated`（rename/PATCH）和 `titled`——后台自动标题线程对占位会话标题的升级；它额外携带 `task_summary` 字段（新标题），而其他状态都没有。前端将 `completed|failed|cancelled|success|done` 视为终态——注意 `done` 在前端的终态集合中，但 gateway **从不**将其作为 `frame_update` 状态发出（它只是已完成轮次的*已存储* frame 状态）。 |
| `kernel_status` | `frame_id`、`status` ∈ `restarted|stopped|started|env_changed|packages_installed|ended`，以及各状态的额外字段（`generation`、`env`、`installed`、`ok`、`state`、`ended_reason`、`requires_kernel_recovery`） | Kernel 生命周期变更。成功的 branch revert 在使两个语言 slot 失效后发出 `ended`。 |
| `execution_state` | `frame_id`、`execution_id`、`owner:{kind,id}`、`status`（`queued|running|finalizing|completed|failed|cancelled`）、`queue_position`、`reason` | 一个精确 ticket 的状态发生变化。 |
| `execution_queue` | 来自 `GET /frames/{fid}/execution` 的权威快照字段 | 队列/位置投影；也会在 `view_session` 后立即发送。 |
| `execution_owner` | `execution_id`、`owner`、先前身份、`reason` | 活动 writer 发生变化。 |
| `execution_cancel_result` | 作用域受限的取消结果 | 对 WS 取消请求的直接回复。 |
| `checkpoint_created` | `branch_id`、`checkpoint_id`、`reason` | 一个不可变 checkpoint 已提交。 |
| `branch_created` | `branch_id`、`from_checkpoint_id` | 一个以 checkpoint 为基础的分支已提交。 |
| `branch_revert_conflict` | `branch_id`、`operation_id`、`target_checkpoint_id`、`reason` | Revert 已记录，但因冲突检查失败而未应用。 |
| `branch_reverted` | `branch_id`、`operation_id`、`target_checkpoint_id`、`checkpoint_id`、`undo_checkpoint_id`、`ok`、`requires_kernel_recovery` | Revert 已提交追加式状态；客户端必须刷新 branch/recovery 投影。完整 preview/checkpoint 记录只保留在直接 REST 结果中，绝不会进入 WebSocket。 |
| `artifact_created` | **不统一——见下文** | Artifact 已被产出、编辑、重命名、上传、恢复或删除。 |
| `pong` | — | 对 JSON ping 的回复。 |

### `artifact_created` 载荷不统一（瑕疵，但属于关键兼容行为）

Gateway 会在同一个事件类型下发出**四种不同形状**：

1. **自动捕获**（Cell 写入文件）——信息最丰富的形式：
   `{"type":"artifact_created","artifact":{"id","artifact_id","version_id",
   "filename","content_type","size_bytes","project_id","root_frame_id"}}`。
   注意重复的 `id`/`artifact_id`。
2. **编辑 / 重命名 / 上传**——局部 `artifact` 对象：edit 有
   `{id,filename,version_id,root_frame_id}`；rename 有
   `{id,filename,root_frame_id}`（**没有** `version_id`）；upload 有
   `{id,filename,content_type,root_frame_id}`（**没有** `version_id`）。
3. **Plan artifact**（`plan_*.json`）——一个**没有嵌套 `artifact` 键**的扁平
   事件：`{"type":"artifact_created","frame_id",
   "artifact_id","filename"}`。
4. **删除 / 版本恢复**——裸刷新信号：
   `{"type":"artifact_created","root_frame_id"}`，**完全没有 artifact 信息**。

该事件也可能**完全缺失**：edit/rename/upload/delete/restore 广播仅在 artifact
具有 `root_frame_id` 时触发（对于 upload，仅当请求中提供了 `frame_id`）——
没有 `frame_id` 的上传会存储文件，但完全不发出 `artifact_created`。

使用者必须将每个字段都视为可选。前端就是这样做的
（`const art = m.artifact || {}; const aid = art.id || art.artifact_id;`）：
存在 `version_id` 时将其用作图像缓存失效键，否则事件只会触发 artifact 列表
重新加载。**不要**依赖 `artifact_created.artifact.id` 在各发出位置都存在或稳定。

## 4. JSON 序列化器（共享形状）

这些序列化器定义在 `gateway.py` 模块级别，以便测试导入。所有时间戳都是
ISO-8601 字符串（或 null）。

- **Frame**（`_frame_json`）：`{id, root_frame_id, parent_frame_id, project_id,
  name, task_summary, model, status, folder_id,
  conversation_type:"agent", message_count, input_tokens, output_tokens,
  created_at, updated_at}`。列表行还会获得 `running` 和 `kernel_alive`。
- **Project**（`_project_json`）：`{project_id, id, name, description, context,
  conversation_count, last_active_at, created_at, updated_at, is_example}`
  （`project_id`/`id` 重复）。
- **Artifact**（`_artifact_json`）：`{id, artifact_id, filename, content_type,
  size_bytes, version_id`（= 最新版本，UI 缓存失效键）`,
  checksum, project_id, root_frame_id, priority, created_at,
  is_user_upload}`（`id`/`artifact_id` 重复）。
- **Note**（`_note_json`）：`{note_id, id, content, created_at, updated_at}`。
- **Annotation**（`_annotation_json`）：`{id, annotation_id, root_frame_id,
  artifact_id, artifact_name, x, y`（0–1 的比例）`, number, body,
  status("open"|"sent"), created_at, updated_at}`。

重复键模式（`id` + 类型化 id）是有意为前端兼容而保留的；修改这些序列化器
时请保留两者。

## 5. 已知缺口与尖锐边缘（摘要）

- `GET /api/projects` 接受但**忽略** `limit`/`offset`；不存在项目分页。消息的
  `from`/`limit`、frames 的 `limit`，以及 Timeline 的
  `before_ordinal`/`after_ordinal` + `limit` 窗口（§2）才是真正的有界读取。
- `artifact_created` 有四种载荷形状；每个字段都可选（§3）。
- 上传使用 JSON/base64，而非 multipart；base64 中的非字母表字符会被静默
  丢弃，仍然解码失败的输入会被静默存为原始 UTF-8 文本（§2）。
- 资源缺失的信号不一致：有些路由以 `{error}` 返回 404，另一些返回 `{}`
  （frame/project GET）、`{"ok":true}`（幂等删除）、填充 null 的 200
  （`/artifacts/{aid}/lineage`），或包含 `{error}` 的 200 响应体
  （`/connectors/{id}/call`）。
- 格式错误的 JSON 请求体会被视为 `{}`，而不是被拒绝。
- 原始字节 Artifact 路由在 404 时返回 JSON 响应体。
- Skill 启用/禁用状态是持久化的；旧式内置 agent roster 开关仍然只在进程内
  生效。Specialist runtime 策略有独立的持久化能力状态。
- 在默认回环绑定上没有身份认证；CSRF Origin 检查和回环绑定仍是 HTTP 边界。
  Kernel 执行还使用环境净化、权限/审计层和已配置的 OS 沙箱；本地
  `/compute/jobs` 仍是一个高权限接口表面。
- WS 重放缓冲仅覆盖**当前正在进行的轮次**；轮次结束后连接的客户端必须通过
  REST 重新加载状态（前端就是这样做的）。
- 结构化 `notebook_cell_*` 事件是实时投影；重连安全仍依赖兼容的
  `text_chunk` 流和权威 `/execution-log` 重新加载，而不是持久化的逐 Cell WS
  backlog。
- 工作台具备经过验证的恢复变更、checkpoint/Cell fork、activation、
  Revert/Undo、Notebook export 与专用 renderer 控件。尽管如此，该 API 仍是
  本地工作台的实现接口：消息边界 fork 不如 Cell/checkpoint 流程完整；UI 只
  暴露三种变更型恢复操作；而且不存在面向第三方客户端的兼容性/版本协商层。
