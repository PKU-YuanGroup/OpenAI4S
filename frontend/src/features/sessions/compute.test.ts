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
  classList = {
    contains: (name: string) => this.className.split(" ").includes(name),
  };
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
let modal: FakeEl;
let modalBody: FakeEl;
const byId = (id: string) => conv.find((node) => node.id === id);

vi.mock("./api", () => {
  // The same envelope the real helper throws: `compute.ts` classifies a failed
  // read by HTTP status and code, so the mock has to keep both.
  class MockApiError extends Error {
    name = "ApiError";
    code: string;
    status: number;
    requestId = "";
    body: unknown;
    constructor(body: unknown, httpStatus: number) {
      const rec = body && typeof body === "object" ? (body as Record<string, unknown>) : null;
      super(String((rec && (rec.error || rec.detail)) || "HTTP " + httpStatus));
      this.code = rec && rec.code != null ? String(rec.code) : "";
      this.status = Number((rec && rec.status) || httpStatus);
      this.body = body;
    }
  }
  return { ApiError: MockApiError, api: vi.fn(), apiErrorText: String };
});
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
  closeModalEl: vi.fn((node: FakeEl) => { node.className = "hidden"; }),
  openModalEl: vi.fn((node: FakeEl) => { node.className = ""; }),
}));

import { _openGen, currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { computeStatus } from "../../stores/timeline";
import { _modalMode } from "../../stores/ui";
import { ApiError, api } from "./api";
import { hint } from "./chrome";
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
  vi.mocked(hint).mockClear();
  conv = new FakeEl("div");
  actions = conv.appendChild(new FakeEl("div", "conv-head-actions"));
  messages = conv.appendChild(new FakeEl("div"));
  modal = conv.appendChild(new FakeEl("div", "hidden"));
  modal.id = "modal";
  modalBody = modal.appendChild(new FakeEl("div"));
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
    expect(computeStatus.value?.phase).toBe("unavailable");
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
    expect(computeStatus.value?.phase).toBe("unavailable");
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
    expect(computeStatus.value?.phase).toBe("unavailable");
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
  expect(computeStatus.value?.phase).toBe("unavailable");
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

describe("run-location modal ownership", () => {
  it("does not render a read after the dialog was dismissed", async () => {
    const pending = deferred<unknown>();
    vi.mocked(api).mockReturnValue(pending.promise);
    const reading = openRunLocationDialog("f");
    modal.className = "hidden";
    pending.resolve({ location: "local", profiles: [] });
    await reading;
    expect(modalBody.texts()).toEqual([t("compute.status.loading")]);
  });

  it("does not overwrite a viewer that replaces the body without setting modal mode", async () => {
    const pending = deferred<unknown>();
    vi.mocked(api).mockReturnValue(pending.promise);
    const reading = openRunLocationDialog("f");
    // Artifact preview and Ketcher share #modal without writing _modalMode.
    modalBody.innerHTML = "";
    modalBody.appendChild(new FakeEl("iframe"));
    pending.resolve({ location: "local", profiles: [] });
    await reading;
    expect(modalBody.firstChild?.tag).toBe("iframe");
  });

  it("does not close a newer dialog when an earlier allocation finishes", async () => {
    daemon({ location: "local" }, [{ name: "gpu" }]);
    await openRunLocationDialog("f");
    const pending = deferred<unknown>();
    vi.mocked(api).mockReturnValueOnce(pending.promise);
    modalBody.find((node) => node.text === "gpu")!.parentNode!.onclick!();
    await openRunLocationDialog("f");
    pending.resolve({ ok: true });
    await vi.waitFor(() => expect(computeStatus.value?.phase).toBe("available"));
    expect(closeModalEl).not.toHaveBeenCalled();
  });

  it("does not show an earlier allocation error in a replacement viewer", async () => {
    daemon({ location: "local" }, [{ name: "gpu" }]);
    await openRunLocationDialog("f");
    const pending = deferred<unknown>();
    vi.mocked(api).mockImplementationOnce(async () => { await pending.promise; throw new Error("old allocation failure"); });
    modalBody.find((node) => node.text === "gpu")!.parentNode!.onclick!();
    modalBody.innerHTML = "";
    modalBody.appendChild(new FakeEl("iframe"));
    pending.resolve(null);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(hint).not.toHaveBeenCalled();
  });
});

// A failed *read* and an absent *feature* are different facts. A 5xx, an auth
// refusal or a dropped connection says "ask again later"; only a routing answer
// says this daemon has no run-location surface at all. Neither may be dressed
// up as `local`, because that is a claim about where the kernel runs.
function failingApi(status: number, body: unknown = { error: "unavailable" }) {
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/orchestration/profiles") return { profiles: [] };
    throw new ApiError(body, status);
  });
}

