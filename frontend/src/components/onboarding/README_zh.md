# frontend/src/components/onboarding

[English](README.md)

M-01 首次运行向导视图。四个决策步骤、skip/清单，以及三态能力 badge。API key 是非受控 password 输入，绝不写入向导状态或文本节点。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`CapabilityBadges.tsx`](CapabilityBadges.tsx) | `true` / `false` / `unknown` 胶囊；unknown 原因原样展示。 |
| [`index.ts`](index.ts) | 再导出 host、badge 与 readiness 面板。 |
| [`onboarding.css`](onboarding.css) | 车道本地遮罩；≤900px 时触控目标 ≥40px。 |
| [`ReadinessPanel.tsx`](ReadinessPanel.tsx) | 来自 GET `/onboarding` 的 standard-profile + 网络姿态。`EnvironmentCard` / `CopyCommand` 与「设置 → 计算」共用；复制走 `copyText`。 |
| [`ReadinessPanel.test.tsx`](ReadinessPanel.test.tsx) | 就绪卡片的复制在纯 http 下通过选区复制完成，没复制成功时如实提示；卡片头部带上宿主传入的操作按钮。 |
| [`Wizard.tsx`](Wizard.tsx) | `#onboarding` 对话框；路径 / Test / readiness / 项目。Test 运行时只禁用它自己的按钮：Skip、清单和 Continue 仍可使用，离开该步骤后，probe 迟到的结果会被丢弃。向导已保存过的云端或本机路径再点「继续」，会更新那个配置档，而不是再新建一个。 |
