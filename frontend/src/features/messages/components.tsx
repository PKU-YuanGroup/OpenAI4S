import { currentId, _openGen, historyLoad } from "../../stores/session";
import { historyT as t } from "./copy";
import { recoverConversation } from "./open";

/** Read feedback lives outside the transcript replaced by imperative history renders. */
export function HistoryLoadStatus() {
  const state = historyLoad.value;
  if (!state || state.fid !== currentId.value || state.generation !== _openGen.value || state.status === "loaded") return null;
  return (
    <div className="history-load-status" role="status" aria-live="polite" data-history-state={state.status}>
      <span>{t("history." + state.status)}</span>
      {Object.entries(state.errors).map(([part, message]) => <p key={part}>{t("history.part." + part)}: {message}</p>)}
      {state.status !== "loading" && <button type="button" className="outline-btn small"
        onClick={() => { void recoverConversation(state.fid, state.generation); }}>{t("history.retry")}</button>}
    </div>
  );
}
