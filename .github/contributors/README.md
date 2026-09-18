# Contributor avatars

[中文说明](README_zh.md)

One PNG per contributor, cropped to a circle with transparent corners, linked
from the contributor wall in the root READMEs. `scripts/update_contributors.py`
writes them from the GitHub contributors API and a maintained list of publicly
recognized non-commit contributors. The same script updates the marked file
tables in both directory READMEs from the PNGs left on disk after refresh and
pruning. A refresh rewrites the root walls too, so stage all of it (`git add
-A`) before running `scripts/check_directory_readmes.py`: that check reads
Git's file list, which keeps a pruned avatar until its deletion is staged.
Nothing in the OpenAI4S runtime reads this directory.

All changed READMEs are prepared as sibling temporary files before any
destination is replaced. A staging failure leaves every README unchanged;
each replacement is atomic, but the batch is not an all-or-nothing transaction.

## Files

<!-- AVATAR-FILES:START -->
| File | Purpose |
| --- | --- |
| `ChampionZhong.png` | Render-ready avatar for contributor `ChampionZhong`. |
| `ClarenceYC.png` | Render-ready avatar for contributor `ClarenceYC`. |
| `Devin-jun.png` | Render-ready avatar for contributor `Devin-jun`. |
| `EQSTLab.png` | Render-ready avatar for contributor `EQSTLab`. |
| `Grace-xyx.png` | Render-ready avatar for contributor `Grace-xyx`. |
| `HowardLi1984.png` | Render-ready avatar for contributor `HowardLi1984`. |
| `Linmj-Judy.png` | Render-ready avatar for contributor `Linmj-Judy`. |
| `Lyu6PosHao.png` | Render-ready avatar for contributor `Lyu6PosHao`. |
| `Nobody-Zhang.png` | Render-ready avatar for contributor `Nobody-Zhang`. |
| `WenyuLiang.png` | Render-ready avatar for contributor `WenyuLiang`. |
| `YuyangSunshine.png` | Render-ready avatar for contributor `YuyangSunshine`. |
| `cursoragent.png` | Render-ready avatar for contributor `cursoragent`. |
| `difficulttopickaname.png` | Render-ready avatar for contributor `difficulttopickaname`. |
| `jiangzx25.png` | Render-ready avatar for contributor `jiangzx25`. |
| `riiiiiiin.png` | Render-ready avatar for contributor `riiiiiiin`. |
| `stau-7001.png` | Render-ready avatar for contributor `stau-7001`. |
| `wangyu-sd.png` | Render-ready avatar for contributor `wangyu-sd`. |
| `yusowa0716.png` | Render-ready avatar for contributor `yusowa0716`. |
<!-- AVATAR-FILES:END -->

Do not hand-edit the raster files. Regenerate them through the contributor
workflow so cropping and README links stay consistent; the script prunes any
image that no longer belongs to a current contributor, so a hand-added file
will simply disappear on the next run.
