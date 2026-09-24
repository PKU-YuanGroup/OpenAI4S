/**
 * A language switch reads the attention page again: each card's kind, action
 * and "untitled" labels are built when its page is read, in the language of
 * that moment, so a repaint alone would keep them in the old one.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ refreshAttention: vi.fn(async () => {}) }));
vi.mock("./api", () => api);
vi.mock("preact", async (original) => ({ ...await original<typeof import("preact")>(), render: vi.fn() }));

import { LANG, setLang } from "../../i18n/runtime";
import { startDashPoll, stopDashPoll } from "../sessions/dashboard";
import { bootAttention } from "./boot";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the attention stream and the language", () => {
  it("reads the page again after a switch, and only after a switch", async () => {
    const host = { classList: { toggle: vi.fn() } };
    const dashboard = { classList: { contains: () => false } };
    vi.stubGlobal("document", {
      hidden: false,
      documentElement: { lang: "" },
      addEventListener: vi.fn(),
      querySelectorAll: () => [],
      getElementById: (id: string) => (id === "dash-attention" ? host : id === "dashboard" ? dashboard : null),
    });
    bootAttention();
    expect(api.refreshAttention).toHaveBeenCalledTimes(1);

    await setLang(LANG);
    expect(api.refreshAttention).toHaveBeenCalledTimes(1);

    await setLang(LANG === "en" ? "zh" : "en");
    expect(api.refreshAttention).toHaveBeenCalledTimes(2);

    // Its reads ride the dashboard's own poll, started when the dashboard shows.
    vi.stubGlobal("setInterval", () => 1);
    startDashPoll();
    stopDashPoll();
    expect(api.refreshAttention).toHaveBeenCalledTimes(3);
  });
});
