let playwright;
try {
  playwright = await import("playwright");
} catch (error) {
  const fallback = process.env.OPENAI4S_PLAYWRIGHT_MODULE;
  if (!fallback) throw error;
  playwright = await import(fallback);
}
const { chromium } = playwright;

const baseUrl = process.env.OPENAI4S_BROWSER_URL || "http://127.0.0.1:8760/";
const executablePath = process.env.OPENAI4S_BROWSER_EXECUTABLE || undefined;
const browser = await chromium.launch({ headless: true, executablePath });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
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
    const out = { executed: [], scriptTags: 0, imgTags: 0, missing: [] };
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
  await permissionCard.locator(".perm-allow").click();
  await permissionCard.waitFor({ state: "attached" });
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
  const upload = await api("/uploads", {
    method: "POST",
    data: {
      frame_id: frameId,
      project_id: projectId,
      filename: "browser-versioned.txt",
      content_base64: Buffer.from("VERSION-ONE", "utf8").toString("base64"),
    },
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
  if (pageErrors.length) {
    throw new Error(`browser page errors: ${pageErrors.join(" | ")}`);
  }
  console.log("OpenAI4S browser smoke passed");
} finally {
  await browser.close();
}
