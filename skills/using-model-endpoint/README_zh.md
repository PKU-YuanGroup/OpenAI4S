# Using Model Endpoint Skill（规划中 / 尚未接线）

这里放着一份 endpoint 专属推理内核的设计，以及它将来需要的可信 provider shim 与 manifest。**本目录没有任何东西接进可执行的 provider 路径：**`ComputeManager` 只发现 `remote-compute-*` 下的 provider，支持 BYOC 与 SSH 两个家族；`host.endpoints.*` 只做 endpoint 的注册与探测，从不创建这里描述的专属推理内核。

Skill 本身仍然是能被发现的——loader 会扫描每一个 `skills/<name>/SKILL.md`，这一份也在内，所以 agent 依然可以通过渐进披露列出并加载它；只是跑不起来而已。因此，[`SKILL.md`](SKILL.md) 要当成写在实现之前的 runbook 来读，几个 provider 文件则是休眠的实现资产。它们摆在这里，并不能证明 `compute_provider({'provider': ...})` 眼下能用。在发现、生命周期与路由接通并测试完成之前，不得把这些东西说成一项已经存在的端到端能力。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。
有 Node 18+ 即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install using-model-endpoint --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入
`./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则
写到你指定的任意位置；`--dry-run` 只打印解析出的绝对路径，不写任何文件。重装时
若目标副本被你改过则拒绝覆盖，`uninstall` 也只删除它自己写过的文件。同一条命令
的发布名写法是 `npx openai4s-skills install using-model-endpoint`，只是这个包还
没有发布到 npm。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/using-model-endpoint
python3 -m zipfile -c using-model-endpoint.zip using-model-endpoint
```

同一份内容的图形界面版本是点击下载整个
[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)：解压
后把 `skills/using-model-endpoint/` 拷出来即可。如果你本来就在跑 OpenAI4S，那这
里没有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于
`<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的
那些事：
[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 规划中的 recipe：Cell 直接调用已注册 endpoint 自己的 HTTP API，请求 URL 一律从预加载的 `BASE_URL` 拼出；托管 endpoint 要带上 `Authorization: Bearer $INFER_API_KEY`；出网走 endpoint 专属的沙箱代理。只有请求和响应，没有 submit/harvest 生命周期。 |
| [`provider.json`](provider.json) | 用来注册 provider ID `infer` 的 manifest：一个 Python 3.11 + pip 的 helper 环境，装 `httpx==0.28.1`；control egress 目标目前还是占位值。`ComputeManager` 不会来这里找它。 |
| [`provider.py`](provider.py) | `InferProvider`，可信，但没人调得到它。进程里原有的 `INFER_*`、`NVIDIA_*` 变量在认证前会被清掉，所以 API key 只能从 Host 的认证通道进来；进来之后导出为规范名 `INFER_API_KEY`，如果注册时自带的 credential 名通过了别名校验，就再按那个名字导出一份。形如 `nvapi-…` 的 token 会从输出里脱敏。它没有 SDK 需要 import，因为 Cell 被要求自己发 HTTP；create、exec、list、读 owner、terminate 这些 job 生命周期操作一概拒绝。 |
| [`requirements.lock`](requirements.lock) | `httpx` helper 的哈希锁定依赖：`anyio`、`certifi`、`h11`、`httpcore`、`idna`，以及只在 Python 3.13 以下才需要的 `typing-extensions`。除非将来接线后的 provider 真去构建这个 helper 环境，否则不会有任何东西照它安装。 |
