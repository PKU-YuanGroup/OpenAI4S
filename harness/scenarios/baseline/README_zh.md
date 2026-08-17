# Baseline 场景

[English](README.md)

这些场景组成必需的、确定性的离线 `tier:pr` Harness 门禁。每个文件都由 [`../../schema.py`](../../schema.py) 校验。通用场景由 [`../../runner.py`](../../runner.py) 执行；`surface: auto_mode_contract` 场景由 [`../../auto_mode_contract.py`](../../auto_mode_contract.py) 执行，`surface: auto_mode_terminal_contract` 场景由 [`../../auto_mode_terminal_contract.py`](../../auto_mode_terminal_contract.py) 执行。两个 Auto Mode adapter 都是与生产实现无关的拟议契约。每次运行都必须停在文件写明的终止状态上，并按顺序发出声明的事件。声明了 `script_consumed` 的通用场景，还得把脚本化的 model 序列消费干净。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`auto_mode_budget_exhausted.json`](auto_mode_budget_exhausted.json) | 精确运行预算耗尽时在 admission 前停止；同一运行不能静默补充预算或自动续跑。 |
| [`auto_mode_completed_with_issues.json`](auto_mode_completed_with_issues.json) | 保证 `candidate` 不是终态，并钉住 Auto Fix 预算耗尽后的结构化 open material finding。 |
| [`auto_mode_guardian_action_hash_mismatch.json`](auto_mode_guardian_action_hash_mismatch.json) | Adapter 对实际变异的 runtime action 做规范化并重新散列，然后在独立的 `safety_boundary` 失败；trace 保留两个计算所得的 hash。 |
| [`auto_mode_guardian_allow_once_replay.json`](auto_mode_guardian_allow_once_replay.json) | 原子消费绑定到一个精确动作的 capability，随后在安全边界拒绝重放已消费的 capability。 |
| [`auto_mode_guardian_allow_once_variant.json`](auto_mode_guardian_allow_once_variant.json) | 首次使用改变精确 action digest 时拒绝并烧毁 one-shot capability，使变体既不能执行也不能复用 token。 |
| [`auto_mode_guardian_audit_failure.json`](auto_mode_guardian_audit_failure.json) | 审计追加失败不能授权动作，也不能冒充 Guardian deny；它终止于 `safety_boundary`。 |
| [`auto_mode_guardian_denial_circuit_consecutive.json`](auto_mode_guardian_denial_circuit_consecutive.json) | 当前持久拒绝成为连续第三次拒绝时，只打开 denial circuit。 |
| [`auto_mode_guardian_denial_circuit_window.json`](auto_mode_guardian_denial_circuit_window.json) | 当前持久拒绝成为有序最近 50 次中的第十次拒绝时，只打开 denial circuit。 |
| [`auto_mode_guardian_denied.json`](auto_mode_guardian_denied.json) | 把显式拒绝绑定到一个精确的 SHA-256 action digest，不签发一次性 capability、不创建 standing allow，也不提前打开 denial circuit；当前运行以 `no_safe_continuation` 终止。 |
| [`auto_mode_guardian_parse_failure.json`](auto_mode_guardian_parse_failure.json) | 无法解析的 Guardian 决策失败即拒绝，绝不发出 `action_authorized`。 |
| [`auto_mode_guardian_timeout.json`](auto_mode_guardian_timeout.json) | 两次 Guardian 超时耗尽两次尝试预算，随后记录 `decision=unavailable`，只打开 infra breaker，动作始终不执行。 |
| [`auto_mode_guardian_timeout_then_deny.json`](auto_mode_guardian_timeout_then_deny.json) | 第一次超时只安排一次重试、两个 breaker 都保持关闭；第二次尝试记录非 fallback 的持久拒绝，仍保持两个 breaker 关闭，并以 `no_safe_continuation` 终止。 |
| [`auto_mode_loop_detected.json`](auto_mode_loop_detected.json) | 在精确阈值打开通用 no-progress 熔断并禁止自动重提；属于 Reviewer 或 Guardian 的循环会路由到对应 owner 的终态。 |
| [`auto_mode_outcome_unknown.json`](auto_mode_outcome_unknown.json) | 有界 readback 无法核实已经派发的外部副作用时保留不确定性；只能进入 reconciliation 或 operator review，绝不能盲目重试动作。 |
| [`auto_mode_policy_requires_explicit_setup.json`](auto_mode_policy_requires_explicit_setup.json) | 在执行前持久拒绝缺失的策略前置条件，也不调用 Guardian 作为绕过手段。 |
| [`auto_mode_review_audit_failure.json`](auto_mode_review_audit_failure.json) | Reviewer 审计无法落盘是独立的安全边界失败，不是 pass 或普通 review 结果。 |
| [`auto_mode_review_evidence_hash_mismatch.json`](auto_mode_review_evidence_hash_mismatch.json) | Adapter 对实际变异且完整的 evidence snapshot 做规范化并重新散列，然后在安全边界失败，并记录两个计算所得的 hash。 |
| [`auto_mode_review_only_completed_with_issues.json`](auto_mode_review_only_completed_with_issues.json) | 将 review-only 的 material findings 保留为 `completed_with_issues`，不启动 Repair，也不声称已验证。 |
| [`auto_mode_review_parse_failure.json`](auto_mode_review_parse_failure.json) | 两次无法解析的 Scientific Reviewer verdict 耗尽两次尝试预算，终止为可恢复的 `review_unavailable`，绝不 false-pass。 |
| [`auto_mode_review_timeout.json`](auto_mode_review_timeout.json) | 两次 Scientific Reviewer 超时后，candidate 仍非终态，并以可恢复的 `review_unavailable` 结束。 |
| [`auto_mode_review_timeout_then_pass.json`](auto_mode_review_timeout_then_pass.json) | 第一次 Reviewer 超时只安排一次重试；第二次独立审阅有效时可以产生 `verified`。 |
| [`auto_mode_safe_rollback_unavailable.json`](auto_mode_safe_rollback_unavailable.json) | 无法证明 rollback admission 时，在正式工作区发生任何修改之前阻止 Auto Repair。 |
| [`auto_mode_verified.json`](auto_mode_verified.json) | 钉住拟议的 Scientific Reviewer 成功路径和唯一 `verified` 终态。 |
| [`scheduled_timeout.json`](scheduled_timeout.json) | 在运行第一次到达 `before_model` 这个 fault point 时，注入一条可重试的 `timeout` 故障。预期只有一次 model attempt、终止原因为 `model_error`，trace 里还必须出现显式的 `fault_injected` 事件；故障挡在前面的那条脚本化 response 是故意不被消费的。 |
| [`single_response_submitted.json`](single_response_submitted.json) | 顺利路径：一条成功的脚本化 response、一次 model attempt、`submitted` 终止原因、按顺序发出的生命周期事件，脚本一条不剩。 |
| [`two_response_sequence.json`](two_response_sequence.json) | 两条脚本化 response，循环因此跑两轮。检查两次 attempt 的先后顺序，以及只有第二轮才把这次运行带到最终的 `submitted` 终止事件。 |

