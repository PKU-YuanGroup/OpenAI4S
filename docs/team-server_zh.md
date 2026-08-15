# 把 OpenAI4S 当实验室服务器跑

[English](team-server.md)

这是多用户模式的运维页：开什么、按什么顺序开、每个开关到底暴露了什么。背后的设计决策在 [`team-server-plan.md`](team-server-plan.md)，这一页讲的是你要做的事。

下面所有东西**默认都是关的**。默认安装就是它一直以来的那个单用户工作台——同样的路由、同样的行为、同样的测试（INV-1）。不会因为你升级了就发生任何变化。

## 1. 打开团队模式

```bash
export OPENAI4S_TEAM_MODE=1
openai4s serve
```

打开后，浏览器入口是 `/login`，没有会话 cookie 就什么都不答。第一个账号在机器本地建：

```bash
openai4s user add alice --role admin
```

loopback CLI 按决策 D2 等同管理员——能读到宿主上 access-token 文件的人本来就拥有这台机器——它的动作以 `cli` 记账，而不是冒充某个真人账号。

**绑定地址仍然是安全边界。** 团队模式加的是账号，不是"可以暴露"。放在终结 TLS 的反向代理后面，或者走 SSH——见 [`security.md`](security.md)。明文 HTTP 上的口令，就是这张网上的口令。

## 2. 项目、可见性与配额

会话属于创建它的人，可选地属于某个项目。`project` 可见性意味着项目成员可读，`private` 意味着只有属主。没有项目的会话按构造即为 private；完全没有归属行的会话——团队模式之前的历史、CLI 直跑、demo 播种——则是仅管理员可见。最后这条是刻意的 fail-closed：「我们不知道这是谁的」绝不能解析成「大家的」。

管理员读一个 private 会话会往审计日志写一行 `admin_read_private`。这就是管理员权限的全部代价，而且是**每次查看**一行而不是每个会话一行。

配额按用户或项目、按种类、按窗口设置：

```bash
curl -X PUT .../api/v1/team/quotas -d '{"scope":"user","scope_id":"...",
  "kind":"llm_output_tokens","limit_amount":2000000,"window":"month"}'
```

只有存在真实执行点的 kind 才允许设置。一个没人查的限额比没有限额更糟，因为总会有人按它做计划。

## 2b. 文件区

`OPENAI4S_DATA_ROOTS` 是冒号分隔的目录白名单，而 D8 说了三种根：**只读的 datasets 区**、项目区、以及**个人 scratch**。策略就写在同一个值里：

```bash
export OPENAI4S_DATA_ROOTS=/lab/datasets=ro:/lab/scratch
```

`=ro` 让一个根对所有人只读，管理员也不例外——只读根的意义就在于每个分析都在读的参考数据不能漂。可写的根有一个固定的命名空间：每位成员的上传落在 `<root>/users/<用户名>/`，由身份算出而绝不来自请求；别人的 `users/<name>/` 不可读——共享区仍然共享，scratch 是个人的。这是固定命名空间而不是猜测，于是"这是不是别的成员的区域"是一个关于路径的问题，而不是"叫 `alice` 的目录是人还是数据集"的问题。

## 2c. 只有管理员能做的事

团队模式加的是账号，并不会把每一个 daemon 级的面都变成按人的。有些事是**对着整个实例**做的，那就是运维的事，无论谁登录了：

- 写实例配置——LLM 提供方、它的端点与凭据、模型 profile、默认模型。改一下 `llm_base_url`，全员的流量就指向了写的人选的主机；
- 旧的 compute-job 运行器（`/compute/jobs`），它以 daemon 自己的 uid 执行 `bash -c <command>`——读也不给，因为一条作业行就是某个人敲下的命令；
- 注册远程算力、往所有内核共用的 venv 里装包、配置携带组凭据的连接器、往每位成员的 agent 都会加载的目录里发布 skill、重置常设权限规则，以及创建**全局**权限规则（成员可以为自己的会话、或自己参与的项目建规则）。

成员保留 UI 需要的每一项读取。完整清单在 `openai4s/server/team_policy.py`；不在其上的路由就是成员的。

## 3. 集群会话（可选）

会话要能跑在调度器上，必须先满足两件事：站点被描述过，且 daemon 接受 worker 拨回。

**描述站点**：写 `<data_dir>/cluster.toml`。profile 是用户唯一能看到的词汇——它们各自映射到的队列与服务等级只留在这个文件里（决策 D5、INV-2）：

```toml
job_name_prefix = "openai4s"

[profiles.cpu-interactive]
cpus = 8
memory_mb = 32768
walltime_s = 14400
partition = "compute"          # 永不离开这个文件

[profiles.gpu-interactive]
cpus = 16
memory_mb = 131072
gpus = 1
walltime_s = 14400
partition = "gpu"
qos = "interactive"
```

