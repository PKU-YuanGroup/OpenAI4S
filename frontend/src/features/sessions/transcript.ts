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
import { blankMentionTokens, composerMentions, type ComposerMention } from "../autocomplete/mentions";
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
  const found: Array<{ name: string; version: string; pin?: ComposerMention }> = [];
  const seen = new Set<string>();
  // Files the `@` menu pinned: the text says `@name`, the version is kept
  // beside it (ART-16). Their tokens are blanked before the scan below, so a
  // name with a space in it is not also read as a shorter, unresolved one.
  const pins = composerMentions(text);
  for (const pin of pins) {
    const key = pin.filename + "#" + pin.version_id;
    if (seen.has(key) || found.length >= 8) continue;
    seen.add(key);
    found.push({ name: pin.filename, version: pin.version_id, pin });
  }
  const re = /(?:^|\s)@([^\s@#]+)(?:#(v-[A-Za-z0-9_-]+))?/g;
  const rest = blankMentionTokens(text, pins);
  let m: RegExpExecArray | null;
  while (found.length < 8 && (m = re.exec(rest)) !== null) {
    const key = (m[1] || "") + "#" + (m[2] || "");
    if (seen.has(key)) continue;
    seen.add(key);
    found.push({ name: m[1] || "", version: m[2] || "" });
  }
  if (!found.length) {
    host.classList.add("hidden");
    return;
  }
  const pool = [...((artifacts.value || []) as Array<Record<string, unknown>>)];
  found.forEach((ref) => {
    const pin = ref.pin;
    const match = pin
      ? pool.find((a) => a && pin.artifact_id && (a.artifact_id || a.id) === pin.artifact_id)
      : pool.find(
          (a) =>
            a &&
            a.filename === ref.name &&
            (!ref.version || String(a.version_id || "") === ref.version),
        );
    // A pin is resolved by construction: the menu offered that exact version,
    // possibly from another session, which this session's list cannot show.
    const resolved = !!(match || pin);
    const chip = el("span", "msg-ref-chip" + (resolved ? "" : " unresolved"));
    chip.appendChild(iconEl(resolved ? "file-text" : "alert-triangle", 11));
    chip.appendChild(el("span", null, publicText(ref.name, 60)));
    if (!resolved) {
      chip.title = t("refs.unresolvedChip");
      host.appendChild(chip);
      return;
    }
    const parts = [String(ref.version || (match && match.version_id) || "")];
    if (match && match.checksum && String(match.version_id || "") === parts[0]) {
      parts.push("sha256:" + String(match.checksum).slice(0, 12));
    }
    const root = String((pin && pin.root_frame_id) || (match && match.root_frame_id) || "");
    const elsewhere = root && currentId.value && root !== currentId.value;
    if (elsewhere) parts.push("↗ " + root.slice(0, 12));
    chip.title = parts.filter(Boolean).join(" · ");
    if (elsewhere) chip.classList.add("elsewhere");
    if (match) {
      chip.classList.add("clickable");
      chip.onclick = () => {
        callLane("openViewer", match);
      };
    }
    host.appendChild(chip);
  });
  host.classList.remove("hidden");
}
