let playwright;
try {
  playwright = await import("playwright");
} catch (error) {
  const fallback = process.env.OPENAI4S_PLAYWRIGHT_MODULE;
  if (!fallback) throw error;
  playwright = await import(fallback);
}
const { chromium } = playwright;
import { authenticate } from "./browser_auth.mjs";

const baseUrl = process.env.OPENAI4S_BROWSER_URL || "http://127.0.0.1:8760/";
const executablePath = process.env.OPENAI4S_BROWSER_EXECUTABLE || undefined;
const browser = await chromium.launch({ headless: true, executablePath });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
// Log in before anything else. The daemon requires a token by default now, so
// without this every check below fails on a 401 and reports it as a product
// failure. Doing it through the query-string bootstrap means the harness also
// exercises the 303 and the cookie hand-off a real browser goes through.
const accessToken = await authenticate(page, baseUrl);
const pageErrors = [];
const workbenchSockets = [];
const workbenchEvents = [];
page.on("pageerror", (error) => pageErrors.push(String(error)));
page.on("websocket", (socket) => {
  if (!/\/api\/v1\/ws(?:\?|$)/.test(socket.url())) return;
  workbenchSockets.push(socket.url());
  socket.on("framereceived", (frame) => {
    try {
      const text = typeof frame.payload === "string" ? frame.payload : frame.payload.toString("utf8");
      const event = JSON.parse(text);
      if (event && typeof event === "object") workbenchEvents.push(event);
    } catch {}
  });
});

async function api(path, { method = "GET", data } = {}) {
  const response = await page.request.fetch(new URL(`api/v1${path}`, baseUrl).toString(), {
    method,
    data,
    headers: data === undefined ? undefined : { "Content-Type": "application/json" },
  });
  if (!response.ok()) {
    throw new Error(`${method} ${path} returned HTTP ${response.status()}: ${await response.text()}`);
  }
  return response.json();
}

async function requireOne(selector, message = selector) {
  const count = await page.locator(selector).count();
  if (count !== 1) throw new Error(`expected one ${message}, found ${count}`);
}

async function waitUntil(label, predicate, timeoutMs = 20000, intervalMs = 60) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const value = await predicate();
      if (value) return value;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(`timed out waiting for ${label}${lastError ? `: ${lastError.message}` : ""}`);
}

async function ensureDockOpen() {
  if (await page.locator("#rightdock.collapsed").count()) {
    await page.locator(".nb-tray").click();
  }
  await page.locator("#rightdock:not(.collapsed)").waitFor({ state: "visible" });
}

function queueTickets(snapshot) {
  return [snapshot?.owner, ...(snapshot?.queue || [])].filter(Boolean);
}

function executionEvents(executionId) {
  return workbenchEvents.filter((event) =>
    event && event.execution_id === executionId &&
    ["execution_state", "execution_ticket_state"].includes(event.type),
  );
}

