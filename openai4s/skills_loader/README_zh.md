# Skill 加载与版本管理

[English](README.md)

这里管两件事：Skill 的发现，和 Skill 的版本。loader 找到以 recipe 为中心的 Skill，在渐进披露真正来取全文之前，只把摘要交给外层循环；版本服务把可写 Skill 的包体作为不可变版本存进 Store，磁盘上的目录视图则整体原子替换。两条路径都会先对可选的 Python sidecar 做编译检查，之后内核才可能 import 它。内置目录树若自己声明为一个**集合（collection）**，在 prompt 里就只占一行——正是这一点，让数百份固定版第三方配方既能被逐个检索，摘要又不必挤进每一次系统 prompt。

## 在架构中的位置

Skill 是 Code-as-Action 的扩展面，不是原生 JSON 工具 schema。一个 Skill 目录里放着 `SKILL.md`、可选的 `kernel.py` sidecar 和可选资源。外层循环的 prompt 只看得到名称和一行摘要；[`../tools/skills.py`](../tools/skills.py) 与 Host 服务会在任务确实需要时才取回完整 recipe。Agent 写出的 Python 随后就能在科学 worker 里导入这个已通过编译检查的 sidecar。

内置 Skill 是只读的，名称冲突时也由它胜出。可写 Skill 位于配置好的数据目录和 project root 下，版本由 Store 管理。默认 loader 自己不持有仓储对象，每次读写能力状态都重新向当前的 Store generation 要一次：一个 loader 完全可能活得比创建它的那个 Store 更久，否则就会指向一条已经关闭的连接。

## 集合（collection）

集合就是一个用 `COLLECTION.json` 自我声明的内置目录——文件里写着它的 id 和它想要的那一行 prompt——成员 Skill 放在它下一层。这里没有对某棵具体目录树的硬编码：标记文件本身就是全部的发现规则，因此 `skills/bioskills/` 里 561 份固定版配方和将来任何一个集合走的都是同一段代码，id 撞车会直接报错，而不是悄悄丢掉一棵树。每个成员仍然保留自己的名字，照样可启用、可搜索、可加载；变的只是披露方式。`system_context()` 为每个集合渲染一行、而不是每个成员一行，其中 `{count}` 替换为**调用方**真正看得见的成员数，因此带允许名单的 Specialist 拿到的是按自己那份子集计数的折叠行。所有分桶都会被排空——否则一个压根没有分支能把它送进 prompt 的第二个集合，就不是被紧凑披露，而是根本没有披露。精确枚举交给原生 `list_skills` 工具：用 `collection=<id>` 加 `offset`/`next_offset` 一页页翻。

