<a id="en"></a>

# Docker and Kubernetes

**English** · [简体中文](#zh)

OpenAI4S ships a container image of the daemon and the web workbench, a
`compose.yaml` for a single machine, and Kubernetes manifests for a cluster.
This page is the operator's guide to all three: how to run them, what the
container boundary does and does not replace, and the limitations that are
real today rather than hypothetical.

Everything here is built from the repository:

| File | What it is |
|---|---|
| [`Dockerfile`](../Dockerfile) | The image: Debian-slim CPython, the wheel this tree builds, and the `science` extra |
| [`compose.yaml`](../compose.yaml) | One machine, one volume, published to loopback |
| [`deploy/kubernetes.yaml`](../deploy/kubernetes.yaml) | PVC + single-replica Deployment + ClusterIP Service |
| [`deploy/kubernetes-ingress.yaml`](../deploy/kubernetes-ingress.yaml) | Optional, and the piece most likely to be got wrong |
| [`scripts/container_smoke.sh`](../scripts/container_smoke.sh) | Builds the image and proves the daemon works inside it |

## Read this first: what "expose" means here

The daemon binds `127.0.0.1` by default, and [security.md](security.md) says
not to put `0.0.0.0` on an untrusted network. The image binds `0.0.0.0`
anyway — and that is not a contradiction, because the two statements are about
different networks.

Inside a container, `0.0.0.0` is the container's *own* network namespace. It is
the only address a published port or a Service can reach; a container bound to
`127.0.0.1` is reachable from nothing but itself. What actually decides
exposure is the next hop out: `-p 127.0.0.1:8760:8760` keeps it on your
machine's loopback, `-p 8760:8760` puts it on every interface your host has,
and a `LoadBalancer` Service puts it on the internet. The shipped compose file
publishes to loopback and the shipped Service is a `ClusterIP`, deliberately.

Two things change the moment the bind is not loopback, and both are worth
understanding before you publish anything:

- **The access token becomes mandatory and cannot be turned off.**
  `OPENAI4S_REQUIRE_TOKEN=0` is honoured on loopback only.
- **The DNS-rebinding `Host` allowlist stops applying.** With a wildcard bind
  the set of legitimate external hostnames is unknowable, so the daemon stops
  second-guessing the `Host` header. The token is then the only control in
  front of `/api/v1/kernel/execute`, `/api/v1/compute/jobs` and the Host RPC —
  all of which execute code.

## Quick start

```bash
docker build -t openai4s:local .
docker run -d --name openai4s \
  -p 127.0.0.1:8760:8760 \
  -v openai4s-data:/data \
  -e OPENAI4S_LLM_API_KEY="$OPENAI4S_LLM_API_KEY" \
  openai4s:local
docker logs openai4s
```

The last line prints the URL to open, token included. Or with Compose, which
sets the same things and adds a health check and a stop grace period:

```bash
docker compose up -d --build
docker compose exec openai4s openai4s url
```

Build the stdlib-only image — much smaller, no scientific stack for cells —
with `docker build --build-arg OPENAI4S_EXTRAS= -t openai4s:core .`.

## The access token

The daemon mints it once into `/data/access-token` and prints it at startup.
Three ways to get it:

```bash
docker compose exec openai4s openai4s url       # the URL, ready to open
docker logs openai4s 2>&1 | grep token          # the startup banner
kubectl -n openai4s exec deploy/openai4s -- openai4s url
```

It lives on the volume, so it survives restarts and every browser session you
have already authorised stays valid. Lose the volume and every cookie is
invalidated along with it.

There is **no environment variable that supplies the server's token**. If you
need a token you control (GitOps, a shared cluster), pre-seed
`/data/access-token` with a long random string, mode 0600, owned by uid 1000,
before the daemon first starts; `load_or_mint` uses an existing file unchanged.

Note that the token is printed in cleartext to stderr on every boot, so it
reaches pod logs and any aggregator you ship them to. Treat that log stream as
credential-bearing, or rotate the token by deleting the file and restarting.

## Configuration

Only the daemon's own knobs are listed; the full set is in
[configuration.md](configuration.md).

| Variable | Image default | Notes |
|---|---|---|
| `OPENAI4S_DATA_DIR` | `/data` | Everything worth keeping. Mount a volume here |
| `OPENAI4S_HOST` | `0.0.0.0` | Read once at import; set it in the image or pod spec, never from Python |
| `OPENAI4S_PORT` | `8760` | |
| `OPENAI4S_SECRET_LLM_LLM_API_KEY` | unset | The credential. Supply it; see below |
| `OPENAI4S_LLM_API_KEY` | unset | The credential, config-layer alternative. `OPENAI4S_<PROVIDER>_API_KEY` wins over it |
| `OPENAI4S_LLM_PROVIDER` / `_MODEL` | unset | Ordinary settings — set here or in the UI |
| `OPENAI4S_SECRET_STORE` | `env` | Credentials from the environment, written to disk nowhere |
| `OPENAI4S_SECRET_ENV` | `1` | Marks that backend available before any credential exists |
| `OPENAI4S_KERNEL_SANDBOX` | `auto` | See below |
| `OPENAI4S_NO_OPEN` | `1` | Nothing here can open a browser |
| `OPENAI4S_SKIP_DOTENV` | `1` | `.env` discovery starts from site-packages and cannot reach a file you mount |

### Credentials: supply them, do not save them

The image selects the broker's **environment-injection** backend
(`OPENAI4S_SECRET_STORE=env`, `OPENAI4S_SECRET_ENV=1`). Credentials arrive as
environment variables, the daemon reads them, and nothing credential-shaped is
ever written to the volume — which is stronger than the keychain a container
cannot have, not a fallback from it. In Kubernetes that is a `Secret` projected
into the pod.

The variable name is derived, not chosen: `OPENAI4S_SECRET_<SCOPE>_<NAME>`,
upper-cased with every non-alphanumeric run collapsed to `_`. So:

| Credential | Variable |
|---|---|
| model API key | `OPENAI4S_SECRET_LLM_LLM_API_KEY` |
| Tavily search key | `OPENAI4S_SECRET_SEARCH_TAVILY_API_KEY` |

The backend is **read-only by design**: if the environment owns the secret, the
app must not overwrite it behind the operator's back. **Customize → Models
therefore cannot save an API key** — it refuses, naming the exact variable to
set. That is the intended boundary, not a limitation to work around. Clearing a
key from the UI does not unset an injected one either; the settings route
reports the `has_api_key` it re-reads afterwards rather than claiming the clear
took.

Two alternatives, both supported:

- **The plain configuration variables still work** — `OPENAI4S_LLM_API_KEY`, or
  `OPENAI4S_<PROVIDER>_API_KEY` which outranks it. They resolve at the config
  layer, below the broker, and are the shorter path for a one-off `docker run`.
- **`OPENAI4S_SECRET_STORE=plaintext`** if you would rather manage keys in the
  UI. Understand the trade: they then sit in the clear inside `openai4s.db` on
  your volume, where a file mode is the only thing protecting them, and a
  backup or an image layer copies them out.

> Injected credentials resolving **with no settings row at all** is recent. Until
> then `resolve_setting` stopped at the empty row and never consulted the
> backend, so on a fresh volume the variable was dead — the Secret mounted, the
> pod healthy, the model reporting itself unconfigured, and nothing raised to
> say why. If you are running an older build, use `OPENAI4S_LLM_API_KEY`.

**If you override the store back to `auto`, expect a traceback on every boot.**
`auto` fails closed on a host with no keychain and no session bus, which is
right about credentials and wrong about noise: the once-per-boot credential
migration constructs a broker it does not need — there is nothing to migrate —
and boot prints

```
openai4s.security.secret_broker.SecretStoreUnavailable: refusing to handle
credentials without a secure store (no secure secret store on this host).
```

ahead of the access-token banner. It is caught and the daemon serves normally.
Choosing a backend the container actually has is what removes it.

### Kernel isolation, honestly

On Linux the kernel sandbox is bubblewrap, and bubblewrap needs to create user,
mount, IPC, UTS and network namespaces. An unprivileged container cannot: the
default seccomp profile masks the namespace flags for a process without
`CAP_SYS_ADMIN`, and a masked `/proc` blocks the fresh procfs mount even when
it can. The image ships `bwrap` so that a suitably privileged container *can*
enforce, and so `openai4s doctor` reports on the real backend.

With the default `OPENAI4S_KERNEL_SANDBOX=auto` you will therefore see this
once at startup, and it is the truth rather than a misconfiguration:

```
OPENAI4S SECURITY WARNING: OS kernel sandbox is not enforced; ...
```

What is lost is specific, not vague. A degraded sandbox runs the cell command
unwrapped, which drops the secret-read masks and the network namespace
together. Those masks are what normally hide `<data_dir>/openai4s.db` and
`<data_dir>/access-token` from a cell — and a cell's working directory is
`<data_dir>/agent-workspaces/<session>`, two levels below the token. **A cell
that follows a prompt injection can read the token and, with raw network also
restored, send it somewhere.** On a wildcard bind that token is the only
control, so this is the failure that matters most in a container.

Your options, in order of strength:

1. Keep the daemon unreachable from anything you do not trust — loopback
   publishing, a `ClusterIP` and `kubectl port-forward`. This is what the
   shipped files do.
2. Grant the namespace privileges and set `OPENAI4S_KERNEL_SANDBOX=enforce`, so
   a kernel that cannot be isolated refuses to start instead of degrading.
   Verify with `openai4s doctor` inside the container:
   `docker run --rm --security-opt seccomp=unconfined --security-opt apparmor=unconfined --cap-add SYS_ADMIN openai4s:local openai4s doctor`.
   Note that this hands the container capabilities that weaken *its* boundary,
   which is a real trade and not a free upgrade.
3. Set `OPENAI4S_KERNEL_SANDBOX=off` if you have decided the container is the
   boundary. It silences the warning and changes nothing else.

## Persistence

One volume at `/data`. It holds `openai4s.db` (every session, the audit ledger,
settings), `artifacts/` and `artifact-versions/`, `agent-workspaces/`,
`workspace-cas/` (fork and revert cannot work without it), `compaction-history/`,
`user-skills/`, `shares/`, and `access-token`.

The image owns `/data` as uid 1000, so a **named volume** inherits that. A
**bind mount** does not: `chown -R 1000:1000 ./your-dir` on the host first, or
the daemon cannot create its database. In Kubernetes `fsGroup: 1000` does it.

Never point two daemons at one volume. The store is SQLite in rollback-journal
mode, the pidfile singleton only guards `serve`, and the manifests pin
`replicas: 1` with a `Recreate` strategy for exactly this reason.

## Kubernetes

```bash
kubectl create namespace openai4s
kubectl -n openai4s create secret generic openai4s-llm --from-literal=api-key='...'
kubectl -n openai4s apply -f deploy/kubernetes.yaml
kubectl -n openai4s exec deploy/openai4s -- openai4s url
kubectl -n openai4s port-forward svc/openai4s 8760:8760
```

Probes use `GET /health`, which is one of exactly two routes that answer
without a credential (the other is `/api/v1/auth/status`). Every other path
returns 401 on a non-loopback bind, so no other path can serve as a probe.
`/health` does disclose the configured model id to anyone who can reach it.

The startup probe allows 120s: first boot on an empty volume runs the schema
migration before the socket binds.

If you put an Ingress in front, read
[`deploy/kubernetes-ingress.yaml`](../deploy/kubernetes-ingress.yaml) rather
than copying a template from elsewhere. Four things break by default:

- **`Host` must be preserved.** Mutating `/api/v1/*` requests and the WebSocket
  upgrade are refused when `Origin` and `Host` disagree. A proxy that rewrites
  `Host` to the backend gives you 403s on every write while GETs keep working,
  which reads as a broken app.
- **Request buffering must stay on.** The daemon answers a chunked request body
  with 400; it needs a `Content-Length`.
- **The read timeout must outlast a cell.** The workbench holds a WebSocket
  open with no server-side heartbeat, so a 60s default closes it mid-turn.
- **The body limit must be raised**, to no more than 128 MiB — the daemon's own
  ceiling, above which a larger proxy limit only buys you a rejected upload
  that crossed the network first.

## Known limitations

- **No R.** The R channel needs `Rscript` on `PATH`; the image does not install
  it. Add `RUN apt-get update && apt-get install -y --no-install-recommends r-base-core`
  to your own layer, or build the conda `r` environment — but note that
  `openai4s setup` creates conda environments under `$HOME`, i.e. in the
  container layer, so they do not survive image replacement.
- **IPv6 is unsupported.** The server is `AF_INET` only. `OPENAI4S_HOST=::` is
  recognised as a wildcard by the request guards but cannot bind, and fails
  with a raw traceback. IPv6-only clusters cannot run this.
- **`install-id` is written under `$HOME`, not the data dir**, so it is
  regenerated on every restart. Only remote BYOC compute cares — losing it
  orphans sandboxes from `reconcile`. Set `OPENAI4S_INSTALL_ID` to any stable
  value if you use it.
- **Remote GPU compute via the bundled NVIDIA provider needs a Docker daemon**
  on the machine running OpenAI4S. Inside a container it has none.
- **Script-driven use denies every permission prompt.** With no interactive
  WebSocket attached, `OPENAI4S_UNATTENDED_APPROVAL` defaults to `deny` and any
  tool call needing approval is refused. That default is deliberate; know it
  before automating against the Service.
- **No access logs.** The daemon logs nothing per request unless
  `OPENAI4S_STRUCTURED_LOGS` is set.
- **In-flight requests are dropped on shutdown, not drained.** Handler threads
  are daemon threads. The Kubernetes manifest uses a `preStop` pause so the
  Service stops routing first.

## Verifying a change

`scripts/container_smoke.sh` builds the image and checks the things that
actually break: that the daemon answers `/health` unauthenticated, refuses an
unauthenticated API call, accepts its own token, runs as uid 1000, imports the
science stack, survives a `SIGKILL` that leaves a colliding pidfile on the
volume, and exits 0 on `SIGTERM`. It needs a Docker daemon and runs the same on
a laptop as in CI.

```bash
bash scripts/container_smoke.sh
```

The manifests themselves are pinned against the code by
`tests/test_container_deployment.py`, which runs in the ordinary offline suite.

---
---

<a id="zh"></a>

# Docker 与 Kubernetes

[English](#en) · **简体中文**

OpenAI4S 提供了 daemon 与 Web 工作台的容器镜像、面向单机的 `compose.yaml`，以及
面向集群的 Kubernetes 清单。本页是这三者的运维指南：怎么跑起来、容器边界替代得了
什么又替代不了什么，以及今天真实存在（而非假想）的限制。

这些文件都在仓库里：

| 文件 | 是什么 |
|---|---|
| [`Dockerfile`](../Dockerfile) | 镜像：Debian-slim 上的 CPython、由本仓库构建出的 wheel，以及 `science` extra |
| [`compose.yaml`](../compose.yaml) | 单机一份、一个卷，只发布到 loopback |
| [`deploy/kubernetes.yaml`](../deploy/kubernetes.yaml) | PVC + 单副本 Deployment + ClusterIP Service |
| [`deploy/kubernetes-ingress.yaml`](../deploy/kubernetes-ingress.yaml) | 可选，也是最容易做错的一块 |
| [`scripts/container_smoke.sh`](../scripts/container_smoke.sh) | 构建镜像并验证 daemon 在里面真的能跑 |

## 先读这一节：这里说的"暴露"是什么意思

daemon 默认绑 `127.0.0.1`，[security.md](security.md) 也写着不要把 `0.0.0.0`
放到不可信网络上。镜像却仍然绑 `0.0.0.0`——这不矛盾，因为两句话说的是不同的网络。

在容器里，`0.0.0.0` 指的是这个容器**自己**的网络命名空间。它是发布端口或 Service
唯一能到达的地址；绑在 `127.0.0.1` 的容器除了自己谁也够不着。真正决定暴露程度的是
往外的下一跳：`-p 127.0.0.1:8760:8760` 把它留在你本机的 loopback 上，
`-p 8760:8760` 把它放到宿主机的每一个网卡上，而 `LoadBalancer` 类型的 Service
则是把它放到公网上。随附的 compose 文件发布到 loopback、随附的 Service 是
`ClusterIP`，都是有意为之。

一旦绑定不再是 loopback，有两件事立刻改变，公开任何东西之前值得先弄清楚：

- **访问令牌变成强制的，且关不掉。** `OPENAI4S_REQUIRE_TOKEN=0` 只在 loopback
  绑定下才被承认。
- **防 DNS 重绑定的 `Host` 白名单不再生效。** 通配绑定下，合法外部主机名的集合是
  不可知的，于是 daemon 不再去猜 `Host` 头。此时挡在
  `/api/v1/kernel/execute`、`/api/v1/compute/jobs` 与 Host RPC 前面的，就只剩
  令牌——而这些端点都会执行代码。

## 快速开始

```bash
docker build -t openai4s:local .
docker run -d --name openai4s \
  -p 127.0.0.1:8760:8760 \
  -v openai4s-data:/data \
  -e OPENAI4S_LLM_API_KEY="$OPENAI4S_LLM_API_KEY" \
  openai4s:local
docker logs openai4s
```

最后一行会打印出可直接打开的 URL，带着令牌。或者用 Compose——它设置同样的东西，
另外加上健康检查与停止宽限期：

```bash
docker compose up -d --build
docker compose exec openai4s openai4s url
```

想要纯标准库镜像（小得多，cell 里没有科学栈）：
`docker build --build-arg OPENAI4S_EXTRAS= -t openai4s:core .`。

## 访问令牌

daemon 只铸造一次，写进 `/data/access-token`，并在启动时打印出来。三种取法：

```bash
docker compose exec openai4s openai4s url       # 可直接打开的 URL
docker logs openai4s 2>&1 | grep token          # 启动横幅
kubectl -n openai4s exec deploy/openai4s -- openai4s url
```

它在卷上，所以能跨重启存活，你已经授权过的浏览器会话也都继续有效。卷丢了，所有
cookie 也随之失效。

**没有任何环境变量能设置服务端的令牌。** 如果你需要一个自己掌控的令牌（GitOps、
共享集群），就在 daemon 首次启动前把 `/data/access-token` 预置好：一个足够长的
随机串，权限 0600，属主 uid 1000；`load_or_mint` 会原样沿用已存在的文件。

另外注意：令牌每次启动都会以明文打到 stderr，因此会进入 Pod 日志和你转运日志的
任何聚合系统。要么把那条日志流当作含凭据来对待，要么删掉文件并重启以轮换令牌。

## 配置

这里只列 daemon 自身的开关，完整列表见 [configuration.md](configuration.md)。

| 变量 | 镜像默认值 | 说明 |
|---|---|---|
| `OPENAI4S_DATA_DIR` | `/data` | 所有值得保留的东西。把卷挂在这里 |
| `OPENAI4S_HOST` | `0.0.0.0` | 在模块导入时读取一次；请在镜像或 Pod spec 里设，别在 Python 里改 |
| `OPENAI4S_PORT` | `8760` | |
| `OPENAI4S_SECRET_LLM_LLM_API_KEY` | 未设置 | 凭据。由你提供，见下 |
| `OPENAI4S_LLM_API_KEY` | 未设置 | 凭据的配置层备选写法。`OPENAI4S_<PROVIDER>_API_KEY` 优先级更高 |
| `OPENAI4S_LLM_PROVIDER` / `_MODEL` | 未设置 | 普通设置项——在这里设或在 UI 里设都行 |
| `OPENAI4S_SECRET_STORE` | `env` | 凭据来自环境，不写入磁盘任何位置 |
| `OPENAI4S_SECRET_ENV` | `1` | 在任何凭据存在之前就把该后端标记为可用 |
| `OPENAI4S_KERNEL_SANDBOX` | `auto` | 见下 |
| `OPENAI4S_NO_OPEN` | `1` | 这里没有任何东西能打开浏览器 |
| `OPENAI4S_SKIP_DOTENV` | `1` | `.env` 查找是从 site-packages 往上走的，够不到你挂进来的文件 |

### 凭据：供给它，而不是保存它

镜像选定的是 broker 的**环境注入**后端（`OPENAI4S_SECRET_STORE=env`、
`OPENAI4S_SECRET_ENV=1`）。凭据以环境变量的形式送进来，daemon 读它，不会有任何
凭据形状的东西被写到卷上——这比容器根本不可能有的 keychain 更强，而不是它的降级
方案。在 Kubernetes 里，它就是一个投射进 Pod 的 `Secret`。

变量名是推导出来的，不是自己起的：`OPENAI4S_SECRET_<SCOPE>_<NAME>`，全大写，且把
每一段非字母数字折叠成一个 `_`。于是：

| 凭据 | 变量 |
|---|---|
| 模型 API key | `OPENAI4S_SECRET_LLM_LLM_API_KEY` |
| Tavily 搜索 key | `OPENAI4S_SECRET_SEARCH_TAVILY_API_KEY` |

这个后端**按设计是只读的**：既然环境拥有这个密钥，应用就不该背着运维把它覆盖掉。
因此 **Customize → Models 无法保存 API key**——它会拒绝，并告诉你该设哪个变量。
这是有意划下的边界，不是需要绕开的限制。从 UI 清除一个注入的 key 同样不会真的取消
它；设置路由回报的是它写完之后重新读到的 `has_api_key`，而不是宣称清除生效了。

另有两条同样受支持的路：

- **普通配置变量仍然有效**——`OPENAI4S_LLM_API_KEY`，或优先级更高的
  `OPENAI4S_<PROVIDER>_API_KEY`。它们在 broker 下面的配置层解析，是一次性
  `docker run` 更省事的写法。
- **`OPENAI4S_SECRET_STORE=plaintext`**，如果你更想在 UI 里管理 key。要理解这笔
  交易：它们会明文躺在卷上的 `openai4s.db` 里，唯一的保护是文件权限位，一次备份或
  一层镜像就能把它们带走。

> 「注入的凭据能在**完全没有 settings 行**的情况下解析出来」是最近才有的。在那之前
> `resolve_setting` 停在空行上、根本不去问后端，于是在全新的卷上这个变量是死的——
> Secret 挂上了、Pod 是健康的、模型却报告自己没配置，而且没有任何东西报错说明原因。
> 如果你跑的是更早的构建，请改用 `OPENAI4S_LLM_API_KEY`。

**如果你把存储改回 `auto`，那每次启动都会看到一段 traceback。** 在既没有 keychain
也没有 session bus 的主机上，`auto` 会 fail closed——这对凭据是对的，对噪音是错的：
每次启动都会跑一遍的凭据迁移会构造一个它并不需要的 broker（根本没有什么可迁移的），
于是启动时会在访问令牌横幅之前打印

```
openai4s.security.secret_broker.SecretStoreUnavailable: refusing to handle
credentials without a secure store (no secure secret store on this host).
```

异常被捕获，daemon 照常服务。真正让它消失的办法，是选一个容器确实拥有的后端。

### 关于内核隔离，实话实说

在 Linux 上内核沙箱是 bubblewrap，而 bubblewrap 需要创建 user、mount、IPC、UTS
与 network 命名空间。非特权容器做不到：默认 seccomp 配置会对没有 `CAP_SYS_ADMIN`
的进程屏蔽掉那些命名空间标志位，而被 mask 过的 `/proc` 又会挡住新挂一个 procfs
——即使前一关过了。镜像里仍然装了 `bwrap`，是为了让一个被授予了相应权限的容器
*能够*强制隔离，也是为了让 `openai4s doctor` 报告的是真实后端的状况。

因此在默认的 `OPENAI4S_KERNEL_SANDBOX=auto` 下，你会在启动时看到这么一行——它是
事实，不是配置错误：

```
OPENAI4S SECURITY WARNING: OS kernel sandbox is not enforced; ...
```

失去的东西是具体的，不是笼统的。降级后的沙箱会直接执行未经包裹的命令，于是
secret-read 掩码与网络命名空间是一起消失的。那些掩码正是平时用来对 cell 遮住
`<data_dir>/openai4s.db` 与 `<data_dir>/access-token` 的——而 cell 的工作目录是
`<data_dir>/agent-workspaces/<session>`，就在令牌下面两层。**一个被提示注入牵着走
的 cell 可以读到令牌，而由于原始网络也一并恢复了，它还能把令牌发出去。** 在通配
绑定下那个令牌是唯一的控制，所以这是容器里最要紧的一种失败。

按强度排序，你的选择是：

1. 让 daemon 对你不信任的一切都不可达——只发布到 loopback、用 `ClusterIP` 加
   `kubectl port-forward`。随附文件就是这么做的。
2. 授予命名空间权限并设 `OPENAI4S_KERNEL_SANDBOX=enforce`，这样无法隔离的内核会
   拒绝启动而不是降级。在容器里用 `openai4s doctor` 验证：
   `docker run --rm --security-opt seccomp=unconfined --security-opt apparmor=unconfined --cap-add SYS_ADMIN openai4s:local openai4s doctor`。
   注意这是把削弱*容器自身*边界的能力交了出去，是一笔真实的交易，不是白捡的升级。
3. 如果你已经决定由容器充当边界，就设 `OPENAI4S_KERNEL_SANDBOX=off`。它只是让那
   条警告不再出现，别的什么都不改变。

## 持久化

一个卷，挂在 `/data`。里面是 `openai4s.db`（所有会话、审计账本、设置）、
`artifacts/` 与 `artifact-versions/`、`agent-workspaces/`、`workspace-cas/`
（没有它 fork 与 revert 都无法工作）、`compaction-history/`、`user-skills/`、
`shares/`，以及 `access-token`。

镜像里 `/data` 属于 uid 1000，所以**具名卷**会继承这个属主。**bind mount** 不会：
先在宿主机上 `chown -R 1000:1000 ./your-dir`，否则 daemon 建不了自己的数据库。在
Kubernetes 里由 `fsGroup: 1000` 负责这件事。

绝不要让两个 daemon 指向同一个卷。存储是 rollback-journal 模式的 SQLite，pidfile
单例也只守着 `serve`；清单里把 `replicas: 1` 与 `Recreate` 策略钉死，正是为此。

## Kubernetes

```bash
kubectl create namespace openai4s
kubectl -n openai4s create secret generic openai4s-llm --from-literal=api-key='...'
kubectl -n openai4s apply -f deploy/kubernetes.yaml
kubectl -n openai4s exec deploy/openai4s -- openai4s url
kubectl -n openai4s port-forward svc/openai4s 8760:8760
```

探针打的是 `GET /health`，它是仅有的两条不需要凭据就能应答的路由之一（另一条是
`/api/v1/auth/status`）。在非 loopback 绑定下其余每一条路径都返回 401，所以没有别
的路径能当探针用。`/health` 确实会向能够到达它的人透露所配置的模型 id。

startup 探针给了 120s：在空卷上首次启动会先跑完 schema 迁移，然后才 bind 端口。

如果要在前面放 Ingress，请读
[`deploy/kubernetes-ingress.yaml`](../deploy/kubernetes-ingress.yaml)，别从别处
抄模板。默认配置会坏掉四件事：

- **`Host` 必须原样保留。** `Origin` 与 `Host` 不一致时，变更类 `/api/v1/*` 请求
  与 WebSocket 升级都会被拒。把 `Host` 改写成后端地址的代理，会让你所有写操作都
  403 而 GET 一切正常——看起来就像应用坏了。
- **请求缓冲必须保持开启。** daemon 对分块（chunked）请求体一律回 400；它需要
  `Content-Length`。
- **读超时必须比一个 cell 活得久。** 工作台开着一条 WebSocket，而服务端没有任何
  心跳，所以 60s 的默认值会在一轮对话中途把它关掉。
- **请求体上限要调大**，但不要超过 128 MiB——那是 daemon 自己的天花板，再大的代理
  上限只会让你多传一次注定被拒的上传。

## 已知限制

- **没有 R。** R 通道需要 `PATH` 上有 `Rscript`，镜像并不安装它。可以在你自己的层里
  加上
  `RUN apt-get update && apt-get install -y --no-install-recommends r-base-core`，
  或者去构建 conda 的 `r` 环境——但要注意 `openai4s setup` 是把 conda 环境建在
  `$HOME` 下的，也就是容器层里，换镜像就没了。
- **不支持 IPv6。** 服务端只有 `AF_INET`。`OPENAI4S_HOST=::` 虽然会被请求侧的守卫
  识别为通配，却根本 bind 不上，并以一个裸 traceback 失败。纯 IPv6 集群跑不了。
- **`install-id` 写在 `$HOME` 而不是数据目录下**，因此每次重启都会重新生成。只有
  远程 BYOC 计算在意它——丢了会让 `reconcile` 找不到自己创建的 sandbox。用得上就
  把 `OPENAI4S_INSTALL_ID` 设成任意稳定值。
- **通过随附 NVIDIA provider 使用远程 GPU 计算，需要运行 OpenAI4S 的机器上有
  Docker daemon。** 容器里没有。
- **脚本驱动的用法会拒绝每一次权限询问。** 没有交互式 WebSocket 挂着时，
  `OPENAI4S_UNATTENDED_APPROVAL` 默认为 `deny`，任何需要审批的工具调用都会被拒。
  这个默认值是有意的；在对着 Service 做自动化之前先知道它。
- **没有访问日志。** 除非设置 `OPENAI4S_STRUCTURED_LOGS`，daemon 不会为每个请求
  记录任何东西。
- **关停时在途请求是被丢弃而不是被排空的。** 处理线程是 daemon 线程。Kubernetes
  清单用了一个 `preStop` 停顿，好让 Service 先停止转发。

## 改动之后怎么验证

`scripts/container_smoke.sh` 会构建镜像，并检查那些真会坏掉的点：daemon 是否能在
无凭据下应答 `/health`、是否拒绝无凭据的 API 调用、是否接受自己铸造的令牌、是否
以 uid 1000 运行、科学栈能否导入、能否在一次留下冲突 pidfile 的 `SIGKILL` 之后
活过来，以及收到 `SIGTERM` 是否以 0 退出。它需要 Docker daemon，在笔记本上和在 CI
里跑的是同一份。

```bash
bash scripts/container_smoke.sh
```

清单本身由 `tests/test_container_deployment.py` 钉在代码上，那个测试跑在普通的
离线测试套件里。
