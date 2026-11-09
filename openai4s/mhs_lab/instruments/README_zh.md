# openai4s/mhs_lab/instruments

[English](README.md)

以 OpenMHS 驱动包（`mhs.driver.Driver`）编写的模拟仪器，用 `python3 -m mhs serve <目录> --backend sim` 发布。每台都声明 `mode = "simulated"`、带缺口说明的保真度契约，以及引用本仪器 `MANUAL.md` 的限值；端点在模拟器运行之前强制执行这些限值。隐藏的模型常数不会出现在任何读数、输出或清单里。

## 目录

| 目录 | 仪器 |
| --- | --- |
| [`assay_station/`](assay_station/) | AS-96 酶抑制工作站：剂量梯度、启动反应、动力学读数（竞争性抑制 Michaelis-Menten，带种子噪声）。 |
| [`flow_reactor/`](flow_reactor/) | FR-10 连续流反应器与在线 HPLC（四反应 Arrhenius 动力学，RK4），以及 LIMS 产物收集。 |
