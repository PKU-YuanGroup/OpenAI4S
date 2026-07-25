# Contributor avatars

[中文说明](README_zh.md)

One PNG per contributor, cropped to a circle with transparent corners, linked
from the contributor wall in the root READMEs. `scripts/update_contributors.py`
writes them from the GitHub contributors API. Nothing in the OpenAI4S runtime
reads this directory.

## Files

| File | Purpose |
| --- | --- |
| `Grace-xyx.png` | Render-ready avatar for contributor `Grace-xyx`. |
| `HowardLi1984.png` | Render-ready avatar for contributor `HowardLi1984`. |
| `Linmj-Judy.png` | Render-ready avatar for contributor `Linmj-Judy`. |
| `Lyu6PosHao.png` | Render-ready avatar for contributor `Lyu6PosHao`. |
| `Nobody-Zhang.png` | Render-ready avatar for contributor `Nobody-Zhang`. |
| `YuyangSunshine.png` | Render-ready avatar for contributor `YuyangSunshine`. |
| `jiangzx25.png` | Render-ready avatar for contributor `jiangzx25`. |
| `riiiiiiin.png` | Render-ready avatar for contributor `riiiiiiin`. |
| `wangyu-sd.png` | Render-ready avatar for contributor `wangyu-sd`. |
| `yusowa0716.png` | Render-ready avatar for contributor `yusowa0716`. |

Do not hand-edit the raster files. Regenerate them through the contributor
workflow so cropping and README links stay consistent; the script prunes any
image that no longer belongs to a current contributor, so a hand-added file
will simply disappear on the next run.
