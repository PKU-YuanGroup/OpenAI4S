# 贡献者头像

[English](README.md)

每位贡献者一张 PNG，裁成四角透明的圆形，由根 README 的贡献者墙引用。这些图片由
`scripts/update_contributors.py` 从 GitHub contributors API 与一份维护中的、已公开
署名的非 commit 贡献者名单生成。脚本同时根据刷新和清理后磁盘上保留的 PNG 文件，
更新本目录两份 README 中带标记的文件表。一次刷新同时会改写两份根 README 的贡献者墙，
所以运行 `scripts/check_directory_readmes.py` 之前要把改动整体暂存（`git add -A`）：该
检查读取 Git 的文件清单，被清理的头像在删除被暂存之前仍留在其中。OpenAI4S 的运行时不会
读取本目录。

所有待更新 README 都会先写入目标旁的临时文件，再依次替换。暂存失败不会改动任何
README；单次替换是原子的，但整批替换不是全部成功或全部回滚的事务。

## 文件

<!-- AVATAR-FILES:START -->
| 文件 | 职责 |
| --- | --- |
| `ChampionZhong.png` | 贡献者 `ChampionZhong` 的可直接渲染头像。 |
| `ClarenceYC.png` | 贡献者 `ClarenceYC` 的可直接渲染头像。 |
| `Devin-jun.png` | 贡献者 `Devin-jun` 的可直接渲染头像。 |
| `EQSTLab.png` | 贡献者 `EQSTLab` 的可直接渲染头像。 |
| `Grace-xyx.png` | 贡献者 `Grace-xyx` 的可直接渲染头像。 |
| `HowardLi1984.png` | 贡献者 `HowardLi1984` 的可直接渲染头像。 |
| `Linmj-Judy.png` | 贡献者 `Linmj-Judy` 的可直接渲染头像。 |
| `Lyu6PosHao.png` | 贡献者 `Lyu6PosHao` 的可直接渲染头像。 |
| `Nobody-Zhang.png` | 贡献者 `Nobody-Zhang` 的可直接渲染头像。 |
| `WenyuLiang.png` | 贡献者 `WenyuLiang` 的可直接渲染头像。 |
| `YuyangSunshine.png` | 贡献者 `YuyangSunshine` 的可直接渲染头像。 |
| `cursoragent.png` | 贡献者 `cursoragent` 的可直接渲染头像。 |
| `difficulttopickaname.png` | 贡献者 `difficulttopickaname` 的可直接渲染头像。 |
| `jiangzx25.png` | 贡献者 `jiangzx25` 的可直接渲染头像。 |
| `riiiiiiin.png` | 贡献者 `riiiiiiin` 的可直接渲染头像。 |
| `stau-7001.png` | 贡献者 `stau-7001` 的可直接渲染头像。 |
| `wangyu-sd.png` | 贡献者 `wangyu-sd` 的可直接渲染头像。 |
| `yusowa0716.png` | 贡献者 `yusowa0716` 的可直接渲染头像。 |
<!-- AVATAR-FILES:END -->

不要手工编辑这些位图文件，一律通过贡献者 workflow 重新生成，以保持裁剪方式与
README 链接一致；脚本会清掉所有不属于当前贡献者的图片，手工放进来的文件下次运行就
会被删掉。
