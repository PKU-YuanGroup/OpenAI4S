# openai4s/mhs_lab/instruments/assay_station

[English](README.md)

AS-96 酶抑制工作站（模拟）：96 孔板、8 通道移液器和动力学吸光度读板仪。模型为竞争性抑制（`v0 = Vmax S / (Km (1 + (I/Ki)^h) + S)`），拟合出的 IC50 服从 Cheng-Prusoff 关系。`load_sample` 扫描 LIMS 样品瓶，只有在 xArm 自己的证据链显示它把该瓶从反应器出口送到本工作台之后才接受。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`driver.py`](driver.py) | OpenMHS 驱动：读数、带输入/输出 schema 的动作、引用 MANUAL.md 的限值、急停、声明的复位后重新使能、保真度契约。 |
| [`sim.py`](sim.py) | 后端对象：解析/动力学模型及其状态。 |
| [`MANUAL.md`](MANUAL.md) | 仪器规格表；`driver.py` 里每个限值都引用其中一行。 |
