# Literature Review Skill

以证据为先的文献工作流程：先检索，再动笔。你写出来的 DOI，要么真的指向一篇说了你所说内容的论文，要么就是编造，而这两者只需几秒钟就能分辨——所以这份 recipe 要求核验发生在工具调用的记录里，而不是回复里的一句话，哪怕那篇论文你闭着眼都能背出来。Sidecar 可以查询公共学术 API 并核验 identifier，但索引覆盖到哪里、某篇论文有没有被标记撤稿、全文能不能拿到，都是外部条件，而且会随时间变化。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。
有 Node 18+ 即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install literature-review --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入
`./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则
写到你指定的任意位置；`--dry-run` 只打印解析出的绝对路径，不写任何文件。重装时
若目标副本被你改过则拒绝覆盖，`uninstall` 也只删除它自己写过的文件。同一条命令
的发布名写法是 `npx openai4s-skills install literature-review`，只是这个包还没
有发布到 npm。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/literature-review
python3 -m zipfile -c literature-review.zip literature-review
```

同一份内容的图形界面版本是点击下载整个
[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)：解压
后把 `skills/literature-review/` 拷出来即可。如果你本来就在跑 OpenAI4S，那这里
没有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于
`<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的
那些事：
[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 先弄清对方到底在问什么；每一条论断都落在检索到的文献上；DOI 靠查不靠记；撤稿与“根本没有这篇论文”这种诚实答案怎么给；综述是比较而不是罗列；结论的把握程度要和证据强度匹配；引用就地内联；以及保存之前的文字检查。 |
| [`kernel.py`](kernel.py) | 可选 sidecar：`lr_sdk` 取得的 `host` 句柄不会因为内核里这个名字被重新绑定而失效，`litrev_contact` 在能拿到用户邮箱时把它放进 polite-pool 的 User-Agent。`litrev_get` 与 `litrev_head` 是底下那层有界的 HTTP 请求（429 重试一次，出错返回 `None`），`quote_doi_path` 把 DOI 编码进请求路径，`crossref_year` 再把年份读回来。在这之上：`verify_dois`、`crossref_lookup`、`search_openalex` 负责解析与检索，`expand_citations` 沿引文网络前后各走一步，`extract_dois` 和 `html_decode` 把 DOI 从正文里抠出来。`style_pass` 是对成稿跑的正则 lint，特意不含任何 LLM 调用：稿子里引着检索来的第三方文本，而一条让 Agent 照做的自由文本修改建议，本身就是一条注入通道。 |

查得到不等于核验过全文；查不到也不能证明这篇论文不存在。最终的论断必须落在真正检索到的原始文献上。
