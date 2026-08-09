# 命令行接口

[English](README.md)

`openai4s` 命令都放在这里：daemon 生命周期（`serve`、`status`、`stop`、`url`）、本地一次性任务执行（`run`）、首次模型配置（`init`）、科学计算环境创建（`setup`）以及与之并列、把环境当事务处理的 `env` generation、几个支持面（`doctor`、`diagnostics`、`verify-package`）、工作流 `benchmark`、只读会话分享（`share`）与替它对外承接流量的公网 `relay`，还有可选的 Jupyter 适配器命令。

## 在架构中的位置

CLI 只负责组合，不负责编排。`openai4s run` 用 [`../agent/`](../agent/) 搭出本地的外层循环，只有当某个回合真的路由到代码 Cell 时，常驻内核才会启动。`openai4s serve` 把活交给 HTTP/WebSocket server。setup 和 status 这类命令都跑在 Agent 回合之外。

同一棵 argparse 树下其实有三类命令，而失败意味着什么，取决于它属于哪一类。`run`、`init`、`setup`、`env`、`doctor`、`diagnostics`、`verify-package`、`benchmark` 和 Jupyter 命令都在当前进程里做完；`serve` 自己变成 daemon；`share` 则是一个 REST 客户端，要求 daemon 已经在跑，因此 daemon 不在或凭据缺失时，它在触及功能本身之前就先失败。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 重新导出 `main`，包本身就是 CLI 入口。 |
| [`main.py`](./main.py) | 一棵 argparse 树和它的 handler。在本进程内跑完的：`run`、`init`、`setup`、`env`（`plan`/`apply`/`list`/`rollback`/`recover`）、`doctor`、`diagnostics`、`verify-package`、`benchmark`，以及 `jupyter` 的 describe/export/install。与 daemon 打交道的：`serve`、`status`、`stop`、`url`，以及 `share` 的八个子命令；`relay serve` / `relay gen-token` 跑的是架在 VPS 上的公网 share relay，不是本机的东西。daemon 的 pidfile 与 statefile 也归它管；建环境时也是它在调 conda —— `setup --profile standard` 建日常用的 Python 与 R 这一对，`full` 建全部四个，`--only <name>` 只建一个。已有的环境除非加 `--update`，否则不动它；更新绝不会 prune 掉你自己装的包；而且更新时传的是发现逻辑真正找到的那个 prefix，而不是 spec 里的 `name:` —— 只给名字的话 conda 会拿它去解析自己的 root prefix，结果是在 Agent 根本不会用到的地方另建一个环境，还报成功。 |

## 运维契约

- `run` 在进程内跑完，动作路由与完成判定用的是与本地 Agent facade 相同的一套 Engine 规则。
- `serve` 的绑定地址必须始终取自 `Config`，默认也必须留在 loopback 上，不要写死。要把 daemon 暴露到本机之外，应该交给可信的反向代理或 SSH tunnel。access token 现在是默认必需的，loopback 上同样如此：Gateway 会把它写进数据目录下一个仅属主可读的文件，跨重启复用，并要求除 `/health` 和 `/api/v1/auth/status` 之外的每条路径都带上它 —— 一个客户端总不能靠一份它无权读取的响应来得知自己需要 token。`OPENAI4S_REQUIRE_TOKEN=0` 仍可关掉这道门，但仅限 loopback 绑定，且只到 Gateway 里写明的那个移除版本为止。这个 token 只是最后一道薄薄的防线，不能把它当成可以放心暴露端口的理由。
- 正因为这道门开着，`serve`、`status`、`url` 打印给人看的 URL 会带上 `?token=…`，否则 SPA 在能给出任何入口之前就先收到 401。凡是不交到人手上的用法，取的都是同一个 URL 的不带 token 版本：凭据不该出现在日志行或窗口标题里。
- 与 daemon 通信的子命令则把 token 放在 header 里，因为 daemon 对写操作本就拒收 query token。它读那个仅属主可读的 token 文件，daemon 跑在别的账号下时则读 `OPENAI4S_TOKEN`。请求路径由 `contract.API_ROOT` 拼出来，调用方自带前缀会直接报错而不是给出一个 404 —— 把 `/api/` 写死，正是当初每个 `share` 子命令都只收到 daemon 那句「API 是带版本的」404、而没有任何一个真正走到路由的原因。
- `setup` 是就地改一个环境；`env` 把环境当事务处理 —— 一个 generation 从 apply 锁下暂存的 spec 构建出来，靠真正启动解释器来验证（路径上有个文件并不等于有个环境），验证通过之后才把指针指过去。`env recover` 报告重启后需要知道的东西，包括某次 apply 是否仍持有锁。
- 退出码是判定结果，不是装饰。`doctor` 用 0 正常 / 1 可用但降级 / 2 检查失败作答，并且不需要 daemon —— 会让人想跑它的场景，多半正是 daemon 起不来；数据目录本身就是坏的那一项时，它也退回到朴素的 `Config` 而不是直接抛异常。`benchmark` 在工作流数为零时判失败，而不是对着零个工作流报一次干净的通过。
- 可选的 Jupyter import 只发生在 Jupyter 子命令的 handler 里。
- CLI 的输出和退出码是运维接口。改动它们，就要连测试和文档一起改。
