# GPTomics bioSkills 资源包

[English](README.md)

本目录完整引入了已归档的
[GPTomics/bioSkills](https://github.com/GPTomics/bioSkills) 仓库中的 561 份生物信息学操作配方，
版本固定在 commit
[`d91ed3d563019e649dc854c56ccd62551359488a`](https://github.com/GPTomics/bioSkills/tree/d91ed3d563019e649dc854c56ccd62551359488a)。
上游采用 MIT 许可证，原文完整保存在 `LICENSE`。OpenAI4S 将这批内容视为一份固定版本、
只读的第三方资源，而不是 561 个由 OpenAI4S 分别维护的实现。

## 包含内容

资源包覆盖 63 个类别，包括序列与比对文件 I/O、变异检测、表达与表观组学、单细胞与空间
分析、结构生物学、蛋白质组、代谢组、微生物组、群体遗传学、临床生物统计、可视化、
报告和工作流管理。`COLLECTION.json` 是让本目录成为一个目录项、而不是 561 个平级 Skill 的关键：它记录
集合 id 和该集合在 system prompt 中占用的那一行文案。`MANIFEST.json` 是权威清单：记录每个公开 Skill 名称、上游路径、
转换后目录、来源 commit，以及每个导入文件的 SHA-256 与大小。

上游的 `clawhub-installer` 元 Skill 被有意排除；它是另一个 agent 平台的安装器，不是
科学分析配方。

## OpenAI4S 转换规则

| 上游布局 | 内置布局 |
| --- | --- |
| `<category>/<skill>/SKILL.md` | `bio-<category>-<skill>/SKILL.md` |
| `examples/` | `scripts/` |
| `usage-guide.md` | `references/usage-guide.md` |
| 顶层 `tool_type` 与 `primary_tool` | 移入 `metadata` |
| 没有分发来源信息 | 补 `origin: openai4s`、类别、仓库、commit 与许可证元数据 |

`origin: openai4s` 表示只读分发边界；作者与许可证仍是相邻元数据所记录的
GPTomics/MIT。命令示例也按本仓库的中转安全约定做了规范化：`python -m` / `python -c`
片段改用 `python3`，静默 `curl` 的各种写法补上遇错即停的参数，两处把下载内容直接交给
shell 的 Nextflow 安装示例改为文档已有的 bioconda 安装方式。所用规则写入
`MANIFEST.json`。这只是写法层面的规范化，不是一次安全审计：裸 `python script.py`、
`wget`、`pip install git+`、`install_github(...)`、`docker run` 和 `sudo apt install`
等指令都原样保留。若执行，它们仍受 shell 审批、OS 沙箱及已配置的原始网络姿态约束；
一条原始网络调用不会因为出现在 Skill 里就自动获得 Host egress 或 SSRF 防护。

## 发现机制与上下文成本

每份配方仍可通过 `list_skills`、`search_skills` 和 `load_skill` 单独发现和加载。
常驻 system prompt 只用一行摘要表示整个集合，不注入 561 条 description——上游安装器
估算那些描述约占 109,000 token。搜索仍为每份完整配方建立索引；集合提示明确要求
agent 在编写任何生物信息学 pipeline 前先搜索，若用户使用其他语言，则把方法、工具、
数据类型和工作流转换成英文关键词。显式限定能力的 specialist 看到的同样是这一行汇总，
只是计数换成它的允许名单里的配方数量。

各 Skill 子目录属于机械生成的第三方资产，不执行仓库通用的「每个目录一对中英 README」
规则；由本说明文档对、`LICENSE` 和 `MANIFEST.json` 统一记录其边界。manifest 不是一句
声明而是可校验的：`python3 scripts/import_bioskills.py --check` 会逐个文件重算哈希与
目录树比对，离线测试套件也会跑这一步。

## 依赖与执行安全

这些文件是配方，不是预装软件。整个集合涉及数百个可选 Python/R 包、命令行程序、
数据库、容器、参考数据和外部服务。因此，引入目录不会给 OpenAI4S 零依赖核心增加
依赖，也不声称一个环境能直接运行所有配方。每次执行前仍要本地核对版本与 PATH；GPU、
网络、凭据、授权软件或临床数据访问仍须遵守 OpenAI4S 原有的审批和环境配置流程。

导入的脚本只是示例，发现或搜索 Skill 时绝不会自动执行。若 agent 选择运行，该次执行
仍受常规的内核/shell 审批和 OS 沙箱姿态约束。大量 vendored 示例使用 `requests`、`urllib`、
`curl` 或 `wget` 发起原始网络请求；这些请求都不经过 Host egress allowlist 或 SSRF 检查。
`tests/test_egress_surface.py` 中的窄 Python AST 扫描只冻结了两份 stdlib `urllib` 示例，
并不是该集合完整 raw-network surface 的清单。执行前应先审核或改写配方，并优先用
`host.web_fetch` / `host.web_download` 访问网络。配方里的科学论述是方法指导，
不是针对新数据集的已验证结果。

## 复现导入

维护者用 `scripts/import_bioskills.py` 读取固定在上述 commit 的本地 checkout，并写入空目标
目录。导入过程不联网；commit 不同、Skill 数量不是 561、声明名重复或目标非空都会
fail closed。未来若更新上游，必须按一次新的 vendor 更新审查：有意修改 pin、审核 diff
与许可证、重新生成 manifest，并重跑 Skill、打包、secret scan 和目录文档检查。
