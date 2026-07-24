# `openai4s/benchmark/`

带版本的科学工作流基准的 runner，清单本身放在 [`workflows/`](../../workflows/README_zh.md)：十个 workflow、二十个真的会执行的用例。

提出这套基准的方案对「什么会让它一文不值」讲得很明确——一个没人执行的 fixture 目录，或者因为被测对象是 mock 所以能过的用例。所以这里每一步都驱动真实子系统：真实的 Store、真实的 kernel manager、真实的 host dispatcher、真实的 compute manager、真实的 connector service、真实的环境事务。被注入的只有离线跑不了的那些——LLM（测试套件本来就 mock 了它）、网络（connector 抓取喂的是录制下来的 body）、包管理器（单元测试里的环境构建不可能去下载一个 solver）——而且每一样都是注入**进**生产代码，而不是把生产代码替换掉。一个自己造答案的 step，衡量的是这个 step 自己。

**声明的结果是契约的一部分。** 一个期望 `failure` 的用例如果跑出了干净的成功，它失败的程度和一个期望成功却抛异常的用例完全一样——只会打分「没抛异常」的基准，对系统中「职责就是拒绝」的那一半什么也没衡量。`provenance`、`recovered`、`permission_denied` 存在的理由相同。

| 文件 | 用途 |
| --- | --- |
| `__init__.py` | 对外表面：`Case`、`Workflow`、`load_workflows`、`CaseResult`、`run_case`、`run_all`。树里其他东西不应被调用方 import。 |
| `model.py` | workflow 与 case **是什么**，以及从哪里读。清单用 JSON 而非 YAML，理由与内核一致——决定一次发布好不好的东西，不能要求先装一个第三方库才能读；而且它带版本，因为用例能被悄悄改动的基准，跨时间什么也衡量不了。 |
| `runner.py` | 跑一个用例，并判定发生的事情是不是它所声明的。有意思的是这个判定而不是执行：声明的结果与观察到的结果对比，任一方向不一致都算失败。 |
| `steps.py` | 各个 step 的实现，一个 step 名对应一个函数，登记在 `STEPS` 里。每个函数接收共享的 `Context` 与用例的 inputs，返回一个并入结果的 dict；抛异常是 step 报告「工作流走不下去」的方式，由 runner 判定它是否符合声明。`SkipCase` 留给宿主确实跑不了某一步的情况（没有 `Rscript`、没有 shell），那是跳过，不是悄悄算过。 |

## 为什么清单不放在这里

它们在仓库根目录的 [`workflows/`](../../workflows/README_zh.md)，这样「改动基准的期望」就是一份挨着被它评判的代码的、可评审的 diff——而不是埋在某个包底下的一次 fixture 编辑。
