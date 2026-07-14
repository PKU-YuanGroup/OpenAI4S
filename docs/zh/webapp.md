---
title: Web 工作台
description: 浏览器工作台已实现的行为及其明确的运行时限制。
outline: deep
status: current
audience: [contributors, operators, users]
verified_commit: a92e736
last_verified: 2026-07-14
owner: OpenAI4S maintainers
---

# Web 应用

> 已于 2026-07-14 对仓库 revision `a92e736` 完成核验。

`openai4s serve` 在 `http://127.0.0.1:8760/` 启动纯标准库科学工作台：`http.server`、手写 WebSocket，以及静态 HTML/CSS/JavaScript。Source checkout 从工作树提供这些资源；installed wheel 则从 package-local static asset 提供。

## 当前可用

- **Project 与 session**——按目录/日期分组、deep link、会话搜索、command palette、重命名/删除，以及关闭标签页后仍继续的后台轮次。
- **实时轮次**——文本、语义步骤、权限暂停、plan、Cell 输出和 Artifact 通过 WebSocket 流式传输。重新打开运行中的会话会重放有界的 current-turn buffer；已完成历史通过 REST 重新加载。
- **版本化 Artifact**——文件写入会创建以追加为主的 version record，服务器随后尝试为其绑定不可变 snapshot。只有 copy/binding 成功的版本才具备 restore-grade 不可变性。版本带有 provenance、环境快照、lineage、annotation、priority、edit、rename、restore，以及 Artifact/project ZIP 下载。当前 viewer 支持图片、CSV/TSV 表格、Markdown/text、HTML/PDF preview 和 vendored 3Dmol 三维分子结构。Restore 校验受信的不可变 snapshot，追加一个新版本和 source→restored lineage；它不会把 Artifact head 移回旧 row。
- **默认只读 Notebook**——稳定 Cell ID 投影 Python/R source、stdout/stderr、error、figure、file 和 retry revision。失败的旧 revision 保持折叠且只读；运行中的 Cell 就地更新。模型书写 Python/R fence 时会就地更新一个临时 draft block，只有执行开始时才由不可变 Cell 替换。
- **显式 developer REPL**——`OPENAI4S_NOTEBOOK_REPL=1` 启用多行 Python/R 输入。Shift+Enter 追加新 Cell，绝不编辑已执行 Cell。User Cell 与 Agent turn 共享同一 per-session FIFO execution coordinator。
- **精确所有权与取消**——runtime event 暴露 active execution owner 和 queue position。Cancel/interrupt 请求发送精确 `execution_id`、`owner.kind` 和 `owner.id`。Notebook Stop 只选择 active 或 queued 的 `user_repl` ticket；没有时 fail closed，不会退回去中断 Agent。Composer/session cancel 以当前显示的精确 owner 为目标。
- **Action Timeline 工作台表面**——右侧 dock 为 native tool（含 delegation call）、Python/R Cell、domain mutation 和 finalization 提供安全 card；另有 Recovery card 显示 restore/retry/fresh-restart 可用性，以及包含 checkpoint fork/activate/revert/undo 控件的 Branch panel。Branch activation 是显式 FIFO lifecycle mutation：停止旧 runtime，并原子选择 checkpoint 的结构化 side-state。Workspace materialization 仍逐文件进行；runtime recovery 会报告 `Active`、`Partial` 或 `Failed`，而不是假装任意内存已存活。Context、child-agent 和 Sandbox container 保持分离。Permission wait 仍使用既有 interactive prompt，不使用持久 Timeline card。Frontend 从最近 500 个 action 开始，可显式加载更早的 500-action page；在保留最新状态的同时最多保存 2,000 个 action，且永不渲染 raw argument 或 provider wire state。Canonical token usage 会显示；cost 只有在记录 action 时部署提供了明确 price metadata 才显示，否则保持 unknown。
- **可移植 Session package**——session menu export 生成一个 deterministic、versioned ZIP，并为 ledger、Notebook、branch、workspace CAS、Artifact version、environment/bootstrap reference、lineage、plan/review/memory 和非秘密 policy state 保存 hash。Import 把 ZIP 当作 untrusted input 校验，把 identity remap 到新 project/root，降低 permission/capability，并始终以 `Ended · view only` 打开在持久 quarantine 中。Conversation、Notebook 和 file 仍可读；在用户显式确认 `Restart fresh` 之前，所有 live mutation 返回 423。Package code、hook 和 Kernel generation 永不 replay。
- **Customize 与科研 UX**——model profile、Skills/Specialists、connector、compute、network、memory、permission rule、plan/explore mode、voice dictation、upload/paste/drag-drop、annotation，以及中英双语。

## Notebook 生命周期与真实性

Python 与 R 是 lazy、相互独立的 persistent slot。仅含 metadata 或 tool 的轮次不会启动任何 kernel。有对应 projection 时，Notebook 显示 generation、branch/revision placeholder、owner/queue，以及 `Live / Busy / Ended · view only / Restoring / Partial / Failed` 等状态。

停止或重启 kernel 会保留 message、Cell history、workspace file 和 Artifact version，但不会保留任意内存对象。只有经过验证的 recovery pipeline 确实重建并校验状态后，才能把 daemon restart 描述为 namespace recovery。

## Session-domain 功能状态

