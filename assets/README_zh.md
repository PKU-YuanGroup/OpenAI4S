# 品牌素材

[English](README.md)

提交进仓库的图形素材：打包应用的图标，以及根目录 README 内嵌的演示媒体。
守护进程从不加载这些文件，只有发布构建和 GitHub 的 README 渲染会读它们。

## 文件

| 文件 | 用途 |
| --- | --- |
| `app-icon-1024.png` | macOS 应用图标：OpenAI4S 标识——五个成键原子环绕中央终端方块，方块里是红色提示符 `>`——按 Big Sur 图标网格排布。`scripts/build_macos_dmg.sh` 会把它切成 `.icns`；文件缺失时构建直接失败，因为 `Info.plist` 声明了这个图标。需要重新生成请用 `scripts/make_app_icon.py`，不要手工改图。 |

## 子目录

| 目录 | 用途 |
| --- | --- |
| `readme-gifs-hd/` | 提交进仓库的高分辨率 GIF，由根目录 README 内嵌：六个产品演示外加动画横幅。 |

## 它处在什么位置

这个标识在仓库其他地方只以字形尺寸的位图存在——`readme-gifs-hd/openai4s_penta.gif`
里的横幅，以及 64px 的 Web favicon——两者放大到 `.icns` 所需的 1024px 都会糊掉。
因此图标是按标识的实测几何、用平面矢量图元重绘出来并提交在这里的；这样 DMG 构建就只剩
`sips` + `iconutil` 两步，既没有绘图代码，也不再依赖任何图像库。
