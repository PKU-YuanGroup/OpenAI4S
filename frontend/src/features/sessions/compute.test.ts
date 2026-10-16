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
  disabled = false;
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

import { _openGen, currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { computeStatus } from "../../stores/timeline";
import { _modalMode } from "../../stores/ui";
import { api } from "./api";
import { computeT as t, openRunLocationDialog, refreshComputeStatus } from "./compute";
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
    daemon({ location: "local" });
    vi.mocked(api).mockResolvedValue({ location: "local" });
    await refreshComputeStatus("g");
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
    expect(modalBody.texts()).toContain(t("compute.profiles.empty"));
  });
});

describe("compute read failures", () => {
  it("retains the last cluster badge and undismissed loss on disconnect", async () => {
    daemon({ location: "cluster", readiness: { ready: true }, workload: { profile: "gpu" }, state_lost_epochs: [1] });
    await refreshComputeStatus("f");
    vi.mocked(api).mockRejectedValue(new Error("disconnected"));
    await refreshComputeStatus("f");
    expect(byId("compute-badge")?.texts().join(" ")).toContain("gpu");
    expect(byId("compute-lost")).not.toBeNull();
  });

  it("does not mark Local current when status failed", async () => {
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === "/orchestration/profiles") return { profiles: [] };
      throw new Error("HTTP 500");
    });
    await openRunLocationDialog("f");
    expect(modalBody.find((node) => node.className.includes("current"))).toBeNull();
  });
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

describe("truthful compute read ownership", () => {
  it.each([null, {}, "gateway HTML", { location: "elsewhere" },
    { location: "cluster", state_lost_epochs: "bad" },
    { location: "cluster", workload: { profile: 7 } },
    { location: "cluster", readiness: { ready: "true" } },
  ])("treats malformed status %j as unavailable", async (status) => {
    daemon(status);
    await refreshComputeStatus("f");
    expect(computeStatus.value?.phase).toBe("error");
    expect(byId("compute-badge")?.texts()).toContain(t("compute.status.unavailable"));
    await openRunLocationDialog("f");
    expect(modalBody.find((node) => node.className.includes("current"))).toBeNull();
    expect(modalBody.find((node) => node.className === "rl-option")?.disabled).toBe(true);
  });

  it("shows loading without a false Local selection, then confirms Local", async () => {
    const status = deferred<unknown>();
    vi.mocked(api).mockImplementation(async (path) => path.endsWith("/profiles") ? { profiles: [] } : status.promise);
    const reading = refreshComputeStatus("f");
    const dialog = openRunLocationDialog("f");
    expect(computeStatus.value?.phase).toBe("loading");
    expect(modalBody.texts()).toEqual([t("compute.status.loading")]);
    expect(modalBody.find((node) => node.className.includes("current"))).toBeNull();
    status.resolve({ location: "local", workload: null });
    await Promise.all([reading, dialog]);
    expect(computeStatus.value?.phase).toBe("available");
    expect(byId("compute-badge")).toBeNull();
    expect(modalBody.find((node) => node.className.includes("current"))?.texts()).toContain(t("compute.location.local"));
  });

  it("retains warning and labels the previous location stale while checking and after HTTP 500", async () => {
    daemon({ location: "cluster", workload: { profile: "gpu" }, state_lost_epochs: [2] });
    await refreshComputeStatus("f");
    const pending = deferred<unknown>();
    vi.mocked(api).mockReturnValue(pending.promise);
    const reading = refreshComputeStatus("f");
    expect(byId("compute-lost")).not.toBeNull();
    expect(byId("compute-badge")?.texts().join(" ")).toContain(t("compute.status.stale"));
    pending.resolve({ error: "HTTP 500" });
    await reading;
    expect(byId("compute-lost")).not.toBeNull();
    expect(byId("compute-badge")?.texts().join(" ")).toContain("gpu");
    expect(computeStatus.value?.phase).toBe("error");
  });

  it("keeps dismissal effective through failure and raises a new loss", async () => {
    daemon({ location: "cluster", state_lost_epochs: [1] });
    await refreshComputeStatus("f");
    byId("compute-lost")!.find((node) => node.text === t("compute.lost.dismiss"))!.onclick!();
    vi.mocked(api).mockRejectedValue(new Error("HTTP 500"));
    await refreshComputeStatus("f");
    expect(byId("compute-lost")).toBeNull();
    daemon({ location: "cluster", state_lost_epochs: [1, 2] });
    await refreshComputeStatus("f");
    expect(byId("compute-lost")).not.toBeNull();
  });

  it("does not let an older success overwrite a newer error", async () => {
    const older = deferred<unknown>();
    vi.mocked(api).mockReturnValueOnce(older.promise).mockRejectedValueOnce(new Error("newer failure"));
    const first = refreshComputeStatus("f");
    await refreshComputeStatus("f");
    older.resolve({ location: "local" });
    await first;
    expect(computeStatus.value?.phase).toBe("error");
    expect(byId("compute-badge")?.texts()).toContain(t("compute.status.unavailable"));
  });

  it("does not let an older failure erase a newer cluster status", async () => {
    const older = deferred<unknown>();
    vi.mocked(api).mockReturnValueOnce(older.promise).mockResolvedValueOnce({ location: "cluster", readiness: { ready: true }, workload: { profile: "new" } });
    const first = refreshComputeStatus("f");
    await refreshComputeStatus("f");
    older.resolve(null);
    await first;
    expect(byId("compute-badge")?.texts()).toEqual(["new"]);
  });

  it("rejects an old read after leaving and reopening the same session", async () => {
    const older = deferred<unknown>();
    vi.mocked(api).mockReturnValueOnce(older.promise);
    const first = refreshComputeStatus("f");
    currentId.value = "g";
    _openGen.value++;
    currentId.value = "f";
    _openGen.value++;
    daemon({ location: "local" });
    await refreshComputeStatus("f");
    older.resolve({ location: "cluster", workload: { profile: "old" } });
    await first;
    expect(computeStatus.value?.status?.location).toBe("local");
    expect(byId("compute-badge")).toBeNull();
  });

  it("does not retain another session's cluster or loss warning on failure", async () => {
    daemon({ location: "cluster", state_lost_epochs: [1] });
    await refreshComputeStatus("f");
    currentId.value = "g";
    vi.mocked(api).mockRejectedValue(new Error("disconnected"));
    await refreshComputeStatus("g");
    expect(computeStatus.value?.status).toBeNull();
    expect(byId("compute-lost")).toBeNull();
    expect(byId("compute-badge")?.texts()).toEqual([t("compute.status.unavailable")]);
  });

  it.each([new Error("HTTP 500"), { profiles: "broken" }, { profiles: [{}] }])(
    "does not report a failed or malformed profile catalog as unconfigured: %j", async (catalog) => {
      vi.mocked(api).mockImplementation(async (path) => {
        if (path.endsWith("/compute")) return { location: "local" };
        if (catalog instanceof Error) throw catalog;
        return catalog;
      });
      await openRunLocationDialog("f");
      expect(modalBody.texts()).toContain(t("compute.profiles.unavailable"));
      expect(modalBody.texts()).not.toContain(t("compute.profiles.empty"));
      expect(modalBody.find((node) => node.className === "rl-retry")).not.toBeNull();
    },
  );

  it("retries failed status and catalog reads without issuing a mutation", async () => {
    vi.mocked(api).mockRejectedValue(new Error("offline"));
    await openRunLocationDialog("f");
    daemon({ location: "local" }, [{ name: "gpu" }]);
    modalBody.find((node) => node.className === "rl-retry")!.onclick!();
    await vi.waitFor(() => expect(modalBody.texts()).toContain("gpu"));
    expect(modalBody.texts()).not.toContain(t("compute.status.unavailable"));
    expect(computeStatus.value?.phase).toBe("available");
    expect(byId("compute-badge")).toBeNull();
    expect(modalBody.find((node) => node.className.includes("current"))?.texts()).toContain(t("compute.location.local"));
    expect(vi.mocked(api).mock.calls.every(([, options]) => !options?.method)).toBe(true);
  });

  it("only lets the newest same-session dialog read render", async () => {
    const older = deferred<unknown>();
    vi.mocked(api).mockImplementation(async (path) => path.endsWith("/profiles") ? { profiles: [] } : older.promise);
    const first = openRunLocationDialog("f");
    daemon({ location: "local" });
    await openRunLocationDialog("f");
    older.resolve({ location: "cluster", workload: { profile: "old" } });
    await first;
    expect(modalBody.find((node) => node.className.includes("current"))?.texts()).toContain(t("compute.location.local"));
  });

  it("does not replace another modal or render after navigation", async () => {
    const pending = deferred<unknown>();
    vi.mocked(api).mockReturnValue(pending.promise);
    const reading = openRunLocationDialog("f");
    _modalMode.value = "other";
    modalBody.innerHTML = "";
    modalBody.appendChild(new FakeEl("div", null, "Other dialog"));
    pending.resolve({ location: "local", profiles: [] });
    await reading;
    expect(modalBody.texts()).toEqual(["Other dialog"]);

    vi.mocked(api).mockResolvedValue({ location: "local", profiles: [] });
    const navigating = openRunLocationDialog("f");
    _openGen.value++;
    modalBody.innerHTML = "";
    await navigating;
    expect(modalBody.texts()).toEqual([]);
  });
});