Gateway 现已暴露 session-domain read/write adapter，但部分产品能力仍有意保持 Partial：

| 功能 | 状态 |
|---|---|
| Action Ledger 与安全 Timeline projection | Backend、经脱敏且最大 500 条的 latest/older/newer REST window、UI card 和显式“load earlier actions”控件已实现。近期已完成历史从 REST 重载；没有独立的持久 per-action WS backlog。 |
| Daemon restart 后的持久审批 | Pending card 直接从 SQLite 重载，无需启动 kernel/runtime。Live decision 恢复被它精确阻塞的调用；restart 后的审批绝不 replay 已存 argument，会记录旧 action 未执行并显示显式 “Continue and replan” 控件。仅 restart 的 `once` grant 精确匹配，并在 15 分钟后过期。 |
| Checkpoint / branch / revert preview | Content-addressed snapshot 及公开 checkpoint/fork/activate/preview/apply/undo route 已实现。Durable Cell 与 user message 以 best-effort 捕获精确 cursor checkpoint；只有映射已证明的 record 才显示 Fork，旧 history 返回 409。UI 暴露 Cell Fork，并折叠内部 checkpoint。Activation 恢复 workspace、Artifact head、environment、capability/permission state 和 checkpoint 完整 plan/review/memory snapshot，然后重建 branch-bound runtime。缺少该 sidecar 的 legacy checkpoint 报告 `Partial`，并保留 live structured state。Revert/Undo 从相同 append-only cursor 投影 provider history、chat 与 Notebook。 |
| Recovery Journal 与 verified recovery pipeline | Active branch 已实现 status/action 及 `restore`/`retry`/`restart_fresh` mutation。Candidate worker 只有在 bootstrap、CAS/Artifact validation、replay safety 和 state validation 后才 publish。Bootstrap v2 捕获真实 worker 的完整 package set、locale、interpreter prefix 和 SDK/provenance/Host protocol version；精确的已加载 Skill sidecar bytes/hash 永不泄漏到普通 Cell output。没有 verified recipe 的任意历史 namespace 最终仍是 Partial。 |
| Python/R `.ipynb` export | 已实现 deterministic language export/ZIP route，以及 Notebook header 和 provenance execution view 中稳定的 bundle ZIP 下载。独立 Python/R single-notebook selector 仍仅 API 可用。 |
| 独立 Jupyter adapter | Daemon 外可使用可选 `openai4s-python` / `openai4s-r` KernelSpec 和 lazy `ipykernel` wire bridge。它们拥有独立 namespace，不接入 Web session Host RPC、Artifact、ledger、queue 或 recovery。 |
| Scientific renderer registry | 已实现安全 catalog 与 version-bound descriptor route，并驱动专用 2D chemistry、genome、sequence/MSA 和 LaTeX UI component。Descriptor 始终绑定不可变 Artifact version 与 provenance。 |
| Variable Inspector | 已通过专门的 idle-only protocol request 实现手动 Python/R namespace refresh。它不会启动 worker 或运行 Cell，会避开 custom repr/active binding/promise，并在 Busy/Restoring/Ended 时 fail closed。Fingerprint 是有界 sample，不是 namespace snapshot。 |
| Local model discovery | Customize → Models 在禁用 proxy 和 redirect 的情况下扫描固定 literal-loopback catalog。结果只是建议，用户必须显式添加 profile。未知 local model capability 保持保守，直到被显式配置。 |
| Context composition / security panel | 已实现安全的 session-specific REST projection 与 frontend container。Sandbox status 汇总真正启动过的 Python/R worker；两者不同时采用较弱声明。只有在两种语言都尚未运行 self-test 时才保持 `not_started`。 |
| Delegation durability 与 policy | Session-wide persistent tree 持有 spawn budget、child progress/result、cancellation propagation 和 turn-boundary steering inbox。Child 的 model/step/permission/capability restriction 在 native-tool catalog 与 Host RPC 两个边界强制执行，不能放宽 parent ceiling。Daemon restart 保留 tree，但如实把未完成 child 标为 `stopped: daemon_restart`；不宣称进程延续。 |
| Session export/import | 已实现 deterministic hashed package 与 dashboard/session-menu UI。Package 保留 branch-owned conversation、完整 canonical provider group、Revert projection metadata、Notebook/Artifact/lineage state、evidence-review history 和 checkpoint plan/review/memory snapshot。Import 拒绝 traversal、duplicate/symlink entry、tampering、compression/size/secret violation；不会覆盖既有 session，不启动 Kernel，并在确认 fresh restart 前始终处于持久 quarantine。 |

Frontend 有意把缺失 route 视为不可用：控件被禁用或显示 empty state，而不会修改 history 或伪装 backend action 成功。

## Demo session（首次启动时生成）

首次启动时，应用会生成一个 NIF3/DUF34 蛋白家族分析；它调用真实 UniProt 和 RCSB PDB API 以及内置 MCP connector，在没有 LLM key 时运行六个 deterministic Notebook Cell：

1. 获取 UniProt sequence。
2. 调用内置 MCP connector。
3. 绘制 Kyte-Doolittle hydropathy plot。
4. 生成 `family_biochemistry.csv`。
5. 执行 RCSB search 并下载 `nif3_structure.pdb`。
6. 生成 `nif3_report.md`。

网络不可用的步骤会被跳过并报告；demo data 绝不伪造。
