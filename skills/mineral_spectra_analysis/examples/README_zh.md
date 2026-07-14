# Mineral Spectra 示例

一个录下来的合成评测算例。盲分析摘要与隐藏的真值答案分别 commit 成两份文件，这样报告既能演示与真值的对比，推断循环又始终读不到真值。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`build_example.py`](build_example.py) | 只用标准库，从 commit 下来的 analysis / components / truth 三份 JSON 重建示例报告：格式化预测结果、诊断、真值指标和迭代历史。它不会重跑那套可选的科学 pipeline。 |
| [`case1_analysis.json`](case1_analysis.json) | 盲 pipeline 录下来的结果：运行时用的配置、三个预测矿物相及其比例与支持峰、残差诊断、评测指标、迭代历史，以及这次运行涉及的 Artifact 文件名。 |
| [`case1_components.json`](case1_components.json) | 可读的合成过程说明。三个矿物相各自的真实比例、分别取自哪条 RRUFF 参考谱，以及污染这条混合谱时用的噪声水平、尖峰个数和基线强度。 |
| [`case1_mineral_spectra_report.md`](case1_mineral_spectra_report.md) | 生成的报告：盲预测与隐藏真值并排放，附可信度诊断、评测指标、迭代轨迹，以及算例里每个文件各自是干什么的。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`case1/`](case1/) | 可观测的谱与其图像，隐藏真值放在同一目录，但单独成文件。 |

成分识别 F1 满分只是这一个录制算例的属性，不是一般性的性能声明。