try {
  const response = await page.goto(baseUrl, { waitUntil: "networkidle" });
  if (!response || !response.ok()) {
    throw new Error(`workbench returned HTTP ${response?.status() ?? "unknown"}`);
  }

  await page.locator("#dashboard").waitFor({ state: "visible" });
  for (const selector of [
    "#dash-projects",
    "#dash-sessions",
    "#workspace",
    "#messages",
    "#dock-notebook",
  ]) {
    if ((await page.locator(selector).count()) !== 1) {
      throw new Error(`missing workbench surface: ${selector}`);
    }
  }

  // --- browser data boundary ------------------------------------------------
  // The UI renders strings this process did not author: markdown from the
  // model, tracebacks from the kernel, a remote host's label, and a GPU name
  // that is literally `nvidia-smi` stdout. None may become executable markup.
  // These samples are the ones the improvement proposal named (malicious link,
  // image, attribute, remote hostname, GPU name); they run against the real
  // app functions so a regression in renderMd/escaping fails CI rather than a
  // browser somewhere.
  const securityHeaders = response.headers();
  for (const [header, expected] of [
    ["content-security-policy", "default-src 'self'"],
    ["x-content-type-options", "nosniff"],
  ]) {
    if (!(securityHeaders[header] || "").includes(expected)) {
      throw new Error(`response missing hardened header ${header}: ${expected}`);
    }
  }
  // script-src must never carry 'unsafe-inline' (style-src legitimately does):
  // that concession is what would make the whole policy decorative against the
  // injection it exists to stop.
  const scriptSrc = (securityHeaders["content-security-policy"] || "")
    .split(";").map((s) => s.trim()).find((s) => s.startsWith("script-src")) || "";
  if (scriptSrc.includes("'unsafe-inline'")) {
    throw new Error("CSP script-src must not allow 'unsafe-inline'");
  }

  const boundary = await page.evaluate(() => {
    const out = { executed: [], scriptTags: 0, imgTags: 0, missing: [], searchUrls: null };
    window.__xssProbe = () => out.executed.push("fired");
    const host = document.createElement("div");
    host.style.display = "none";
    document.body.appendChild(host);

    const attacks = [
      "before <script>window.__xssProbe()<\/script> after",
      "text <img src=x onerror=\"window.__xssProbe()\"> text",
      "<div onclick=\"window.__xssProbe()\">x</div>",
      "[link](javascript:window.__xssProbe())",
      "<svg onload=\"window.__xssProbe()\"></svg>",
    ];
    if (typeof renderMd !== "function") { out.missing.push("renderMd"); }
    else {
      for (const md of attacks) {
        const d = document.createElement("div");
        host.appendChild(d);
        d.innerHTML = renderMd(md);
      }
    }
    if (typeof highlightTraceback === "function") {
      const pre = document.createElement("pre");
      host.appendChild(pre);
      pre.innerHTML = highlightTraceback(
        'File "<img src=x onerror=\"window.__xssProbe()\">", line 1\n'
        + 'Error: <script>window.__xssProbe()<\/script>',
      );
    }
    if (typeof searchResultHttpUrl !== "function") {
      out.missing.push("searchResultHttpUrl");
    } else {
      out.searchUrls = {
        https: searchResultHttpUrl(" HTTPS://Example.com/A?X=Y "),
        http: searchResultHttpUrl("hTtP://Example.com/A"),
        javascript: searchResultHttpUrl("javascript:window.__xssProbe()"),
        data: searchResultHttpUrl("data:text/html,<script>window.__xssProbe()<\/script>"),
        relative: searchResultHttpUrl("//evil.example/path"),
      };
    }
    out.scriptTags = host.querySelectorAll("script").length;
    out.imgTags = host.querySelectorAll("img").length;
    return out;
  });
  // A brief tick so any onerror/onload that WAS going to fire has fired.
  await page.waitForTimeout(300);
  const executed = await page.evaluate(() => window.__xssProbe && document.querySelectorAll("script").length);
  if (boundary.missing.length) {
    throw new Error(`XSS probe could not reach: ${boundary.missing.join(", ")}`);
  }
  if (boundary.executed.length) {
    throw new Error(`hostile markup executed in renderMd/traceback: ${boundary.executed.join(", ")}`);
  }
  if (boundary.scriptTags || boundary.imgTags) {
    throw new Error(
      `hostile markup became live nodes (script=${boundary.scriptTags} img=${boundary.imgTags}) — `
      + "escaping regressed",
    );
  }
  if (!boundary.searchUrls
      || boundary.searchUrls.https !== "https://Example.com/A?X=Y"
      || boundary.searchUrls.http !== "http://Example.com/A"
      || boundary.searchUrls.javascript !== ""
      || boundary.searchUrls.data !== ""
      || boundary.searchUrls.relative !== "") {
    throw new Error(`search result URL scheme boundary regressed: ${JSON.stringify(boundary.searchUrls)}`);
  }

  const projects = await api("/projects");
  if (!Array.isArray(projects.projects)) {
    throw new Error("projects API did not return a projects array");
  }

  // Exercise the real persisted workbench rather than only its static shell.
  const project = await api("/projects", {
    method: "POST",
    data: { name: "Browser smoke project", description: "CI-only workbench state" },
  });
  const projectId = project.project_id || project.id;
  if (!projectId) throw new Error("project creation did not return an id");
  const frame = await api("/frames", {
    method: "POST",
    data: { project_id: projectId },
  });
  const frameId = frame.id || frame.frame_id;
  if (!frameId) throw new Error("frame creation did not return an id");
  await api(`/frames/${encodeURIComponent(frameId)}`, {
    method: "PATCH",
    data: { name: "Browser smoke session" },
  });
  const checkpoint = await api(`/frames/${encodeURIComponent(frameId)}/branches/checkpoints`, {
    method: "POST",
    data: { reason: "browser-smoke" },
  });
  if (!checkpoint.checkpoint_id) throw new Error("checkpoint creation did not return an id");

  const deepLink = new URL(
    `projects/${encodeURIComponent(projectId)}/frames/${encodeURIComponent(frameId)}`,
    baseUrl,
  ).toString();
  const workspaceResponse = await page.goto(deepLink, { waitUntil: "networkidle" });
  if (!workspaceResponse || !workspaceResponse.ok()) {
    throw new Error(`workspace deep link returned HTTP ${workspaceResponse?.status() ?? "unknown"}`);
  }
  await page.locator("#workspace:not(.hidden)").waitFor({ state: "visible" });
  // The workbench intentionally keeps the right dock closed on navigation.
  // Open Notebook through the same user-facing tray before interacting with
  // controls inside the otherwise hidden pane.
  await ensureDockOpen();
  await page.locator("#dock-notebook:not(.hidden)").waitFor({ state: "visible" });
  await requireOne('[data-variable-inspector="python"]', "Variable Inspector");

  // A namespace read on a never-started session must stay read-only and must
  // not create a Python worker merely to populate the panel.
  await page.locator('[data-action="refresh-variables"]').click();
  await page.locator(".nb-variables-empty").filter({
    hasText: /never started|从未启动|not been started/i,
  }).waitFor({ state: "visible" });
  const kernelStatus = await api(`/frames/${encodeURIComponent(frameId)}/kernel`);
  const pythonStatus = (kernelStatus.kernels || []).find((item) => item.language === "python") || kernelStatus.python || {};
  if (kernelStatus.alive === true || kernelStatus.state === "active" || pythonStatus.alive === true || pythonStatus.state === "active") {
    throw new Error("Variable Inspector started a Python kernel");
  }

  // Exact cancellation identifiers are mandatory and a well-formed but stale
  // identity must not start or interrupt a kernel as a side effect.
  const staleInterrupt = await api(`/frames/${encodeURIComponent(frameId)}/kernel/interrupt`, {
    method: "POST",
    data: {
      execution_id: "browser-smoke-stale",
      owner: { kind: "user_repl", id: "browser-smoke-stale" },
    },
  });
  if (staleInterrupt.ok !== false) {
    throw new Error("stale scoped interrupt unexpectedly matched an execution");
  }
  const afterInterruptStatus = await api(`/frames/${encodeURIComponent(frameId)}/kernel`);
  if (afterInterruptStatus.alive === true || afterInterruptStatus.state === "active") {
    throw new Error("stale scoped interrupt started a kernel");
  }

  // One scientific writer owns the session. A user REPL cell keeps the lease,
  // an Agent turn queues behind it, and the queued turn is admitted
  // automatically after the REPL completes.
  const holderExecutionId = `browser-holder-${Date.now()}`;
  const holder = await api(`/frames/${encodeURIComponent(frameId)}/kernel/execute`, {
    method: "POST",
    data: {
      execution_id: holderExecutionId,
      language: "python",
      code: "import time\ntime.sleep(2.5)\nprint('browser queue holder released')",
      wait: false,
    },
  });
  if (holder.status !== "accepted" || holder.execution_id !== holderExecutionId) {
    throw new Error("asynchronous REPL did not return its exact ticket");
  }
  await waitUntil("REPL execution ownership", async () => {
    const snapshot = await api(`/frames/${encodeURIComponent(frameId)}/execution-queue`);
    return snapshot.owner?.execution_id === holderExecutionId && snapshot;
  });
  const queuedAgent = await api(`/frames/${encodeURIComponent(frameId)}/message`, {
    method: "POST",
    data: {
      request: "Reply with one short sentence, then finalize structurally.",
      wait: false,
    },
  });
  if (queuedAgent.status !== "accepted" || !queuedAgent.execution_id) {
    throw new Error("Agent message did not return an asynchronous execution ticket");
  }
  await waitUntil("Agent queued behind REPL", async () => {
    const snapshot = await api(`/frames/${encodeURIComponent(frameId)}/execution-queue`);
    return snapshot.owner?.execution_id === holderExecutionId &&
      (snapshot.queue || []).some((ticket) => ticket.execution_id === queuedAgent.execution_id) && snapshot;
  });
  const queuedInterrupt = await api(`/frames/${encodeURIComponent(frameId)}/kernel/interrupt`, {
    method: "POST",
    data: { execution_id: queuedAgent.execution_id, owner: queuedAgent.owner },
  });
  if (queuedInterrupt.ok !== false) {
    throw new Error("queued Agent identity incorrectly interrupted the active REPL");
  }
  const stillHeld = await api(`/frames/${encodeURIComponent(frameId)}/execution-queue`);
  if (stillHeld.owner?.execution_id !== holderExecutionId) {
    throw new Error("scoped interrupt injured the wrong active execution");
  }
  const agentAdmission = await waitUntil("queued Agent automatic admission", async () => {
    const snapshot = await api(`/frames/${encodeURIComponent(frameId)}/execution-queue`);
    const active = snapshot.owner?.execution_id === queuedAgent.execution_id;
    const terminal = executionEvents(queuedAgent.execution_id).some((event) =>
      ["completed", "failed", "cancelled"].includes(String(event.status || "").toLowerCase()),
    );
    return (active || terminal) && { snapshot, active, terminal };
  });
  if (agentAdmission.active) {
    await api(`/frames/${encodeURIComponent(frameId)}/cancel`, {
      method: "POST",
      data: {
        execution_id: queuedAgent.execution_id,
        owner: queuedAgent.owner,
        reason: "browser smoke admitted the queued Agent",
      },
    });
  }
  await waitUntil("queued Agent terminal state", async () => {
    const snapshot = await api(`/frames/${encodeURIComponent(frameId)}/execution-queue`);
    return !queueTickets(snapshot).some((ticket) => ticket.execution_id === queuedAgent.execution_id);
  });

  // Reload while a real cell owns the kernel. The replacement socket must
  // receive a bounded replay envelope and preserve the exact cancellation id.
  const reloadExecutionId = `browser-reload-${Date.now()}`;
  const reloadCell = await api(`/frames/${encodeURIComponent(frameId)}/kernel/execute`, {
    method: "POST",
    data: {
      execution_id: reloadExecutionId,
      language: "python",
      code: "import time\ntime.sleep(30)\nprint('should be interrupted after replay')",
      wait: false,
    },
  });
  await waitUntil("reload cell ownership", async () => {
    const snapshot = await api(`/frames/${encodeURIComponent(frameId)}/execution-queue`);
    return snapshot.owner?.execution_id === reloadExecutionId && snapshot;
  });
  const socketCountBeforeReload = workbenchSockets.length;
  const eventCountBeforeReload = workbenchEvents.length;
  await page.reload({ waitUntil: "networkidle" });
  await page.locator("#workspace:not(.hidden)").waitFor({ state: "visible" });
  await waitUntil("WebSocket reconnect", () => workbenchSockets.length > socketCountBeforeReload);
  await waitUntil("replay envelope", () => {
    const replay = workbenchEvents.slice(eventCountBeforeReload);
    return replay.some((event) => event.type === "replay_begin") &&
      replay.some((event) => event.type === "replay_end") && replay;
  });
  const staleDuringReload = await api(`/frames/${encodeURIComponent(frameId)}/kernel/interrupt`, {
    method: "POST",
    data: {
      execution_id: `${reloadExecutionId}-stale`,
      owner: reloadCell.owner,
    },
  });
  if (staleDuringReload.ok !== false) throw new Error("stale replay interrupt unexpectedly matched");
  const liveAfterReplay = await api(`/frames/${encodeURIComponent(frameId)}/execution-queue`);
  if (liveAfterReplay.owner?.execution_id !== reloadExecutionId) {
    throw new Error("reload lost the active execution identity");
  }
  const exactInterrupt = await api(`/frames/${encodeURIComponent(frameId)}/kernel/interrupt`, {
    method: "POST",
    data: { execution_id: reloadExecutionId, owner: reloadCell.owner },
  });
  if (exactInterrupt.ok !== true) throw new Error("exact REPL interrupt did not match");
  await waitUntil("interrupted reload cell terminal state", async () => {
    const snapshot = await api(`/frames/${encodeURIComponent(frameId)}/execution-queue`);
    return !queueTickets(snapshot).some((ticket) => ticket.execution_id === reloadExecutionId);
  });

  // A gated Host RPC pauses the live cell and renders a real permission card.
  // Resolving that card resumes the same execution rather than replaying it.
  const permissionExecutionId = `browser-permission-${Date.now()}`;
  await api(`/frames/${encodeURIComponent(frameId)}/kernel/execute`, {
    method: "POST",
    data: {
      execution_id: permissionExecutionId,
      language: "python",
      code: "permission_job = host.exec_background(\"print('browser permission resumed')\", origin='user')\nprint(permission_job['exec_id'])",
      wait: false,
    },
  });
  const permissionCard = page.locator(".perm-card:not(.resolved)").last();
  await permissionCard.waitFor({ state: "visible", timeout: 20000 });
  // Count resolved cards before clicking: the resolution receipt can land
  // before the next locator poll, at which point no unresolved card exists
  // and waiting on the unresolved locator would time out.
  const resolvedCards = page.locator(".perm-card.resolved");
  const resolvedBefore = await resolvedCards.count();
  await permissionCard.locator(".perm-allow").click();
  // Locator-only (the workbench CSP has no unsafe-eval, so waitForFunction
  // is refused): nth(n) attaches exactly when at least n+1 cards resolved.
  await resolvedCards.nth(resolvedBefore).waitFor({ state: "attached" });
  await waitUntil("permission-resumed REPL completion", async () => {
    const snapshot = await api(`/frames/${encodeURIComponent(frameId)}/execution-queue`);
    return !queueTickets(snapshot).some((ticket) => ticket.execution_id === permissionExecutionId);
  });
  if (!workbenchEvents.some((event) => event.type === "await_permission") ||
      !workbenchEvents.some((event) => event.type === "permission_resolved")) {
    throw new Error("permission pause/resume did not cross the WebSocket/UI boundary");
  }

  // The installed notebook exporter is an HTTP artifact contract, not only a
  // Python service contract. A never-started session still exports a valid,
  // empty notebook with immutable digest metadata.
  const notebookResponse = await page.request.get(
    new URL(`api/v1/frames/${encodeURIComponent(frameId)}/notebook/export?language=python`, baseUrl).toString(),
  );
  if (!notebookResponse.ok()) {
    throw new Error(`notebook export returned HTTP ${notebookResponse.status()}`);
  }
  const notebook = JSON.parse(await notebookResponse.text());
  if (notebook.nbformat !== 4 || !Array.isArray(notebook.cells)) {
    throw new Error("notebook export did not return a valid nbformat v4 document");
  }
  if (!/\.ipynb"?$/.test(notebookResponse.headers()["content-disposition"] || "")) {
    throw new Error("notebook export did not advertise an .ipynb filename");
  }
  if (!/^[0-9a-f]{64}$/.test(notebookResponse.headers()["x-content-sha256"] || "")) {
    throw new Error("notebook export did not provide a SHA-256 digest");
  }

  // Version restore is append-only. Historical bytes stay immutable while a
  // restored copy becomes a fresh latest version and invalidates UI caches.
  // The permission block above left a short-lived background execution
  // behind, and external workspace mutation is refused while one is running.
  // Retry the busy admission instead of racing the background job's exit.
  const upload = await waitUntil("workspace capture idle for artifact upload", async () => {
    try {
      return await api("/uploads", {
        method: "POST",
        data: {
          frame_id: frameId,
          project_id: projectId,
          filename: "browser-versioned.txt",
          content_base64: Buffer.from("VERSION-ONE", "utf8").toString("base64"),
        },
      });
    } catch (error) {
      if (String(error).includes("trusted_capture_busy")) return null;
      throw error;
    }
  });
  if (!upload.artifact_id) throw new Error("artifact upload did not return an id");
  await api(`/artifacts/${encodeURIComponent(upload.artifact_id)}/edit`, {
    method: "POST",
    data: { content: "VERSION-TWO" },
  });
  const beforeRestore = await api(`/artifacts/${encodeURIComponent(upload.artifact_id)}/versions`);
  if ((beforeRestore.versions || []).length !== 2) {
    throw new Error("artifact edit did not append a second immutable version");
  }
  const firstVersion = beforeRestore.versions.find((version) => version.ordinal === 1);
  if (!firstVersion?.version_id) throw new Error("artifact v1 was not addressable");
  const restoredArtifact = await api(
    `/artifacts/${encodeURIComponent(upload.artifact_id)}/versions/${encodeURIComponent(firstVersion.version_id)}/restore`,
    { method: "POST", data: {} },
  );
  if (restoredArtifact.ok !== true || restoredArtifact.restored_from_version_id !== firstVersion.version_id ||
      restoredArtifact.version_id === firstVersion.version_id) {
    throw new Error("artifact restore did not append a fresh current version");
  }
  const artifactBody = await page.request.get(
    new URL(`api/v1/artifacts/${encodeURIComponent(upload.artifact_id)}`, baseUrl).toString(),
  );
  const restoredBodyText = await artifactBody.text();
  if (!artifactBody.ok() || restoredBodyText !== "VERSION-ONE") {
    throw new Error(
      `restored artifact bytes did not become current: HTTP ${artifactBody.status()} ${JSON.stringify(restoredBodyText)}`,
    );
  }
  const afterRestore = await api(`/artifacts/${encodeURIComponent(upload.artifact_id)}/versions`);
  if ((afterRestore.versions || []).length !== 3 ||
      afterRestore.versions[0]?.version_id !== restoredArtifact.version_id) {
    throw new Error("artifact version projection did not refresh after restore");
  }

  const contextState = await api(`/frames/${encodeURIComponent(frameId)}/context`);
  const securityState = await api(`/frames/${encodeURIComponent(frameId)}/security`);
  const recoveryState = await api(`/frames/${encodeURIComponent(frameId)}/recovery/actions`);
  if (!Array.isArray(contextState.layers) || !securityState.sandbox || !securityState.permission) {
    throw new Error("workbench context/security projections are incomplete");
  }
  // The projection is a menu of mutations, so it must advertise exactly what
  // a client can invoke. It used to also offer `inspect_log` and
  // `continue_view_only`, which no route accepted and the client's sanitiser
  // dropped; asserting all five here locked that contradiction in as a
  // contract. The set equality is the point — an extra id is as wrong as a
  // missing one.
  const recoveryIds = new Set((recoveryState.actions || []).map((action) => action.id));
  const expectedRecoveryIds = ["restore", "retry", "restart_fresh"];
  for (const actionId of expectedRecoveryIds) {
    if (!recoveryIds.has(actionId)) throw new Error(`missing recovery action: ${actionId}`);
  }
  for (const actionId of recoveryIds) {
    if (!expectedRecoveryIds.includes(actionId)) {
      throw new Error(`recovery action advertised but not invocable: ${actionId}`);
    }
  }

  await ensureDockOpen();
  const timelineTab = page.locator("#dock-tabs .dock-tab").filter({
    hasText: /Action Timeline|行动时间线/i,
  });
  await timelineTab.click();
  await page.locator(".branch-panel").waitFor({ state: "visible" });
  await page.locator(".recovery-action-list").waitFor({ state: "visible" });
  // One button per advertised action, enabled or not — `disabledWorkbenchButton`
  // always emits a <button> and only toggles `disabled`. This matched the three
  // above even while the API offered five, because the client's sanitiser
  // projects onto its own allowlist; the count agreeing was luck, not
  // agreement. Now both ends name the same three.
  const recoveryButtons = await page.locator(".recovery-action-list button").count();
  if (recoveryButtons !== expectedRecoveryIds.length) {
    throw new Error(
      `expected ${expectedRecoveryIds.length} Recovery actions, found ${recoveryButtons}`,
    );
  }

  // The session trajectory is a keyed ledger, not a card stream. Exercise its
  // renderer with non-contiguous ordinals, a retry, a live WS append, and a
  // branch boundary while preserving the real session state for the remaining
  // end-to-end checks below.
  await page.evaluate(async () => {
    const saved = {
      timeline: S.actionTimeline,
      selectedGroup: S.actionTimelineSelectedGroupId,
      selectedBranch: S.actionTimelineSelectedBranchId,
      workbenchErrors: { ...S.workbenchErrors },
    };
    const originalFetch = window.fetch;
    const group = (groupId, ordinal, turnId, branchId, overrides = {}) => ({
      group_id: groupId,
      ordinal,
      turn_id: turnId,
      branch_id: branchId,
      kind: "native_tools",
      title: `Ledger action ${ordinal}`,
      status: "completed",
      owner: "owner-fixture",
      permission: { state: "allowed", scope: "once" },
      usage: { input_tokens: 11, output_tokens: 7, total_tokens: 18 },
      cost: 0.000321,
      replay_policy: "requires_review",
      language: "python",
      events: [{
        event_id: `event-${groupId}`,
        sequence: 0,
        type: "result",
        resource_keys: ["resource-fixture"],
        artifacts: ["artifact-fixture"],
        side_effect_class: "workspace_write",
      }],
      attempts: [
        { attempt_ordinal: 1, generation_id: "generation-old", allocated_at: 50, started_at: 100, response_at: 150, finished_at: 200, terminal_state: "failed", error: "old attempt error" },
        { attempt_ordinal: 2, generation_id: "generation-latest", allocated_at: 900, started_at: 1000, response_at: 1500, finished_at: 2250, terminal_state: "failed", error: "latest attempt error" },
      ],
      created_at: ordinal,
      ...overrides,
    });
    const projection = (branchId, groups, overrides = {}) => ({
      root_frame_id: S.currentId,
      branch_id: branchId,
      groups,
      count: groups.length,
      total_count: groups.length,
      first_ordinal: groups[0]?.ordinal ?? null,
      last_ordinal: groups[groups.length - 1]?.ordinal ?? null,
      has_more_before: false,
      has_more_after: false,
      ...overrides,
    });
    const nextFrame = () => new Promise((resolve) => requestAnimationFrame(() => resolve()));
    const waitFor = async (predicate, label) => {
      for (let attempt = 0; attempt < 120; attempt += 1) {
        if (predicate()) return;
        await nextFrame();
      }
      throw new Error(`timed out waiting for ${label}`);
    };
    clearTimeout(S._workbenchTimer);
    S._workbenchReq = (S._workbenchReq || 0) + 1;
    try {
      const branch = "ledger-branch";
      S.actionTimelineSelectedGroupId = null;
      S.actionTimelineSelectedBranchId = null;
      S.actionTimeline = sanitizeActionTimeline(projection(branch, [
        group("ledger-a", 41, "turn-alpha", branch, { title: "Alpha Microscopy SharedTerm" }),
        group("ledger-b", 73, "turn-alpha", branch, {
          kind: "code", title: "Second action",
          events: [
            { event_id: "event-ledger-b-1", sequence: 0, type: "result", resource_keys: ["plain-resource"], artifacts: [], side_effect_class: "none" },
            { event_id: "event-ledger-b-2", sequence: 1, type: "result",
              resource_keys: [...Array.from({ length: 24 }, (_, index) => `resource-${index}`), "rack/Needle-Resource"],
              artifacts: [], side_effect_class: "none" },
          ],
        }),
        group("ledger-c", 91, "turn-beta", branch, {
          kind: "finalize", title: "Third action",
          events: [
            { event_id: "event-ledger-c-1", sequence: 0, type: "result", resource_keys: ["sharedterm"], artifacts: [], side_effect_class: "none" },
            { event_id: "event-ledger-c-2", sequence: 1, type: "result", resource_keys: [],
              artifacts: [...Array.from({ length: 16 }, (_, index) => `artifact-${index}`), "reports/Needle-Artifact.CSV"], side_effect_class: "none" },
            ...Array.from({ length: 98 }, (_, index) => ({
              event_id: `event-ledger-c-filler-${index}`, sequence: index + 2, type: "result",
              resource_keys: [], artifacts: [], side_effect_class: "none",
            })),
            { event_id: "event-ledger-c-late", sequence: 100, type: "result",
              resource_keys: ["late-event-resource"], artifacts: [], side_effect_class: "none" },
          ],
        }),
      ]));
      renderActionTimeline();

      let rows = Array.from(document.querySelectorAll(".timeline-ledger-row"));
      const ordinals = rows.map((row) => row.querySelector(".timeline-ordinal-value")?.textContent);
      if (ordinals.join(",") !== "#41,#73,#91") {
        throw new Error(`trajectory ordinals came from render order: ${ordinals.join(",")}`);
      }
      const boundaries = rows.filter((row) => row.classList.contains("turn-boundary"));
      if (boundaries.length !== 1 || boundaries[0].dataset.groupId !== "ledger-c") {
        throw new Error("Turn boundary was not attached to the first row of the new turn");
      }
      if (!rows.every((row) => row.dataset.groupId) ||
          !rows.every((row) => row.querySelector(".timeline-kind-icon svg")) ||
          !rows.every((row) => row.querySelector(".timeline-row-button")) ||
          rows.some((row) => row.getAttribute("role") === "button")) {
        throw new Error("trajectory rows lost their group_id key, kind icon, button, or table semantics");
      }
      if (rows[0].querySelector(".timeline-ledger-tokens")?.textContent !== "18") {
        throw new Error("trajectory row did not show the projected token total");
      }

      // Search is strictly local to the loaded projection and indexes only
      // title, raw kind, resources, and artifacts. This fixture fits in one
      // virtual window, so logical match ids and mounted action ids must agree.
      const searchInput = document.querySelector(".timeline-search-input");
      const searchToolbar = document.querySelector(".timeline-toolbar");
      if (!searchInput || !searchToolbar || document.querySelector(".timeline-search-scope")?.textContent !== t("timeline.search.scope", 3)) {
        throw new Error("trajectory search did not disclose its loaded-only scope");
      }
      const setSearch = async (query, expectedIds, expectedMountedIds = expectedIds, loadedCount = 3) => {
        searchInput.value = query; searchInput.dispatchEvent(new Event("input", { bubbles: true })); await nextFrame();
        const logicalIds = S._timelineView.groups.map((item) => item.group_id);
        const mountedIds = Array.from(document.querySelectorAll(".timeline-ledger-row[data-group-id]"), (row) => row.dataset.groupId);
        const overviewIds = S._timelineView.overview.model.items.map((item) => item.groupId);
        const expectedStatus = t(query ? "timeline.search.matches" : "timeline.search.loaded",
          query ? expectedIds.length : loadedCount, loadedCount);
        const visibleStatus = searchToolbar.querySelector(".timeline-search-status")?.textContent;
        if (logicalIds.join(",") !== expectedIds.join(",") || mountedIds.join(",") !== expectedMountedIds.join(",") ||
            overviewIds.join(",") !== expectedIds.join(",") || Number(searchToolbar.dataset.matchCount) !== expectedIds.length ||
            Number(searchToolbar.dataset.loadedCount) !== loadedCount || visibleStatus !== expectedStatus ||
            document.querySelector(".timeline-search-scope")?.textContent !== t("timeline.search.scope", loadedCount)) {
          throw new Error(`trajectory search mismatch for ${query}: ${JSON.stringify({ logicalIds, mountedIds, overviewIds, count: searchToolbar.dataset.matchCount,
            loadedCount: searchToolbar.dataset.loadedCount, visibleStatus, expectedStatus,
            entries: S._timelineView.entries.map((entry) => actionTimelineEntryKey(entry)), start: S._timelineView.start, end: S._timelineView.end,
            scrollTop: S._timelineView.scroll.scrollTop, clientHeight: S._timelineView.scroll.clientHeight, tbody: S._timelineView.tbody.innerText })}`);
        }
        if (query && Array.from(document.querySelectorAll(".timeline-ledger-row[data-group-id]")).some((row) => !row.classList.contains("search-match"))) {
          throw new Error(`trajectory search did not highlight every match for ${query}`);
        }
      };
      let localInteractionRequests = 0;
      window.fetch = (input, init) => { localInteractionRequests += 1; return originalFetch(input, init); };
      await setSearch("microscopy", ["ledger-a"]);
      await setSearch("CODE", ["ledger-b"]);
      await setSearch("needle-resource", ["ledger-b"]);
      await setSearch("needle-artifact.csv", ["ledger-c"]);
      await setSearch("late-event-resource", ["ledger-c"]);
      await setSearch("sharedterm", ["ledger-a", "ledger-c"]);
      await setSearch("", ["ledger-a", "ledger-b", "ledger-c"]);
      if (document.querySelector(".timeline-ledger-row.search-match") || S._timelineView.overview.model.items.length !== 3) {
        throw new Error("clearing trajectory search did not fully restore ledger and overview");
      }

      // Fold the first Turn through its real divider button. Search temporarily
      // reveals matching actions while retaining the Set, and a tab round-trip
      // must preserve both states on this same session/branch view.
      document.querySelector('.timeline-ledger-row[data-group-id="ledger-a"] .timeline-turn-toggle')?.click(); await nextFrame();
      let summary = document.querySelector('.timeline-turn-summary[data-turn-id="turn-alpha"]');
      if (!summary || !S._timelineView.collapsedTurns.has("turn-alpha") || !summary.textContent.includes(t("timeline.turn.summary", 2)) ||
          summary.querySelector(".timeline-ledger-duration")?.textContent !== "2.5 s") {
        throw new Error("Turn collapse did not replace its actions with the count/duration summary");
      }
      await setSearch("alpha microscopy", ["ledger-a"]);
      if (!S._timelineView.collapsedTurns.has("turn-alpha") || document.querySelector(".timeline-turn-summary")) {
        throw new Error("search did not temporarily reveal a retained collapsed Turn");
      }
      if (localInteractionRequests !== 0) throw new Error("searching or folding the loaded trajectory triggered a data request");
      window.fetch = originalFetch;
      const stableView = S._timelineView, loadingBeforeTabSwitch = S._workbenchLoading;
      S._workbenchLoading = S.currentId; setActiveTab("notebook"); setActiveTab("timeline"); S._workbenchLoading = loadingBeforeTabSwitch; await nextFrame();
      if (S._timelineView !== stableView || S._timelineView.searchQuery !== "alpha microscopy" ||
          !S._timelineView.collapsedTurns.has("turn-alpha") || document.querySelector(".timeline-search-input")?.value !== "alpha microscopy") {
        throw new Error("trajectory search or Turn fold state did not survive a tab switch");
      }
      await setSearch("", ["ledger-a", "ledger-b", "ledger-c"], ["ledger-c"]);
      summary = document.querySelector('.timeline-turn-summary[data-turn-id="turn-alpha"]');
      if (!summary) throw new Error("clearing search did not restore the prior Turn fold");
      summary.querySelector(".timeline-turn-toggle")?.click(); await nextFrame();
      rows = Array.from(document.querySelectorAll(".timeline-ledger-row[data-group-id]"));
      if (rows.length !== 3 || S._timelineView.collapsedTurns.has("turn-alpha")) throw new Error("expanding a Turn did not restore all action rows");

      const firstRow = rows[0];
      firstRow.click();
      const inspector = document.querySelector(".timeline-inspector");
      const inspectorText = inspector?.textContent || "";
      for (const value of [
        "owner-fixture",
        "allowed",
        "resource-fixture",
        "artifact-fixture",
        "generation-latest",
        "requires_review",
        "1.3 s",
        "11",
        "7",
        "$0.000321",
        "latest attempt error",
      ]) {
        if (!inspectorText.includes(value)) throw new Error(`trajectory inspector omitted ${value}`);
      }
      if (inspectorText.includes("old attempt error")) {
        throw new Error("trajectory inspector showed a stale attempt error");
      }
      if (!document.querySelector(".timeline-inspector")?.contains(document.activeElement)) {
        throw new Error("opening a trajectory row did not move focus into its inspector");
      }

      onEvent({
        type: "action_timeline",
        root_frame_id: S.currentId,
        ...projection(branch, [group("ledger-d", 105, "turn-beta", branch, { title: "WS appended action" })]),
      });
      rows = Array.from(document.querySelectorAll(".timeline-ledger-row"));
      const firstAfterAppend = rows.find((row) => row.dataset.groupId === "ledger-a");
      if (rows[rows.length - 1]?.dataset.groupId !== "ledger-d" || !firstRow.isSameNode(firstAfterAppend)) {
        throw new Error("WS append did not preserve the group_id-keyed row node");
      }
      if (!document.querySelector('.timeline-inspector[data-group-id="ledger-a"]')?.contains(document.activeElement)) {
        throw new Error("WS append dropped focus from the active group_id-keyed inspector");
      }
      document.querySelector('.timeline-inspector[data-group-id="ledger-a"] button')?.click();
      if (document.activeElement?.closest(".timeline-ledger-row")?.dataset.groupId !== "ledger-a") {
        throw new Error("closing the inspector did not restore its group_id-keyed row focus");
      }
      let postWsSearchRequests = 0;
      window.fetch = (input, init) => { postWsSearchRequests += 1; return originalFetch(input, init); };
      await setSearch("ws appended", ["ledger-d"], ["ledger-d"], 4);
      await setSearch("stream-match-token", [], [], 4);
      onEvent({
        type: "action_timeline",
        root_frame_id: S.currentId,
        ...projection(branch, [group("ledger-d", 105, "turn-beta", branch, {
          title: "WS appended action",
          events: [{ event_id: "event-ledger-d-stream", sequence: 1, type: "result",
            resource_keys: ["stream-match-token"], artifacts: [], side_effect_class: "none" }],
        })]),
      });
      await nextFrame();
      await setSearch("stream-match-token", ["ledger-d"], ["ledger-d"], 4);
      await setSearch("", ["ledger-a", "ledger-b", "ledger-c", "ledger-d"], undefined, 4);
      window.fetch = originalFetch;
      if (postWsSearchRequests !== 0) throw new Error("searching a WS-appended action triggered a request");

      const otherBranch = "ledger-other-branch";
      // Branch activation is an authoritative full reload, not a WS delta.
      // Even when group_id happens to match, the scoped ledger must rebuild.
      S._timelineView.searchQuery = "stale-query"; S._timelineView.searchNeedle = "stale-query"; S._timelineView.collapsedTurns.add("turn-other");
      S.actionTimeline = projection(otherBranch, [group("ledger-a", 4, "turn-other", otherBranch, { title: "Same id, other branch" })]);
      renderActionTimeline();
      const replacement = document.querySelector('.timeline-ledger-row[data-group-id="ledger-a"]');
      if (!replacement || firstRow.isSameNode(replacement) || document.querySelector(".timeline-inspector") ||
          S._timelineView.searchQuery || S._timelineView.collapsedTurns.size) {
        throw new Error("branch replacement reused a row or inspector from the previous branch");
      }

      // Accumulate seven 500-row pages into a 3500-action trajectory. The
      // mocked fetch remains behind the real loadEarlierActionTimeline path so
      // this exercises the loading button, branch cursor, merge, compensation,
      // virtual window, and automatic top trigger together.
      const longBranch = "ledger-long-branch";
      const longGroup = (ordinal, overrides = {}) => {
        const allocated = 1786600000000 + ordinal * 2000;
        return group(
          `long-${ordinal}`,
          ordinal,
          `turn-${Math.floor(ordinal / 3)}`,
          longBranch,
          {
            attempts: [{
              attempt_id: `long-attempt-${ordinal}`,
              attempt_ordinal: 1,
              generation_id: "long-generation",
              allocated_at: allocated,
              started_at: allocated + 100,
              response_at: allocated + 500,
              capture_at: allocated + 550,
              finished_at: allocated + 1500,
              terminal_state: "completed",
              error: null,
            }],
            events: [],
            ...overrides,
          },
        );
      };
      const pendingPages = [];
      window.fetch = (input, init) => {
        const url = new URL(String(input), location.href);
        const beforeText = url.searchParams.get("before_ordinal");
        if (url.pathname.includes(`/frames/${encodeURIComponent(S.currentId)}/action-timeline`) && beforeText !== null) {
          const before = Number(beforeText);
          if (url.searchParams.get("branch_id") !== longBranch) {
            throw new Error(`history request omitted branch scope: ${url.search}`);
          }
          const start = Math.max(0, before - ACTION_TIMELINE_PAGE_SIZE);
          const pageGroups = Array.from({ length: before - start }, (_, offset) => longGroup(start + offset));
          const payload = projection(longBranch, pageGroups, { has_more_before: start > 0, total_count: 3500 });
          return new Promise((resolve) => {
            pendingPages.push({
              before,
              release: () => resolve(new Response(JSON.stringify(payload), {
                status: 200,
                headers: { "content-type": "application/json" },
              })),
            });
          });
        }
        return originalFetch(input, init);
      };
      const latestGroups = Array.from({ length: ACTION_TIMELINE_PAGE_SIZE }, (_, offset) => longGroup(3000 + offset));
      S.actionTimeline = projection(longBranch, latestGroups, { has_more_before: true, total_count: 3500 });
      S.actionTimelineSelectedGroupId = null;
      S.actionTimelineSelectedBranchId = null;
      renderActionTimeline();
      await nextFrame(); await nextFrame();
      const longScroll = document.querySelector(".timeline-ledger-scroll");
      const longTable = document.querySelector(".timeline-ledger");
      const longBody = document.querySelector(".timeline-ledger-body");
      if (!longScroll || !longTable || !longBody || longScroll.scrollHeight <= longScroll.clientHeight) {
        throw new Error(`trajectory virtual viewport did not establish an independent scroll range: ${JSON.stringify({
          scrollHeight: longScroll?.scrollHeight,
          clientHeight: longScroll?.clientHeight,
          tableHeight: longTable?.getBoundingClientRect().height,
          bodyHeight: longBody?.getBoundingClientRect().height,
          inlineBodyHeight: longBody?.style.height,
          mountedRows: document.querySelectorAll(".timeline-ledger-row").length,
        })}`);
      }
      if (Math.abs(longScroll.scrollHeight - longScroll.clientHeight - longScroll.scrollTop) > 2) {
        throw new Error("trajectory did not initially follow the newest action");
      }

      let crossTurnSummaryNode = null;
      for (let pageIndex = 0; pageIndex < 6; pageIndex += 1) {
        if (pageIndex === 0) {
          const fallback = document.querySelector('[data-action="load-earlier-timeline"]');
          if (!fallback || fallback.disabled) throw new Error("history fallback button was unavailable");
          fallback.click();
        } else {
          longScroll.scrollTop = 0;
          longScroll.dispatchEvent(new Event("scroll"));
        }
        await waitFor(() => pendingPages.length === 1, `automatic history page ${pageIndex + 1}`);
        await nextFrame();
        const fallback = document.querySelector('[data-action="load-earlier-timeline"]');
        if (!fallback?.disabled || fallback.getAttribute("aria-busy") !== "true" || fallback.textContent !== t("timeline.loadingEarlier")) {
          throw new Error("history fallback button did not expose its loading state");
        }
        const expectedBefore = 3000 - pageIndex * ACTION_TIMELINE_PAGE_SIZE;
        const pending = pendingPages.shift();
        if (pending.before !== expectedBefore) throw new Error(`unexpected history cursor ${pending.before}, wanted ${expectedBefore}`);
        if (pageIndex === 0) {
          longScroll.scrollTop = 0; longScroll.dispatchEvent(new Event("scroll")); await nextFrame();
        }
        const anchorId = `long-${expectedBefore}`;
        const anchorSelector = pageIndex === 1
          ? '.timeline-turn-summary[data-turn-id="turn-833"]'
          : `.timeline-ledger-row[data-group-id="${anchorId}"]`;
        const anchor = document.querySelector(anchorSelector);
        if (!anchor) throw new Error(`history anchor ${anchorId} was not mounted at the top`);
        if (pageIndex === 1) crossTurnSummaryNode = anchor;
        const oldHeight = longScroll.scrollHeight;
        const oldTop = longScroll.scrollTop;
        const oldAnchorTop = anchor.getBoundingClientRect().top;
        const oldCount = S.actionTimeline.groups.length;
        pending.release();
        await waitFor(
          () => S.actionTimeline.groups.length === oldCount + ACTION_TIMELINE_PAGE_SIZE && !S._timelineHistoryLoading,
          `history prepend ${pageIndex + 1}`,
        );
        await nextFrame();
        const delta = longScroll.scrollHeight - oldHeight;
        if (Math.abs(longScroll.scrollTop - (oldTop + delta)) > 2) {
          throw new Error(`history prepend ${pageIndex + 1} did not compensate scrollTop`);
        }
        const anchoredAfter = document.querySelector(anchorSelector);
        if (!anchoredAfter || Math.abs(anchoredAfter.getBoundingClientRect().top - oldAnchorTop) > 2) {
          throw new Error(`history prepend ${pageIndex + 1} moved the visible anchor`);
        }
        if (pageIndex === 0) {
          // turn-833 starts with actions 2500/2501 in the loaded window. The
          // next prepend adds action 2499 to that same Turn; the fold Set and
          // keyed summary row must survive while its count grows in place.
          toggleActionTimelineTurn(S._timelineView, "turn-833"); await nextFrame();
          const summaryEntry = S._timelineView.entries.find((entry) => entry.type === "turn" && entry.turnId === "turn-833");
          if (!S._timelineView.collapsedTurns.has("turn-833") || !summaryEntry || summaryEntry.stats.count !== 2) {
            throw new Error("cross-page Turn did not enter the collapsed state");
          }
        } else if (pageIndex === 1) {
          const summaryEntry = S._timelineView.entries.find((entry) => entry.type === "turn" && entry.turnId === "turn-833");
          if (!S._timelineView.collapsedTurns.has("turn-833") || !summaryEntry || summaryEntry.stats.count !== 3 ||
              !crossTurnSummaryNode?.isSameNode(anchoredAfter)) {
            throw new Error("history prepend broke or rebuilt the existing cross-page Turn fold");
          }
        }
        if (pageIndex < 5) {
          const readyFallback = document.querySelector('[data-action="load-earlier-timeline"]');
          if (!readyFallback || readyFallback.disabled || readyFallback.getAttribute("aria-busy") !== "false") {
            throw new Error(`history fallback did not re-enable after prepend ${pageIndex + 1}`);
          }
        }
      }
      if (S.actionTimeline.groups.length !== 3500 || S.actionTimeline.has_more_before) {
        throw new Error("trajectory did not retain all 3500 automatically paged actions");
      }
      const mountedRows = Array.from(document.querySelectorAll(".timeline-ledger-row"));
      const rowHeights = mountedRows.map((row) => row.getBoundingClientRect().height);
      const rowHeight = rowHeights[0] || ACTION_TIMELINE_ROW_HEIGHT;
      const rowLimit = Math.ceil(longScroll.clientHeight / ACTION_TIMELINE_ROW_HEIGHT) + ACTION_TIMELINE_OVERSCAN * 2 + 4;
      if (!mountedRows.length || mountedRows.length > rowLimit ||
          rowHeights.some((height) => Math.abs(height - ACTION_TIMELINE_ROW_HEIGHT) > 1)) {
        throw new Error(`trajectory virtual window mounted ${mountedRows.length} non-fixed rows (limit ${rowLimit})`);
      }
      const longOverview = document.querySelector(".timeline-overview svg");
      const longOverviewQueue = document.querySelector(".timeline-overview-phase.queue");
      if (S._timelineView.overview.model.items.length !== 3500 || !longOverview || longOverview.children.length > 24) {
        throw new Error("3500-action overview did not keep a complete constant-size SVG model");
      }

      // Reading above the tail pauses follow. An append must preserve both the
      // viewport's absolute position and a visible group; returning to the
      // bottom re-enables follow for the next append.
      longScroll.scrollTop = longScroll.scrollHeight - longScroll.clientHeight;
      longScroll.dispatchEvent(new Event("scroll")); await nextFrame();
      longScroll.scrollTop = Math.max(0, longScroll.scrollTop - ACTION_TIMELINE_ROW_HEIGHT * 20);
      longScroll.dispatchEvent(new Event("scroll")); await nextFrame(); await nextFrame();
      const scrollRect = longScroll.getBoundingClientRect();
      const pausedAnchor = Array.from(document.querySelectorAll(".timeline-ledger-row")).find((row) => {
        const rect = row.getBoundingClientRect();
        return rect.top >= scrollRect.top + 28 && rect.bottom <= scrollRect.bottom;
      });
      if (!pausedAnchor) throw new Error("trajectory had no fully visible paused-follow anchor");
      const pausedId = pausedAnchor.dataset.groupId;
      const pausedTop = longScroll.scrollTop;
      const pausedRectTop = pausedAnchor.getBoundingClientRect().top;
      onEvent({
        type: "action_timeline",
        root_frame_id: S.currentId,
        ...projection(longBranch, [longGroup(3500, { title: "Paused append" })]),
      });
      await nextFrame();
      const pausedAfter = document.querySelector(`.timeline-ledger-row[data-group-id="${pausedId}"]`);
      if (Math.abs(longScroll.scrollTop - pausedTop) > 2 || !pausedAfter || Math.abs(pausedAfter.getBoundingClientRect().top - pausedRectTop) > 2) {
        throw new Error("WS append interrupted paused trajectory reading");
      }

      longScroll.scrollTop = longScroll.scrollHeight - longScroll.clientHeight;
      longScroll.dispatchEvent(new Event("scroll")); await nextFrame();
      onEvent({
        type: "action_timeline",
        root_frame_id: S.currentId,
        ...projection(longBranch, [longGroup(3501, { title: "Running tail", status: "running" })]),
      });
      await nextFrame();
      if (Math.abs(longScroll.scrollHeight - longScroll.clientHeight - longScroll.scrollTop) > 2 ||
          !document.querySelector('.timeline-ledger-row[data-group-id="long-3501"]')) {
        throw new Error("trajectory did not resume tail following");
      }

      // A streaming update of the same running group reconciles the visible
      // row in place. The table, tbody, running row, and an unchanged neighbor
      // must all survive as the same DOM nodes.
      const runningRow = document.querySelector('.timeline-ledger-row[data-group-id="long-3501"]');
      const unchangedRow = document.querySelector('.timeline-ledger-row[data-group-id="long-3500"]');
      onEvent({
        type: "action_timeline",
        root_frame_id: S.currentId,
        ...projection(longBranch, [longGroup(3501, {
          title: "Completed tail",
          status: "completed",
          usage: { input_tokens: 44, output_tokens: 55, total_tokens: 99 },
        })]),
      });
      const updatedRunning = document.querySelector('.timeline-ledger-row[data-group-id="long-3501"]');
      const updatedUnchanged = document.querySelector('.timeline-ledger-row[data-group-id="long-3500"]');
      if (!longTable.isSameNode(document.querySelector(".timeline-ledger")) ||
          !longBody.isSameNode(document.querySelector(".timeline-ledger-body")) ||
          !longOverview.isSameNode(document.querySelector(".timeline-overview svg")) ||
          !longOverviewQueue.isSameNode(document.querySelector(".timeline-overview-phase.queue")) ||
          !runningRow?.isSameNode(updatedRunning) || !unchangedRow?.isSameNode(updatedUnchanged) ||
          !updatedRunning.classList.contains("status-completed") ||
          updatedRunning.querySelector(".timeline-ledger-tokens")?.textContent !== "99") {
        throw new Error("running trajectory update rebuilt the table or failed to refresh its keyed row");
      }

      // A page requested for one branch must not overwrite a newly activated
      // branch in the same root frame when its response arrives.
      S.actionTimeline = projection(longBranch, [longGroup(500)], { has_more_before: true, total_count: 1000 });
      renderActionTimeline(); await nextFrame();
      const staleRequest = loadEarlierActionTimeline();
      await waitFor(() => pendingPages.length === 1, "stale branch history request");
      const stalePage = pendingPages.shift();
      const replacementBranch = "ledger-replacement-branch";
      S.actionTimeline = projection(replacementBranch, [group("replacement-only", 0, "replacement-turn", replacementBranch)]);
      renderActionTimeline(); stalePage.release(); await staleRequest; await nextFrame();
      if (S.actionTimeline.branch_id !== replacementBranch || S.actionTimeline.groups.length !== 1 ||
          S.actionTimeline.groups[0].group_id !== "replacement-only") {
        throw new Error("a stale history response replaced the newly active branch");
      }
      onEvent({
        type: "action_timeline",
        root_frame_id: S.currentId,
        ...projection(longBranch, [longGroup(501, { title: "Stale branch WS" })]),
      });
      if (S.actionTimeline.branch_id !== replacementBranch || S.actionTimeline.groups[0].group_id !== "replacement-only") {
        throw new Error("a stale WS delta replaced the newly active branch");
      }
    } finally {
      window.fetch = originalFetch;
      S._timelineHistoryReq = (S._timelineHistoryReq || 0) + 1;
      S._timelineHistoryLoading = null;
      S.actionTimeline = saved.timeline;
      S.actionTimelineSelectedGroupId = saved.selectedGroup;
      S.actionTimelineSelectedBranchId = saved.selectedBranch;
      S.workbenchErrors = saved.workbenchErrors;
      S._timelineRestoreFocusGroupId = null;
      renderActionTimeline();
    }
  });

  // The fixed SVG overview is intentionally exercised with real browser
  // pointer input. Synthetic PointerEvent dispatch cannot prove capture,
  // wheel anchoring, or right-button drag semantics.
  const overviewFixture = await page.evaluate(async () => {
    const saved = {
      timeline: S.actionTimeline,
      selectedGroup: S.actionTimelineSelectedGroupId,
      selectedBranch: S.actionTimelineSelectedBranchId,
      workbenchErrors: { ...S.workbenchErrors },
      fetch: window.fetch,
    };
    const branch = "overview-smoke-branch", base = 1786680000000;
    const attempt = (id, allocated, started, response, finished, attemptOrdinal = 1) => ({
      attempt_id: id,
      attempt_ordinal: attemptOrdinal,
      generation_id: `generation-${id}`,
      allocated_at: allocated,
      started_at: started,
      response_at: response,
      capture_at: finished == null ? null : response + 25,
      finished_at: finished,
      terminal_state: finished == null ? null : "completed",
      error: null,
    });
    const group = (groupId, ordinal, allocatedOffset, startedOffset, responseOffset, finishedOffset, overrides = {}) => ({
      group_id: groupId,
      ordinal,
      turn_id: ordinal < 12 ? "overview-turn-a" : "overview-turn-b",
      branch_id: branch,
      kind: "code",
      title: `Overview action ${ordinal}`,
      status: finishedOffset == null ? "running" : "completed",
      owner: "overview-fixture",
      permission: "allowed · once",
      usage: { input_tokens: 2, output_tokens: 3, total_tokens: 5 },
      cost: 0.0001,
      replay_policy: "safe",
      language: "python",
      events: [],
      attempts: [attempt(
        `${groupId}-latest`,
        base + allocatedOffset,
        base + startedOffset,
        base + responseOffset,
        finishedOffset == null ? null : base + finishedOffset,
        2,
      )],
      created_at: base + allocatedOffset,
      ...overrides,
    });
    const groups = [
      group("overview-edge-left", 10, 0, 100, 400, 1000),
      group("overview-middle", 11, 1000, 1200, 1700, 3000, {
        attempts: [
          attempt("overview-middle-old", base - 50000, base - 49900, base - 49800, base - 49700, 1),
          attempt("overview-middle-latest", base + 1000, base + 1200, base + 1700, base + 3000, 2),
        ],
      }),
      group("overview-edge-right", 12, 3000, 3200, 3600, 5000),
      group("overview-running", 13, 6000, 6200, 6800, null),
    ];
    const projection = (items, overrides = {}) => ({
      root_frame_id: S.currentId,
      branch_id: branch,
      groups: items,
      count: items.length,
      total_count: 5,
      first_ordinal: items[0]?.ordinal ?? null,
      last_ordinal: items[items.length - 1]?.ordinal ?? null,
      has_more_before: true,
      has_more_after: false,
      ...overrides,
    });
    const state = window.__timelineOverviewSmoke = {
      saved, branch, base, group, projection, requestCount: 0, apiRequestCount: 0, pending: null,
      contextPrevented: false,
    };
    window.fetch = (input, init) => {
      const url = new URL(String(input), location.href);
      if (url.pathname.includes("/api/v1/")) state.apiRequestCount += 1;
      const beforeText = url.searchParams.get("before_ordinal");
      if (url.pathname.includes(`/frames/${encodeURIComponent(S.currentId)}/action-timeline`) && beforeText !== null) {
        state.requestCount += 1;
        return new Promise((resolve) => {
          state.pending = {
            before: Number(beforeText),
            release: () => {
              const earlier = group("overview-earlier", 9, -2000, -1800, -1400, -500);
              resolve(new Response(JSON.stringify(projection([earlier], {
                has_more_before: false,
                total_count: 5,
              })), { status: 200, headers: { "content-type": "application/json" } }));
            },
          };
        });
      }
      return saved.fetch(input, init);
    };
    clearTimeout(S._workbenchTimer); S._workbenchReq = (S._workbenchReq || 0) + 1;
    S._timelineHistoryReq = (S._timelineHistoryReq || 0) + 1; S._timelineHistoryLoading = null;
    S.actionTimelineSelectedGroupId = null; S.actionTimelineSelectedBranchId = null;
    S.actionTimeline = sanitizeActionTimeline(projection(groups)); renderActionTimeline();
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const view = S._timelineView, overview = view.overview, running = overview.model.byId.get("overview-running");
    const capturedRunning = actionTimelineSpan({
      ...groups[3], attempts: groups[3].attempts.map((entry) => ({ ...entry, capture_at: base + 7200 })),
    }, 0, 1);
    const incompletePoint = actionTimelineSpan({
      ...groups[0], group_id: "overview-incomplete-point", attempts: [{
        attempt_ordinal: 1, allocated_at: base, started_at: null, response_at: null,
        capture_at: null, finished_at: base + 5000,
      }],
    }, 0, 1);
    const incompletePointExtent = actionTimelineOverviewVisualExtent(incompletePoint);
    state.requestCount = 0; state.apiRequestCount = 0;
    overview.svg.addEventListener("contextmenu", (event) => { state.contextPrevented = event.defaultPrevented; });
    state.svg = overview.svg; state.queuePath = overview.queuePath; state.table = view.table; state.tbody = view.tbody;
    const point = (time, rank) => {
      const rect = overview.svg.getBoundingClientRect(), ratio = (time - base) / 6800;
      return { x: rect.left + ratio * rect.width, y: rect.top + (rank + .5) / 4 * rect.height };
    };
    return {
      base,
      hover: point(base + 1450, 1),
      selectionStart: point(base + 1000, 1.5),
      selectionEnd: point(base + 3000, 1.5),
      runningSelectionStart: point(base + 6300, 3),
      runningSelectionEnd: point(base + 6500, 3),
      dataStart: overview.dataStart,
      dataEnd: overview.dataEnd,
      running: { segments: running.segments.length, markerAt: running.markerAt, allocated: running.times.allocated },
      runningAfterKnown: actionTimelineSelectionOverlaps(running, { start: base + 6801, end: base + 7000 }),
      capturedRunning: { end: capturedRunning.end, segments: capturedRunning.segments.length, markerAt: capturedRunning.markerAt },
      incompletePoint: { activeEnd: incompletePoint.end, visualStart: incompletePointExtent.start, visualEnd: incompletePointExtent.end },
      paths: {
        queue: overview.queuePath.getAttribute("d"), ttft: overview.ttftPath.getAttribute("d"),
        decode: overview.decodePath.getAttribute("d"), marker: overview.markerPath.getAttribute("d"),
      },
      markerPath: overview.markerPath.getAttribute("d"),
      svgChildren: overview.svg.children.length,
      prefixVisible: !overview.prefixButton.classList.contains("hidden"),
    };
  });
  try {
    const settleOverview = () => page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    if (overviewFixture.dataStart !== overviewFixture.base || overviewFixture.dataEnd !== overviewFixture.base + 6800) {
      throw new Error("overview domain did not come from the latest attempt's real milestones");
    }
    if (overviewFixture.running.segments !== 0 || overviewFixture.running.markerAt !== overviewFixture.running.allocated ||
        overviewFixture.runningAfterKnown || !overviewFixture.markerPath) {
      throw new Error("running overview action was widened instead of drawn as its real start marker");
    }
    if (overviewFixture.capturedRunning.end !== overviewFixture.base + 7200 || overviewFixture.capturedRunning.segments !== 0 ||
        overviewFixture.capturedRunning.markerAt !== overviewFixture.base + 6000) {
      throw new Error("running capture_at was not kept as a real filter milestone with marker-only drawing");
    }
    if (overviewFixture.incompletePoint.activeEnd !== overviewFixture.base + 5000 ||
        overviewFixture.incompletePoint.visualStart !== overviewFixture.base || overviewFixture.incompletePoint.visualEnd !== overviewFixture.base) {
      throw new Error("incomplete attempt reveal did not use its actual point geometry");
    }
    if (overviewFixture.svgChildren > 24 || !overviewFixture.prefixVisible) {
      throw new Error("overview used per-group DOM nodes or omitted the unloaded-prefix control");
    }
    const expectedX = (offset) => String(Number((offset / 6800 * 1000).toFixed(3)));
    if (!overviewFixture.paths.queue.includes(`M${expectedX(1000)},`) || !overviewFixture.paths.queue.includes(`H${expectedX(1200)}`) ||
        !overviewFixture.paths.ttft.includes(`M${expectedX(1200)},`) || !overviewFixture.paths.ttft.includes(`H${expectedX(1700)}`) ||
        !overviewFixture.paths.decode.includes(`M${expectedX(1700)},`) || !overviewFixture.paths.decode.includes(`H${expectedX(3000)}`) ||
        !overviewFixture.paths.marker.includes(`M${expectedX(6000)},`)) {
      throw new Error(`overview phases or running marker were not linearly mapped: ${JSON.stringify(overviewFixture.paths)}`);
    }

    await page.mouse.move(overviewFixture.hover.x, overviewFixture.hover.y);
    await page.locator(".timeline-overview-tooltip").waitFor({ state: "visible", timeout: 2000 });
    const tooltipText = await page.locator(".timeline-overview-tooltip").innerText();
    for (const value of [
      new Date(overviewFixture.base + 1000).toISOString(),
      new Date(overviewFixture.base + 1200).toISOString(),
      new Date(overviewFixture.base + 1700).toISOString(),
      new Date(overviewFixture.base + 3000).toISOString(),
      "200 ms", "500 ms", "1300 ms",
    ]) {
      if (!tooltipText.includes(value)) throw new Error(`overview hover omitted ${value}`);
    }
    if (tooltipText.includes(new Date(overviewFixture.base - 50000).toISOString())) {
      throw new Error("overview hover used a stale attempt");
    }
    const tooltipBox = await page.locator(".timeline-overview-tooltip").boundingBox();
    if (!tooltipBox) throw new Error("overview tooltip had no hoverable bounds");
    const tooltipTarget = { x: tooltipBox.x + tooltipBox.width / 2, y: tooltipBox.y + tooltipBox.height / 2 };
    for (let step = 1; step <= 12; step += 1) {
      const ratio = step / 12;
      await page.mouse.move(
        overviewFixture.hover.x + (tooltipTarget.x - overviewFixture.hover.x) * ratio,
        overviewFixture.hover.y + (tooltipTarget.y - overviewFixture.hover.y) * ratio,
      );
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(resolve)));
    }
    await settleOverview();
    const hoveredTooltipBox = await page.locator(".timeline-overview-tooltip").boundingBox();
    if (!hoveredTooltipBox || Math.abs(hoveredTooltipBox.x - tooltipBox.x) > 1 || Math.abs(hoveredTooltipBox.y - tooltipBox.y) > 1) {
      throw new Error("overview tooltip moved away or disappeared while the pointer approached its exact timing content");
    }
    const refreshedTooltip = await page.evaluate(async () => {
      const state = window.__timelineOverviewSmoke;
      const current = S.actionTimeline.groups.find((group) => group.group_id === "overview-middle");
      const attempts = current.attempts.map((entry, index) => index === current.attempts.length - 1 ? { ...entry, response_at: state.base + 1800 } : entry);
      onEvent({ type: "action_timeline", root_frame_id: S.currentId, ...state.projection([{ ...current, attempts }]) });
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      return {
        text: S._timelineView.overview.tooltip.textContent,
        visible: !S._timelineView.overview.tooltip.classList.contains("hidden"),
        apiRequests: state.apiRequestCount,
      };
    });
    if (!refreshedTooltip.visible || !refreshedTooltip.text.includes(new Date(overviewFixture.base + 1800).toISOString()) ||
        !refreshedTooltip.text.includes("600 ms") || refreshedTooltip.apiRequests !== 0) {
      throw new Error(`visible overview tooltip did not refresh with its streaming group: ${JSON.stringify(refreshedTooltip)}`);
    }
    await page.keyboard.press("Escape");
    if (await page.locator(".timeline-overview-tooltip").isVisible()) {
      throw new Error("Escape did not dismiss the pointer tooltip outside overview focus");
    }

    await page.mouse.move(overviewFixture.selectionStart.x, overviewFixture.selectionStart.y);
    await page.mouse.down();
    await page.mouse.move(overviewFixture.selectionEnd.x, overviewFixture.selectionEnd.y, { steps: 8 });
    await page.mouse.up();
    await settleOverview();
    let selectionState = await page.evaluate(() => ({
      ids: S._timelineView.groups.map((group) => group.group_id),
      all: S._timelineView.allGroups.length,
      requests: window.__timelineOverviewSmoke.requestCount,
      apiRequests: window.__timelineOverviewSmoke.apiRequestCount,
      selection: { ...S._timelineView.overview.selection },
    }));
    if (selectionState.ids.join(",") !== "overview-edge-left,overview-middle,overview-edge-right" ||
        selectionState.all !== 4 || selectionState.requests !== 0 || selectionState.apiRequests !== 0) {
      throw new Error(`closed overview selection filtered or fetched incorrectly: ${JSON.stringify(selectionState)}`);
    }

    await page.mouse.move(overviewFixture.runningSelectionStart.x, overviewFixture.runningSelectionStart.y);
    await page.mouse.down();
    await page.mouse.move(overviewFixture.runningSelectionEnd.x, overviewFixture.runningSelectionEnd.y, { steps: 6 });
    await page.mouse.up(); await settleOverview();
    const runningFilter = await page.evaluate(() => ({
      ids: S._timelineView.groups.map((group) => group.group_id),
      apiRequests: window.__timelineOverviewSmoke.apiRequestCount,
    }));
    if (runningFilter.ids.join(",") !== "overview-running" || runningFilter.apiRequests !== 0) {
      throw new Error(`running action was not filtered through its latest known real milestone: ${JSON.stringify(runningFilter)}`);
    }
    await page.locator(".timeline-overview-clear").click();
    if (!await page.evaluate(() => document.activeElement === S._timelineView.overview.shell)) {
      throw new Error("hiding the overview clear button did not restore focus to the stable overview");
    }
    await page.keyboard.press("ArrowDown");
    const keyboardDetail = await page.evaluate(() => ({
      groupId: S._timelineView.overview.keyboardGroupId,
      tooltip: S._timelineView.overview.tooltip.textContent,
    }));
    if (keyboardDetail.groupId !== "overview-middle" || !keyboardDetail.tooltip.includes(new Date(overviewFixture.base + 1800).toISOString())) {
      throw new Error(`keyboard overview navigation did not expose exact timing: ${JSON.stringify(keyboardDetail)}`);
    }
    await page.keyboard.press("Shift+Enter"); await settleOverview();
    const keyboardFilter = await page.evaluate(() => ({
      ids: S._timelineView.groups.map((group) => group.group_id),
      apiRequests: window.__timelineOverviewSmoke.apiRequestCount,
    }));
    if (keyboardFilter.ids.join(",") !== "overview-edge-left,overview-middle,overview-edge-right" || keyboardFilter.apiRequests !== 0) {
      throw new Error(`keyboard time selection filtered or fetched incorrectly: ${JSON.stringify(keyboardFilter)}`);
    }
    await page.locator(".timeline-overview-clear").click();
    await page.keyboard.press("Enter");
    if (!await page.evaluate(() => S.actionTimelineSelectedGroupId === "overview-middle" &&
        S._timelineView.inspectorHost.contains(document.activeElement))) {
      throw new Error("keyboard overview activation did not focus the linked inspector");
    }
    await page.locator('.timeline-inspector[data-group-id="overview-middle"] button').click();

    await page.locator('.timeline-ledger-row[data-group-id="overview-middle"] .timeline-row-button').click();
    let linked = await page.evaluate(() => ({
      selected: S.actionTimelineSelectedGroupId,
      highlight: S._timelineView.overview.selectedPath.getAttribute("d"),
      inspector: S._timelineView.inspectorHost.textContent,
    }));
    if (linked.selected !== "overview-middle" || !linked.highlight ||
        !linked.inspector.includes(new Date(overviewFixture.base + 1000).toISOString()) ||
        !linked.inspector.includes(new Date(overviewFixture.base + 1800).toISOString()) ||
        !linked.inspector.includes("600 ms") || !linked.inspector.includes("1200 ms")) {
      throw new Error("ledger selection did not highlight the same overview group_id");
    }
    const runningPoint = await page.evaluate(() => {
      const view = S._timelineView, overview = view.overview, item = overview.model.byId.get("overview-running");
      const rect = overview.svg.getBoundingClientRect(), x = timelineOverviewTimeToX(overview, item.markerAt);
      return { x: rect.left + x / ACTION_TIMELINE_OVERVIEW_WIDTH * rect.width, y: rect.top + (item.rank + .5) / item.laneCount * rect.height };
    });
    await page.mouse.click(runningPoint.x, runningPoint.y);
    linked = await page.evaluate(() => ({
      selected: S.actionTimelineSelectedGroupId,
      selection: S._timelineView.overview.selection,
      rows: S._timelineView.groups.length,
    }));
    if (linked.selected !== "overview-running" || linked.selection || linked.rows !== 4 ||
        await page.locator('.timeline-ledger-row.selected[data-group-id="overview-running"]').count() !== 1 ||
        await page.locator('.timeline-inspector[data-group-id="overview-running"]').count() !== 1) {
      throw new Error("overview selection did not open and focus the same ledger group_id");
    }

    // Real input and pointer events verify that a searched overview resolves
    // compressed SVG lanes from its match model, not from allGroups[rank].
    await page.locator('.timeline-inspector[data-group-id="overview-running"] button').click();
    const searchRequestsBefore = await page.evaluate(() => window.__timelineOverviewSmoke.apiRequestCount);
    await page.locator(".timeline-search-input").fill("ACTION 13"); await settleOverview();
    const searchedOverview = await page.evaluate(() => ({
      ids: S._timelineView.groups.map((group) => group.group_id),
      modelIds: S._timelineView.overview.model.items.map((item) => item.groupId),
      dataStart: S._timelineView.overview.dataStart,
      dataEnd: S._timelineView.overview.dataEnd,
      count: Number(S._timelineView.search.shell.dataset.matchCount),
      status: S._timelineView.search.status.textContent,
      paths: {
        queue: S._timelineView.overview.queuePath.getAttribute("d"),
        ttft: S._timelineView.overview.ttftPath.getAttribute("d"),
        decode: S._timelineView.overview.decodePath.getAttribute("d"),
        marker: S._timelineView.overview.markerPath.getAttribute("d"),
      },
      rect: (() => { const rect = S._timelineView.overview.svg.getBoundingClientRect(); return { left: rect.left, top: rect.top, width: rect.width, height: rect.height }; })(),
    }));
    if (searchedOverview.ids.join(",") !== "overview-running" || searchedOverview.modelIds.join(",") !== "overview-running" ||
        searchedOverview.count !== 1 || searchedOverview.status !== await page.evaluate(() => t("timeline.search.matches", 1, 4)) ||
        searchedOverview.paths.queue || searchedOverview.paths.ttft || searchedOverview.paths.decode || !searchedOverview.paths.marker ||
        searchedOverview.dataStart !== overviewFixture.base || searchedOverview.dataEnd !== overviewFixture.base + 6800) {
      throw new Error(`searched overview did not keep matched bars on the loaded time domain: ${JSON.stringify(searchedOverview)}`);
    }
    const autoPageWhileSearching = await page.evaluate(async () => {
      const state = window.__timelineOverviewSmoke, view = S._timelineView, before = state.apiRequestCount;
      view.autoLoadArmed = true; view.scroll.scrollTop = 0; view.scroll.dispatchEvent(new Event("scroll"));
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      return { delta: state.apiRequestCount - before, pending: !!state.pending };
    });
    if (autoPageWhileSearching.delta || autoPageWhileSearching.pending) {
      throw new Error(`loaded-window search triggered automatic history loading: ${JSON.stringify(autoPageWhileSearching)}`);
    }
    await page.mouse.click(
      searchedOverview.rect.left + 6000 / 6800 * searchedOverview.rect.width,
      searchedOverview.rect.top + searchedOverview.rect.height * .5,
    );
    if (!await page.evaluate(() => S.actionTimelineSelectedGroupId === "overview-running" &&
        !!document.querySelector('.timeline-inspector[data-group-id="overview-running"]') &&
        !!document.querySelector('.timeline-ledger-row.selected[data-group-id="overview-running"]'))) {
      throw new Error("searched overview pointer hit selected the wrong group_id");
    }
    await page.locator(".timeline-search-clear").click(); await settleOverview();
    const clearedSearch = await page.evaluate(() => ({
      groups: S._timelineView.groups.length,
      items: S._timelineView.overview.model.items.length,
      query: S._timelineView.searchQuery,
      apiRequests: window.__timelineOverviewSmoke.apiRequestCount,
      paths: {
        queue: S._timelineView.overview.queuePath.getAttribute("d"),
        ttft: S._timelineView.overview.ttftPath.getAttribute("d"),
        decode: S._timelineView.overview.decodePath.getAttribute("d"),
        marker: S._timelineView.overview.markerPath.getAttribute("d"),
      },
    }));
    if (clearedSearch.groups !== 4 || clearedSearch.items !== 4 || clearedSearch.query || clearedSearch.apiRequests !== searchRequestsBefore ||
        !clearedSearch.paths.queue || !clearedSearch.paths.ttft || !clearedSearch.paths.decode || !clearedSearch.paths.marker) {
      throw new Error(`clearing overview search did not restore the loaded window without fetching: ${JSON.stringify(clearedSearch)}`);
    }

    // Search count follows the actual ledger intersection when a time brush is
    // also active, while separately reporting the loaded-window query total.
    await page.locator(".timeline-search-input").fill("Overview action"); await settleOverview();
    const combinedFilter = await page.evaluate(async () => {
      const state = window.__timelineOverviewSmoke, view = S._timelineView;
      commitActionTimelineOverviewSelection(view, state.base + 1000, state.base + 3000);
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      return {
        ids: view.groups.map((group) => group.group_id),
        count: Number(view.search.shell.dataset.matchCount),
        searchCount: Number(view.search.shell.dataset.searchMatchCount),
        status: view.search.status.textContent,
        expectedStatus: t("timeline.search.matchesInSelection", 3, 4, 4),
        apiRequests: state.apiRequestCount,
      };
    });
    if (combinedFilter.ids.join(",") !== "overview-edge-left,overview-middle,overview-edge-right" || combinedFilter.count !== 3 ||
        combinedFilter.searchCount !== 4 || combinedFilter.status !== combinedFilter.expectedStatus ||
        combinedFilter.apiRequests !== searchRequestsBefore) {
      throw new Error(`search/time selection count diverged from ledger rows: ${JSON.stringify(combinedFilter)}`);
    }
    await page.locator(".timeline-overview-clear").click();
    await page.locator(".timeline-search-clear").click(); await settleOverview();

    const selectionPoints = await page.evaluate(() => {
      const view = S._timelineView, overview = view.overview, rect = overview.svg.getBoundingClientRect();
      const point = (time) => ({ x: rect.left + timelineOverviewTimeToX(overview, time) / ACTION_TIMELINE_OVERVIEW_WIDTH * rect.width, y: rect.top + rect.height * .5 });
      return { start: point(window.__timelineOverviewSmoke.base + 1000), end: point(window.__timelineOverviewSmoke.base + 3000) };
    });
    await page.mouse.move(selectionPoints.start.x, selectionPoints.start.y); await page.mouse.down();
    await page.mouse.move(selectionPoints.end.x, selectionPoints.end.y, { steps: 8 }); await page.mouse.up();
    const zoomBefore = await page.evaluate(() => ({
      start: S._timelineView.overview.viewStart,
      end: S._timelineView.overview.viewEnd,
      selection: { ...S._timelineView.overview.selection },
      requests: window.__timelineOverviewSmoke.requestCount,
      apiRequests: window.__timelineOverviewSmoke.apiRequestCount,
      rect: (() => { const rect = S._timelineView.overview.svg.getBoundingClientRect(); return { left: rect.left, top: rect.top, width: rect.width, height: rect.height }; })(),
    }));
    await page.mouse.move(zoomBefore.rect.left + zoomBefore.rect.width * .5, zoomBefore.rect.top + zoomBefore.rect.height * .5);
    await page.mouse.wheel(0, -480); await settleOverview();
    const zoomAfter = await page.evaluate(() => ({
      start: S._timelineView.overview.viewStart,
      end: S._timelineView.overview.viewEnd,
      selection: { ...S._timelineView.overview.selection },
      requests: window.__timelineOverviewSmoke.requestCount,
      apiRequests: window.__timelineOverviewSmoke.apiRequestCount,
      prefixVisible: !S._timelineView.overview.prefixButton.classList.contains("hidden"),
    }));
    if (zoomAfter.end - zoomAfter.start >= zoomBefore.end - zoomBefore.start || zoomAfter.requests !== zoomBefore.requests || zoomAfter.apiRequests !== zoomBefore.apiRequests ||
        zoomAfter.selection.start !== zoomBefore.selection.start || zoomAfter.selection.end !== zoomBefore.selection.end || zoomAfter.prefixVisible) {
      throw new Error("overview wheel zoom changed selection, fetched data, or retained an out-of-view prefix");
    }

    let panRect = await page.evaluate(() => { const rect = S._timelineView.overview.svg.getBoundingClientRect(); return { left: rect.left, top: rect.top, width: rect.width, height: rect.height }; });
    const panY = panRect.top + panRect.height * .5, panX = panRect.left + panRect.width * .55;
    await page.mouse.move(panX, panY); await page.mouse.down({ button: "right" });
    await page.mouse.move(panX - panRect.width * .2, panY, { steps: 8 }); await page.mouse.up({ button: "right" });
    await settleOverview();
    const panAfter = await page.evaluate(() => ({
      start: S._timelineView.overview.viewStart,
      end: S._timelineView.overview.viewEnd,
      selection: { ...S._timelineView.overview.selection },
      requests: window.__timelineOverviewSmoke.requestCount,
      apiRequests: window.__timelineOverviewSmoke.apiRequestCount,
    }));
    if (panAfter.start <= zoomAfter.start || Math.abs((panAfter.end - panAfter.start) - (zoomAfter.end - zoomAfter.start)) > 1 ||
        panAfter.selection.start !== zoomAfter.selection.start || panAfter.selection.end !== zoomAfter.selection.end ||
        panAfter.requests !== zoomAfter.requests || panAfter.apiRequests !== zoomAfter.apiRequests) {
      throw new Error("right-drag did not pan the zoomed viewport without changing selection or fetching");
    }

    panRect = await page.evaluate(() => { const rect = S._timelineView.overview.svg.getBoundingClientRect(); return { left: rect.left, top: rect.top, width: rect.width, height: rect.height }; });
    await page.mouse.click(panRect.left + panRect.width * .5, panRect.top + panRect.height * .5, { button: "right" });
    const rightClick = await page.evaluate(() => ({
      start: S._timelineView.overview.viewStart,
      end: S._timelineView.overview.viewEnd,
      selection: S._timelineView.overview.selection,
      rows: S._timelineView.groups.length,
      requests: window.__timelineOverviewSmoke.requestCount,
      apiRequests: window.__timelineOverviewSmoke.apiRequestCount,
      contextPrevented: window.__timelineOverviewSmoke.contextPrevented,
    }));
    if (rightClick.selection || rightClick.rows !== 4 || rightClick.start !== panAfter.start || rightClick.end !== panAfter.end ||
        rightClick.requests !== panAfter.requests || rightClick.apiRequests !== panAfter.apiRequests || !rightClick.contextPrevented) {
      throw new Error("right-click did not clear only the time selection and suppress the context menu");
    }

    panRect = await page.evaluate(() => { const rect = S._timelineView.overview.svg.getBoundingClientRect(); return { left: rect.left, top: rect.top, width: rect.width, height: rect.height }; });
    const ctrlY = panRect.top + panRect.height * .5;
    await page.mouse.move(panRect.left + panRect.width * .25, ctrlY); await page.mouse.down();
    await page.mouse.move(panRect.left + panRect.width * .45, ctrlY, { steps: 6 }); await page.mouse.up(); await settleOverview();
    if (!await page.evaluate(() => !!S._timelineView.overview.selection)) throw new Error("Ctrl-click fixture did not create its selection");
    await page.keyboard.down("Control");
    await page.mouse.click(panRect.left + panRect.width * .5, ctrlY);
    await page.keyboard.up("Control"); await settleOverview();
    const ctrlClick = await page.evaluate(() => ({
      selection: S._timelineView.overview.selection,
      start: S._timelineView.overview.viewStart,
      end: S._timelineView.overview.viewEnd,
      apiRequests: window.__timelineOverviewSmoke.apiRequestCount,
    }));
    if (ctrlClick.selection || ctrlClick.start !== rightClick.start || ctrlClick.end !== rightClick.end || ctrlClick.apiRequests !== rightClick.apiRequests) {
      throw new Error(`Ctrl-click was not normalized to secondary-click clear: ${JSON.stringify(ctrlClick)}`);
    }

    const outsideGroup = await page.evaluate(() => {
      const overview = S._timelineView.overview;
      const item = overview.model.items.find((candidate) => {
        const start = candidate.markerAt == null ? candidate.start : candidate.markerAt;
        const end = candidate.markerAt == null ? candidate.end : candidate.markerAt;
        return end < overview.viewStart || start > overview.viewEnd;
      });
      return item && item.groupId;
    });
    if (!outsideGroup) throw new Error("zoomed overview fixture had no offscreen row to test linkage");
    await page.locator(`.timeline-ledger-row[data-group-id="${outsideGroup}"] .timeline-row-button`).click();
    const revealed = await page.evaluate((groupId) => {
      const overview = S._timelineView.overview, item = overview.model.byId.get(groupId);
      const point = item.markerAt == null ? item.start : item.markerAt;
      return {
        selected: S.actionTimelineSelectedGroupId,
        highlighted: !!overview.selectedPath.getAttribute("d"),
        inView: point >= overview.viewStart && point <= overview.viewEnd,
        apiRequests: window.__timelineOverviewSmoke.apiRequestCount,
      };
    }, outsideGroup);
    if (revealed.selected !== outsideGroup || !revealed.highlighted || !revealed.inView || revealed.apiRequests !== rightClick.apiRequests) {
      throw new Error(`ledger selection was not revealed in the zoomed overview: ${JSON.stringify(revealed)}`);
    }

    for (let attempt = 0; attempt < 3 && !(await page.locator('[data-action="load-omitted-timeline"]').isVisible()); attempt += 1) {
      panRect = await page.evaluate(() => { const rect = S._timelineView.overview.svg.getBoundingClientRect(); return { left: rect.left, top: rect.top, width: rect.width, height: rect.height }; });
      const y = panRect.top + panRect.height * .5, x = panRect.left + panRect.width * .5;
      await page.mouse.move(x, y); await page.mouse.down({ button: "right" });
      await page.mouse.move(panRect.left + panRect.width - 4, y, { steps: 8 }); await page.mouse.up({ button: "right" });
      await settleOverview();
    }
    const prefix = page.locator('[data-action="load-omitted-timeline"]');
    if (!(await prefix.isVisible()) || await prefix.innerText() !== "…") {
      throw new Error("loaded-domain start did not expose the neutral omitted-prefix control");
    }
    const prefixBox = await prefix.boundingBox();
    if (!prefixBox || prefixBox.width <= 0 || prefixBox.height <= 0) throw new Error("omitted-prefix control had no hit target");
    await prefix.click();
    await waitUntil("overview prefix history request", () => page.evaluate(() => !!window.__timelineOverviewSmoke.pending));
    if (await prefix.isEnabled() || await prefix.getAttribute("aria-busy") !== "true" ||
        await page.locator('[data-action="load-earlier-timeline"]').isEnabled()) {
      throw new Error("overview prefix and fallback did not share the loading state");
    }
    await page.evaluate(() => window.__timelineOverviewSmoke.pending.release());
    await waitUntil("overview prefix prepend", () => page.evaluate(() => !S._timelineHistoryLoading && S.actionTimeline.groups.length === 5));
    const prefixResult = await page.evaluate(() => ({
      requests: window.__timelineOverviewSmoke.requestCount,
      apiRequests: window.__timelineOverviewSmoke.apiRequestCount,
      first: S.actionTimeline.groups[0]?.group_id,
      items: S._timelineView.overview.model.items.length,
      hasMore: S.actionTimeline.has_more_before,
      prefixVisible: !S._timelineView.overview.prefixButton.classList.contains("hidden"),
      focusRestored: document.activeElement === S._timelineView.overview.shell,
    }));
    if (prefixResult.requests !== 1 || prefixResult.apiRequests !== 1 || prefixResult.first !== "overview-earlier" || prefixResult.items !== 5 ||
        prefixResult.hasMore || prefixResult.prefixVisible || !prefixResult.focusRestored) {
      throw new Error(`overview prefix fabricated or failed to load history: ${JSON.stringify(prefixResult)}`);
    }

    const stable = await page.evaluate(() => {
      const state = window.__timelineOverviewSmoke, current = S.actionTimeline.groups.find((group) => group.group_id === "overview-running");
      const updated = { ...current, attempts: current.attempts.map((entry, index) => index === current.attempts.length - 1 ? { ...entry, response_at: state.base + 6900 } : entry) };
      onEvent({ type: "action_timeline", root_frame_id: S.currentId, ...state.projection([updated], { has_more_before: false }) });
      const item = S._timelineView.overview.model.byId.get("overview-running");
      return {
        svg: state.svg.isSameNode(S._timelineView.overview.svg),
        path: state.queuePath.isSameNode(S._timelineView.overview.queuePath),
        table: state.table.isSameNode(S._timelineView.table),
        tbody: state.tbody.isSameNode(S._timelineView.tbody),
        runningSegments: item.segments.length,
        markerAt: item.markerAt,
      };
    });
    if (!stable.svg || !stable.path || !stable.table || !stable.tbody || stable.runningSegments !== 0 || stable.markerAt !== overviewFixture.base + 6000) {
      throw new Error("running overview update rebuilt stable DOM or widened its marker");
    }
    const noAttemptPrefix = await page.evaluate(async () => {
      const state = window.__timelineOverviewSmoke, before = state.apiRequestCount;
      const noAttempt = state.group("overview-no-attempt", 20, 0, 0, 0, 0, { attempts: [], status: "completed" });
      S.actionTimeline = sanitizeActionTimeline(state.projection([noAttempt], { has_more_before: true }));
      S._timelineView.autoLoadArmed = false; updateActionTimelineLedger({ direction: "latest" });
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const overview = S._timelineView.overview;
      return {
        dataStart: overview.dataStart,
        items: overview.model.items.length,
        visible: !overview.prefixButton.classList.contains("hidden"),
        left: overview.prefixButton.style.left,
        apiRequests: state.apiRequestCount - before,
      };
    });
    if (noAttemptPrefix.dataStart !== null || noAttemptPrefix.items !== 0 || !noAttemptPrefix.visible ||
        noAttemptPrefix.left !== "0%" || noAttemptPrefix.apiRequests !== 0) {
      throw new Error(`attempt-less loaded page hid or fabricated its omitted prefix: ${JSON.stringify(noAttemptPrefix)}`);
    }
  } finally {
    await page.evaluate(() => {
      const state = window.__timelineOverviewSmoke; if (!state) return;
      window.fetch = state.saved.fetch;
      S._timelineHistoryReq = (S._timelineHistoryReq || 0) + 1; S._timelineHistoryLoading = null;
      S.actionTimeline = state.saved.timeline; S.actionTimelineSelectedGroupId = state.saved.selectedGroup;
      S.actionTimelineSelectedBranchId = state.saved.selectedBranch; S.workbenchErrors = state.saved.workbenchErrors;
      S._timelineRestoreFocusGroupId = null; delete window.__timelineOverviewSmoke; renderActionTimeline();
    });
  }

  // Fork is a real mutation with a browser prompt. The new branch remains
  // isolated until explicit activation reconstructs its own runtime state.
  page.once("dialog", (dialog) => dialog.accept("Browser smoke fork"));
  const forkButton = page.locator(".checkpoint-row button").filter({ hasText: /^Fork$/i }).first();
  if (await forkButton.isDisabled()) throw new Error("checkpoint Fork was unexpectedly disabled");
  await forkButton.click();
  await page.locator(".branch-name").filter({ hasText: "Browser smoke fork" }).waitFor({ state: "visible" });
  await page.reload({ waitUntil: "networkidle" });
  await ensureDockOpen();
  await page.locator("#dock-tabs .dock-tab").filter({
    hasText: /Action Timeline|行动时间线/i,
  }).click();
  await page.locator(".branch-name").filter({ hasText: "Browser smoke fork" }).waitFor({ state: "visible" });

  const branchState = await api(`/frames/${encodeURIComponent(frameId)}/branches`);
  const forkedBranch = (branchState.branches || []).find((branch) => branch.name === "Browser smoke fork");
  if (!forkedBranch?.branch_id) {
    throw new Error("forked branch was not persisted by the backend");
  }
  const activation = await api(
    `/frames/${encodeURIComponent(frameId)}/branches/${encodeURIComponent(forkedBranch.branch_id)}/activate`,
    { method: "POST", data: {} },
  );
  if (!new Set(["active", "partial"]).has(String(activation.status || "").toLowerCase()) ||
      activation.current_branch_id !== forkedBranch.branch_id) {
    throw new Error("branch activation did not publish the requested runtime boundary");
  }
  const activatedBranchState = await api(`/frames/${encodeURIComponent(frameId)}/branches`);
  if (activatedBranchState.current_branch_id !== forkedBranch.branch_id &&
      activatedBranchState.branch_id !== forkedBranch.branch_id) {
    throw new Error("active branch selection was not durable");
  }

  // Exercise the mutation APIs behind the branch UI: immutable preview,
  // append-only revert, and undo. The empty workspace keeps this deterministic
  // while still proving cursor/checkpoint ownership and route composition.
  const laterCheckpoint = await api(`/frames/${encodeURIComponent(frameId)}/branches/checkpoints`, {
    method: "POST",
    data: { reason: "browser-smoke-revert-head" },
  });
  const preview = await api(`/frames/${encodeURIComponent(frameId)}/branches/revert-preview`, {
    method: "POST",
    data: { target_checkpoint_id: checkpoint.checkpoint_id },
  });
  if (!preview.preview?.can_apply || preview.preview.current_checkpoint_id !== laterCheckpoint.checkpoint_id) {
    throw new Error("revert preview did not bind the current and target checkpoints");
  }
  const reverted = await api(`/frames/${encodeURIComponent(frameId)}/branches/revert`, {
    method: "POST",
    data: { target_checkpoint_id: checkpoint.checkpoint_id },
  });
  const revertCheckpointId = reverted.checkpoint?.checkpoint_id;
  if (reverted.ok !== true || !revertCheckpointId) {
    throw new Error("branch revert did not publish an undo checkpoint");
  }
  const undone = await api(`/frames/${encodeURIComponent(frameId)}/revert/undo`, {
    method: "POST",
    data: { branch_id: forkedBranch.branch_id, revert_checkpoint_id: revertCheckpointId },
  });
  if (undone.ok !== true) throw new Error("branch revert undo failed");

  // A stopped, checkpointed namespace is view-only until explicit recovery.
  // Restore replays only the safe recipe and verifies the expected symbol.
  const recoveryFrame = await api("/frames", {
    method: "POST",
    data: { project_id: projectId },
  });
  const recoveryFrameId = recoveryFrame.id || recoveryFrame.frame_id;
  const safeCell = await api(`/frames/${encodeURIComponent(recoveryFrameId)}/kernel/execute`, {
    method: "POST",
    data: { language: "python", code: "browser_restore_value = 41", wait: true },
  });
  if (safeCell.error) throw new Error(`safe recovery cell failed: ${safeCell.error}`);
  const recoveryCheckpoint = await api(`/frames/${encodeURIComponent(recoveryFrameId)}/branches/checkpoints`, {
    method: "POST",
    data: { reason: "browser-smoke-recovery" },
  });
  if (!recoveryCheckpoint.checkpoint_id) throw new Error("recovery checkpoint was not created");
  await api(`/frames/${encodeURIComponent(recoveryFrameId)}/kernel/stop`, { method: "POST", data: {} });
  const endedKernel = await api(`/frames/${encodeURIComponent(recoveryFrameId)}/kernel`);
  if (endedKernel.alive === true || endedKernel.state === "active") {
    throw new Error("stopped recovery session did not enter Ended/view-only state");
  }
  const availableRecovery = await api(`/frames/${encodeURIComponent(recoveryFrameId)}/recovery/actions`);
  const restoreAction = (availableRecovery.actions || []).find((action) => action.id === "restore");
  if (!restoreAction?.enabled) throw new Error(`Restore was unavailable: ${restoreAction?.reason || "unknown"}`);
  const restoredKernel = await api(`/frames/${encodeURIComponent(recoveryFrameId)}/recovery/actions/restore`, {
    method: "POST",
    data: { branch_id: availableRecovery.branch_id },
  });
  if (restoredKernel.ok !== true || !["active", "partial"].includes(String(restoredKernel.status || restoredKernel.state || "").toLowerCase())) {
    throw new Error("Ended session did not reach a verified Active/Partial recovery state");
  }
  const restoredVariables = await api(`/frames/${encodeURIComponent(recoveryFrameId)}/kernel/variables?language=python`);
  if (String(restoredKernel.status || restoredKernel.state).toLowerCase() === "active" &&
      !(restoredVariables.variables || []).some((item) => item.name === "browser_restore_value")) {
    throw new Error("recovery claimed Active without restoring its required symbol");
  }

  // Session packages cross a real binary HTTP boundary. Import always creates
  // a new project/root and leaves it Ended/view-only until explicit recovery.
  const sessionExport = await page.request.get(
    new URL(`api/v1/frames/${encodeURIComponent(frameId)}/session/export`, baseUrl).toString(),
  );
  if (!sessionExport.ok() ||
      !/application\/vnd\.openai4s\.session\+zip/.test(sessionExport.headers()["content-type"] || "") ||
      !/^[0-9a-f]{64}$/.test(sessionExport.headers()["x-content-sha256"] || "")) {
    throw new Error("session export did not return a versioned, hashed package");
  }
  const sessionPackage = await sessionExport.body();
  const importResponse = await page.request.fetch(
    new URL("api/v1/sessions/import", baseUrl).toString(),
    {
      method: "POST",
      headers: { "Content-Type": "application/vnd.openai4s.session+zip" },
      data: sessionPackage,
    },
  );
  if (importResponse.status() !== 201) {
    throw new Error(`session import returned HTTP ${importResponse.status()}: ${await importResponse.text()}`);
  }
  const imported = await importResponse.json();
  if (!imported.project_id || !imported.root_frame_id || imported.root_frame_id === frameId ||
      imported.view_only !== true || imported.explicit_recovery_required !== true || imported.kernel_state !== "ended") {
    throw new Error("session import did not create a new, safe view-only root");
  }
  const importedKernel = await api(`/frames/${encodeURIComponent(imported.root_frame_id)}/kernel`);
  if (importedKernel.alive === true || importedKernel.state === "active") {
    throw new Error("imported Session started a kernel before explicit recovery");
  }

  if (workbenchSockets.length < 2) {
    throw new Error(`expected WebSocket reconnection after navigation/reload, saw ${workbenchSockets.length}`);
  }
  // ---- tabular parsing, run against the page's own loaded functions -------
  // A source-level assertion can say `delimiterFor` exists. Only the real
  // loaded code can say what it returns, and this defect was entirely about
  // the answer: a three-column TSV reported "1 column", with the whole header
  // line as the column name.
  const tabular = await page.evaluate(() => {
    const columnsOf = (name, text) => {
      const rows = parseTable(text, { filename: name });
      return rows && rows.length ? Object.keys(rows[0]).length : 0;
    };
    return {
      tsv: columnsOf("de.tsv", "gene\tlogFC\tpval\nTP53\t2.4\t0.001\n"),
      csv: columnsOf("t.csv", "gene,logFC,pval\nTP53,2.4,0.001\n"),
      // No usable extension: science writes tab-separated `.txt` constantly.
      sniffed: columnsOf("counts.txt", "gene\ts1\ts2\ts3\nA\t1\t2\t3\n"),
      // A delimiter inside a quoted field must not split it.
      quoted: columnsOf("q.tsv", 'a\tb\n"x\ty"\tz\n'),
      plain: columnsOf("notes.txt", "one column\nsecond line\n"),
    };
  });
  for (const [label, expected] of [["tsv", 3], ["csv", 3], ["sniffed", 4], ["quoted", 2], ["plain", 1]]) {
    if (tabular[label] !== expected) {
      throw new Error(
        `tabular ${label}: expected ${expected} column(s), got ${tabular[label]}`
      );
    }
  }

  // ---- the artifact table viewer must state its own truncation -----------
  // `renderSheet` caps at 5000x100 and used to append the capped table and
  // nothing else, so a 5001x101 matrix rendered as a table that looked
  // complete. Only the loaded page can prove the banner: the function needs a
  // real DOM, and the pre-fix baseline is no banner element at all.
  const sheetNotes = await page.evaluate(() => {
    const noteFor = (rows) => {
      const box = document.createElement("div");
      renderSheet(box, rows);
      const note = box.querySelector(".renderer-note");
      return note ? note.textContent : "";
    };
    const wideRow = () => { const o = {}; for (let c = 0; c < 101; c++) o["c" + c] = c; return o; };
    const tall = [];
    for (let r = 0; r < 5001; r++) tall.push({ a: r, b: r, c: r });
    return {
      tall: noteFor(tall),
      wide: noteFor([wideRow(), wideRow()]),
      // A field present only in a later record is invisible in the drawn
      // table, so the count has to come from the union of every row's keys.
      ragged: noteFor([{ a: 1 }, { a: 2, late: 3 }]),
      totalRows: (5001).toLocaleString(),
    };
  });
  for (const [label, needle] of [["tall", sheetNotes.totalRows], ["wide", "101"], ["ragged", "2"]]) {
    if (!String(sheetNotes[label]).includes(needle)) {
      throw new Error(
        `table viewer shape banner (${label}): expected it to state ${needle}, got "${sheetNotes[label]}"`
      );
    }
  }

  if (pageErrors.length) {
    throw new Error(`browser page errors: ${pageErrors.join(" | ")}`);
  }
  console.log("OpenAI4S browser smoke passed");
} finally {
  await browser.close();
}
