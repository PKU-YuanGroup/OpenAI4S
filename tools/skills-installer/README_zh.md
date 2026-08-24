# Skills 安装器（`npx openai4s-skills`）

[English](README.md)

把 OpenAI4S 自带的 Skill 库复制到本机——装进 Claude Code、装进 OpenAI4S 数据
目录，或装进命令行指定的任意目录。

```bash
npx openai4s-skills list
npx openai4s-skills install --all                 # 41 个精选 Skill
npx openai4s-skills install --collection bioskills # 561 个固定版第三方配方
npx openai4s-skills install alphafold2 boltz --target claude
npx openai4s-skills installed
npx openai4s-skills uninstall --all
```

`npx github:PKU-YuanGroup/OpenAI4S <command>` 直接从仓库运行同一个 CLI，不需要
先发布到 npm。

## 文件

| 文件 | 职责 |
| --- | --- |
| `cli.mjs` | 参数解析与四个子命令。唯一带 shebang 的文件，也是根 `package.json` 中 `bin` 的指向。 |
| `catalog.mjs` | 发现与 frontmatter。它复述 Python loader 的规则——目录含 `SKILL.md` 即为一个 Skill，含 `COLLECTION.json` 则为一个集合、成员在其下一层——因此两侧都没有硬编码目录名。 |
| `source.mjs` | Skill 树的来源：已经含有 `skills/` 的 checkout，或 codeload.github.com 上的源码 tarball，按 ref 缓存。 |
| `targz.mjs` | 零依赖的 gzip + POSIX tar 读取器，包含那段防止远程归档写出解压根目录的路径校验。 |
| `install.mjs` | 目标目录、复制、`.openai4s-skills.json` 清单，以及卸载。 |
| `selftest.mjs` | 关卡。覆盖解压安全性、针对本仓库真实目录树的发现，以及安装/覆盖/卸载的判定。 |
| `check_package.mjs` | 第二道关卡，刻意独立：`npm pack` 产出的包里还带着 CLI 与那些 Skill 吗？一条不再匹配的 `files` glob 会发布出一个能跑却无物可装的命令。 |

## 它拒绝做的事

三条拒绝承载了这个命令的大部分价值，因为每一条都对应着安装器悄悄毁掉别人成果
的一种方式：

- **它不会覆盖你改过的 Skill。** 每个已安装文件的 SHA-256 都被记录，因此"我们
  写下的副本"能与"你改过的副本"以及"本来就在那里的目录"区分开。`--force` 可以
  强行覆盖，并会说明它覆盖了哪些文件。
- **它不会删除不是自己写下的文件。** `uninstall` 只删清单里的文件并清理由此变空
  的目录；你新增的文件会阻止删除，而不是被一并扫掉。
- **它不会解压逃逸出目标目录的归档成员。** 绝对路径、`..` 片段、盘符与 NUL 字节
  一律拒绝；符号链接或硬链接成员会中止解压而不是被跳过——静默跳过等于汇报了一棵
  它并没有产出的完整目录树。

## 溯源

一次安装会记录它的字节从哪来。对于下载，那就是请求的 ref、URL，以及实际收到的
tarball 的 SHA-256——不是 commit SHA，因为把分支解析成 commit 是第二次请求，而
这里没有任何代码拿它的答案去核对解开的那份归档。想要构造上就可复现的安装，请用
`--ref <commit-sha>`。

## 它处在什么位置

对 OpenAI4S 用户来说这个命令基本是多余的：wheel 已经带上了全部 602 个 Skill，
而 `openai4s/skills_loader/loader.py` 让自带 Skill 优先于 `<data_dir>/user-skills`
中的同名者。它存在的理由是反方向——把这些配方送到不是 OpenAI4S 的 agent 面前。
