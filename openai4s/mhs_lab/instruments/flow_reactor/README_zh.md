# openai4s/mhs_lab/instruments/flow_reactor

[English](README.md)

FR-10 连续流反应器与在线 HPLC（模拟）：两台泵供料的 10 mL 盘管。四个竞争反应（生成产物、热降解、水解、过度加成）用 RK4 积分，收率呈现时间-温度权衡的脊线。`collect_product` 在反应器出口灌装一个带 LIMS 标签的样品瓶。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`driver.py`](driver.py) | OpenMHS 驱动：读数、带输入/输出 schema 的动作、引用 MANUAL.md 的限值、急停、声明的复位后重新使能、保真度契约。 |
| [`sim.py`](sim.py) | 后端对象：解析/动力学模型及其状态。 |
| [`MANUAL.md`](MANUAL.md) | 仪器规格表；`driver.py` 里每个限值都引用其中一行。 |
