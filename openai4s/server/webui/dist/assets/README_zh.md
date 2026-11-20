# Workbench 哈希资源

[English](README.md)

`frontend/`（`npm run build`）提交进来的构建产物。Gateway 在 `/static/dist/` 提供这棵树。它也是 `/` 与工作台深链的默认 SPA 外壳；`OPENAI4S_WEBUI=legacy` 是改发 `webui/index.html` 的逃生舱。脚本全部是带 `src=` 的外链文件，CSP `script-src 'self'` 不需要放行内联脚本。

## 文件

| 文件 | 职责 |
| --- | --- |
| `CapabilityBadges-qkNKZCgC.js` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_AMS-Regular-BQhdFMY1.woff2` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_AMS-Regular-DMm9YOAa.woff` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_AMS-Regular-DRggAlZN.ttf` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Caligraphic-Bold-ATXxdsX0.ttf` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Caligraphic-Bold-BEiXGLvX.woff` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Caligraphic-Bold-Dq_IR9rO.woff2` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Caligraphic-Regular-CTRA-rTL.woff` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Caligraphic-Regular-Di6jR-x-.woff2` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Caligraphic-Regular-wX97UBjC.ttf` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Fraktur-Bold-BdnERNNW.ttf` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Fraktur-Bold-BsDP51OF.woff` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Fraktur-Bold-CL6g_b3V.woff2` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Fraktur-Regular-CB_wures.ttf` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Fraktur-Regular-CTYiF6lA.woff2` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Fraktur-Regular-Dxdc4cR9.woff` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Main-Bold-Cx986IdX.woff2` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Main-Bold-Jm3AIy58.woff` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Main-Bold-waoOVXN0.ttf` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Main-BoldItalic-DxDJ3AOS.woff2` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Main-BoldItalic-DzxPMmG6.ttf` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Main-BoldItalic-SpSLRI95.woff` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Main-Italic-3WenGoN9.ttf` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Main-Italic-BMLOBm91.woff` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Main-Italic-NWA7e6Wa.woff2` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Main-Regular-B22Nviop.woff2` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Main-Regular-Dr94JaBh.woff` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Main-Regular-ypZvNtVU.ttf` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Math-BoldItalic-B3XSjfu4.ttf` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Math-BoldItalic-CZnvNsCZ.woff2` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Math-BoldItalic-iY-2wyZ7.woff` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Math-Italic-DA0__PXp.woff` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Math-Italic-flOr_0UB.ttf` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Math-Italic-t53AETM-.woff2` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_SansSerif-Bold-CFMepnvq.ttf` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_SansSerif-Bold-D1sUS0GD.woff2` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_SansSerif-Bold-DbIhKOiC.woff` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_SansSerif-Italic-C3H0VqGB.woff2` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_SansSerif-Italic-DN2j7dab.woff` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_SansSerif-Italic-YYjJ1zSn.ttf` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_SansSerif-Regular-BNo7hRIc.ttf` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_SansSerif-Regular-CS6fqUqJ.woff` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_SansSerif-Regular-DDBCnlJ7.woff2` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Script-Regular-C5JkGWo-.ttf` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Script-Regular-D3wIWfF6.woff2` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Script-Regular-D5yQViql.woff` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Size1-Regular-C195tn64.woff` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Size1-Regular-Dbsnue_I.ttf` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Size1-Regular-mCD8mA8B.woff2` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Size2-Regular-B7gKUWhC.ttf` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Size2-Regular-Dy4dx90m.woff2` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Size2-Regular-oD1tc_U0.woff` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Size3-Regular-CTq5MqoE.woff` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Size3-Regular-DgpXs0kz.ttf` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Size3-Regular-gV2CO0n9.woff2` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Size4-Regular-BF-4gkZK.woff` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Size4-Regular-DWFBv043.ttf` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Size4-Regular-Dl5lxZxV.woff2` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Typewriter-Regular-C0xS9mPB.woff` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Typewriter-Regular-CO6r4hn1.woff2` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `KaTeX_Typewriter-Regular-D3Ib7_Hf.ttf` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `en-BRK109D2.js` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `index-CoTIyM7y.js` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `index-Dt8mQQKn.css` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `katex-DAR36jk3.css` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `katex-IejJBL89.js` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `mount-Bv08Oltp.css` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `mount-C9nhbqLd.js` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `mount-CdYs6Lai.css` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `mount-DFK9zqlK.js` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `zh-CbWSriuu.js` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
