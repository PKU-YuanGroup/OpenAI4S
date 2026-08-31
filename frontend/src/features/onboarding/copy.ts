import { LANG, tOptional } from "../../i18n/runtime";

/**
 * M-01 copy. Existing model/readiness keys stay in the F-07 dictionaries.
 * New wizard strings live here so we do not rewrite generated i18n/en.ts / zh.ts.
 */
const COPY: Record<"zh" | "en", Record<string, string>> = {
  zh: {
    "onboarding.title": "首次设置",
    "onboarding.subtitle": "四个必需步骤。在你按下「测试连接」之前，不会向模型供应商发请求。",
    "onboarding.skip": "暂时跳过",
    "onboarding.checklist": "清单",
    "onboarding.checklist.title": "首次设置清单",
    "onboarding.checklist.hint": "可按任意顺序完成。不必回到上一步也能创建或打开项目。",
    "onboarding.next": "继续",
    "onboarding.finish": "完成",
    "onboarding.retry": "重试",
    "onboarding.step.path": "选择模型路径",
    "onboarding.step.test": "测试连接",
    "onboarding.step.readiness": "环境与网络",
    "onboarding.step.project": "创建或打开项目",
    "onboarding.path.existing": "已有配置",
    "onboarding.path.cloud": "云端协议",
    "onboarding.path.local": "本机目录（未扫描，不开套接字）",
    "onboarding.path.empty": "还没有配置。选一个协议或本机端点。",
    "onboarding.path.choose": "请先选择一条模型路径。",
    "onboarding.path.localModel": "本机模型 id（目录不探测，需手填）",
    "onboarding.test.warning":
      "按下「测试连接」会向你的模型供应商发出 1–2 个极小、无副作用的请求。在此之前，供应商请求数为 0。",
    "onboarding.test.idle": "尚未向供应商发出任何请求。",
    "onboarding.test.needProfile": "测试需要一个已保存的模型配置。",
    "onboarding.readiness.network": "网络姿态（本地标志，未出站）",
    "onboarding.readiness.allowNetwork": "允许网络：{0}",
    "onboarding.readiness.egress": "egress：{0}",
    "onboarding.readiness.contacted": "已联系外部：{0}",
    "onboarding.readiness.platform": "平台：{0}",
    "onboarding.readiness.runtimeYes": "本机 Python/R 运行时受支持。",
    "onboarding.readiness.runtimeNo": "本机 Python/R 运行时不受支持。",
    "onboarding.readiness.yes": "是",
    "onboarding.readiness.no": "否",
    "onboarding.project.open": "打开此项目",
    "onboarding.project.empty": "还没有项目。创建一个即可进入工作台。",
    "onboarding.project.needName": "请输入项目名称。",
    "onboarding.load.err": "无法加载首次设置：{0}",
  },
  en: {
    "onboarding.title": "First-run setup",
    "onboarding.subtitle":
      "Four required steps. No provider request is made until you press Test.",
    "onboarding.skip": "Skip for now",
    "onboarding.checklist": "Checklist",
    "onboarding.checklist.title": "First-run checklist",
    "onboarding.checklist.hint":
      "Complete these in any order. You can create or open a project without going back.",
    "onboarding.next": "Continue",
    "onboarding.finish": "Done",
    "onboarding.retry": "Retry",
    "onboarding.step.path": "Choose a model path",
    "onboarding.step.test": "Test the connection",
    "onboarding.step.readiness": "Environment and network",
    "onboarding.step.project": "Create or open a project",
    "onboarding.path.existing": "Existing profile",
    "onboarding.path.cloud": "Cloud protocol",
    "onboarding.path.local": "Local catalogue (not scanned; no sockets)",
    "onboarding.path.empty": "No profile yet. Pick a protocol or a local endpoint.",
    "onboarding.path.choose": "Choose a model path first.",
    "onboarding.path.localModel": "Local model id (the catalogue does not probe; type it)",
    "onboarding.test.warning":
      "Pressing Test sends 1–2 tiny, side-effect-free requests to your model provider. Until then, provider requests stay at 0.",
    "onboarding.test.idle": "No provider request has been made yet.",
    "onboarding.test.needProfile": "Test needs a saved model profile.",
    "onboarding.readiness.network": "Network posture (local flags, nobody contacted)",
    "onboarding.readiness.allowNetwork": "Allow network: {0}",
    "onboarding.readiness.egress": "egress: {0}",
    "onboarding.readiness.contacted": "Contacted anyone: {0}",
    "onboarding.readiness.platform": "Platform: {0}",
    "onboarding.readiness.runtimeYes": "Native Python/R runtime is supported on this OS.",
    "onboarding.readiness.runtimeNo": "Native Python/R runtime is not supported on this OS.",
    "onboarding.readiness.yes": "yes",
    "onboarding.readiness.no": "no",
    "onboarding.project.open": "Open this project",
    "onboarding.project.empty": "No projects yet. Create one to enter the workbench.",
    "onboarding.project.needName": "Enter a project name.",
    "onboarding.load.err": "Could not load first-run setup: {0}",
  },
};

export function ot(key: string, ...args: unknown[]): string {
  const fromDict = tOptional(key);
  let s = fromDict != null ? fromDict : COPY[LANG]?.[key] || COPY.en[key] || key;
  if (args.length) {
    s = String(s).replace(/\{(\d+)\}/g, (m, i) =>
      args[+i] != null ? String(args[+i]) : m,
    );
  }
  return s;
}
