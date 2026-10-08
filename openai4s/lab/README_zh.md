# Lab 合同

[English](README.md)

这是 OpenAI4S 自己的内部仿真合同，按 MHS 公开设计理念组织；
不实现也不宣称兼容官方 MHS API。导入时不登记设备、不启动进程。

## 文件

| 文件 | 职责 |
| --- | --- |
| `__init__.py` | 公共合同导出。 |
| `models.py` | 不可变数据类、严格 JSON 解码、状态机与哈希。 |
| `ports.py` | 设备、账本与管理器协议。 |
| `manifest.py` | 描述校验、单位匹配与公开投影。 |
| `devices.py` | 线程安全的显式设备登记。 |
| `fake.py` | 确定性的进程内玩具设备与故障注入。 |
| `manager.py` | 进程级准入、派发、对账与生命周期。 |
| `policy.py` | 准入与预算的纯函数。 |
| `reconcile.py` | 启动对账，不重放设备操作。 |
| `runtime.py` | 管理器装配与启动对账。 |
| `README.md` | 英文目录说明。 |
| `README_zh.md` | 中文目录说明。 |
