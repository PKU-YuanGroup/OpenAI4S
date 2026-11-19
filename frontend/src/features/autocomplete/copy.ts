import { copyLookup, type CopyTable } from "../../i18n/copy";

/**
 * The `/` skill picker's footer lines. Feature-local: the generated
 * dictionaries are not edited by hand.
 */
const COPY: CopyTable = {
  zh: {
    "ac.skills.all": "共 {0} 个技能 · 输入名称、说明里的词或中文关键词筛选",
    "ac.skills.allMore": "共 {0} 个技能，显示前 {1} 个 · 输入名称、说明里的词或中文关键词筛选",
    "ac.skills.one": "1 个匹配",
    "ac.skills.matches": "{0} 个匹配",
    "ac.skills.matchesMore": "显示前 {1} 个，共 {0} 个匹配 · 继续输入可缩小范围",
    "ac.skills.none": "没有匹配「{0}」的技能 · 换个说法，或用英文名称试试",
  },
  en: {
    "ac.skills.all": "{0} skills · type a name, a word from the description, or a Chinese keyword",
    "ac.skills.allMore": "{0} skills, showing the first {1} · type a name, a word from the description, or a Chinese keyword",
    "ac.skills.one": "1 match",
    "ac.skills.matches": "{0} matches",
    "ac.skills.matchesMore": "Showing {1} of {0} matches · keep typing to narrow",
    "ac.skills.none": "No skill matches “{0}” · try other words or the English name",
  },
};

export const acT = copyLookup(COPY);
