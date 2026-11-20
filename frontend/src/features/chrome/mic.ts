/**
 * Voice dictation via SpeechRecognition. Port of app.js:10919-10932.
 */

import { LANG, t } from "../../i18n/runtime";
import { $, grow, hint } from "./dom";

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

/**
 * What a SpeechRecognition error code means for the person at the keyboard.
 * The raw code ("not-allowed") was printed as is, which says neither what
 * happened nor what to do about it.
 */
const MIC_ERRORS: Record<"en" | "zh", Record<string, string>> = {
  en: {
    "not-allowed":
      "The browser blocked the microphone for this page. Allow it in the site settings (the icon left of the address), then click the mic again.",
    "service-not-allowed":
      "The browser does not allow speech recognition here. Check its microphone and speech settings, or type instead.",
    "no-speech": "No speech was heard. Click the mic and start speaking.",
    "audio-capture": "No microphone was found. Check that one is connected and not in use by another app.",
    network: "Speech recognition could not reach the browser's speech service. Check the connection, or type instead.",
    "language-not-supported": "Speech recognition does not support this language in this browser.",
  },
  zh: {
    "not-allowed": "浏览器禁止了本页面使用麦克风。请在站点设置（地址栏左侧的图标）里允许麦克风，然后再点一次麦克风。",
    "service-not-allowed": "浏览器不允许在这里使用语音识别。请检查浏览器的麦克风和语音设置，或直接输入文字。",
    "no-speech": "没有听到声音。请点击麦克风后开始说话。",
    "audio-capture": "没有找到麦克风。请确认已连接麦克风，且没有被其他应用占用。",
    network: "语音识别无法连接浏览器的语音服务。请检查网络，或直接输入文字。",
    "language-not-supported": "此浏览器的语音识别不支持当前语言。",
  },
};

/** `null` when there is nothing to tell: `aborted` is the user's own stop. */
export function micErrorText(code: string | undefined): string | null {
  const key = String(code || "");
  if (key === "aborted") return null;
  return MIC_ERRORS[LANG === "en" ? "en" : "zh"][key] ?? t("toast.micError", key);
}

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
      grow();
    }
  };
  r.onerror = (e) => {
    const said = micErrorText(e.error);
    if (said) hint(said, true);
    else hint("");
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