function option(label: string): FakeEl {
  const button = modalBody.find((node) => node.text === label)?.parentNode;
  expect(button, `no run-location option labelled ${label}`).toBeTruthy();
  return button!;
}

function tick(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

describe("run-location read tri-state", () => {
  it.each([
    [401, "unauthorized", ""],
    [403, "admin only", "admin_only"],
    [500, "internal error", ""],
    [503, "compute unavailable", "compute_record_unavailable"],
  ])("treats HTTP %i as temporarily unavailable, never as a missing feature", async (status, message, code) => {
    failingApi(status, { error: message, code });
    await refreshComputeStatus("f");
    expect(computeStatus.value?.phase).toBe("unavailable");
    const badge = byId("compute-badge")?.texts().join(" ") || "";
    expect(badge).toContain(t("compute.status.unavailable"));
    expect(badge).not.toContain(t("compute.status.unsupported"));
  });

  it("treats a dropped connection as temporarily unavailable", async () => {
    vi.mocked(api).mockRejectedValue(new TypeError("Failed to fetch"));
    await refreshComputeStatus("f");
    expect(computeStatus.value?.phase).toBe("unavailable");
  });

  it.each([
    [404, { error: "not found", path: "/sessions/f/compute", method: "GET" }],
    [405, { error: "method not allowed" }],
    [501, { error: "not implemented" }],
  ])("treats HTTP %i route absence as explicitly unsupported", async (status, body) => {
    failingApi(status, body);
    await refreshComputeStatus("f");
    expect(computeStatus.value?.phase).toBe("unsupported");
    expect(byId("compute-badge")?.texts().join(" ")).toContain(t("compute.status.unsupported"));
  });

  it("does not read a missing session as a daemon without the feature", async () => {
    failingApi(404, { error: "session not found" });
    await refreshComputeStatus("f");
    expect(computeStatus.value?.phase).toBe("unavailable");
  });

  it("keeps a malformed 200 payload as a failed read, not a capability answer", async () => {
    daemon({ location: "elsewhere" });
    await refreshComputeStatus("f");
    expect(computeStatus.value?.phase).toBe("unavailable");
  });

  it("does not mark Local current and offers a retry when the read is temporarily unavailable", async () => {
    failingApi(503);
    await openRunLocationDialog("f");
    expect(modalBody.texts()).toContain(t("compute.status.unavailable"));
    expect(modalBody.find((node) => node.className.includes("current"))).toBeNull();
    expect(modalBody.find((node) => node.className === "rl-retry")).not.toBeNull();
  });

  it("says the daemon does not provide run-location control instead of claiming this machine", async () => {
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/orchestration/profiles") return { profiles: [{ name: "gpu" }] };
      throw new ApiError({ error: "not found", path: "/sessions/f/compute", method: "GET" }, 404);
    });
    await openRunLocationDialog("f");
    expect(modalBody.texts()).toContain(t("compute.status.unsupported"));
    expect(modalBody.texts()).not.toContain(t("compute.location.local"));
    // A profile list is not an invitation to switch when the current
    // location cannot be read: offering it would be a blind mutation.
    expect(modalBody.texts()).not.toContain("gpu");
    expect(modalBody.find((node) => node.className === "rl-retry")).toBeNull();
  });
});

