# Scenario 测试用例

[English](README.md)

本目录统一管理评测示例、数据来源、安装和私有评分，并与 `../pipelines/` 严格
分离。随仓库提供的 JSON 用例是 CC0 合成协议检查，只验证 schema、隔离、预算、
哈希和 evaluator 接线，不声称代表模型科学精度。

一条命令安装一个用例：

```bash
uv run python skills/retrosynthesis_planning/scenarios/test_cases/install.py \
  --case skills/retrosynthesis_planning/scenarios/test_cases/01_single_step_retrosynthesis.json \
  --workspace /tmp/openai4s-retro-case
```

安装器生成互相分离的 `public/`、`private_evaluator/`、`results/`，并在
`installation.json` 记录所有安装文件的 SHA256。GT pipeline 只校验公开文件的哈希，
不打开私有文件。evaluator 在评分前校验两个边界；文件被修改或缺失会使安装失效，
不会静默改变冻结结果的分数。之后运行匹配的公开 pipeline，
冻结输出，再由 evaluator 读取私有 Ground Truth：

```bash
uv run python skills/retrosynthesis_planning/scenarios/gt_codebases/01_single_step_retrosynthesis.py \
  --workspace /tmp/openai4s-retro-case
uv run python skills/retrosynthesis_planning/scenarios/test_cases/evaluate.py \
  --scenario single_step --workspace /tmp/openai4s-retro-case
```

每个用例的 `private_evaluator/references.json` 都**刻意不等于**模型自己的 rank-1
公开输出：参考答案若照抄预测，所有准确率都会因构造而恒为 1.0，一个只会返回常数的
scorer 也能通过。`tests/test_retrosynthesis_evaluator.py` 钉住了由此得到的非平凡
数值，因此把参考答案"改回"模型的答案会让测试变红，而不是悄悄变好。

`database_sources.json` 是正式数据库注册表。标为 `not_frozen` 的条目不得静默下载或
重新发布；维护者必须先冻结来源 revision、许可证结论、split 和 SHA256。在此之前，
一键安装只允许使用随仓库发布的协议 fixture。

## 正式数据来源核验（2026-09-09）

本次只读核验检查了官方说明和小型元数据响应，没有读取数据集字节。六个注册条目仍为
`release_status: not_frozen`，且均缺少 `revision`、`license`、`split`、`sha256`。
下列候选证据用于维护者准入审查，不代表数据已获准或已冻结。代码 commit 标识说明或
预处理实现，不能固定外部 Dropbox、Box 或 Google Drive 归档的字节。

