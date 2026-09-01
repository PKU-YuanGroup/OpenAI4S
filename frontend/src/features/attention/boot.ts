import { h, render } from "preact";
import { effect } from "@preact/signals";
import { AttentionStream } from "../../components/attention/AttentionStream";
import "../../components/attention/attention.css";
import { refreshAttention } from "./api";
import { ATTENTION_POLL_MS } from "./types";
import { readPollFlags, shouldFetchAttention } from "./poll";
import { attentionCards } from "./state";

let pollTimer: ReturnType<typeof setInterval> | null = null;
let visBound = false;
let classObserver: MutationObserver | null = null;
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
  const grid = dash.querySelector(".dash-grid");
  if (grid) dash.insertBefore(host, grid);
  else dash.appendChild(host);
  return host;
}

function syncHostVisibility(): void {
  const host = document.getElementById("dash-attention");
  if (!host) return;
  host.classList.toggle("hidden", attentionCards.value.length === 0);
}

export function stopAttentionPoll(): void {
  if (pollTimer != null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

export function startAttentionPoll(): void {
  stopAttentionPoll();
  pollTimer = setInterval(() => {
    void refreshAttention();
  }, ATTENTION_POLL_MS);
}

function onVisibility(): void {
  if (!shouldFetchAttention(readPollFlags())) return;
  startAttentionPoll();
  void refreshAttention();
}

function onDashboardClass(): void {
  if (shouldFetchAttention(readPollFlags())) {
    startAttentionPoll();
    void refreshAttention();
  } else {
    stopAttentionPoll();
  }
}

function bindVisibility(): void {
  if (visBound || typeof document === "undefined") return;
  visBound = true;
  document.addEventListener("visibilitychange", onVisibility);
}

function bindDashboardObserver(): void {
  if (typeof document === "undefined" || typeof MutationObserver === "undefined") return;
  const dash = document.getElementById("dashboard");
  if (!dash || classObserver) return;
  classObserver = new MutationObserver(onDashboardClass);
  classObserver.observe(dash, { attributes: true, attributeFilter: ["class"] });
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
 * M-02 boot. Mounts the dashboard attention stream and starts the 4s
 * poll only while the dashboard page is visible.
 */
export function bootAttention(): void {
  if (booted || typeof document === "undefined") return;
  booted = true;
  mountStream();
  bindVisibility();
  bindDashboardObserver();
  if (shouldFetchAttention(readPollFlags())) {
    startAttentionPoll();
    void refreshAttention();
  }
}
