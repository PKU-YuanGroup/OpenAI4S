# 安全分层

[English](README.md)

围绕 Code-as-Action 的安全层里，有六道住在这个包中——代码分类、内核 OS 隔离、CPython 审计钩子、shell 预检、提示注入标注、生物安全筛查——旁边还有两条根本不是冲着 Cell 去的边界：BYOC provider helper 外面那层 OS 隔离，其形状与内核那层刻意相反；以及凭据存储，也就是 secret 从数据库里搬出来之后的去处。其余几道是有意放在别处的。子进程环境过滤在 [`kernel/environment.py`](../kernel/environment.py)，Host 权限和持久化审批在 [`host_dispatch.py`](../host_dispatch.py) 与 [`storage/permissions.py`](../storage/permissions.py)，shell capability 本身在 [`host/bash.py`](../host/bash.py) 和 [`sdk/bash.py`](../sdk/bash.py)，应用出网在 [`egress.py`](../egress.py)。这里没有一道可以指着说“就是它”的总边界，这正是设计意图：每道控制拦的东西不同，失败的方式也不同，谁都不是那道必须扛住一切的防线。

## 在架构中的位置

- Python/R Cell 执行前，外层 runtime 可以调用 [`classifier.py`](classifier.py)。Cell 被拒绝后不会进入 worker，只会作为一条 observation 回到外层循环。
- [`kernel/manager.py`](../kernel/manager.py) 请求 [`sandbox.py`](sandbox.py) 包装 worker 进程，并把实测到的沙箱状态发布出去。
- [`kernel/worker.py`](../kernel/worker.py) 在 CPython 里安装 [`audit_hook.py`](audit_hook.py)；`host.bash` 在 capability 授权执行之前，还会先跑一遍 [`shellcheck.py`](shellcheck.py)。
- 工具/MCP/web 的输出可以过一遍 [`injection.py`](injection.py)，把疑似指令标注成不可信数据。
- [`biosecurity.py`](biosecurity.py) 提供校准过的 prompt 策略，以及一个可选的轨迹判定。Host 权限和持久化的人工审批不在本目录，而在 [`HostDispatcher`](../host_dispatch.py) 和 [`storage/permissions.py`](../storage/permissions.py)。
- [`ComputeManager`](../compute/manager.py) 请求 [`byoc_confinement.py`](byoc_confinement.py) 包装 BYOC provider helper，并把 helper 自查时要比对的锚值递进去——边界由 Host 建立，由被限制的那个进程来验证。[`secret_broker.py`](secret_broker.py) 是 [`secret_migration.py`](secret_migration.py) 把凭据从数据库里搬出来之后它的去处；在此期间让数据目录保持仅属主可读的，则是 [`permissions.py`](permissions.py)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 说明这套分层模型，并重新导出代码分类、注入扫描和生物安全判定的 API。 |
| [`audit_hook.py`](audit_hook.py) | 在 worker 内安装 CPython 审计钩子：从可写的工作区、临时目录或 Artifact 根目录 `ctypes.dlopen` 一个动态库会被拒绝，而解释器与包安装前缀下的加载照常放行。它把依赖的函数捕获成定义期的关键字默认值，并在安装完成后删掉指向钩子自身的所有 Python 句柄，因此 Cell 内对模块命名空间的 monkeypatch 无法直接解除这道检查。这是抵抗，不是免疫：目标是让它在 Cell 内难以被绕过，而不是绝无可能。 |
| [`biosecurity.py`](biosecurity.py) | 存放校准问责式的 prompt 片段和轨迹筛查器。先由一个低成本的相关性触发器判断这次筛查值不值得花一次模型调用；真正的筛查是一次独立调用，返回 ALLOW、ESCALATE 或 BLOCK，解析时对格式松散的回复也留了余地。 |
| [`byoc_confinement.py`](byoc_confinement.py) | BYOC provider helper 外面的那层 OS 边界，形状与内核那层刻意不同：helper **必须**能上网——调 provider 的 REST API 就是它的全部工作——并且**不得**读用户的家目录，而 Cell 反倒是可以读的。macOS 上是一份 Seatbelt profile，对 `$HOME` 拒绝的是*整个读取类别*，同时把解释器自身路径的各级组件以 `file-read-metadata` 字面量的形式重新放行，好让加载器仍然找得到那个二进制；此外还有一条单独的 `file-read-xattr` 拒绝——因为在 macOS 上，扩展属性经常*就是*文件本身——而正因为它是针对具体操作的规则，后面那条覆盖各读取根目录的整类放行并不会把它重新打开。它同时拒绝 keychain 服务，因为凭据不是一个文件：`security find-generic-password` 是去问 securityd，那个进程不受文件系统规则约束。Linux 上则是 bubblewrap 的 `--tmpfs $HOME` 再把解释器自己的路径绑回来，加上一个私有 PID namespace，免得 `/proc` 被穿到 daemon 的根，再加上盖在 `/run` 上的一层 tmpfs——只读绑定并不妨碍 helper 去 `connect(2)` 那些容器控制 socket——同时把解析器配置重新绑回来，好让 DNS 在这层遮蔽下仍然能用。`available()` 是真去建立边界并实测，而不是报告某个二进制装没装：在一台禁用了 user namespace 的主机上，`bwrap` 装着，却什么也隔离不了。网络隔离是另一项能力，而且**没有**启用——`confinement_status()` 会把这一点明说出来，而不是留给人去假设。 |
| [`classifier.py`](classifier.py) | 分三层对一个 Cell 做分类：不含任何风险 token 的代码走快速通道；命中高置信度静态攻击特征的直接判定；剩下的在 `llm` 模式里交给模型复核。它返回结构化的 SAFE/UNSAFE 证据，自身不抛异常。 |
| [`injection.py`](injection.py) | 用静态特征、以及可选的一次 LLM 调用，扫描工具返回的不可信文本。疑似内容会被加上一段警告前缀，原文一个字都不删。 |
| [`permissions.py`](permissions.py) | 数据目录、凭据数据库以及 SQLite 在旁边建出来的那些附属文件，一律收成仅属主可读写。数据库原本是按进程 umask 创建的——通常本机任何账号都读得到——在共享工作站或实验室服务器上，这等于整个凭据库离一次 `cat` 只差一步。按契约是尽力而为：一个表达不了 POSIX 权限位的文件系统（Windows、FAT、某些网络挂载）不该拦住 daemon 启动，所以调用方拿到的是一个布尔值，据此如实报告当前姿态，而不是假定它成立。权限位不是加密，也不能让明文存储变得可以接受；它挡掉的是最省事的那种读取，并且是它让数据目录的备份或 rsync 不至于在对端变成全局可读。 |
| [`fsprobe.py`](fsprobe.py) | 基于 `os.lstat` 的 `lstat_is_symlink` / `lstat_kind`，给那些「读不到就必须拒绝」的守卫用。CPython 3.14 把 `Path.is_symlink()`、`is_dir()`、`is_file()`、`exists()` 改到了 `os.path` 之上，而后者会吞掉所有 `OSError`，于是 `try: p.is_symlink() except OSError: 拒绝` 这种守卫在容器所用的解释器上不再拒绝，而是把读不到的路径报成「不是符号链接」。这些探针对缺失给出明确答案，其余错误一律上抛；`host/files.py` 的 secret 别名遍历、环境代际布局检查、动态 scope 删除、陈旧 artifact 清理、工作区快照和 bring-up 校验器都经由它们读取。 |
| [`sandbox.py`](sandbox.py) | 用 Seatbelt（macOS）或 bubblewrap（Linux）包装 worker 命令。它构造工作区、私有临时目录、secret 读取屏蔽和网络策略，用一次真实的 allow/deny 自测来验证策略确实生效，并回报一份状态，其中的 `state` 是 `enabled`、`disabled` 或 `unavailable`。团队策略还会遮住 OpenAI4S 数据、其他成员的 data-root 个人区和系统临时目录中的旧 kernel，只精确开放本会话输入，并拒绝工作区中无法完全归属的既存硬链接。在强制生效的 bubblewrap 下，它还负责中断投递：SIGINT 经由 pidfd 钉住的 worker 身份送达（团队模式从 bubblewrap 的 `--info-fd` 报告里钉住），而不是靠碰运气的数字 PID；结构上送不到的停止请求会被丢弃并记下原因，由 `take_interrupt_gap()` 交给 `Kernel.interrupt()`，让一次没有送达的停止被如实上报，而不是被宣称成功。探测与命令执行都可注入，因此即使身处一个不允许嵌套沙箱的父沙箱里，这些受支持的路径仍然测得了。 |
| [`secret_broker.py`](secret_broker.py) | 把凭据放在与 canonical 数据目录绑定的不透明命名空间后面：业务表只存指针，同一系统钥匙串中的两个 Store 也不会撞槽。移动或复制数据目录、或者遇到无法判定归属的 v1 系统存储引用时，需要重新输入凭据；旧的全局环境变量契约仍作为运维显式选择的兼容回退。核心只用标准库，因此 macOS 用 `security`、Linux 用 `secret-tool`，值一律走 stdin，避免出现在本机 `ps` 可见的 argv 中。环境注入只读且不落盘；`OPENAI4S_SECRET_STORE=auto` 失败即关闭，不会无声降级成明文，也有意不提供伪装成加密的混淆文件后端。 |
| [`secret_migration.py`](secret_migration.py) | 把明文凭据从数据库里搬出去，且可恢复：写入新存储 → 读回验证 → 把行替换成引用 → 到这一步才删掉旧值。这个顺序的任何一个前缀被打断都是安全的。读回是那一步吃劲的：钥匙串在锁着、或者授权弹窗被拒时，完全可能接受了写入却什么也没存下，而仅凭"写入没抛异常"就删掉明文，正是一次"安全改进"把用户锁在自己模型配置之外的方式。这里不打印任何 secret；进度只以引用和一小段哈希前缀来汇报。 |
| [`shellcheck.py`](shellcheck.py) | 内核本地的 `host.bash` 执行前跑的一小份静态阻止名单，只拦明显灾难性的字面命令，再隐蔽一点的就拦不住。它不做完整的 shell 解析，这是有意为之。 |

