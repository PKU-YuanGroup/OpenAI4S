# Mineral Spectra Case 1

合成算例中可观测的那一半输入；它的真值答案单独放一个文件，只有评测环节才允许打开。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`spectrum.csv`](spectrum.csv) | 盲 pipeline 真正读的那份带噪混合矿物 Raman 谱：626 行、两列 `raman_shift,intensity`。 |
| [`input.png`](input.png) | 同一条带噪谱的 1200×360 RGBA 图，用来肉眼看一眼输入。它只是数据的画像，不是另一次测量。 |
| [`truth.json`](truth.json) | 隐藏的真值答案：Clinoptilolite-Ca、Bertrandite、Diopside 三者的真实比例，以及生成这个算例时用的噪声、尖峰和基线设置。评测代码只能在盲推断结束之后读它。 |