**接受 worker**：给出一个计算节点够得着的地址：

```bash
export OPENAI4S_WORKER_LISTEN=0.0.0.0:8761      # worker 拨进来的地方
export OPENAI4S_WORKER_ADVERTISE=head01.lab     # 告诉它们去拨哪里
```

`OPENAI4S_WORKER_LISTEN` 是监听器开关本身。默认不开，因为在每一台永远不会跑集群作业的笔记本上开一个监听器，是攻击面而不是便利。凡是绑定地址不是计算节点解析得了的名字，就要设 `OPENAI4S_WORKER_ADVERTISE`——绑 `0.0.0.0` 是"从哪儿都收"的写法，而 `0.0.0.0` 不是任何东西拨得过去的地方。

保护那个端口的是 bootstrap 凭据，不是网络。worker 出示的是对 `(allocation, epoch, rank, expires, nonce)` 的 HMAC，用每 daemon 一份的密钥签名；网关在交换任何一个协议字节**之前**就校验并烧掉它——这条 socket 承载 Host RPC，先服务后检查的监听器，在"后"的那段时间里就是一个远程执行面。拒绝只说 "refused"：过期、重放、伪造之间的差别，对猜的人就是一个 oracle。

凭据以 `0600` 文件传递，只把路径告诉调度器（INV-9）。作业的环境，任何能向调度器查询该作业的人都读得到，所以提交环境会直接拒绝凭据形状的变量名。

### 租约

集群会话占着真资源，所以它有两个时钟：闲置 TTL（默认 2 小时）与最大生命期（默认 48 小时）。**worker 活着不等于用户在场**——内核健康、socket 连着的会话，只要没人在里面跑过东西就是闲置的，而且仍占着别人在排队等的东西。只有用户的执行，或显式续期，才续租约。

是哪个时钟到点，决定了告诉用户什么：`SESSION_IDLE_TIMEOUT` 意思是"回来它还在"；`SESSION_MAX_LIFETIME_EXCEEDED` 意思是"这一个到此为止，做什么都没用"。

### 节点挂掉的时候

恢复策略是 `WORKSPACE_ONLY`，而且明说。文件活下来是因为它们本来就在共享文件系统上；内核的内存活不下来——变量、import、三个 cell 之前有人设的随机种子。会话在新的 epoch 上继续，界面升起 `KERNEL_STATE_LOST` 横幅，因为悄悄重连之后产出的结果，看起来和那个已经丢掉的会话产出的结果一模一样（INV-11）。

`CHECKPOINT` 被声明且以 `501` 拒绝。真正实现它需要进程级快照，且集群也得支持；做一半会恢复一部分状态、悄悄丢掉其余——那是三种可能行为里最糟的一种。

## 4. 个人 LLM 密钥（可选）

成员可以经 `PUT /api/v1/auth/me/llm-key` 按 provider 提供自己的凭据。密钥进的是与其他凭据同一个 secret broker，数据库只留引用。没配置就是回落，所以什么都不设的成员照旧用组里的 key。

配置了却读不出来的密钥会**拒绝该轮**而不是回落。用户明确要求用自己的凭据，悄悄记到组里是一个他没有做过的决定。

## 5. 从实验室外面访问

daemon 默认绑 loopback，这也是推荐做法。从别处访问，两条受支持的路，按优先级：

1. **SSH 隧道。** `ssh -N -L 8760:127.0.0.1:8760 you@lab-host`。什么都不暴露，鉴权用你已有的 SSH 配置，也没有新组件要运维。
2. **实验室网内带 TLS 的反向代理**，并打开团队模式。代理终结 TLS 后转发到 loopback；`Host` 白名单仍然生效，所以要把代理的主机名加进 `OPENAI4S_ALLOWED_HOSTS`。

**relay 不是跑实验室服务器的第三条路。** `openai4s relay` 与 `openai4s share` 是另一件事——**单个**会话的只读、已脱敏快照，经由 daemon 主动拨出的隧道发送（见 [`webshare.md`](webshare.md)）。relay 看得到明文，而 share 投影刻意不是一个登录面：它不带 cookie、没有写路由、也没有活的内核。把它指向团队部署，得到的是某一个会话的投影，而不是把工作台开出去。

要在外网用工作台本身，走第 1 或第 2 条。

## 6. 配好之后查什么

```bash
openai4s doctor                     # 配置、凭据、内核
curl -s localhost:8760/api/v1/auth/status
```

集群不可用时 daemon 会打印原因而不是拒绝启动：`cluster.toml` 写坏了就降级为仅本机并把原因写到 stderr，worker 监听器绑不上也一样。运维在配置文件里打错一个字，不该把整个工作台给所有人搞停。
