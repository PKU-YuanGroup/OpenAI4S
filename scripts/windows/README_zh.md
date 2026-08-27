# Windows 启动器源文件

[English](README.md)

Windows 发布包用作入口的三个文件。[`../build_windows_zip.sh`](../build_windows_zip.sh)
会把它们和作为 payload 携带的 Linux bundle 一起放进包里；这里没有任何东西会在构建时
被执行。

它们以真正的文件形式存在，而不是塞在构建脚本里的 heredoc，因为这是要在一台我们所有 CI
镜像都不像的机器上运行的代码：它必须自己就能读、能 diff、能被解析。
[`../../.github/workflows/ci.yml`](../../.github/workflows/ci.yml) 与
[`../../.github/workflows/release.yml`](../../.github/workflows/release.yml)
里的 `windows-launcher` 作业会在真正的 Windows runner 上解析 `openai4s.ps1`，正是为了
这个原因——它里面的语法错误对所有 Linux 和 macOS 作业都是不可见的，只会在第一个用户的
机器上暴露。

## 为什么跑在 WSL2 里

这不是打包上的偷懒。[`../../openai4s/platform_support.py`](../../openai4s/platform_support.py)
在 `win32` 上拒绝启动内核：内核要拉起 POSIX 子进程，R 通道靠 shell 重定向走文件描述符
3 和 4，而 OS 沙箱根本没有 Windows 后端。一个照样启动的包，只会让科研人员从一次半吊子的
分析里才发现问题——这正是那道拒绝要防的「先警告、再照跑」，只不过下沉到了发布渠道这一层。

WSL2 报告自己是 `linux`，属于受支持平台，所以这个包跑的就是其他平台跑的同一个程序，而不是
它的近似版。平台支持矩阵见 [`../../docs/platforms.md`](../../docs/platforms.md)。

面向用户的完整步骤见 [`../../docs/windows-wsl.md`](../../docs/windows-wsl.md)。当前启动链路会优先沿用已存有 OpenAI4S 数据的发行版、其次才是 Ubuntu 24.04，使用与真实 Cell 一致的生命周期、IPC、UTS 和 network namespace 参数验证 bubblewrap 0.8.0+，写入国内镜像配置（只覆盖带有其托管标记的文件——用户自行编辑过的 `pip.conf`/condarc 会被保留）和 `~/.local/bin/openai4s`，以 `enforce` 沙箱后台启动，并只打开 `openai4s url` 返回的安全地址。

## 文件

| 文件 | 职责 |
| --- | --- |
| `OpenAI4S.cmd` | 可双击的入口。资源管理器双击 `.ps1` 是用编辑器打开而不是运行，默认执行策略连命令行调用也会拦，所以这层包装只对这一个进程加 `-ExecutionPolicy Bypass` 调起 PowerShell，并原样透传参数与退出码。以 CRLF 发布。 |
| `openai4s.ps1` | Windows 这一半：挑选一个 WSL **2** 发行版（拒绝 WSL 1——它没有 user namespace，也就没有沙箱）——已经存有 OpenAI4S 数据的发行版会钉住选择，这样为别的原因安装 Ubuntu 24.04 不会让既有会话看似被删，否则优先 Ubuntu 24.04——传递国内软件源镜像（设为 `off` 恢复官方源）与可选的 WSL 可达代理，用 `wslpath` 翻译包路径，安装 payload，把 OpenAI4S 与占着端口的无关进程区分开，从 `openai4s url` 拿到带鉴权的地址，然后打开 Windows 浏览器。每一处拒绝都同时说明原因和那条能解决它的确切命令。以 CRLF 发布。 |
| `bootstrap.sh` | Linux 这一半，在发行版内部运行。它在解包前先校验 payload 的 checksum（归档要跨 9p/DrvFs 边界，那里的短读会给出一个被截断的文件而不是一个错误），幂等安装，并把 daemon 完全脱离终端地拉起。以 LF 发布——这里出现回车符会让它在 WSL 里以 `bad interpreter` 失败，而 `../verify_windows_zip.py` 会直接拒绝这样的包。 |

bootstrap 还会用与真实 Cell 相同的 lifecycle、IPC、UTS 和 network namespace
参数验证 bubblewrap 0.8.0+，写入所选镜像配置和 `~/.local/bin/openai4s`
链接，并以 `OPENAI4S_KERNEL_SANDBOX=enforce`、禁止自动开浏览器的方式启动
daemon。把任一镜像变量设为 `off` 会明确恢复相应官方源。只有带启动器管理
标记的文件会被修改；删除标记即把文件交给用户管理，之后启动器会完整保留。
当 WSL 明确关闭 localhost 转发时，`OPENAI4S_HOST=0.0.0.0` 仍作为 daemon
的通配监听地址，Windows 端则使用当前 WSL IPv4 访问。随包 HTTP 服务只支持
IPv4，因此启动器会在启动前直接拒绝 IPv6 host 并给出修正提示。
Clash 一类代理在 WSL 中使用 Fake-IP DNS 时，启动器会自动识别；RFC 2544
合成地址只对内置或经用户批准的公开域名放行，IP 字面量及其他私网地址仍受
SSRF 防护拒绝。

## 在架构中的位置

这些都不属于 daemon，运行中的应用永远不会 import 或调用它们。它们只存在于「用户双击下载
下来的 zip」和「受支持的 Linux daemon 起来」之间；从那一刻起，这就是
[`../../docs/release-validation.md`](../../docs/release-validation.md) 里描述的那个普通
Linux 安装。