## 执行与失败矩阵

| 层 | 命中后的行为 | 重要失败行为 |
| --- | --- | --- |
| 代码分类器 | 静态特征或 LLM 判出 `UNSAFE`，这个 Cell 就不会执行。 | `off` 会关掉这一层。分类器自身异常、LLM 未配置时失败即放行；但 LLM 确实被调用了、响应却解析不了时，会偏保守判成 `UNSAFE`。`heuristic` 模式会放行那些带风险 token、却没命中静态攻击特征的残余代码。 |
| OS 沙箱 | 已生效的 backend 把写入限制在工作区和私有临时目录，屏蔽对 secret 文件的读取，并在没有显式允许时封禁原始网络访问。 | `enforce` 失败即拒绝。单用户 worker 的 `auto` 仍可在明确告警后无隔离运行；团队读取策略绝不降级：`auto`/`off`、后端缺失、自测失败、硬链接清单不确定或路径组合不安全都会拒绝 Cell。 |
| CPython 审计钩子 | 拒绝覆盖范围内、从可写路径发起的 `ctypes.dlopen` 事件。 | 它只管 Python、只管这一类事件，既不是通用的原生代码沙箱，也管不到 R；而且可信前缀策略本身必须是对的，否则这道检查没有意义。 |
| Shell 预检 | 拦下少数无歧义的破坏性命令字符串。 | 它基于正则，自身出错时失败即放行，并且明确不是抗混淆的沙箱。Host capability 与 OS 隔离仍然不可少。 |
| 注入扫描 | 给模型要读的内容加上一段警告横幅。 | 它不删除、也不阻断内容。静态扫描之后，出错、模型未配置、LLM 输出无法解析，都会失败即放行。 |
| 生物安全筛查 | 返回 ALLOW、ESCALATE 或 BLOCK，交给调用方按策略处理。 | 只有关键词触发之后才会运行；不可用时失败即放行，返回 ALLOW。一条判定本身不是执行隔离。 |
| BYOC helper 边界 | provider helper 运行时，用户的家目录不可读、钥匙串被拒绝；它在读取凭据之前从内部自查这一点，边界不成立就以 71 退出，什么都不做。 | 只要建立不起边界，`enforce` 就拒绝*每一个* byoc 操作，而不只是 submit；`auto` 则跑不受限的那种形态并如实上报。网络任何时候都没有被隔离。有两处残留是明说而不是留白：Seatbelt 对被拒路径答 `EPERM`、对不存在的路径答 `ENOENT`，所以 `$HOME` 下某个东西*存不存在*仍然观察得到；另外扩展属性的*名字*沿着解释器自己那条遍历路线仍然读得到。 |
| Secret 存储 | 凭据存在系统钥匙串里，或者由进程环境提供，数据库行里只留一个不透明引用。 | 两种后端都不可用时，`auto` 是 fail closed，而不是降级成明文。`plaintext` 仍然存在，但永远不会隐式生效。迁移在删除任何东西之前先读回验证，所以在任何一个前缀被打断都是安全的。 |

