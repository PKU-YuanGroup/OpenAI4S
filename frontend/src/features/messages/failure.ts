/** Shared live/reopened failure details and explicit continuation. */
import { isReady } from "../../compat/stub";
import { LANG, t } from "../../i18n/runtime";
import { currentId } from "../../stores/session";
import { openCustomize } from "../customize";
import { running } from "../../stores/stream";
import { hint } from "../sessions/chrome";
import { el } from "./dom";

type Failure = { code?: unknown; output_committed?: unknown; request_id?: unknown };

// Feature-local copy: the shared en.ts/zh.ts dictionaries are frozen extracts.
const COPY: Record<"en" | "zh", Record<string, string>> = {
  en: {
    llm_stream_timeout: "The upstream stopped sending data and the stream timed out. Completed work is preserved. Continue to finish the remaining work.",
    llm_stream_interrupted: "The response stream was interrupted. Completed work is preserved. Continue to finish the remaining work.",
    llm_deadline_exceeded: "The model call reached its total time limit. Completed work is preserved. Continue with a narrower next step.",
    llm_response_too_large: "The model response exceeded its size limit. Completed work is preserved. Continue with smaller output or tool arguments.",
    no_progress: "Stopped repeating actions. Continue with the recorded results and ask the model to choose a different approach.",
    llm_no_credential: "No API key is configured for the model. Add one in Settings → Models, then send again.",
    llm_auth_rejected: "The model provider rejected the API key. Check it in Settings → Models.",
    llm_quota_exhausted: "The model account is out of quota. Top up with the provider, or pick another model in Settings → Models.",
    llm_model_not_found: "The model provider has no such model or route. Check the model name and base URL in Settings → Models.",
    // The server's own sentence for this code (`llm/models.py`): one wording.
    llm_network:
      "The model service could not be reached, or the request was interrupted. This is usually a passing network problem: send the message again in a moment. If it keeps failing, check the network, any proxy, and the base URL in Settings → Models.",
    llm_authentication_failed: "The model service did not accept the credentials (401). Check the API key in Settings → Models.",
    llm_access_denied: "The model service refused access (403). Check that this key may use the model and endpoint in Settings → Models.",
    llm_model_unavailable: "The model service reports that this model is not available. Check the model id in Settings → Models.",
    llm_request_target_not_found: "The model service found nothing at the requested address (404). Check the endpoint and the model id in Settings → Models.",
    llm_endpoint_unreachable: "The model endpoint refused the connection or its host name did not resolve, so no request was sent. Check the base URL in Settings → Models.",
    openModels: "Open model settings",
    retry: "Retry",
    supportLog: "Details: logs/support.log in the data directory.",
    continue: "Continue",
    draft: "Send or clear the draft in the composer first: this button sends its own message.",
    prompt: "Continue from the completed work. Follow the stopped-turn recovery guidance, choose a different approach if the last attempt repeated itself, and finish only the remaining work.",
    continued: "Continued · the platform asked the model to build on the recorded results and finish only what is left",
  },
  zh: {
    llm_stream_timeout: "上游长时间未发送数据，流式响应已超时。已完成的工作已保留，可继续处理未完成部分。",
    llm_stream_interrupted: "流式响应中断。已完成的工作已保留，可继续处理未完成部分。",
    llm_deadline_exceeded: "模型调用已达到总时限。已完成的工作已保留，可缩小范围后继续。",
    llm_response_too_large: "模型响应超过大小限制。已完成的工作已保留，可缩小输出或工具参数后继续。",
    no_progress: "已停止重复动作。继续时将携带已有结果，并要求模型换一种方法。",
    llm_no_credential: "还没有配置模型的 API Key。请在 设置 → 模型 填写后再发送。",
    llm_auth_rejected: "模型服务拒绝了这个 API Key。请在 设置 → 模型 检查。",
    llm_quota_exhausted: "模型账户的额度已用完。请到服务商处充值，或在 设置 → 模型 换一个模型。",
    llm_model_not_found: "模型服务找不到这个模型或接口。请在 设置 → 模型 检查模型名称和服务地址。",
    llm_network:
      "无法连接到模型服务（或请求中途断开）。这通常是暂时的网络问题：稍后重新发送即可。如果一直失败，再检查网络、代理，以及 设置 → 模型 里的服务地址。",
    llm_authentication_failed: "模型服务未接受身份凭据（401）。请在 设置 → 模型 检查 API Key。",
    llm_access_denied: "模型服务拒绝了访问（403）。请在 设置 → 模型 检查这个 Key 是否有权使用该模型和服务地址。",
    llm_model_unavailable: "模型服务报告这个模型不可用。请在 设置 → 模型 检查模型 ID。",
    llm_request_target_not_found: "模型服务上找不到所请求的地址（404）。请在 设置 → 模型 检查服务地址和模型 ID。",
    llm_endpoint_unreachable: "模型服务地址拒绝连接或域名无法解析，请求没有发出。请在 设置 → 模型 检查服务地址。",
    openModels: "打开模型设置",
    retry: "重试",
    supportLog: "详情见数据目录下的 logs/support.log。",
    continue: "继续",
    draft: "请先发送或清空输入框里的草稿：这个按钮会发送它自己的消息。",
    prompt: "请基于已完成的工作继续，遵循中断恢复提示；如果上次陷入重复，请换一种方法，只处理尚未完成的部分。",
    continued: "已继续 · 平台已要求模型利用已有结果，只处理尚未完成的部分",
  },
};