it("keeps confirmed same-session evidence when reopening starts a new navigation generation", async () => {
  daemon({ location: "cluster", workload: { profile: "gpu" }, state_lost_epochs: [1] });
  await refreshComputeStatus("f");
  _openGen.value++;
  vi.mocked(api).mockRejectedValue(new Error("HTTP 500"));
  await refreshComputeStatus("f");
  expect(byId("compute-lost")).not.toBeNull();
  expect(byId("compute-badge")?.texts().join(" ")).toContain("gpu");
  expect(computeStatus.value?.phase).toBe("error");
});

it.each(["error", "loading"] as const)("does not revive an earlier dialog status after a newer %s while catalog is pending", async (phase) => {
  const catalog = deferred<unknown>();
  vi.mocked(api).mockImplementation(async (path) =>
    path.endsWith("/profiles") ? catalog.promise : { location: "local" });
  const dialog = openRunLocationDialog("f");
  await vi.waitFor(() => expect(computeStatus.value?.phase).toBe("available"));

  const newer = deferred<unknown>();
  vi.mocked(api).mockReturnValue(newer.promise);
  const refresh = refreshComputeStatus("f");
  if (phase === "error") {
    newer.resolve(null);
    await refresh;
  }
  catalog.resolve({ profiles: [{ name: "gpu" }] });
  await dialog;
  expect(modalBody.find((node) => node.className.includes("current"))).toBeNull();
  expect(modalBody.find((node) => node.className === "rl-option")?.disabled).toBe(true);
  expect(modalBody.texts()).toContain(t(phase === "error" ? "compute.status.unavailable" : "compute.status.loading"));
  expect(modalBody.find((node) => node.className === "rl-retry")).not.toBeNull();
  newer.resolve({ location: "local" });
  await refresh;
});
