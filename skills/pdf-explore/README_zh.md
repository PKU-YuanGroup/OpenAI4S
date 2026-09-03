# PDF Explore Skill

用来对付大到无法常驻对话 context 的 PDF。用 `read_file` 附上的页面，过一轮就会被丢掉，于是一个横跨多节的问题会变成反复重读同一批页；这个 Skill 改为在 Python 内核里把文档解析一次，文本就留在那里不走了。先找到需要的章节，再从里面把要的东西取出来，其余的留在磁盘上。Sidecar 缓存解析好的页面，然后对这些页面并行发出有界的 `host.llm` 调用。模型能看到的，只是文本层或 OCR 能读出来的那些内容，不会更多。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。
有 Node 18+ 即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install pdf-explore --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入
`./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则
写到你指定的任意位置；`--dry-run` 只打印解析出的绝对路径，不写任何文件。重装时
若目标副本被你改过则拒绝覆盖，`uninstall` 也只删除它自己写过的文件。同一条命令
的发布名写法是 `npx openai4s-skills install pdf-explore`，只是这个包还没有发布
到 npm。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/pdf-explore
python3 -m zipfile -c pdf-explore.zip pdf-explore
```

同一份内容的图形界面版本是点击下载整个
[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)：解压
后把 `skills/pdf-explore/` 拷出来即可。如果你本来就在跑 OpenAI4S，那这里没有任
何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于
`<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的
那些事：
[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 教你挑对工具：先看 outline，再按需要用相关性 scan、逐页 map、结构化抽取，或者在要从图里读数时渲染高 dpi 再裁剪。同时说明扫描件模式、页面缓存、一次 fan-out 的成本，以及什么时候直接读页比这一整套都省事。 |
| [`kernel.py`](kernel.py) | 可选 sidecar；它与内核的 `__main__` 共享命名空间，所以所有名字都带 `pdf_` 前缀。`pdf_resolve` 把路径或 Artifact ID 落成本地文件，`pdf_pages` 解析并缓存逐页文本与页面渲染图。在此之上，`pdf_outline` 构建目录（PDF 自带 outline 就用自带的，没有才让模型来做），`pdf_scan` 按查询给页面排序，`pdf_map` 逐页总结，`pdf_extract` 按 JSON Schema 从每页抽取记录，全部走并行 `host.llm` 调用，并受批量上限约束。页面文本是不可信输入：每次调用都用随机 nonce 作为分隔符构造 prompt，形似标签的页面文本在插入前会被中和，过长的页面按显式标记截断。跑完之后用 `pdf_scan_cost` 汇总 token 用量。 |

可选的 PDF 与 OCR 库必须存在于当前内核中。抽取出来的文本丢掉了页面的视觉结构，因此凡是要据以下结论的 label 和数值，都应该回到渲染出的页面或它的 crop 上核对一遍。
