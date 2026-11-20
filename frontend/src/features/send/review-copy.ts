/**
 * M-75: what an on-demand review does, and why one did not produce a result,
 * in the UI language.
 *
 * "Request review" started at once and, ~40 s later, could end as a red
 * "Review unavailable" card with an empty body. The server now refuses up
 * front what cannot produce a result (`review_<reason>` codes) and puts a
 * `reason` on a review that started but ended without a verdict; these turn
 * both into a sentence that says what happened and what to do.
 */

import { copyLookup, type CopyTable } from "../../i18n/copy";

const COPY: CopyTable = {
  en: {
    "review.menuDesc":
      "A second model checks the latest answer against the recorded code, outputs and files. One model call, usually under a minute.",
    "review.refused.nothing_to_review":
      "Nothing to review yet: this session has no message, code or file. Ask the agent something first.",
    "review.failed.no_model":
      "The reviewer model has no API key. Add one in Customize → Models, or choose another reviewer model in the session options.",
    "review.failed.model_unreachable":
      "The reviewer model did not answer in time or could not be reached. Try again later, or choose a faster reviewer model in the session options.",
    "review.failed.model_error":
      "The reviewer model returned an error. The details are below; try again, or choose another reviewer model in the session options.",
    "review.failed.unusable_answer":
      "The reviewer answered, but not in the form a review needs, so there is no verdict. Try again, or choose another reviewer model.",
    "review.failed.internal": "The review could not finish. The details are below.",
    "review.failed.details": "Details",
  },
  zh: {
    "review.menuDesc": "由另一个模型对照已记录的代码、输出和文件检查最新的回答。调用一次模型，通常不到一分钟。",
    "review.refused.nothing_to_review": "还没有可审阅的内容：本会话没有消息、代码或文件。请先向代理提问。",
    "review.failed.no_model": "审阅模型没有配置 API 密钥。请在「自定义 → 模型」中添加，或在会话选项里换一个审阅模型。",
    "review.failed.model_unreachable": "审阅模型没有及时回应或无法连接。请稍后重试，或在会话选项里换一个更快的审阅模型。",
    "review.failed.model_error": "审阅模型返回了错误，详情见下方。请重试，或在会话选项里换一个审阅模型。",
    "review.failed.unusable_answer": "审阅模型有回复，但不是审阅所需的格式，因此没有结论。请重试，或换一个审阅模型。",
    "review.failed.internal": "审阅没能完成，详情见下方。",
    "review.failed.details": "详情",
  },
};

export const reviewT = copyLookup(COPY);

const REFUSALS = new Set(["nothing_to_review"]);
const FAILURES = new Set(["no_model", "model_unreachable", "model_error", "unusable_answer", "internal"]);

/** The hint for a refused "Request review", or the server's own message. */
export function reviewRefusalText(error: unknown): string {
  const code = String((error as { code?: unknown } | null)?.code || "");
  const reason = code.startsWith("review_") ? code.slice("review_".length) : "";
  if (REFUSALS.has(reason)) return reviewT(`review.refused.${reason}`);
  return error instanceof Error ? error.message : String(error ?? "");
}

/** What a review that ended without a verdict means, by its `reason`. */
export function reviewFailureText(reason: unknown): string {
  const key = FAILURES.has(String(reason)) ? String(reason) : "internal";
  return reviewT(`review.failed.${key}`);
}
