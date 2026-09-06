/**
 * Attachment / @-ref problem cards. Port of app.js:13035-13096.
 *
 * Not one card: attachments carry {name, reason, limit, bytes} and the client
 * owns the wording; refs carry {ref, code, message} and the server owns it.
 */

import { t } from "../../i18n/runtime";
import { bytes } from "../artifacts/api";
import { $, el, messagesHost } from "../messages/dom";
import { down } from "../messages/scroll";
import { publicText } from "../scrub/scrub";

const ATTACH_REASONS: Record<string, string> = {
  version_changed: "attach.versionChanged",
  not_found: "attach.notFound",
  unsupported_type: "attach.unsupported",
  decode_failed: "attach.decodeFailed",
};

export function renderAttachmentProblems(problems: unknown): void {
  if (!Array.isArray(problems) || !problems.length) return;
  const messages = messagesHost() || $("#messages");
  if (!messages) return;
  const card = el("div", "ref-problems");
  card.appendChild(el("div", "ref-problems-head", t("attach.problemsTitle", problems.length)));
  problems.slice(0, 8).forEach((raw) => {
    const p = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
    const row = el("div", "ref-problem");
    row.appendChild(el("code", "ref-problem-ref", publicText(p.name || "", 80)));
    const reason = String(p.reason || "");
    const limit = Number(p.limit) || 0;
    const reasonKey = ATTACH_REASONS[reason];
    const detail =
      reason === "too_large"
        ? t("attach.tooLarge", bytes(Number(p.bytes) || 0), bytes(limit))
        : reason === "budget_exhausted"
          ? t("attach.budget", bytes(limit))
          : reason === "too_many"
            ? t("attach.tooMany", limit)
            : reasonKey
              ? t(reasonKey)
              : reason;
    row.appendChild(el("span", "ref-problem-msg", detail));
    card.appendChild(row);
  });
  messages.appendChild(card);
  down();
}

export function renderRefProblems(problems: unknown): void {
  if (!Array.isArray(problems) || !problems.length) return;
  const messages = messagesHost() || $("#messages");
  if (!messages) return;
  const card = el("div", "ref-problems");
  card.appendChild(el("div", "ref-problems-head", t("refs.problemsTitle", problems.length)));
  problems.slice(0, 8).forEach((raw) => {
    const p = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
    const row = el("div", "ref-problem");
    row.appendChild(el("code", "ref-problem-ref", "@" + String(p.ref || "")));
    // The server's message, not a code lookup: it already names the file and
    // says what to do, and re-deriving it here is how the two drift apart.
    row.appendChild(el("span", "ref-problem-msg", String(p.message || "")));
    card.appendChild(row);
  });
  messages.appendChild(card);
  down();
}
