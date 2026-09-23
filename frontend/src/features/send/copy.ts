import { LANG } from "../../i18n/runtime";

/** Composer labels the generated F-07 dictionaries do not carry. */
export function sendCopy(key: "send"): string {
  const copy = LANG === "en" ? { send: "Send" } : { send: "发送" };
  return copy[key];
}
