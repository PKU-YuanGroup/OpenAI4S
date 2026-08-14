# Kubernetes 部署清单

[English](README.md)

容器化 OpenAI4S 的集群那一半。镜像本身由仓库根目录的
[`Dockerfile`](../Dockerfile) 构建，单机部署走
[`compose.yaml`](../compose.yaml)；两者与这里的清单一起记在
[docs/docker.md](../docs/docker.md)。

这里有意是朴素的清单，而不是 Helm chart。可部署的单元只有一个——一个带单个卷、
单个 Service、没有可选组件的有状态单例——给四个对象套一层模板，读者反而不如直接
改值来得清楚。

## 文件

| 文件 | 职责 |
| --- | --- |
| `kubernetes.yaml` | 整套部署：给数据目录用的 `ReadWriteOnce` PersistentVolumeClaim、单副本 `Recreate` Deployment（startup/readiness/liveness 探针都打在 `/health` 上），以及一个 ClusterIP Service。所有对象都不写 namespace，因此 apply 到你指定的那个即可。其中两个值是承重的而非惯例，且都就地写了注释：`replicas: 1`（存储是 SQLite，daemon 又是 pidfile 单例）与 `automountServiceAccountToken: false`（这个 Pod 跑的是模型写的代码，不该握着访问集群自身 API 的凭据）。 |
| `kubernetes-ingress.yaml` | 可选，也是最容易做错的一块。把工作台公开出去需要四件默认 Ingress 不会做的事——一个撑得住 WebSocket 的读超时、一个装得下真实数据集的请求体上限、关掉缓冲以免流式回复被攒到整轮结束才吐出来，以及最要紧的：保留原始 `Host` 头，因为 `Origin` 与 `Host` 不一致时，变更类 API 调用和 WebSocket 升级都会被拒。应用它就等于把能执行任意代码的端点公开出去，所以里面的代理鉴权注解是注释掉的，而不是干脆没有。 |

## 公开之前

通配绑定必然会关掉 daemon 的 Host 头重绑定白名单，于是挡在 `kernel/execute`
以及其余代码执行面前的，就只剩访问令牌。因此这些清单止步于 ClusterIP：用
`kubectl port-forward` 访问工作台，或者在前面放一层带鉴权的 TLS 代理。相关推理，
以及容器边界替代得了什么、替代不了什么，写在
[docs/security.md](../docs/security.md)。
