# Mineral Spectra 示例

本目录记录一个 synthetic evaluation case。Blind-analysis summary 与 hidden answer key 分开 committed，使报告可以演示评测，同时不允许 inference loop 读取 truth。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`build_example.py`](build_example.py) | 纯标准库 report rebuilder：读取 committed analysis/components/truth JSON，格式化 prediction、diagnostic、truth metric 与 iteration history，不重新运行可选科学 pipeline。 |
| [`case1_analysis.json`](case1_analysis.json) | 录制的 blind-pipeline config、三个预测 mineral phase/fraction/support peak、residual diagnostic、evaluation metric、iteration history 与 Artifact 名称。 |
| [`case1_components.json`](case1_components.json) | 人类可读 synthetic-generation summary，定义三个 mineral phase、true fraction、RRUFF source role、noise/spike count 与 baseline strength。 |
| [`case1_mineral_spectra_report.md`](case1_mineral_spectra_report.md) | 对比 blind prediction 与 hidden truth 的生成报告，包含 reliability diagnostic、evaluation metric、iteration trace 与文件角色。 |

## 直属子目录

| 目录 | 职责 |
| --- | --- |
| [`case1/`](case1/) | Synthetic case 的 observable spectrum/plot 与单独保存的 hidden truth。 |

Perfect component F1 只是这个录制 synthetic case 的属性，不是一般性能声明。
