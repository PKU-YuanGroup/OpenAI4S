# `workflows/codebase-mode/`

**源码交付物，以及 Host 是否相信关于它的申报** — 当交付物是可复用管线或一次代码变更时，运行必须把实现写成源码文件、保留一个薄入口、真正跑一遍测试，然后在完成时申报 `source_files`、`entry_points`、`architecture_summary` 与 `test_evidence`。这个工作流用真实子系统端到端地跑通它：真实的持久 Python 内核逐个写文件，真实的 Store 把每个文件登记为 artifact 版本，测试作为子进程从一条真实 cell 里跑起来、那条 cell 落进真实的 `execution_log`，最后由真实的 `HostDispatcher` 决定是否接受这次提交。9 个用例分为 3 条接受路径和 6 条拒绝路径，每条各瞄准一项检查。

拒绝路径比接受路径更要紧。只看好运行成功的基准，对契约里负责说“不”的那一半什么都没测——而这里每一个变异产出的载荷**看上去**都是完整的。

Steps: `open_session`, `produce_codebase`, `verify_codebase`（变异用例插入 `tamper_codebase`）
Permissions: `workspace:read`, `workspace:write`, `kernel:execute`
Declared artifacts: `seqpipe/domain.py`, `seqpipe/io.py`, `seqpipe/pipeline.py`, `run_pipeline.py`, `tests/test_domain.py`

| 文件 | 用途 |
| --- | --- |
| `workflow.json` | 版本化清单：步骤、权限、声明产物、失败条件、每个用例写出的源码树，以及下面这些用例。版本 `1.0.0`。 |

## 用例

| 用例 | 声明结果 | 它钉住什么 |
| --- | --- | --- |
| `codebase-mode/structured-pipeline-accepted` | `success` | 一个包、一个薄入口、一次真正通过的测试被逐项核验并接受，且核验过的申报随完成一起落库 |
| `codebase-mode/single-file-task-stays-single-file` | `success` | 不存在文件数或行数门槛：诚实的单模块任务原样通过 |
| `codebase-mode/analysis-run-is-unaffected` | `success` | 向后兼容的另一半——`analysis_run` 既不要求这些字段，也不校验它们 |
| `codebase-mode/deleted-source-file` | `failure` | 被申报的源码文件已不存在，必须拒绝 |
| `codebase-mode/corrupted-source-file` | `failure` | 申报之后内容被替换，由声明的 sha256 抓住 |
| `codebase-mode/broken-entry-point` | `failure` | 入口已无法编译，**即便伪造者连它的摘要一起刷新了**、申报内部自洽，仍必须拒绝 |
| `codebase-mode/forged-test-cell` | `failure` | `test_evidence` 指向本次运行从未执行过的 cell，必须拒绝 |
| `codebase-mode/tests-actually-failed` | `failure` | 一条真实 cell 真的跑了、也真的失败了；通过与否只从存储的 stdout 读，模型说它通过并不改变任何事 |
| `codebase-mode/interrupted-multi-file-write` | `failure` | 7 个文件只写了 3 个却申报了全部 7 个——写到一半却宣称完整，必须拒绝 |
