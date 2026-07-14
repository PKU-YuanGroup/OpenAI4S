# Remote Compute SSH Skill

这个渐进披露 recipe 说明已配置 SSH/SLURM compute 的控制平面流程：发现主机详情、stage 文件、经审批提交、等待通知、harvest 输出，并记录可复用主机知识。它本身不注册 SSH provider，也不授予主机访问权限。

可用性取决于用户配置、credential、scheduler/allocation 状态、远端软件与审批。提交 job 会消耗真实资源；recipe 要求验证与明确 intent，不能仅凭命令已排队就声称成功。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | `host.compute`/控制内核用法、compute detail discovery、环境 activation、文件 staging、direct/SLURM job submit、notification、harvest、cancel/recovery 与主机 note 更新的 runbook。 |

## 直属子目录

无。
