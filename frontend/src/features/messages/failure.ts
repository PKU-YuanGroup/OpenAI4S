/** Shared live/reopened failure details and explicit continuation. */
import { isReady } from "../../compat/stub";
import { LANG, t } from "../../i18n/runtime";
import { currentId } from "../../stores/session";
import { running } from "../../stores/stream";
import { hint } from "../sessions/chrome";
import { el } from "./dom";

type Failure = { code?: unknown; output_committed?: unknown; request_id?: unknown };

// Feature-local copy: the shared en.ts/zh.ts dictionaries are frozen extracts.
const COPY: Record<"en" | "zh", Record<string, string>> = {
  en: {
    llm_stream_timeout: "The upstream stopped sending data and the stream timed out. Completed work is preserved. Continue to finish the remaining work.",
    llm_stream_interrupted: "The response stream was interrupted. Completed work is preserved. Continue to finish the remaining work.",
    no_progress: "Stopped repeating actions. Continue with the recorded results and ask the model to choose a different approach.",
    continue: "Continue",
    draft: "Send or clear the draft in the composer first; Continue sends its own message.",
    prompt: "Continue from the completed work. Follow the stopped-turn recovery guidance, choose a different approach if the last attempt repeated itself, and finish only the remaining work.",
  },
  zh: {
    llm_stream_timeout: "上游长时间未发送数据，流式响应已超时。已完成的工作已保留，可继续处理未完成部分。",
    llm_stream_interrupted: "流式响应中断。已完成的工作已保留，可继续处理未完成部分。",
    no_progress: "已停止重复动作。继续时将携带已有结果，并要求模型换一种方法。",
    continue: "继续",
    draft: "请先发送或清空输入框里的草稿；“继续”会发送它自己的消息。",
    prompt: "请基于已完成的工作继续，遵循中断恢复提示；如果上次陷入重复，请换一种方法，只处理尚未完成的部分。",
  },
};

export function canContinueFailure(code: unknown): boolean {
  return ["llm_stream_timeout", "llm_stream_interrupted", "no_progress"].includes(String(code || ""));
}

export function failureCodeHint(code: unknown): string {
  if (canContinueFailure(code)) return COPY[LANG][String(code)] || "";
  const key = ({
    llm_request_burst: "turn.failure.llmRequestBurst",
    llm_rate_limited: "turn.failure.llmRateLimited",
    llm_upstream_overloaded: "turn.failure.llmUpstreamOverloaded",
  } as Record<string, string>)[String(code || "")];
  return key ? t(key) : "";
}

export function failureMeta(failure: Failure): HTMLElement {
  const box = el("div", "msg-failure-meta");
  const bits: string[] = [];
  const recoverable = canContinueFailure(failure.code);
  // A recoverable stop already explains itself in this same bubble: the
  // gateway's tail text is the row's content, live and on reopen. Repeating
  // the cause here put two near-identical sentences back to back.
  const cause = recoverable ? "" : failureCodeHint(failure.code);
  if (cause) bits.push(cause);
  if (failure.output_committed && !recoverable) bits.push(t("turn.failedCommitted"));
  if (failure.request_id) bits.push(t("turn.supportId", String(failure.request_id).slice(0, 96)));
  box.textContent = bits.join(" ");
  box.dataset.requestId = failure.request_id ? String(failure.request_id).slice(0, 96) : "";
  box.dataset.failureCode = failure.code ? String(failure.code).slice(0, 64) : "";
  if (failure.output_committed) box.dataset.committed = "1";
  if (recoverable) {
    const frame = currentId.value;
    // messages.css hides this once a later row exists; the handler below still
    // refuses, and retires the button, in case that rule did not apply.
    const button = el("button", "outline-btn small turn-continue", COPY[LANG].continue) as HTMLButtonElement;
    button.type = "button";
    button.onclick = async () => {
      if (button.disabled || running.value) return;
      const rows = document.getElementById("messages")?.querySelectorAll(".msg");
      const stale =
        !frame ||
        currentId.value !== frame ||
        !rows?.length ||
        rows[rows.length - 1]?.querySelector(".msg-failure-meta") !== box;
      if (stale) {
        // An older failure cannot submit a new turn -- and must not keep
        // offering to. A clickable control that does nothing reads as broken.
        button.remove();
        return;
      }
      const composer = document.getElementById("composer") as HTMLTextAreaElement | null;
      if (composer?.value.trim()) {
        composer.focus();
        hint(COPY[LANG].draft, false);
        return;
      }
      const send = (globalThis as Record<string, unknown>).send;
      if (!isReady(send)) return;
      button.disabled = true;
      try {
        await (send as (text: string) => unknown)(COPY[LANG].prompt || COPY.en.prompt!);
      } finally {
        button.disabled = false;
      }
    };
    box.appendChild(button);
  }
  return box;
}