/**
 * Whether a user-role row is the text Continue sends. The turn needs a
 * message to start from, so the button sends one, but the user pressed a
 * button and did not write it: shown as their bubble it read as something
 * they had said.
 */
export function isContinuePrompt(text: unknown): boolean {
  const value = String(text ?? "").trim();
  return value !== "" && (value === COPY.en.prompt || value === COPY.zh.prompt);
}

/** The one muted platform line that stands in for that bubble, live and on reopen. */
export function continueMarker(): HTMLElement {
  const box = el("div", "msg-plan-seed msg-continue-seed");
  box.dataset.continueSeed = "1";
  // What was actually sent stays one hover away.
  box.title = COPY[LANG].prompt || COPY.en.prompt!;
  box.appendChild(el("span", "lbl", COPY[LANG].continued || COPY.en.continued!));
  return box;
}

export function canContinueFailure(code: unknown): boolean {
  // The two local-limit codes sit here for the same reason as the upstream
  // ones: the turn stopped part-way with its completed work recorded, and
  // `recovery_message` emits the matching "do not replay" note for them.
  return [
    "llm_stream_timeout",
    "llm_stream_interrupted",
    "llm_deadline_exceeded",
    "llm_response_too_large",
    "no_progress",
  ].includes(String(code || ""));
}

/**
 * M-37: a lost connection is usually passing, and its sentence says to try
 * again; the bubble offers that as a button. It sends what Continue sends, so
 * work the turn had already done is built on rather than repeated.
 */
export function canRetryFailure(code: unknown): boolean {
  return String(code || "") === "llm_network";
}

/**
 * Failures fixed in Settings → Models, not by trying again: no key, a
 * rejected key, no quota, no such model, no connection (`llm_error_kind`).
 * NV-05 split the first version's rejected key / missing model / no
 * connection by what the daemon can prove (401, 403, a listed 404 code, any
 * other 404, a refusal before sending); rows stored with the first version's
 * codes keep their own sentence and the same button.
 */
export function needsModelSettings(code: unknown): boolean {
  return [
    "llm_no_credential",
    "llm_auth_rejected",
    "llm_quota_exhausted",
    "llm_model_not_found",
    "llm_network",
    "llm_authentication_failed",
    "llm_access_denied",
    "llm_model_unavailable",
    "llm_request_target_not_found",
    "llm_endpoint_unreachable",
  ].includes(String(code || ""));
}

export function failureCodeHint(code: unknown): string {
  if (canContinueFailure(code) || needsModelSettings(code)) return COPY[LANG][String(code)] || "";
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
  const setup = needsModelSettings(failure.code);
  // A recoverable stop already explains itself in this same bubble: the
  // gateway's tail text is the row's content, live and on reopen. Repeating
  // the cause here put two near-identical sentences back to back. The same
  // holds for a failure fixed in Settings, whose bubble says what to do.
  const cause = recoverable || setup ? "" : failureCodeHint(failure.code);
  if (cause) bits.push(cause);
  if (failure.output_committed && !recoverable) bits.push(t("turn.failedCommitted"));
  // The id is only worth quoting if it leads somewhere: the daemon writes one
  // line per id there, whether or not structured logs are on.
  if (failure.request_id) bits.push(t("turn.supportId", String(failure.request_id).slice(0, 96)), COPY[LANG].supportLog || "");
  box.textContent = bits.join(" ");
  box.dataset.requestId = failure.request_id ? String(failure.request_id).slice(0, 96) : "";
  box.dataset.failureCode = failure.code ? String(failure.code).slice(0, 64) : "";
  if (failure.output_committed) box.dataset.committed = "1";
  // Trying again comes first for a lost connection; Settings is for one that keeps failing.
  if (canRetryFailure(failure.code)) {
    box.appendChild(continueButton(box, COPY[LANG].retry || COPY.en.retry!, " turn-retry"));
  }
  if (setup) {
    // The sentence names Settings → Models; this is the way there. It stays
    // on an older row too: unlike Continue, it submits nothing.
    const open = el("button", "outline-btn small turn-open-models", COPY[LANG].openModels) as HTMLButtonElement;
    open.type = "button";
    open.onclick = () => void openCustomize("models");
    box.appendChild(open);
  }
  if (recoverable) box.appendChild(continueButton(box, COPY[LANG].continue || COPY.en.continue!, ""));
  return box;
}

/**
 * The button that sends the continuation: Continue after a recoverable stop,
 * Retry after a lost connection. Only the newest failure may submit a turn.
 */
function continueButton(box: HTMLElement, label: string, extraClass: string): HTMLButtonElement {
  const frame = currentId.value;
  // messages.css hides this once a later row exists; the handler below still
  // refuses, and retires the button, in case that rule did not apply.
  const button = el("button", "outline-btn small turn-continue" + extraClass, label) as HTMLButtonElement;
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
  return button;
}
