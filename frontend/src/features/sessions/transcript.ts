/**
 * Composer @-ref chips, and the stored-row names the sessions lane has always
 * exported. app.js:7220-7409, 7766-7787.
 *
 * A stored row renders once, in `messages/list.ts`: the first page, "load
 * earlier" (sessions/messages.ts, through this module) and the live turn all
 * use that one implementation. Two copies had drifted -- the first page's
 * 👍/👎 did nothing, this one's review badge and candidate identity went
 * through window names nobody assigned.
 */

import { publicText } from "../scrub/scrub";
import { t } from "../../i18n";
import { artifacts } from "../../stores/artifacts";
import { currentId } from "../../stores/session";
import { $, el } from "./dom";
import { iconEl } from "./icon";
import { callLane } from "./lane";

export {
  addMsgActions,
  insertMessageByTime,
  renderEmptySession,
  renderMessageRefChips,
  renderStored,
} from "../messages/list";

export function renderComposerRefChips(): void {
  const host = $("#composer-refs");
  if (!host) return;
  host.innerHTML = "";
  const composer = $("#composer") as HTMLTextAreaElement | null;
  const text = (composer && composer.value) || "";
  const found: Array<{ name: string; version: string }> = [];
  const seen = new Set<string>();
  const re = /(?:^|\s)@([^\s@#]+)(?:#(v-[A-Za-z0-9_-]+))?/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const key = (m[1] || "") + "#" + (m[2] || "");
    if (seen.has(key)) continue;
    seen.add(key);
    found.push({ name: m[1] || "", version: m[2] || "" });
    if (found.length >= 8) break;
  }
  if (!found.length) {
    host.classList.add("hidden");
    return;
  }
  const pool = [...((artifacts.value || []) as Array<Record<string, unknown>>)];
  found.forEach((ref) => {
    const match = pool.find(
      (a) =>
        a &&
        a.filename === ref.name &&
        (!ref.version || String(a.version_id || "") === ref.version),
    );
    const chip = el("span", "msg-ref-chip" + (match ? "" : " unresolved"));
    chip.appendChild(iconEl(match ? "file-text" : "alert-triangle", 11));
    chip.appendChild(el("span", null, publicText(ref.name, 60)));
    if (!match) {
      chip.title = t("refs.unresolvedChip");
      host.appendChild(chip);
      return;
    }
    const parts = [String(ref.version || match.version_id || "")];
    if (match.checksum) parts.push("sha256:" + String(match.checksum).slice(0, 12));
    const elsewhere =
      match.root_frame_id && currentId.value && match.root_frame_id !== currentId.value;
    if (elsewhere) parts.push("↗ " + String(match.root_frame_id).slice(0, 12));
    chip.title = parts.filter(Boolean).join(" · ");
    if (elsewhere) chip.classList.add("elsewhere");
    chip.classList.add("clickable");
    chip.onclick = () => {
      callLane("openViewer", match);
    };
    host.appendChild(chip);
  });
  host.classList.remove("hidden");
}
