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

1. 选择 Ubuntu 24.04（可用 `OPENAI4S_WSL_DISTRO` 覆盖）；
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
| 浏览器打不开 | 先运行 `.\OpenAI4S.cmd status`，再运行 `.\OpenAI4S.cmd url`；启动器可自动处理显式的 `localhostForwarding=false`，其他转发故障可删除该配置后运行 `wsl --shutdown`。 |
| 服务启动后退出 | 查看 `wsl -d Ubuntu-24.04 -- tail -80 ~/.openai4s/logs/app.out`。 |
| WSL 提示 localhost 代理未镜像 | 按上一节启用 mirrored networking，或使用允许局域网连接的 Windows 网关地址。 |

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