有三处后果容易被忽略。`search()` 把调用方的允许名单放在打分**之后**、截断 limit **之前**：同一份词法索引里躺着 561 份配方，先截断再过滤，会让 Specialist 在那个正是允许名单存在理由的查询上拿到空结果。内核内的 bootstrap finder 把每个集合根注册成独立的 import 前缀，于是成员按 `<collection>.<member>` 解析，未知成员抛 `ModuleNotFoundError`，而带目录命名空间的 `<catalog>.<collection>.…` 形式直接拒绝。还有，bootstrap manifest 只嵌入真正带 sidecar 的条目——集合配方一个都没有，因此生成的代码片段不再把四分之一兆字节根本用不上的条目塞进每一次内核启动、每一条持久 generation 记录和每一个 cursor checkpoint。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 用 docstring 写清 Skill 目录的约定，并重新导出公开名字：`Skill`、`SkillLoader`、`SkillVersionService` 和 `discover_skills`。 |
| [`frontmatter_edit.py`](./frontmatter_edit.py) | 只改 `name`/`description`/`origin`，其余原样保留。Web Customize 的保存原先是拿这三个字段把 frontmatter 整个重建一遍，于是 `license`（42 个精选内置 Skill 里有 29 个）、`category`（25 个）、嵌套的 `metadata` 块（23 个）、`requirements`（14 个）和 `fold_cue`（1 个）都会被「顺手改个错别字」的人删掉。真正让它从元数据问题变成正确性问题的是 `requirements`：readiness 由它算出，丢了 `[gpu]` 的 skill 不再报 `needs_setup`，转而声称自己哪儿都能跑。先解析再重新输出解决不了——`_parse_frontmatter` 按设计就会压平列表值、忽略嵌套块——所以这里改的是原始文本行，凡是它不拥有的内容都逐字节保留。 |
| [`loader.py`](./loader.py) | 负责找到 Skill，并决定露出多少。它解析 `SKILL.md` 的 frontmatter，扫描内置、project 与用户三个 root，解析能力状态，并按关键词重合度给搜索结果打分。系统 prompt 只拿得到摘要；完整 recipe、sidecar 的 import 提示，以及内核启动用的 bootstrap manifest 都按需生成。`kernel.py` sidecar 在被任何人 import 之前，先过一遍编译检查。其中两个视图接受调用方的允许名单，因为被委派的子 Agent 本就只应拿到语料的一个子集：`system_context(only=…)` 只用放行的 Skill 渲染 prompt，`bootstrap_code(allowed=…)` 则按其补集关上内核内的 import 闸——这道闸原先只认得 `disabled`（用户自己的开关），压根没有「被扣下」这个概念，于是真正执行代码的那一半一直是敞着的。传 `None` 表示不受限，输出与之前逐字节一致，因为 bootstrap manifest id 是一个持久的恢复键，不能动。声明的 `requirements:` 终于有人读了：readiness 分 `ready`/`needs_setup`/`unknown` 三态，且只依据本机状态判定——`nvidia-smi` 只在 `PATH` 上查找、从不执行，因为浏览一份 Skill 清单并不等于请求去联络什么，否则渲染一次列表就要按 Skill 数量起一堆子进程。`unknown` 是一个真实答案而非凑数：猜 `ready` 会把失败推迟到任务深处才爆，猜 `needs_setup` 则让用户去装一件可能早就装好的东西。readiness 与 `enabled` 并列而不嵌在里面——停用的 Skill 完全可能是 ready 的，启用一个 Skill 也变不出它要的硬件。集合的发现也在这里：`collections()` 读取每个内置子目录的 `COLLECTION.json`，`bundled_roots()` 报出每个 root 及其所属集合 id，`bundled_directory_collision()` 回答只查名字答不了的那个问题——一个可写包的目录名会不会落在某个内置成员目录上。 |
| [`versions.py`](./versions.py) | 可写 Skill 的安装、升级、发布、回滚与删除。包体先校验（大小有界、不含 symlink、路径不得越出目录），再作为不可变版本存起来；磁盘上的 personal/project 目录只是一份视图，先在旁边重建好再整体换入。数据库侧的激活走 compare-and-swap；这一步失败时，会先把原来的目录换回来，错误才向上抛。回滚在真正落盘之前，会把内置冲突的两项检查——声明的名字和目录 slug——重新跑一遍：公开 API 允许调用方独立于显示名给出 slug，而内置目录自安装那次之后完全可能新增了一个同名或同目录的 Skill。 |

## Skill 编写与安全契约

- `SKILL.md` 是给 Agent 照着写代码的 recipe，不是可执行的控制工具声明。
- 编译检查只能证明 sidecar 语法和结构没问题，证明不了它到底会做什么：真正执行时，内核沙箱、Host 权限和正常的 import 规则一样都不会少。
- 不安全路径、symlink、超限的单个文件或整个包、非法的规范名称，都会在写成可用的 Skill 目录之前被挡掉。
- 内置 root 保持只读；名称撞车时，内置的一方永远优先于可写的一方。
- 集合标记改变的是披露方式，不是信任级别或可写性：它的成员和别的内置 Skill 一样只读，可写包也不得占用它们的目录名。