describe("run-location submission ownership", () => {
  it("sends one POST when the in-flight option is clicked again", async () => {
    const gate = deferred<unknown>();
    let posts = 0;
    vi.mocked(api).mockImplementation(async (path, options) => {
      if (path === "/sessions/f/compute" && options?.method === "POST") {
        posts += 1;
        return gate.promise;
      }
      if (path === "/orchestration/profiles") return { profiles: [{ name: "gpu" }, { name: "cpu" }] };
      return { location: "local" };
    });
    await openRunLocationDialog("f");
    option("gpu").onclick!();
    option("gpu").onclick!();
    option("cpu").onclick!();
    expect(posts).toBe(1);
    // Every action in this window is disabled until this one settles.
    expect(option("gpu").disabled).toBe(true);
    expect(option("cpu").disabled).toBe(true);
    gate.resolve({ ok: true });
    await vi.waitFor(() => expect(closeModalEl).toHaveBeenCalled());
    expect(posts).toBe(1);
    await vi.waitFor(() => expect(computeStatus.value?.phase).toBe("available"));
  });

  it("keeps the last confirmed location, marks it unconfirmed and allows an explicit retry", async () => {
    daemon({ location: "cluster", readiness: { ready: true }, workload: { profile: "gpu" } }, [{ name: "gpu" }, { name: "cpu" }]);
    await refreshComputeStatus("f");
    let posts = 0;
    let refuse = true;
    vi.mocked(api).mockImplementation(async (path, options) => {
      if (path === "/sessions/f/compute" && options?.method === "POST") {
        posts += 1;
        if (refuse) throw new ApiError({ error: "gateway down" }, 503);
        return { ok: true };
      }
      if (path === "/orchestration/profiles") return { profiles: [{ name: "gpu" }, { name: "cpu" }] };
      return { location: "cluster", readiness: { ready: true }, workload: { profile: "gpu" } };
    });
    await openRunLocationDialog("f");
    option("cpu").onclick!();
    await vi.waitFor(() => expect(hint).toHaveBeenCalled());
    expect(closeModalEl).not.toHaveBeenCalled();
    // The old location is kept; it is not silently reported as confirmed.
    expect(computeStatus.value?.status?.location).toBe("cluster");
    expect(computeStatus.value?.unconfirmed).toBe(true);
    expect(byId("compute-badge")?.texts().join(" ")).toContain("gpu");
    expect(byId("compute-badge")?.texts().join(" ")).toContain(t("compute.status.unconfirmed"));
    expect(modalBody.texts()).toContain(t("compute.status.unconfirmed"));
    expect(option("cpu").disabled).toBe(false);
    // A retry is the user's explicit action, not an automatic re-submission.
    refuse = false;
    option("cpu").onclick!();
    await vi.waitFor(() => expect(posts).toBe(2));
    await vi.waitFor(() => expect(closeModalEl).toHaveBeenCalled());
    expect(computeStatus.value?.unconfirmed).toBeUndefined();
  });

  it("clears the unconfirmed mark when a later read succeeds", async () => {
    daemon({ location: "cluster", workload: { profile: "gpu" } }, [{ name: "gpu" }]);
    await refreshComputeStatus("f");
    vi.mocked(api).mockImplementation(async (path, options) => {
      if (path === "/sessions/f/compute" && options?.method === "POST") {
        throw new ApiError({ error: "gateway down" }, 503);
      }
      if (path === "/orchestration/profiles") return { profiles: [{ name: "gpu" }] };
      return { location: "cluster", workload: { profile: "gpu" } };
    });
    await openRunLocationDialog("f");
    option("gpu").onclick!();
    await vi.waitFor(() => expect(computeStatus.value?.unconfirmed).toBe(true));
    daemon({ location: "local" });
    await refreshComputeStatus("f");
    expect(computeStatus.value?.unconfirmed).toBeUndefined();
    expect(computeStatus.value?.status?.location).toBe("local");
  });

  it("does not mark a confirmed location unconfirmed when the daemon definitively refuses", async () => {
    daemon({ location: "cluster", readiness: { ready: true }, workload: { profile: "gpu" } }, [{ name: "gpu" }]);
    await refreshComputeStatus("f");
    vi.mocked(api).mockImplementation(async (path, options) => {
      if (path.startsWith("/sessions/f/compute") && options?.method === "POST") {
        throw new ApiError({ ok: false, code: "not_configured", error: "no worker listener" }, 409);
      }
      if (path === "/orchestration/profiles") return { profiles: [{ name: "gpu" }] };
      return { location: "cluster", readiness: { ready: true }, workload: { profile: "gpu" } };
    });
    await openRunLocationDialog("f");
    option(t("compute.location.local")).onclick!();
    await vi.waitFor(() => expect(hint).toHaveBeenCalled());
    // A refusal never applied the change, so the confirmed location is still
    // confirmed. There is nothing to walk back.
    expect(computeStatus.value?.unconfirmed).toBeUndefined();
    expect(computeStatus.value?.status?.location).toBe("cluster");
    expect(modalBody.texts()).not.toContain(t("compute.status.unconfirmed"));
  });

  it("does not close or reopen the modal when a submitted change answers after dismissal", async () => {
    const gate = deferred<unknown>();
    vi.mocked(api).mockImplementation(async (path, options) => {
      if (path === "/sessions/f/compute" && options?.method === "POST") return gate.promise;
      if (path === "/orchestration/profiles") return { profiles: [{ name: "gpu" }] };
      return { location: "local" };
    });
    await openRunLocationDialog("f");
    option("gpu").onclick!();
    modal.className = "hidden";
    gate.resolve({ ok: true });
    await tick();
    expect(closeModalEl).not.toHaveBeenCalled();
    expect(openModalEl).toHaveBeenCalledTimes(1);
    expect(modal.className).toBe("hidden");
  });

  it("does not publish a finished switch onto another session", async () => {
    const gate = deferred<unknown>();
    vi.mocked(api).mockImplementation(async (path, options) => {
      if (path === "/sessions/f/compute" && options?.method === "POST") return gate.promise;
      if (path === "/orchestration/profiles") return { profiles: [{ name: "gpu" }] };
      return { location: "local" };
    });
    await openRunLocationDialog("f");
    option("gpu").onclick!();
    currentId.value = "g";
    _openGen.value += 1;
    computeStatus.value = null;
    gate.resolve({ ok: true });
    await tick();
    expect(closeModalEl).not.toHaveBeenCalled();
    expect(computeStatus.value).toBeNull();
    expect(vi.mocked(api).mock.calls.some(([path]) => path === "/sessions/g/compute")).toBe(false);
  });

  it("keeps a reopened dialog open when an earlier submission answers", async () => {
    const gate = deferred<unknown>();
    vi.mocked(api).mockImplementation(async (path, options) => {
      if (path === "/sessions/f/compute" && options?.method === "POST") return gate.promise;
      if (path === "/orchestration/profiles") return { profiles: [{ name: "gpu" }] };
      return { location: "local" };
    });
    await openRunLocationDialog("f");
    option("gpu").onclick!();
    currentId.value = "g";
    _openGen.value += 1;
    currentId.value = "f";
    _openGen.value += 1;
    computeStatus.value = null;
    await openRunLocationDialog("f");
    gate.resolve({ ok: true });
    await tick();
    expect(closeModalEl).not.toHaveBeenCalled();
    expect(modalBody.find((node) => node.text === t("compute.location.local"))).not.toBeNull();
  });
});
