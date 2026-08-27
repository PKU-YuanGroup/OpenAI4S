# Run OpenAI4S on Windows with WSL2 / 在 Windows WSL2 上运行 OpenAI4S

[English](#english) | [中文](#中文)

OpenAI4S does not start scientific kernels on native Windows. The Windows
package runs the same Linux application inside WSL2, then opens the UI in the
normal Windows browser. This follows the operating model in the
[Claude Science WSL guide](https://claude.com/docs/claude-science/run-on-windows-wsl):
WSL2, Ubuntu 24.04, an enforced bubblewrap sandbox, a localhost browser UI, and
foreground or detached lifecycle commands.

## 中文

### 最终效果

安装完成后，你会得到：

- Windows 中双击 `OpenAI4S.cmd` 即可启动；
- OpenAI4S 实际运行在 Ubuntu 24.04 / WSL2 中，Windows 浏览器通常访问
  `http://127.0.0.1:8760/`；若 NAT 配置显式关闭 `localhostForwarding`，
  启动器会自动改用本次 WSL IPv4；
- 首次启动校验随包 Linux payload 的 SHA-256，再安装到
  `~/.openai4s/app/`，过程不需要联网；
- `~/.local/bin/openai4s` 命令，可使用 `serve`、`status`、`url`、`stop`
  和 `doctor`；
- bubblewrap 0.8.0+ 强制沙箱。安装器会使用与真实 Cell 相同的生命周期、
  IPC、UTS 和 network namespace 参数做自检，而不只是检查命令是否存在；
- 浏览器打开的是 `openai4s url` 返回的本地登录引导 URL。登录参数换成
  本地 Cookie 后会从地址栏移除，不会打开必然返回 401 的裸地址。

Python 科学栈、Skills 和公共数据库连接器已在 payload 中。R 内核仍是可选
环境，需要 Conda 系工具后运行 `openai4s setup --profile standard`；这点与
Claude Science 首次启动自动准备 Python/R 环境并不完全相同。

### 1. 启用 WSL2

在管理员 PowerShell 中运行：

```powershell
wsl --install -d Ubuntu-24.04
```

根据提示重启 Windows，打开 Ubuntu 24.04 并创建 Linux 用户。确认发行版
使用 WSL2：

```powershell
wsl -l -v
```

如果显示 `VERSION 1`：

```powershell
wsl --set-version Ubuntu-24.04 2
```

Ubuntu 22.04 自带的 bubblewrap 版本偏旧，因此推荐 24.04 或更新版本。

### 2. 安装 bubblewrap

应用本体随 ZIP 离线安装，但 bubblewrap 属于系统安全组件。在 Ubuntu 中先
确认 APT 已使用国内镜像，然后运行：

```bash
sudo apt update
sudo apt install -y bubblewrap
bwrap --version
```

需要 0.8.0 或更新版本。本项目不依赖 `socat`；Windows 包也不需要通过
`curl | bash` 下载应用，因为完整 Linux payload 已包含在 ZIP 中。

### 3. 启动 Windows 包

解压 `OpenAI4S-<version>-windows-x86_64.zip`，不要只取出其中几个文件。
双击：

```text
OpenAI4S.cmd
```

启动器会依次完成：

1. 选择发行版：`OPENAI4S_WSL_DISTRO` 最优先；其次是已经存有
   OpenAI4S 数据（`~/.openai4s/app`）的发行版，这样升级后会话不会
   「消失」在另一个发行版里；再其次才是 Ubuntu 24.04 与默认发行版；
2. 拒绝 WSL1，验证 bubblewrap 版本并运行真实 namespace 自检；
3. 在 WSL 中重新计算 payload SHA-256；
4. 幂等安装或升级应用，并创建 `~/.local/bin/openai4s`；
5. 用 `OPENAI4S_KERNEL_SANDBOX=enforce` 在后台启动服务；
6. 等待 `/health` 确认服务身份，获取安全 URL，再打开 Windows 浏览器。

数据不会写进解压目录：

```text
应用       ~/.openai4s/app/
会话与设置 ~/.openai4s/
日志       ~/.openai4s/logs/app.out
命令       ~/.local/bin/openai4s
```

### 4. 日常命令

在 Windows PowerShell 中：

```powershell
.\OpenAI4S.cmd status
.\OpenAI4S.cmd url
.\OpenAI4S.cmd doctor
.\OpenAI4S.cmd stop
```

在 Ubuntu 中打开新终端，或先执行 `. ~/.profile`：

```bash
openai4s serve --port 8760 --no-browser
openai4s serve --port 8760 --no-browser --detached
openai4s status
openai4s url
openai4s stop
```

`status` 和 `url` 会读取当前守护进程记录的实际监听地址，因此显式关闭
`localhostForwarding` 后改用 WSL IPv4 时也不需要重复设置 `OPENAI4S_HOST`。
这条记录的是**绑定**地址：显式指定 `OPENAI4S_HOST=0.0.0.0` 时它就是通配符，
CLI 会按容器语义渲染成 `localhost`——而在关闭 localhost 转发的 Windows 上正好
不可达。所以经 `OpenAI4S.cmd` 走这两条命令时，启动器会把打印出来的回环地址
改写成可路由的 WSL IPv4；在 WSL 内部直接调用 `openai4s url` 则仍会得到
`localhost`，那在 WSL 内部本来就是对的。

也可以从 PowerShell 直接让 WSL 后台启动：

```powershell
wsl -d Ubuntu-24.04 -- ~/.local/bin/openai4s serve --port 8760 --no-browser --detached
```

### 5. 国内网络与 7897 代理

Windows ZIP 自带应用，不会在首次安装时访问公网。以后使用 pip/Conda
安装扩展时，Windows 启动器默认写入清华 PyPI 与 Conda 镜像配置，可分别用
以下变量覆盖：

```powershell
$env:OPENAI4S_WSL_PYPI_INDEX = 'https://pypi.tuna.tsinghua.edu.cn/simple'
$env:OPENAI4S_WSL_CONDA_MIRROR = 'https://mirrors.tuna.tsinghua.edu.cn/anaconda'
```

不在大陆或想直接使用官方源时，把变量设为 `off`（Windows 无法表达
「空值」环境变量，`off` 即显式停用镜像）。这会从启动器管理的
`pip.conf` 中移除镜像地址，并删除启动器管理的 condarc；pip 的用户目录
安装设置仍会保留。启动器只重写带有
`managed-by-openai4s-windows-launcher` 标记的配置文件；如果要自己管理
`pip.conf` / condarc，请先删除该标记行，之后启动器会逐字保留整个文件。

若要使用 Windows 上的 `127.0.0.1:7897`，需要注意网络方向：Windows 访问
WSL 服务时，`localhost` 通常会自动转发；但 NAT 模式下，WSL 访问 Windows
服务不能把 Windows 的 `localhost` 当作自己的 `localhost`。微软文档也明确
区分了这两个方向，参见
[Accessing network applications with WSL](https://learn.microsoft.com/windows/wsl/networking)。

如果 `.wslconfig` 在 NAT 模式中显式设置了 `localhostForwarding=false`，
OpenAI4S 启动器会读取当前 WSL IPv4，并让 Windows 浏览器直接连接该地址；
地址在 WSL 重启后变化也没关系，下次启动会重新解析。这个回退只解决
Windows 访问 OpenAI4S，不会让 WSL 能通过 `127.0.0.1:7897` 反向访问
Windows 代理；代理方向仍按下面的 mirrored 或网关方案配置。
显式设置 `OPENAI4S_HOST=0.0.0.0` 时，服务仍监听所有 IPv4 接口，但启动器
会用可路由的 WSL IPv4 探测并打开页面。当前服务端不支持 IPv6；Windows
启动器会直接拒绝 IPv6 `OPENAI4S_HOST` 并给出 IPv4 修正提示。

Clash 等 TUN 代理启用 Fake-IP DNS 时，公开域名在 WSL 中可能解析成 RFC
2544 的 `198.18.0.0/15`，旧版本会把它当成私网地址并被 SSRF 防护拒绝。
启动器的默认 `auto` 模式会同时确认 WSL DNS 服务器和一个内置公开科学域名
都落在该区间后，启用受限兼容：只允许内置或经用户明确批准的域名使用这种
合成地址；IP 字面量、未知域名、回环/元数据地址及其他私网段仍然拒绝。
可用 `$env:OPENAI4S_WSL_FAKE_IP_DNS='off'` 禁用自动检测，或在已确认代理
使用 Fake-IP 时设为 `on` 强制启用这条受限路径。

推荐在 Windows 11 22H2+ 的 `%USERPROFILE%\.wslconfig` 中启用镜像网络：

```ini
[wsl2]
networkingMode=mirrored
autoProxy=true
```

保存后运行：

```powershell
wsl --shutdown
$env:OPENAI4S_WSL_PROXY = 'http://127.0.0.1:7897'
.\OpenAI4S.cmd
```

镜像网络允许 WSL 从 `127.0.0.1` 访问 Windows 服务；`autoProxy=true` 会把
Windows HTTP 代理信息交给 WSL。具体版本要求和选项见
[Microsoft WSL networking](https://learn.microsoft.com/windows/wsl/networking) 与
[advanced WSL settings](https://learn.microsoft.com/windows/wsl/wsl-config)。

如果必须保留 NAT 模式，需要让代理监听可从 WSL 访问的 Windows 主机地址，
并在 Ubuntu 中查询网关：

```bash
ip route show default
```

例如网关是 `172.24.128.1`，且代理已经允许局域网连接：

```powershell
$env:OPENAI4S_WSL_PROXY = 'http://172.24.128.1:7897'
.\OpenAI4S.cmd
```

不要把带账号密码的代理 URL 放进该变量；启动器会拒绝带凭据的 URL，避免
凭据进入进程参数。

### 6. 排障

| 现象 | 处理 |
| --- | --- |
| 没有 WSL | 管理员 PowerShell 运行 `wsl --install -d Ubuntu-24.04`，重启并完成 Ubuntu 首次设置。 |
| 发行版是 WSL1 | 运行 `wsl --set-version <发行版名> 2`。 |
| `bubblewrap ... required` | 在 Ubuntu 24.04 中通过国内 APT 镜像安装 `bubblewrap`。 |
| `bubblewrap ... cannot create` | 检查 `wsl -l -v`，并确认系统策略没有禁用 user/network namespace。 |
| 端口被占用 | 设置 `$env:OPENAI4S_PORT='8080'` 后重新启动。 |
| 浏览器打不开 | 先运行 `.\OpenAI4S.cmd status`，再运行 `.\OpenAI4S.cmd url`（务必经 `OpenAI4S.cmd`，它会把通配符绑定打印出的回环地址改写成可路由地址）；启动器可自动处理显式的 `localhostForwarding=false`，其他转发故障可删除该配置后运行 `wsl --shutdown`。 |
| 服务启动后退出 | 查看 `wsl -d Ubuntu-24.04 -- tail -80 ~/.openai4s/logs/app.out`。 |
| WSL 提示 localhost 代理未镜像 | 按上一节启用 mirrored networking，或使用允许局域网连接的 Windows 网关地址。 |
| 公开域名被报为 `private/loopback` | 重新通过 Windows 启动器启动；默认 Fake-IP `auto` 模式会安全识别 `198.18.0.0/15`。若曾显式关闭，可清除 `OPENAI4S_WSL_FAKE_IP_DNS` 或设为 `auto`。 |

## English

### Requirements and installation

1. From an Administrator PowerShell, install WSL2 and Ubuntu 24.04:

   ```powershell
   wsl --install -d Ubuntu-24.04
   ```

2. In Ubuntu, using a suitable local package mirror, install bubblewrap 0.8.0
   or newer:

   ```bash
   sudo apt update
   sudo apt install -y bubblewrap
   ```

3. Unzip `OpenAI4S-<version>-windows-x86_64.zip` and double-click
   `OpenAI4S.cmd`.

The ZIP carries the complete Linux application, so first installation is
offline. The launcher refuses WSL1, performs a real bubblewrap namespace test,
verifies the payload checksum inside WSL, installs under `~/.openai4s/app`,
starts an enforced-sandbox daemon, obtains the authenticated URL from
`openai4s url`, and opens that URL in the Windows browser. In NAT mode, an
explicit `localhostForwarding=false` is detected and handled by using the
current WSL IPv4; ordinary and mirrored configurations keep using loopback.
With an explicit `OPENAI4S_HOST=0.0.0.0`, the daemon keeps its wildcard IPv4
bind while the launcher probes and opens the routable WSL IPv4 address. The
server is IPv4-only; the Windows launcher rejects an IPv6 `OPENAI4S_HOST` with
an actionable message instead of letting the daemon fail during bind.

Distribution choice: `OPENAI4S_WSL_DISTRO` wins; otherwise a distribution that
already holds OpenAI4S data (`~/.openai4s/app`) is kept, so an upgrade never
strands existing sessions in another distribution; only then does the launcher
prefer Ubuntu 24.04 over the Windows default.

The launcher defaults pip/Conda to the Tsinghua mirrors for later package
installs. Set `OPENAI4S_WSL_PYPI_INDEX` / `OPENAI4S_WSL_CONDA_MIRROR` to
another mirror, or to `off` to restore the official indexes. `off` removes the
index from the launcher-managed pip config while retaining its user-install
settings, and removes the launcher-managed condarc. Files the launcher writes
carry a `managed-by-openai4s-windows-launcher` marker. Remove that marker before
taking ownership of either file; later launches then preserve the complete
file byte for byte.

### Lifecycle commands

From PowerShell:

```powershell
.\OpenAI4S.cmd status
.\OpenAI4S.cmd url
.\OpenAI4S.cmd stop
```

From Ubuntu:

```bash
openai4s serve --port 8760 --no-browser --detached
openai4s status
openai4s url
openai4s stop
```

`status` and `url` read the live endpoint recorded by the daemon, so the WSL
IPv4 fallback for an explicit `localhostForwarding=false` needs no repeated
`OPENAI4S_HOST` setting. What the daemon records is the **bind** address, which
for an explicit `OPENAI4S_HOST=0.0.0.0` is the wildcard, and the CLI renders
that as `localhost` — correct inside WSL and in a container, and precisely the
address Windows cannot reach with forwarding off. So when these two run through
`OpenAI4S.cmd`, the launcher rewrites the loopback authority it prints to the
routable WSL IPv4; `openai4s url` called directly inside WSL still says
`localhost`, which is right there.

### Proxy note

In default NAT mode, a Windows service on `127.0.0.1:7897` is not at WSL's
own loopback address. On Windows 11 22H2+, mirrored networking makes localhost
bidirectional:

```ini
[wsl2]
networkingMode=mirrored
autoProxy=true
```

Run `wsl --shutdown`, then set
`OPENAI4S_WSL_PROXY=http://127.0.0.1:7897` before launching. Under NAT, expose
the proxy to the Windows host address shown by `ip route show default` instead.
See the official
[WSL networking documentation](https://learn.microsoft.com/windows/wsl/networking).

Clash-style TUN proxies may return RFC 2544 `198.18.0.0/15` Fake-IP answers in
WSL. The launcher defaults `OPENAI4S_WSL_FAKE_IP_DNS` to `auto`: it requires
both the WSL resolver and a known public science endpoint to use that range,
then enables a restricted bridge. Only built-in or explicitly user-approved
hostnames may use a synthetic answer; literal IPs and all other private or
metadata ranges remain blocked. Set the variable to `off` to disable detection
or `on` to force the restricted mode for a trusted Fake-IP proxy.
