# Mineral Spectra Case 1

本目录把 observable synthetic input 与仅用于 evaluation 的 answer key 分开保存。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`spectrum.csv`](spectrum.csv) | Blind pipeline 消费的 observable 626-row two-column `raman_shift,intensity` dirty mixed-mineral Raman spectrum。 |
| [`input.png`](input.png) | 同一 dirty input spectrum 的 1200×360 RGBA 图，用于视觉检查；它是呈现，不是额外测量。 |
| [`truth.json`](truth.json) | Hidden synthetic answer key，记录 Clinoptilolite-Ca、Bertrandite、Diopside 的 true fraction 及生成 noise/spike/baseline metadata；evaluation code 只能在 blind inference 后读取。 |

## 直属子目录

无。
