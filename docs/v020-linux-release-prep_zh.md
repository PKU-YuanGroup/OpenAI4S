# v0.2.0 Linux 桌面版 — 发布说明草稿与构建配方

这是 v0.2.0 Linux 半边的维护者材料。它**不发布**任何东西。Windows（WSL）与
macOS 镜像在各自的机器上构建，随后一并挂到同一个 annotated tag 和同一份
draft release 上。

## 发布说明草稿（v0.2.0）

**OpenAI4S 0.2.0** 是第一个在现有 macOS 镜像与 Windows/WSL 启动器旁边，同时
发出 Linux 桌面包的版本。

### 下载（发布之后）

| 产物 | 是什么 |
| --- | --- |
| `OpenAI4S-0.2.0-linux-x86_64.tar.gz` | 可重定位的 Linux 桌面包：内嵌 CPython 3.13、预装科学栈、松散源码树、`./OpenAI4S` 启动器、`.desktop` 模板、按用户的 `install.sh`。**不是 AppImage，也不是 `.deb`**——FUSE squashfs 会把 user namespace 嵌进 FUSE 挂载，失败发生在 cell 执行时而不是启动时。 |
| `OpenAI4S-0.2.0-macos-arm64.dmg` | Apple Silicon 应用（在 macOS 上构建）。未配置 Developer ID 时为 ad-hoc 签名；公证在本仓库仍不可达，因此 `--mode release` 仍会拒绝这份 DMG。 |
| `OpenAI4S-0.2.0-windows-x86_64.zip` | 包着**上面那份完全相同的 Linux tarball** 的 Windows 启动器。需要 WSL2。从 Linux 产物封装，不是第二次编译。 |
| `openai4s-0.2.0-py3-none-any.whl` / `openai4s-0.2.0.tar.gz` | 零依赖 wheel 与 sdist，给已经装了 Python ≥ 3.10 的受支持平台。 |

### 相对 v0.1.0 落地的内容

- **Linux 与 Windows 桌面包** — 与 macOS 镜像同一套内嵌解释器与科学栈；Linux
  解压到任意目录后运行 `./OpenAI4S`。
- **只读会话共享** — `openai4s share` / `openai4s relay`，经你自己运营的出站隧道。
- **七个带来源的公共数据库连接器** — UniProt、RCSB PDB、Ensembl、ChEMBL、
  PubChem、arXiv、OpenAlex。
- **带版本的 `/api/v1`** — keyset 分页、统一错误信封、可续传 WebSocket 游标。
- **环境即事务** — `openai4s env plan|apply|list|rollback|recover`。
- **支持面** — 脱敏的 `openai4s doctor` / `openai4s diagnostics`，默关且可撤销的遥测。
- **基准** — 对着真实 Store、内核与 dispatcher 跑 workflow。
- **CLI `--version`** — 不需要子命令即可打印 `openai4s 0.2.0`。

### Linux 安装（最终用户）

```bash
tar -xzf OpenAI4S-0.2.0-linux-x86_64.tar.gz
cd OpenAI4S-0.2.0-linux-x86_64
./OpenAI4S                 # 拉起 daemon，浏览器打开 http://127.0.0.1:8760/
./install.sh               # 可选：把 CLI 放到 PATH，并注册应用菜单项
# 建议安装：
#   Debian/Ubuntu: sudo apt install bubblewrap
#   Fedora:        sudo dnf install bubblewrap
#   Arch:          sudo pacman -S bubblewrap
```

无头环境：`./bin/openai4s serve --no-open`。数据在 `~/.openai4s`。R 内核没有打进包；
装好 micromamba/mamba/conda 后运行 `./bin/openai4s setup`。只发布 `x86_64`；
arm64 Linux 请用 PyPI（`pip install openai4s`）。

### 公开说明里需要写明的限制

- Linux 沙箱层级是 **beta**。完整的文件系统与出口 bubblewrap 冒烟仍是手工跑
  （`OPENAI4S_KERNEL_SANDBOX=enforce uv run python -m harness.smoke.linux_sandbox`）。
  CI 证明的是私有 PID 中断路径。
- `OPENAI4S_REQUIRE_TOKEN=0` 仍是仅 loopback 的逃生口，直到 0.3.0
  （`LEGACY_TOKEN_OPT_OUT_REMOVED_IN`）。默认仍然要求 token。
