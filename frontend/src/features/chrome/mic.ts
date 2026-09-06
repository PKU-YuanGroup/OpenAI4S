/**
 * Voice dictation via SpeechRecognition. Port of app.js:10919-10932.
 */

import { t } from "../../i18n/runtime";
import { $, grow, hint } from "./dom";
import { hostFn, isReady } from "./host";

type SpeechRecCtor = new () => SpeechRec;
type SpeechRec = {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onerror: ((e: { error?: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
};

let _rec: SpeechRec | null = null;

function speechCtor(): SpeechRecCtor | null {
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecCtor;
    webkitSpeechRecognition?: SpeechRecCtor;
  };
  return w.SpeechRecognition || w.webkitSpeechRecognition || null;
}

export function micDictate(): void {
  const SR = speechCtor();
  const btn = $("#mic-btn");
  if (!SR) {
    hint(t("toast.micUnsupported"), true);
    return;
  }
  if (_rec) {
    try {
      _rec.stop();
    } catch {
      /* already stopped */
    }
    _rec = null;
    if (btn) btn.classList.remove("on");
    return;
  }
  const r = new SR();
  _rec = r;
  r.lang = navigator.language || "zh-CN";
  r.interimResults = true;
  r.continuous = true;
  const comp = $("#composer") as HTMLTextAreaElement | null;
  const base = comp ? comp.value : "";
  if (btn) btn.classList.add("on");
  hint(t("toast.micListening"));
  r.onresult = (e) => {
    let txt = "";
    for (let i = 0; i < e.results.length; i++) {
      const alt = e.results[i];
      if (alt && alt[0]) txt += alt[0].transcript;
    }
    if (comp) {
      comp.value = (base ? base + " " : "") + txt;
      const g = hostFn("grow");
      if (isReady(g)) g();
      else grow();
    }
  };
  r.onerror = (e) => {
    hint(t("toast.micError", e.error || ""), true);
    if (btn) btn.classList.remove("on");
    _rec = null;
  };
  r.onend = () => {
    if (btn) btn.classList.remove("on");
    _rec = null;
  };
  try {
    r.start();
  } catch {
    hint(t("toast.micStartFailed"), true);
    if (btn) btn.classList.remove("on");
    _rec = null;
  }
}

export function bindMic(): void {
  const btn = $("#mic-btn");
  if (btn) btn.addEventListener("click", () => micDictate());
}

export function resetMic(): void {
  if (_rec) {
    try {
      _rec.stop();
    } catch {
      /* ignore */
    }
  }
  _rec = null;
}