## 运维注意事项

- 不要只看配置就断定 worker 已经被沙箱包住；要去看 runtime 实测出来的 `SandboxStatus` 和它的 warning。自测通过只说明当前这个 backend、这套策略是生效的，并不等于宣称隔离是完整的。
- 沙箱的原始网络规则，与 Host/应用层的出网策略，是两回事。放开其中一个不等于授权了另一个。
- 一道控制的强度，取决于验证它的那个探测问的是什么问题。BYOC 那条家目录拒绝，当初是用一个读文件*内容*的探测来验证的，于是 `stat()` 和 `getxattr()` 一直在提供 `open()` 已经拒绝的东西，直到有人分别去探它们为止。在这里收紧规则时，要到后面那条放行重新打开的区域里去实测，而不是只在本来就已经被拒的区域里量——在那里，答案对，理由却是错的。
- 两个 backend 不是同一道控制。在 macOS 上验证过的边界，对 Linux 那份形态什么都说明不了，反之亦然；这个包出过的问题，大多是那些压根没在真正生效的平台上跑过的平台分支。
- [`kernel/environment.py`](../kernel/environment.py) 里的环境过滤是另一道 secret 边界。无论是基于名称的过滤，还是输出脱敏，都不可能认出 secret 的所有表示形式。
- 团队 Cell 隔离保护的是 OpenAI4S 管理的数据和 data root 个人命名空间，不是 daemon Unix 账号可读的每一条路径。普通宿主文件以及任意非 OpenAI4S 的同 UID 进程仍属运维信任边界；敌对租户应使用 OS 账号或容器隔离。团队 data root 不得与 canonical 系统临时目录重叠。
- 本项目是本地的、面向可信用户的工作台，不是经过加固的公网多租户执行服务。要把 daemon 绑到公网上，需要另外设计认证与隔离方案。

## 相关文档

- [安全模型](../../docs/security.md)
- [系统架构](../../docs/architecture.md)
- [配置](../../docs/configuration.md)
