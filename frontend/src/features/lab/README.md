# frontend/src/features/lab

[中文说明](README_zh.md)

The Lab dock pane beside the Notebook: the OpenMHS simulated lab as the operator sees it. It reads `/api/v1/mhs-lab/*` every 2 s while the pane is visible: device cards grouped by agent surface (**MHS**: native `mhs_*` tools; **MCP**: connector rows) with their wire, live state and e-stop latch; a device market that installs any OpenMHS market package with one click; a setup button that downloads OpenMHS when it is missing; start/stop, live state and e-stop latch; charts derived from receipt outputs (dose-response, reactor optimisation trace, robot-arm path); the live receipt stream; offline evidence-chain verification; LIMS vials. The e-stop and operator reset buttons are human actions; the agent has no route to them.

## Files

| File | Responsibility |
| --- | --- |
| [`index.ts`](index.ts) | `mountLab()` renders the panel into `#dock-lab` once; `labTabLabel()` names the dock tab. |
| [`LabPanel.tsx`](LabPanel.tsx) | The panel, its bilingual copy and the SVG charts (validated reference categorical slots, light and dark steps). |
| [`MarketPanel.tsx`](MarketPanel.tsx) | The device market: search the OpenMHS market, filter by category, install a device through MCP or MHS with one click. |
| [`lab.test.ts`](lab.test.ts) | Unit tests of the panel helpers (latest read, plain introductions, category labels). |
| [`types.ts`](types.ts) | Shapes of the `/mhs-lab/*` responses. |
| [`lab.css`](lab.css) | Panel styles on the workbench tokens. |
