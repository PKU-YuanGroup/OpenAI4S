# frontend/src/components/onboarding

[English](README.md)

M-01 首次运行向导视图。四个决策步骤、skip/清单，以及三态能力 badge。API key 是非受控 password 输入，绝不写入向导状态或文本节点。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`CapabilityBadges.tsx`](CapabilityBadges.tsx) | `true` / `false` / `unknown` 胶囊；unknown 原因原样展示。 |
| [`index.ts`](index.ts) | 再导出 host、badge 与 readiness 面板。 |
| [`onboarding.css`](onboarding.css) | 车道本地遮罩；≤900px 时触控目标 ≥40px。 |
| [`ReadinessPanel.tsx`](ReadinessPanel.tsx) | 来自 GET `/onboarding` 的 standard-profile + 网络姿态。 |
| [`Wizard.tsx`](Wizard.tsx) | `#onboarding` 对话框；路径 / Test / readiness / 项目。 |
