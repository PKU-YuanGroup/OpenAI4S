# Mineral Spectra Analysis Skill

读取未知混合矿物 Raman 谱。先对谱做一次预处理，然后在残差上循环：寻峰、拿这些峰去参考谱库里匹配、用 NNLS 把已选成分整体重新拟合、相减，再来一轮。最后给出成分列表、估计比例、每个成分的支持峰，以及一个可信度判定。这个循环在设计上就是盲的——带隐藏真值的合成算例可以生成、也可以打分，但分析过程中绝不读 `truth.json`，这条路径刻意不参与推断。

数值 pipeline 放在 sidecar 里：运行时需要 numpy、scipy、pybaselines 和 matplotlib，获准时还会下载并缓存 RRUFF 数据。目前的谱库和 pipeline 都还是原型级的，在合成算例上分数很高，并不能证明真实样本的矿物鉴定是对的。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。
有 Node 18+ 即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install mineral_spectra_analysis --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入
`./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则
写到你指定的任意位置；`--dry-run` 只打印解析出的绝对路径，不写任何文件。重装时
若目标副本被你改过则拒绝覆盖，`uninstall` 也只删除它自己写过的文件。同一条命令
的发布名写法是 `npx openai4s-skills install mineral_spectra_analysis`，只是这个
包还没有发布到 npm。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/mineral_spectra_analysis
python3 -m zipfile -c mineral_spectra_analysis.zip mineral_spectra_analysis
```

同一份内容的图形界面版本是点击下载整个
[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)：解压
后把 `skills/mineral_spectra_analysis/` 拷出来即可。如果你本来就在跑 OpenAI4S，
那这里没有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于
`<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的
那些事：
[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | pipeline 的顺序本身就是内容，而且必须原样保住：两列的谱进来，对齐到谱库网格，去尖峰、去噪、扣基线、归一化，这一整套只做一次——然后残差循环把这份洗干净的谱读回去，一直迭代到残差里没有峰了、相关性掉得太低了，或者再来一轮也捞不到什么为止。围绕它的是：依赖与 RRUFF 数据怎么准备、要产出的诊断与报告、输出目录长什么样、合成算例的评测为什么必须与推断路径隔开，以及这种基于谱库的拟合结果诚实地能读到什么程度。 |
| [`kernel.py`](kernel.py) | 可选 sidecar，数值计算都在这里。它报告哪些可选依赖可以导入、给出固定配置，把 RRUFF ZIP 下载并解析成对齐到同一网格的谱库，再把输入谱重采样到该网格并只预处理一次；之后驱动残差循环（二阶导数寻峰 → 按峰预筛并对参考谱排序匹配 → NNLS 重拟合 → 相减），循环结束后诊断残差、画图、写报告。生成带隐藏真值的合成算例、以及对真值打分也在这里，但与循环隔开。科学库一律推迟到调用时才 import，所以缺 numpy 时模块照样能导入。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`examples/`](examples/) | 一个 committed 的合成算例：可观测输入、单独存放的隐藏真值、录下来的盲分析结果、由它们派生的报告，以及重建报告的纯标准库脚本。 |