- macOS 的 `--mode release` 仍然过不去：本树里 Developer ID + 公证不可达。
  在 macOS 负责人决定如何处理这道门之前，不要把 GitHub release 翻成公开。
- **不要**从打包分支打 tag、`gh release create` 或往 PyPI 发布。三个平台都
  就绪后，在 `main` 上打一个 annotated `v0.2.0`。

## 可复现的 Linux 构建

在 Linux x86_64 上原生执行。脚本可以从 macOS 交叉构建，但
`verify_linux_bundle.py` 的 import 探针只在匹配的 Linux 宿主机上真正执行。

### 系统软件包

Debian/Ubuntu（Linux 打包虚拟机用的就是这一套）：

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  ca-certificates curl tar gzip rsync coreutils
# 可选，给构建机上的 doctor / 沙箱冒烟：
# sudo apt-get install -y bubblewrap
```

如果 `PATH` 上还没有 `uv`：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# 然后确保 ~/.local/bin 在 PATH 里
```

Fedora：`sudo dnf install tar gzip rsync curl`。Arch：`sudo pacman -S tar gzip rsync curl`。

不需要额外的编译器。包里嵌入的是 `uv python install` 拿到的可重定位 CPython，
以及 `scripts/bundled_packages.txt` 里的 manylinux wheel。切图标阶梯时，
Pillow 由 `uv run --no-project --with pillow` 临时拉取。

### 命令

在 `[project] version` 与 `openai4s.__version__` 都是 `0.2.0` 的检出上：

```bash
# 0) 身份
git rev-parse HEAD
uv run --locked python scripts/verify_release_tag.py v0.2.0

# 1) 轻量控制环境（pytest / mypy / uv build）
uv sync --locked --extra science

# 2) wheel + sdist
uv run --locked python scripts/source_secret_scan.py
uv build --no-sources --out-dir dist --clear
uv run --locked python scripts/verify_release_artifacts.py dist

# 3) Linux 桌面 tarball（慢：下载 CPython 3.13 + 科学栈 wheel）
bash scripts/build_linux_bundle.sh
python3 scripts/verify_linux_bundle.py dist/OpenAI4S-0.2.0-linux-x86_64.tar.gz

# 4) 可选收据，形状与 release job 写出的相同
uv run --locked python scripts/release_receipts.py \
  --kind linux --source-sha "$(git rev-parse HEAD)" \
  dist/OpenAI4S-0.2.0-linux-x86_64.tar.gz
```

在 macOS 上，第 3 步是 `bash scripts/build_macos_dmg.sh`，然后
`python3 scripts/verify_macos_bundle.py dist/OpenAI4S-*.dmg`。机器上已经有
Linux tarball 时，Windows zip 是 `bash scripts/build_windows_zip.sh`
（它封装的就是那一份 `.tar.gz`）。

### 对解包后的 Linux 包做冒烟

```bash
tar -xzf dist/OpenAI4S-0.2.0-linux-x86_64.tar.gz -C /tmp
APP=/tmp/OpenAI4S-0.2.0-linux-x86_64
test "$(cat "$APP/VERSION")" = "0.2.0"
"$APP/bin/openai4s" --version          # openai4s 0.2.0
"$APP/bin/openai4s" doctor
OPENAI4S_DATA_DIR=/tmp/openai4s-smoke \
  "$APP/bin/openai4s" serve --host 127.0.0.1 --port 8760 --no-open &
# 等监听起来之后：
curl -fsS http://127.0.0.1:8760/ >/tmp/index.html
"$APP/bin/openai4s" stop
```

daemon 默认会签发 loopback 访问 token。不带 token 去 `curl /` 预期是 401
或 HTML 恢复页；工作台文档仍会发给出示了 token 的浏览器。未认证的存活检查
在门允许时用 `/health`，否则打开 stderr 打出的
`http://localhost:8760/?token=…`。

### 构建机上的离线套件

```bash
OPENAI4S_SECRET_STORE=plaintext \
  uv run pytest -n auto --maxprocesses=4 --dist loadfile
uv run mypy
```

有一个 artifact 捕获测试
（`test_capture_detects_same_length_rewrite_that_restores_mtime`）会在
`ctime` 分辨率较粗的内核上失败。那是宿主机文件系统的问题，不是产品缺陷——
不要为此改仓库里的代码。
