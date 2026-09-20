import { LANG, tOptional } from "../../i18n/runtime";

/**
 * Experimental judgment copy. New strings live here so we do not rewrite
 * the generated `i18n/en.ts` / `zh.ts` extract.
 */
const COPY: Record<"zh" | "en", Record<string, string>> = {
  zh: {
    "judgment.title": "语义判断层",
    "judgment.badge": "Experimental",
    "judgment.intro":
      "默认关闭。需要自备 TypeSafe API key。服务托管在美国；敏感数据不要开启。",
    "judgment.master": "启用实验特性",
    "judgment.masterDesc": "打开后才会向 TypeSafe 发送判断请求。",
    "judgment.source": "来源：{0}",
    "judgment.source.env_off": "环境变量强制关闭",
    "judgment.source.env_on": "环境变量开启",
    "judgment.source.setting": "设置",
    "judgment.source.default": "默认关闭",
    "judgment.source.master_off": "总开关关闭",
    "judgment.source.no_disclosure": "尚未确认数据披露",
    "judgment.envOff": "已被环境变量强制关闭",
    "judgment.cap.skill_suggest": "Skill 推荐",
    "judgment.cap.literature_check": "文献核验",
    "judgment.cap.text_features": "文本特征",
    "judgment.cap.safety_shadow": "安全影子判定",
    "judgment.cap.task_mode_shadow": "任务模式影子",
    "judgment.safetyWarn": "此项会发送待执行的代码，风险最高。",
    "judgment.disclosure.title": "数据披露确认",
    "judgment.disclosure.intro":
      "首次启用前请逐项确认会发送到 api.typesafe.ai 的内容。文案改版后需要重新确认。",
    "judgment.disclosure.confirm": "确认并启用",
    "judgment.disclosure.cancel": "取消",
    "judgment.disclosure.needAll": "请勾选每一项后再确认。",
    "judgment.key": "TypeSafe API key",
    "judgment.keyPh": "不会回显已保存的密钥",
    "judgment.keyConfigured": "已配置",
    "judgment.keyMissing": "未配置",
    "judgment.keySave": "保存",
    "judgment.keyClear": "清除",
    "judgment.keySaved": "密钥已保存",
    "judgment.keyCleared": "密钥已清除",
    "judgment.keyRequired": "请输入密钥后再保存。",
    "judgment.test": "测试连接",
    "judgment.testIdle": "尚未测试。",
    "judgment.testStatus": "状态：{0}",
    "judgment.testError": "error_code：{0}",
    "judgment.testLatency": "延迟：{0} ms",
    "judgment.status": "状态",
    "judgment.provider": "后端：{0}",
    "judgment.model": "模型：{0}",
    "judgment.egress": "出网：{0}",
    "judgment.egressAllowed": "已授权 api.typesafe.ai",
    "judgment.egressNotListed": "未授权 api.typesafe.ai",
    "judgment.loadFailed": "无法加载实验设置：{0}",
    "judgment.saveFailed": "无法保存：{0}",
    "judgment.chips": "实验性推荐",
    "judgment.chipFit": "p_fit {0}",
    "judgment.chipConf": "confidence {0}",
    "judgment.status.unavailable": "语义推荐暂不可用。",
    "judgment.status.uncertain": "语义推荐不确定，请结合词法结果判断。",
    "judgment.ackVersion": "披露版本 {0}",
  },
  en: {
    "judgment.title": "Semantic judgment",
    "judgment.badge": "Experimental",
    "judgment.intro":
      "Off by default. You must supply your own TypeSafe API key. The service is hosted in the United States; do not enable it for sensitive data.",
    "judgment.master": "Enable experimental feature",
    "judgment.masterDesc": "Judgment requests are sent to TypeSafe only when this is on.",
    "judgment.source": "Source: {0}",
    "judgment.source.env_off": "forced off by environment variable",
    "judgment.source.env_on": "enabled by environment variable",
    "judgment.source.setting": "settings",
    "judgment.source.default": "default off",
    "judgment.source.master_off": "master switch off",
    "judgment.source.no_disclosure": "disclosure not acknowledged",
    "judgment.envOff": "Forced off by an environment variable",
    "judgment.cap.skill_suggest": "Skill suggestions",
    "judgment.cap.literature_check": "Literature check",
    "judgment.cap.text_features": "Text features",
    "judgment.cap.safety_shadow": "Safety shadow",
    "judgment.cap.task_mode_shadow": "Task-mode shadow",
    "judgment.safetyWarn": "This sends the pending code cell. Highest outbound risk.",
    "judgment.disclosure.title": "Data-disclosure acknowledgement",
    "judgment.disclosure.intro":
      "Before the first enable, acknowledge each kind of data sent to api.typesafe.ai. A copy change requires a new acknowledgement.",
    "judgment.disclosure.confirm": "Acknowledge and enable",
    "judgment.disclosure.cancel": "Cancel",
    "judgment.disclosure.needAll": "Check every item before confirming.",
    "judgment.key": "TypeSafe API key",
    "judgment.keyPh": "Saved keys are never shown again",
    "judgment.keyConfigured": "Configured",
    "judgment.keyMissing": "Not configured",
    "judgment.keySave": "Save",
    "judgment.keyClear": "Clear",
    "judgment.keySaved": "Key saved",
    "judgment.keyCleared": "Key cleared",
    "judgment.keyRequired": "Enter a key before saving.",
    "judgment.test": "Test connection",
    "judgment.testIdle": "Not tested yet.",
    "judgment.testStatus": "status: {0}",
    "judgment.testError": "error_code: {0}",
    "judgment.testLatency": "latency: {0} ms",
    "judgment.status": "Status",
    "judgment.provider": "provider: {0}",
    "judgment.model": "model: {0}",
    "judgment.egress": "egress: {0}",
    "judgment.egressAllowed": "api.typesafe.ai is authorized",
    "judgment.egressNotListed": "api.typesafe.ai is not authorized",
    "judgment.loadFailed": "Could not load experimental settings: {0}",
    "judgment.saveFailed": "Could not save: {0}",
    "judgment.chips": "Experimental suggestions",
    "judgment.chipFit": "p_fit {0}",
    "judgment.chipConf": "confidence {0}",
    "judgment.status.unavailable": "Semantic suggestions are unavailable.",
    "judgment.status.uncertain":
      "Semantic suggestions are uncertain; use the lexical results as well.",
    "judgment.ackVersion": "disclosure version {0}",
  },
};

export function judgmentT(key: string, ...args: unknown[]): string {
  const fromDict = tOptional(key);
  let s = fromDict != null ? fromDict : COPY[LANG]?.[key] || COPY.en[key] || key;
  if (args.length) {
    s = String(s).replace(/\{(\d+)\}/g, (m, i) =>
      args[+i] != null ? String(args[+i]) : m,
    );
  }
  return s;
}

export function judgmentSourceLabel(source: string): string {
  const key = "judgment.source." + source;
  const mapped = COPY[LANG]?.[key] || COPY.en[key];
  return mapped || source;
}