不要为了迁就漂移就放松预期。只有预期的契约本身变了才去改场景，改完还要审阅产生的 trace。

这些 Auto Mode 场景**不能**证明生产环境已经发出这些状态；其首个事件明确记录 `production_state_machine: false`。它们冻结的是后续实施 Stage 必须满足的 Stage 0 契约。Evidence snapshot 必须完整、冻结且每个引用都可解析；pass 不能携带 open material finding，`completed_with_issues` 也不能早于 Auto Fix 预算耗尽。Guardian 场景只从 deterministic `ask` 开始；hard policy boundary 不能被重新贴成 Guardian decision。规范 fixture 的 hash 在 adapter 内计算，audit request 与 completion assessment 使用不同 digest，并与独立审阅的 golden 对照；不信任调用方提供的 observed digest。

五类非 Guardian 终态场景有各自封闭的条件 schema 和独立 golden。它们刻意设置 `auto_user_state: null`，也不携带 Reviewer 或 Guardian 审计身份。只有 `outcome_unknown` 可恢复，而且恢复只允许 reconciliation 或 operator review，不能自动重放结果不明的副作用。

一次普通拒绝不等于熔断：denial circuit 只有在连续 3 次拒绝，或最近 50 次 decision 中出现 10 次拒绝时才能打开。
