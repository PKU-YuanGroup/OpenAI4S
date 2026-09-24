/**
 * Where a session runs: the badge names the readiness condition still
 * outstanding, a lost kernel state is announced until dismissed (and again for
 * a further loss), and "Run location" lists this machine and the configured
 * profiles and requests the one chosen.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

class FakeEl {
  tag: string;
  className: string;
  text: string;
  id = "";
  title = "";
  type = "";
  style: Record<string, string> = {};
  children: FakeEl[] = [];
  parentNode: FakeEl | null = null;
  onclick: (() => void) | null = null;
  constructor(tag: string, cls: string | null = null, text: string | null = null) {
    this.tag = tag;
    this.className = cls || "";
    this.text = text || "";
  }
  get firstChild(): FakeEl | null {
    return this.children[0] || null;
  }
  set innerHTML(_value: string) {
    this.children.forEach((child) => (child.parentNode = null));
    this.children = [];
  }
  set textContent(value: string) {
    this.text = value;
  }
  appendChild(child: FakeEl): FakeEl {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  insertBefore(child: FakeEl, before: FakeEl | null): FakeEl {
    child.parentNode = this;
    const at = before ? this.children.indexOf(before) : -1;
    if (at < 0) this.children.push(child);
    else this.children.splice(at, 0, child);
    return child;
  }
  remove(): void {
    if (this.parentNode) this.parentNode.children = this.parentNode.children.filter((node) => node !== this);
    this.parentNode = null;
  }
  find(match: (node: FakeEl) => boolean): FakeEl | null {
    for (const child of this.children) {
      if (match(child)) return child;
      const deeper = child.find(match);
      if (deeper) return deeper;
    }
    return null;
  }
  texts(): string[] {
    const out = this.text ? [this.text] : [];
    this.children.forEach((child) => out.push(...child.texts()));
    return out;
  }
}

let conv: FakeEl;
let actions: FakeEl;
let messages: FakeEl;
let modalBody: FakeEl;
const byId = (id: string) => conv.find((node) => node.id === id);

vi.mock("./api", () => ({ api: vi.fn(), apiErrorText: String }));
vi.mock("./chrome", () => ({ hint: vi.fn() }));
vi.mock("./icon", () => ({ iconEl: () => new FakeEl("svg") }));
vi.mock("./dom", () => ({
  $: (sel: string) => {
    if (sel === ".conv-head-actions") return actions;
    if (sel === "#messages") return messages;
    if (sel === "#modal-body") return modalBody;
    if (sel.startsWith("#")) return byId(sel.slice(1)) || null;
    return null;
  },
  el: (tag: string, cls?: string | null, text?: string | null) => new FakeEl(tag, cls, text),
  closeModalEl: vi.fn(),
  openModalEl: vi.fn(),
}));

import { t } from "../../i18n";
import { currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { api } from "./api";
import { openRunLocationDialog, refreshComputeStatus } from "./compute";
import { closeModalEl, openModalEl } from "./dom";

function daemon(status: unknown, profiles: unknown[] = []) {
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/sessions/f/compute") return status;
    if (path === "/orchestration/profiles") return { profiles };
    return { ok: true };
  });
}

beforeEach(() => {
  resetStoreFields();
  vi.mocked(api).mockReset();
  vi.mocked(openModalEl).mockClear();
  vi.mocked(closeModalEl).mockClear();
  conv = new FakeEl("div");
  actions = conv.appendChild(new FakeEl("div", "conv-head-actions"));
  messages = conv.appendChild(new FakeEl("div"));
  modalBody = new FakeEl("div");
  currentId.value = "f";
});

describe("the run-location badge and banner", () => {
  it("names the readiness condition a cluster session is still waiting for", async () => {
    daemon({ location: "cluster", readiness: { ready: false, blocked_on: "worker" }, allocation: { phase: "PENDING" } });
    await refreshComputeStatus("f");
    expect(byId("compute-badge")?.texts()).toEqual([t("compute.blocked.worker")]);
  });

  it("gives a local session no badge, and removes one left from a cluster run", async () => {
    daemon({ location: "cluster", readiness: { ready: true }, workload: { profile: "gpu" } });
    await refreshComputeStatus("f");
    expect(byId("compute-badge")?.texts()).toEqual(["gpu"]);
    daemon({ location: "local" });
    await refreshComputeStatus("f");
    expect(byId("compute-badge")).toBeNull();
  });

  it("announces a lost kernel state until dismissed, and again for a further loss", async () => {
    daemon({ location: "cluster", readiness: { ready: true }, state_lost_epochs: [1] });
    await refreshComputeStatus("f");
    const banner = byId("compute-lost");
    expect(banner?.texts()).toContain(t("compute.lost.title"));
    expect(conv.children.indexOf(banner!)).toBe(conv.children.indexOf(messages) - 1);
    banner!.find((node) => node.text === t("compute.lost.dismiss"))!.onclick!();
    expect(byId("compute-lost")).toBeNull();
    await refreshComputeStatus("f");
    expect(byId("compute-lost")).toBeNull();
    daemon({ location: "cluster", readiness: { ready: true }, state_lost_epochs: [1, 2] });
    await refreshComputeStatus("f");
    expect(byId("compute-lost")).not.toBeNull();
  });

  it("does not paint a session the user has already left", async () => {
    daemon({ location: "cluster", readiness: { ready: true }, workload: { profile: "gpu" } });
    const reading = refreshComputeStatus("f");
    currentId.value = "g";
    await reading;
    expect(byId("compute-badge")).toBeNull();
  });
});

describe("Run location", () => {
  it("lists this machine and the configured profiles, and requests the one chosen", async () => {
    daemon({ location: "local" }, [{ name: "gpu-small", cpus: 8, gpus: 1, memory_mb: 32768, walltime_s: 7200 }]);
    await openRunLocationDialog("f");
    expect(openModalEl).toHaveBeenCalledTimes(1);
    expect(modalBody.texts()).toEqual([
      t("compute.location.local"), t("compute.location.localHint"),
      "gpu-small", "8 CPU · 1 GPU · 32 GiB · 2 h",
    ]);
    modalBody.find((node) => node.text === "gpu-small")!.parentNode!.onclick!();
    await vi.waitFor(() => expect(closeModalEl).toHaveBeenCalled());
    expect(api).toHaveBeenCalledWith("/sessions/f/compute", { method: "POST", body: JSON.stringify({ profile: "gpu-small" }) });
  });

  it("says when this daemon has no cluster profiles", async () => {
    daemon({ location: "local" }, []);
    await openRunLocationDialog("f");
    expect(modalBody.texts()).toContain(t("compute.dialog.notConfigured"));
  });
});
