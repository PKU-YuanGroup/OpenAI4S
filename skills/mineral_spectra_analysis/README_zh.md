# Mineral Spectra Analysis Skill

这个渐进披露 Skill 描述 Raman mixed-mineral 工作流：只预处理一次，针对 reference library 迭代匹配/扣除 residual peak，以 NNLS unmix，诊断可靠性，并可用 hidden truth 评估 synthetic case。

Sidecar 包含数值 pipeline，但使用可选 numpy/scipy/pybaselines/matplotlib，并可能在获准时下载/缓存 RRUFF 数据。当前 library/pipeline 明确是 prototype-oriented；synthetic score 很高不能证明真实样本矿物鉴定。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Observable input、dependency/data 准备、一次性 preprocessing、blind residual loop、NNLS、diagnostic、report、synthetic evaluation 隔离、输出与解读限制的主 recipe。 |
| [`kernel.py`](kernel.py) | 可选 sidecar：检查 optional dependency/config；解析/下载/构建对齐 RRUFF library；读取/resample spectrum；despike、denoise、baseline-correct、normalize；检测/匹配 peak；排序 reference；执行 NNLS reconstruction；运行 blind-loop diagnostic；渲染 figure/report；计算 truth metric；生成/保存 synthetic benchmark case。 |

## 直属子目录

| 目录 | 职责 |
| --- | --- |
| [`examples/`](examples/) | Committed synthetic case 输入、hidden truth、录制 blind-analysis 输出、派生报告与纯标准库报告重建器。 |
