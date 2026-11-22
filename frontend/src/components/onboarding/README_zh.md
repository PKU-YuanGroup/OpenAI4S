# frontend/src/components/onboarding

[English](README.md)

M-01 首次运行向导视图。四个决策步骤、skip，以及三态能力 badge。API key 是非受控 password 输入，绝不写入向导状态或文本节点。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`CapabilityBadges.tsx`](CapabilityBadges.tsx) | `true` / `false` / `unknown` 胶囊；unknown 原因原样展示。 |
| [`index.ts`](index.ts) | 再导出 host、badge 与 readiness 面板。 |
| [`onboarding.css`](onboarding.css) | 车道本地遮罩；≤900px 时触控目标 ≥40px。 |
| [`ReadinessPanel.tsx`](ReadinessPanel.tsx) | 来自 GET `/onboarding` 的 standard-profile + 网络姿态。`EnvironmentCard` / `CopyCommand` 与「设置 → 计算」共用；复制走 `copyText`。 尚未创建的环境只列一次并说明它是什么，不再把它的整份清单列成缺少的软件包；卡片说明这些环境是可选的、什么时候才会阻止运行代码，缺少环境时只给一条指引（`openai4s setup`；单个软件包在 设置 → 计算 里安装），并说明终端找不到 `openai4s` 时怎么运行；不再显示守护进程给出的 `env plan`/`env apply` 两条命令。联网卡片区分应用联网（智能体的网页搜索、读取网页、科学数据库；不影响模型服务）和内核联网（单元里的代码，默认被沙盒阻止）。 |
| [`ReadinessPanel.test.tsx`](ReadinessPanel.test.tsx) | 就绪卡片的复制在纯 http 下通过选区复制完成，没复制成功时如实提示；卡片头部带上宿主传入的操作按钮。 缺失的环境只列一次，不列其清单；缺少的软件包写明所属环境；联网卡片区分应用联网与内核联网，并说明域名限制是否开启；缺少环境时唯一给出的命令是 `openai4s setup`。 |
| [`SearchSetupCard.tsx`](SearchSetupCard.tsx) | 「环境与网络」一步里可选的网页搜索选项（豆包 / Tavily / You.com / 仅内置 / Parallel）；向导仍然只有四个必需步骤。 当前顺序对应的选项显示为已选中（`currentSearchChoice`）；只有改选时才显示 Key 输入框和「应用」；选择豆包时说明为什么环境变量里的模型 Key 不会被搜索复用。 |
| [`SearchSetupCard.test.tsx`](SearchSetupCard.test.tsx) | 引导里的搜索选项与 `openai4s init` 一致（包括 Parallel）；选择免 key 的 Parallel 时不显示 key 输入框，只提交 `primary`。 当前顺序对应的选项被选中且无需应用；选择豆包时显示 Key 说明、Key 输入框和「应用」。 |
| [`Wizard.tsx`](Wizard.tsx) | `#onboarding` 对话框；路径 / Test / readiness / 项目。云端路径可以按所选协议明确获取端点的模型 id，在模型为空时填入端点确实提供的协议默认模型（否则第一个候选），并把这次获取计入供应商请求数，也仍允许手填；模型为空直接点「继续」时会先做同样的获取再保存。Test 运行时只禁用它自己的按钮：Skip 和 Continue 仍可使用，离开该步骤后，probe 迟到的结果会被丢弃。向导已保存过的云端或本机路径再点「继续」，会更新那个配置档，而不是再新建一个。守护进程通过环境变量运行的模型（`has_api_key` 为真、且没有对应的已保存配置档，`environmentPath`）会列在第 1 步；点「继续」或在没选别的模型时点「测试连接」，会把它保存为不含 Key 的配置档（Key 仍留在环境变量里）；云端表单的 Key 输入框会说明环境变量里的 Key 何时适用。 步骤条上的每一步都是按钮，点击即转到该步，显示序号，完成后显示对勾；文案讲模型和服务，不再讲协议、目录和套接字。 没有任何模型时直接进入云端表单，字段顺序为：服务商（按名称列出的预设，带注册表里的地址和默认模型）、名称、Base URL、API Key（附文档写明的获取页面）、模型；缺少名称时在名称字段下方提示。标题行带语言切换；步骤条是唯一的步骤列表（单独的“清单”视图已去掉）。NV-04：配置档保存成功之后、请求激活之前，向导写入同一标签页的续接标记；`load()` 据此续接（已保存配置档为当前激活项时回到测试连接，未激活时回到选择模型步骤），而不是按已完成处理；跳过或完成时清除标记。 |
