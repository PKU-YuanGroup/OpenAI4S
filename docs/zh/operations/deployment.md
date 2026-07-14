---
title: 部署
description: 将公开静态文档与 OpenAI4S 工作台作为两个独立服务部署和运行。
canonical: true
last_verified: 2026-07-14
verification: code-and-tests
status: current
audience: [operators, contributors]
verified_commit: a92e736
owner: OpenAI4S maintainers
---

# 部署

OpenAI4S 有两个信任模型不同的可部署功能面：

1. **文档**是公开的静态 VitePress 构建产物，适合部署在 `openai4s.org/docs/`，可由普通静态 Web 服务器或 CDN 提供。
2. **工作台**是有状态、会执行代码的单用户守护进程。应让它仅监听本地或可信主机的回环地址，并通过 SSH 或可信 VPN 访问。

绝不能将工作台放在公开文档路径后面来发布。工作台包含 Host 侧变更路由与本地计算端点；其可选访问令牌不是多用户身份验证系统。

## 公开文档

文档工具链不属于零依赖 Python 核心。它使用 `package-lock.json` 固定的版本，并通过 Node.js 构建：

```bash
npm ci
npm run docs:build
```

输出位于 `docs/.vitepress/dist/`。VitePress 的 base 是 `/docs/`，因此应在这个前缀下而不是 `/` 下测试生成站点：

```bash
npm run docs:preview
```

将每个发行版发布到不可变目录中，且仅在构建通过后切换一个符号链接。代表性的目录结构如下：

```text
/srv/openai4s-docs/
  releases/<source-revision>/
  current -> releases/<source-revision>/
```

只把生成文件复制进新的发行目录。不要将 `.env`、工作台数据目录、源码树凭据或服务器配置复制到文档根目录。让 Web 服务器的 `/docs/` location 指向 `current`，保留 `/` 上已有的落地页，并把 `/docs` 重定向至 `/docs/`。

静态服务器配置应实现与下例等价的行为：

```nginx
location = /docs {
    return 308 /docs/;
}

location /docs/ {
    # The server document root contains docs -> /srv/openai4s-docs/current.
    # 优先选择生成的 clean-URL HTML 文件，避免被同名章节目录遮蔽。
    try_files $uri.html $uri $uri/ =404;
}
```

具体的 `root`/`alias` 路径取决于部署。重载前先运行服务器配置测试进行验证。发布后，检查英文与中文首页、一个 clean URL、搜索、静态资源和直接访问深层链接后的刷新。回滚只需将 `current` 原子切换到前一个不可变发行目录，再重载 Web 服务器；它不涉及工作台数据库。

## 工作台前置条件

- macOS 或 Linux 上的 Python 3.10 或更新版本。不支持原生 Windows 科学内核；请使用 WSL2。
- 使用源码检出工作流时需要 `uv`。
- 只有需要 R Cell 时才需要真实的 `Rscript`。
- 如果必须启用内核沙箱，需要受支持 macOS 上的 Seatbelt（`sandbox-exec`）或 Linux 上的 bubblewrap（`bwrap`）。
- 只有使用四份环境规格时才需要可选的 conda/mamba/micromamba。
- SSH、Docker、GPU 与 provider 凭据仅是相应远程计算路径的可选依赖。

源码部署方式：

```bash
git clone https://github.com/PKU-YuanGroup/OpenAI4S.git
cd OpenAI4S
./setup.sh
uv run pytest
```

`setup.sh` 会创建 `.venv`、安装 `science` extra 与开发工具，并安装 pre-commit hook。正式打包发行版应改为遵循[发行验证](../release-validation.md)中的制品验证流程，并使用发行版专属虚拟环境。

### “纯 stdlib 核心”的含义

该包没有强制运行时依赖：engine、LLM transport、daemon、WebSocket 实现、storage layer 与 kernel protocol 都使用 Python 标准库。这**不**意味着部署后的科学运行时不含第三方软件包。

执行 `serve` 时，Gateway 会调用 `ensure_core(background=True)`。科学与网络栈中缺失的包会由后台线程使用 `pip` 安装到守护进程解释器中，即使安装失败也会继续启动。这带来三项运维影响：

- `/health` 已开始监听，不代表科学包集合已经就绪；
- 首次启动可能需要访问软件包索引，并可能修改虚拟环境；
- 不可变或离线部署必须在服务启动前预先填充并验证环境。

