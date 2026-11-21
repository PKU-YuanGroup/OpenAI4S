import { copyLookup, type CopyTable } from "../i18n/copy";

/**
 * ART-6 copy for the 3D structure viewer. The original tag and style keys
 * (`mol.tag`, `mol.styleLabel`, `mol.style.*`) stay in the F-07 dictionaries.
 * Colour schemes, reset view, residue labels, structure facts and the mouse
 * hint live here so we do not rewrite the generated `i18n/en.ts` / `zh.ts`.
 * The dictionaries' `mol.foot` said Shift+drag pans; in the vendored 3Dmol it
 * zooms and Ctrl+drag pans, so `mol.hint` replaces it.
 */
const COPY: CopyTable = {
  zh: {
    "mol.color.label": "着色：",
    "mol.color.default": "默认",
    "mol.color.chain": "按链",
    "mol.color.bfactor": "B 因子",
    "mol.color.chain.none": "此文件没有链标识",
    "mol.color.bfactor.none": "此文件没有可用的 B 因子（缺失或全部相同）",
    "mol.legend.bfactor": "B 因子：蓝色低，红色高",
    "mol.reset": "重置视角",
    "mol.reset.title": "恢复打开时的方向和缩放",
    "mol.labels.clear": "清除标签",
    "mol.labels.clearCount": "清除标签（{0}）",
    "mol.atoms": "{0} 个原子",
    "mol.atoms.one": "1 个原子",
    "mol.chains": "{0} 条链（{1}）",
    "mol.chains.one": "1 条链（{0}）",
    "mol.chains.many": "{0} 条链",
    "mol.chains.sep": "、",
    "mol.hint": "拖动旋转 • 滚轮缩放 • Ctrl+拖动平移 • 点击残基添加或移除标签",
    "mol.large.load": "仍然加载",
    "mol.large.loadSize": "仍然加载（{0}）",
    "mol.large.title": "以线条样式加载整个结构；大文件可能需要一些时间",
    "mol.large.noSurface": "这个结构太大，不提供表面样式",
  },
  en: {
    "mol.color.label": "Color:",
    "mol.color.default": "Default",
    "mol.color.chain": "By chain",
    "mol.color.bfactor": "B-factor",
    "mol.color.chain.none": "This file has no chain identifiers",
    "mol.color.bfactor.none": "This file has no usable B-factors (missing or all equal)",
    "mol.legend.bfactor": "B-factor: blue is low, red is high",
    "mol.reset": "Reset view",
    "mol.reset.title": "Restore the orientation and zoom the structure opened with",
    "mol.labels.clear": "Clear labels",
    "mol.labels.clearCount": "Clear labels ({0})",
    "mol.atoms": "{0} atoms",
    "mol.atoms.one": "1 atom",
    "mol.chains": "{0} chains ({1})",
    "mol.chains.one": "1 chain ({0})",
    "mol.chains.many": "{0} chains",
    "mol.chains.sep": ", ",
    "mol.hint": "Drag to rotate • Scroll to zoom • Ctrl+drag to pan • Click a residue to add or remove its label",
    "mol.large.load": "Load anyway",
    "mol.large.loadSize": "Load anyway ({0})",
    "mol.large.title": "Loads the whole structure as lines; a large file can take a while",
    "mol.large.noSurface": "This structure is too large for the surface style",
  },
};

export const molT = copyLookup(COPY);