| 场景 | 候选来源与身份 | 许可证据及待完成准入 |
| --- | --- | --- |
| `single_step` | [GLN](https://github.com/Hanjun-Dai/GLN/tree/b5bd7b181a61a8289cc1d1a33825b2c417bed0ef)，commit `b5bd7b181a61a8289cc1d1a33825b2c417bed0ef`，指向 Schneider50K 原始 CSV 目录及 RetroSim train/validation/test 划分。 | GLN 代码为 MIT；选用派生数据归档仍需独立审查。确认实际文件，冻结场景规定的 1,000 个 test 目标、多参考前体、split manifest 和 SHA256。 |
| `multistep` | [PaRoutes，Zenodo 记录 6275421](https://zenodo.org/records/6275421)，DOI `10.5281/zenodo.6275421`，版本 `1.0.0`；六个 n1/n5 文件见下表。 | [记录 API](https://zenodo.org/api/records/6275421) 明确声明数据许可 `cc-by-4.0`；仓库 Apache-2.0 是代码许可。维护者记录准入与署名安排后，仍需冻结文件 SHA256、公开/私有目标划分、stock 规范化和预算配置。 |
| `atom_mapping` | [RXNMapper](https://github.com/rxn4chemistry/rxnmapper/tree/a01ecdcd5ac944850e9691739c1df858e005fd39)，commit `a01ecdcd5ac944850e9691739c1df858e005fd39`，指向 [RXNMapperData](https://ibm.box.com/v/RXNMapperData)；尚未选定具体真值文件。 | MIT 代码不能证明某个数据文件的许可或独立真值。需指定文件、许可依据及真值审核负责人，确认至少 1,000 条唯一可评分反应、对称对应关系、变化键、歧义决策与分组划分；被测 mapper 的预测不能充当自身真值。 |
| `forward` | [MolecularTransformer](https://github.com/pschwllr/MolecularTransformer/tree/aeb339daf0a029b391f8307fb3f467f461605dd2)，commit `aeb339daf0a029b391f8307fb3f467f461605dd2`，指向 [USPTO/data.zip](https://github.com/wengong-jin/nips17-rexgen/blob/fb7dea369b0721b88cd0133a7d66348d244f65d3/USPTO/data.zip) 及 [tokenized 数据包](https://ibm.box.com/v/MolecularTransformerData)；上游示例使用 `src-test.txt` 和 `tgt-test.txt`。 | MIT 是代码许可观察，不是 USPTO 派生 split 的准入决定。确认选用的 separated 包及成员文件，保持原 test 划分，记录角色转换、去重、私有参考与 SHA256。不得假定上游存在 `test.csv`；本地生成 CSV 时须记录来源文件和转换过程。 |
| `conditions` | [Parrot 下载脚本](https://github.com/wangxr0526/Parrot/blob/0fb2325567e21011589641544e32427c8244e2a9/preprocess_script/download_data.py)，commit `0fb2325567e21011589641544e32427c8244e2a9`，列出 `USPTO_condition_final.zip`，Google Drive 文件 ID 为 `1aX70qzZrJ9TZ9KpqnvUVR8WBxiTwXOsI`。 | 仓库代码为 MIT；该外部数据归档的独立许可依据尚未确认。保留原 split/index、条件词表和完整条件元组参考，再计算哈希。另行准入的 HF 模型快照不能替代本数据集准入；Reaxys 继续排除。 |
| `yield` | [rxn_yields](https://github.com/rxn4chemistry/rxn_yields/tree/d9e6b87ce1b881978490d68bfc00021e3b48127a)，commit `d9e6b87ce1b881978490d68bfc00021e3b48127a`，列出 `data/Buchwald-Hartwig/Dreher_and_Doyle_input_data.xlsx`（2,134,762 字节）；[训练脚本](https://github.com/rxn4chemistry/rxn_yields/blob/d9e6b87ce1b881978490d68bfc00021e3b48127a/training_scripts/launch_buchwald_hartwig_training.py) 使用 `FullCV_01`–`FullCV_10` 和 `Test1`–`Test4` 工作表。 | 原始 HTE 表及派生 split 的许可须与 MIT 代码、模型权重分别审查。核对真实工作表内容和划分边界，保留 random IID 与四个 MFF OOD 评测、单位、输入列、参考和 SHA256。 |

[Lowe 原始 USPTO 元数据](https://api.figshare.com/v2/articles/5104873) 明确声明
**CC0**、版本 `1`，DOI 为 `10.6084/m9.figshare.5104873.v1`。它是可保存的上游
证据，但仍需核对所选派生包的对应关系与条款。`USPTO_MIT` 标识数据集/划分约定，
MIT 代码许可证不能作为该派生数据的许可结论。
[已准入模型 revision 的 HF 元数据](https://huggingface.co/api/models/xiaoruiwang/ChemEnzyRetroPlanner_metadata/revision/b9ef6049d341bfc62d835f09ad6ce33b6f86b047)
声明 MIT，但该观察不能证明 Parrot 另行存放于 Google Drive 的训练/测试数据许可。

### PaRoutes 文件证据

以下为上游公开元数据，不是本地字节核验结果。六个必要评测文件共 **125,966,045 字节**；
仅获取这些目标、库存和参考时，无须下载该记录其余的整套 2.7 GB 资源。

| 文件 | 上游字节数 | 上游 MD5（不是 SHA256） |
| --- | ---: | --- |
| `n1-targets.txt` | 465689 | `5adae99357cdad829073b197c7813152` |
| `n1-stock.txt` | 394013 | `b15d92af317d5639ae69a422c7a1f1c1` |
| `n1-routes.json` | 52012998 | `219c2466795cd7aad6a86d6a797eadc5` |
| `n5-targets.txt` | 495657 | `c05ad490f2f5e203631d05a45d7e9e7f` |
| `n5-stock.txt` | 392504 | `eb2cf057cd8be772f2ba4652d48c6bec` |
| `n5-routes.json` | 72205184 | `a6e814e53fcfa358e6f0e5bceb14d711` |

### 仍需作出的决定与验证

维护者须为各个选用数据集记录许可依据、允许的使用/再分发范围及署名义务。依据未明确时，
仅提供获取说明与证据，数据保持 `not_frozen`。Atom mapping 还须选定独立真值来源和
审核负责人；mapper checkpoint 或下载首页不足以完成这一决定。这些是剩余人工决定，
不能由 agent 根据代码仓库的许可证徽章代填。

获准获取数据后，应从实际输入和生成的评测文件计算 SHA256，生成确定性 split 与去重
manifest，并验证私有参考隔离。上游 MD5 只作补充完整性证据，不能用 MD5、代码 commit
或 Git blob SHA1 替代 SHA256。Checkpoint 的训练重叠证据是报告正式科学分数的独立前提。
现有 `test_production_database_registry_fails_closed_until_frozen` 仅在标记 `frozen`
时检查四字段非空；单独通过该测试不能证明许可审查、文件哈希或真值验证已经完成。

## 文件

| 文件 | 用途 |
| --- | --- |
| `install.py` | 保持数据边界的一键测试数据安装器。 |
| `evaluate.py` | 私有 evaluator 入口。 |
| `database_sources.json` | 正式数据库要求和发布状态。 |
| `01_single_step_retrosynthesis.json` | Scenario 1 合成评测用例。 |
| `02_multistep_route_planning.json` | Scenario 2 合成评测用例。 |
| `03_atom_mapping.json` | Scenario 3 合成评测用例。 |
| `04_forward_prediction.json` | Scenario 4 合成评测用例。 |
| `05_condition_recommendation.json` | Scenario 5 合成评测用例。 |
| `06_yield_estimation.json` | Scenario 6 合成评测用例。 |
| `README.md` | 英文目录说明。 |