要同步准备当前发行版的精确环境：

```bash
.venv/bin/python -c \
  'from openai4s.kernel.preinstall import ensure_core; print(ensure_core(background=False))'
```

在受控的发行构建中运行此命令，启动后再检查 `GET /api/environments/status`。不要让多个发行版共享一个可变虚拟环境。

## 专用服务账户

使用一个不拥有其他仓库、云凭据、浏览器配置或管理员文件的专用账户来运行守护进程。首次启动前创建数据目录：

```bash
install -d -m 0700 -o openai4s -g openai4s /var/lib/openai4s
```

使用由 root 或服务账户拥有且权限为 `0600` 的私有环境文件。至少设置：

```dotenv
OPENAI4S_HOST=127.0.0.1
OPENAI4S_PORT=8760
OPENAI4S_DATA_DIR=/var/lib/openai4s
OPENAI4S_KERNEL_SANDBOX=enforce
OPENAI4S_NO_OPEN=1
```

只有当这个可信主机部署可以接受明确显示的无沙箱降级时，才用 `auto` 取代 `enforce`。不要把 provider secret 放在命令参数、提交到源码控制的 unit 文件或全局可读的环境文件中。

## 服务监管

`openai4s serve` 是前台进程，适合由操作系统 supervisor 管理。下面这个最小 systemd 示例有意没有加入可能悄然破坏科学解释器、bubblewrap、SSH 或远程计算的加固选项：

```ini
[Unit]
Description=OpenAI4S single-user scientific workbench
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=openai4s
Group=openai4s
WorkingDirectory=/opt/openai4s/current
EnvironmentFile=/etc/openai4s/openai4s.env
UMask=0077
ExecStart=/opt/openai4s/current/.venv/bin/openai4s serve --no-open
Restart=on-failure
RestartSec=5
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
```

任何额外的 supervisor 沙箱都必须与 Python/R worker 启动、所选操作系统内核沙箱、Artifact 写入、包导入、SSH 和优雅停机一起测试。不要再为同一数据目录增加第二个进程管理器。

## 远程访问

让监听地址保持在回环接口。对于单一操作员，SSH 隧道是最简单的支持路径：

```bash
ssh -N -L 8760:127.0.0.1:8760 user@trusted-host
```

然后在本机打开 `http://127.0.0.1:8760/`。将可达范围限制为该操作员的可信 VPN 也可以接受，但与绑定非回环地址相比，让守护进程保留回环绑定，再通过本地隧道或已认证的反向代理访问会更安全。

绑定到非回环地址时，系统会在启动时生成一个 process-wide bearer-like token；在该进程的整个生命周期内，query parameter 或 cookie 都可以重复使用它。它既不提供 TLS、用户身份、角色隔离、会话隔离、暴力破解控制，也不构成完整的 CSRF 边界。它是可信网络上的最后一道纵深防御，而不是将守护进程暴露到互联网的授权机制。

## 升级

使用发行目录，避免代码回滚依赖一个可变 checkout：

1. 记录当前源码修订版与运行时版本。
2. 停止接收新工作，并干净地停止守护进程。
3. 按[数据管理](data-management.md)中的说明，制作一份服务已停止状态下的完整数据目录备份。
4. 准备新的发行目录及其独立虚拟环境。
5. 运行离线测试；适用时运行发行制品检查；同步准备科学包，并运行沙箱自检。
6. 将 `current` 符号链接切换至新发行版，并启动守护进程。
7. 验证 `/health`、适用的 Python/R 启动、沙箱状态、一个已有会话与 Artifact、一个新的纯工具轮次，以及一个科学 Cell。

新进程打开数据库时，Store 会应用 schema migration。即使没有用户会话正在运行，也应把首次打开视为一次数据变更操作。

## 回滚

如果验证失败：

1. 停止新守护进程。
2. 单独保留它的数据目录以便诊断。
3. 恢复完整的升级前数据快照；不要将较旧的 SQLite 文件与较新的 Artifact/CAS/workspace 目录混合。
4. 将 `current` 切回匹配的上一代码发行版。
5. 在回环地址上启动，并重新执行恢复验证清单。

系统不提供通用 down-migration 契约。只切换代码、继续使用已经由更新版本迁移过的数据库，并不是安全的回滚方案。
