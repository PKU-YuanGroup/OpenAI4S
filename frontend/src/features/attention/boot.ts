import { h, render } from "preact";
import { effect } from "@preact/signals";
import { AttentionStream } from "../../components/attention/AttentionStream";
import "../../components/attention/attention.css";
import { LANG, onLanguageChange } from "../../i18n/runtime";
import { onDashPoll } from "../sessions/dashboard";
import { refreshAttention } from "./api";
import { readPollFlags, shouldFetchAttention } from "./poll";
import { attentionCards } from "./state";

let hostEffectBound = false;
let booted = false;

function ensureHost(): HTMLElement | null {
  if (typeof document === "undefined") return null;
  let host = document.getElementById("dash-attention");
  if (host) return host;
  const dash = document.getElementById("dashboard");
  if (!dash) return null;
  host = document.createElement("section");
  host.id = "dash-attention";
  host.className = "dash-attention hidden";
  host.setAttribute("aria-live", "polite");
  // Beside the headline when the shell has a hero; before the lists otherwise.
  const hero = dash.querySelector(".dash-hero");
  const grid = dash.querySelector(".dash-grid");
  if (hero) hero.appendChild(host);
  else if (grid) dash.insertBefore(host, grid);
  else dash.appendChild(host);
  return host;
}

function syncHostVisibility(): void {
  const host = document.getElementById("dash-attention");
  if (!host) return;
  host.classList.toggle("hidden", attentionCards.value.length === 0);
}

function mountStream(): void {
  const host = ensureHost();
  if (!host) return;
  render(h(AttentionStream, {}), host);
  if (!hostEffectBound) {
    hostEffectBound = true;
    effect(syncHostVisibility);
  }
}

/**
 * M-02 boot. Mounts the dashboard attention stream. Its reads ride the
 * dashboard's own 4s poll (`onDashPoll`), which runs only while the dashboard
 * is on screen and the page is visible, and starts and stops with it.
 */
export function bootAttention(): void {
  if (booted || typeof document === "undefined") return;
  booted = true;
  mountStream();
  onDashPoll(() => {
    void refreshAttention();
  });
  // A card's kind, action and "untitled" labels are built when its page is
  // read, in the language of that moment: a switch reads the page again.
  let painted = LANG;
  onLanguageChange((lang) => {
    if (lang === painted) return;
    painted = lang;
    void refreshAttention();
  });
  // The dashboard can be on screen before routing starts its poll.
  if (shouldFetchAttention(readPollFlags())) void refreshAttention();
}
