# 命令行接口

[English](README.md)

`openai4s` 命令都放在这里：daemon 生命周期（`serve`、`status`、`stop`、`url`）、本地一次性任务执行（`run`）、首次模型配置（`init`）、科学计算环境创建（`setup`），以及可选的 Jupyter 适配器命令。

## 在架构中的位置

CLI 只负责组合，不负责编排。`openai4s run` 用 [`../agent/`](../agent/) 搭出本地的外层循环，只有当某个回合真的路由到代码 Cell 时，常驻内核才会启动。`openai4s serve` 把活交给 HTTP/WebSocket server。setup 和 status 这类命令都跑在 Agent 回合之外。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 重新导出 `main`，包本身就是 CLI 入口。 |
| [`main.py`](./main.py) | 一棵 argparse 树和它的 handler：`serve`、`status`、`stop`、`url`、`run`、`init`、`setup`，以及 Jupyter 的 describe/export/install 子命令。daemon 的 pidfile 与 statefile 也归它管；建环境时也是它在调 conda —— `setup --profile standard` 建日常用的 Python 与 R 这一对，`full` 建全部四个，`--only <name>` 只建一个。已有的环境除非加 `--update`，否则不动它；而更新绝不会 prune 掉你自己装的包。 |

## 运维契约

- `run` 在进程内跑完，动作路由与完成判定用的是与本地 Agent facade 相同的一套 Engine 规则。
- `serve` 的绑定地址必须始终取自 `Config`，默认也必须留在 loopback 上，不要写死。要把 daemon 暴露到本机之外，应该交给可信的反向代理或 SSH tunnel。一旦绑到非 loopback 地址，Gateway 会签发一个随进程存活的 access token，除 `/health` 之外的每条路径都要带上它才放行，而 `/health` 本身不做鉴权。这个 token 只是最后一道薄薄的防线，不能把它当成可以放心暴露端口的理由。
- 可选的 Jupyter import 只发生在 Jupyter 子命令的 handler 里。
- CLI 的输出和退出码是运维接口。改动它们，就要连测试和文档一起改。
