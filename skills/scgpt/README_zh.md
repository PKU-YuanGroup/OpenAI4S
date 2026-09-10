# scGPT Skill

scGPT 是一个在单细胞表达数据上预训练的 transformer：用外部 checkpoint 做单细胞 embedding、细胞类型注释和基因层面的表示。想要的是一个基础模型对数据集的看法，就用它；想要的是在自己的 count 上拟合出来的生成模型，那是 `scvi-tools`（scVI / scANVI）。scGPT 跑起来需要的东西，本目录一概不提供：checkpoint、vocabulary、`AnnData`、GPU 环境都得自己准备。

数据这一头 recipe 替你查不了，得自己确认：checkpoint 的目录结构对不对、基因名能不能和 vocabulary 对上、counts 是按什么方式预处理的、batch 元数据和标签是不是真如你所想。还有一点：无论是 zero-shot 还是微调之后的注释，都只是模型输出，不是真实标签。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。有 Node 18+、且 `PATH` 上有 `git`（npm 靠它解析 `github:` 形式的包名）即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install scgpt --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入 `./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则写到你指定的任意位置。往目标写任何东西之前，都会先打印解析出的绝对路径；`--dry-run` 到此为止，不再写入。重装时，若目标副本被你改过、或者不是它自己装的，就拒绝覆盖；`uninstall` 也只删除它自己写过的文件。对于 npm 发布版中包含的配方，可用 `npx @pku-yuangroup/openai4s-skills install scgpt --target claude` 安装发布版本。npm 目录可能与当前仓库不同。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包。tarball 是整个仓库（超过 100 MB），下面这条管道是 POSIX shell（macOS、Linux、WSL）写法，只解出这一个目录：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/scgpt
python3 -m zipfile -c scgpt.zip scgpt
```

同一份内容的图形界面版本是点击下载整个[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)，Windows 上也走这条路：解压后把 `skills/scgpt/` 拷出来即可（`unzip main.zip 'OpenAI4S-main/skills/scgpt/*'` 只解这一个目录）。如果你本来就在跑 OpenAI4S，那这里没有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于 `<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的那些事：[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | scGPT 的 checkpoint 是一个原始目录（`args.json`、`best_model.pt`、`vocab.json`），不是 Hugging Face 仓库，所以加载器要的是文件系统路径，repo id 喂不进去。`adata.var` 里的基因名必须和 checkpoint 的 vocabulary 对得上；对不上的会被悄悄丢掉，所以在读结果之前先把 `gene_col` 核一遍。`embed_data` 把逐细胞的 embedding 留在 `.obsm["X_scGPT"]`，注释从这里继续。此外还有批处理与资源需求、远程计算路径，以及真正会咬人的两个默认值：`use_fast_transformer` 默认为 `True`，它走的是未必 import 得起来的 FlashAttention 路径；torchtext 的 `Vocab` shim 过期时，报出来的是属性缺失，而不是一次干脆的失败。 |
