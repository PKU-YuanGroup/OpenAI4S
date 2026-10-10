// Real browser + daemon + provider processes. Only the LLM is scripted.
// Start a disposable daemon with OPENAI4S_LAB_ENABLE_TOY=1 and
// OPENAI4S_NOTEBOOK_REPL=1; set OPENAI4S_DATA_DIR and OPENAI4S_BROWSER_URL.
// --real uses an installed ChemGymRL provider, never physical equipment.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import http from "node:http";
import dns from "node:dns/promises";
import { chromium } from "playwright";
import { authenticate, waitUntil, redactSecrets } from "./browser_auth.mjs";

const baseUrl = process.env.OPENAI4S_BROWSER_URL;
const dataDir = process.env.OPENAI4S_DATA_DIR;
assert.ok(baseUrl && dataDir, "explicit disposable daemon URL and data directory required");
assert.ok(["127.0.0.1", "localhost"].includes(new URL(baseUrl).hostname));
const canonical = (p) => { try { return fs.realpathSync(p); } catch { return path.resolve(p); } };
assert.notEqual(canonical(dataDir), canonical(path.join(os.homedir(), ".openai4s")));
const real = process.argv.includes("--real");
const evidenceDir = process.env.OPENAI4S_LAB_EVIDENCE_DIR;
if (evidenceDir) fs.mkdirSync(evidenceDir, { recursive: true });
const deviceId = real ? "chemgym.extractor.01" : "toy.extractor.01";
const profile = real ? "WaterOilExtract-v0" : "toy-extract-v0";
// Settling is always modeled; a real extraction run need not have liquid in
// the toy's donor vessel. Do not inspect hidden composition to pick an action.
const actionId = real ? "settle_model" : "transfer_liquid:beaker_1->extraction_vessel";
const actionValue = real ? 0.01 : 200;
const dropUpdates = process.env.OPENAI4S_LAB_DROP_UPDATES === "1";
const checks = [];
const evidence = { backend: real ? "chemgymrl" : "toy", scripted_llm: true, checks, runs: [] };
const passed = (name) => { checks.push(name); console.log(`ok  ${name}`); };
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
async function bounded(promise, label, timeout = 30000) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(`timed out: ${label}`)), timeout);
    })]);
  } finally { clearTimeout(timer); }
}

// localtest.me preserves native-tool capability selection. Resolve it before
// saving the profile and refuse anything except loopback. No real key is used.
const addresses = await dns.lookup("localtest.me", { all: true });
assert.ok(addresses.length && addresses.every((a) => ["127.0.0.1", "::1"].includes(a.address)));
let scenario;
const heldReplies = new Set();
const mockErrors = [];
// One id sequence for the whole session, so tool-call ids never repeat.
let callSerial = 0;
function reply(res, body, action) {
  const choice = action.tool ? { role: "assistant", content: null, tool_calls: [{
    id: `lab-call-${callSerial++}`, type: "function",
    function: { name: action.tool, arguments: JSON.stringify(action.args) },
  }] } : { role: "assistant", content: action.text || "Lab browser fixture" };
  const finish = action.tool ? "tool_calls" : "stop";
  if (body.stream) {
    res.writeHead(200, { "content-type": "text/event-stream" });
    const delta = { ...choice };
    if (delta.tool_calls) delta.tool_calls = delta.tool_calls.map((call, index) => ({ ...call, index }));
    res.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta, finish_reason: null }] })}\n\n`);
    res.end(`data: ${JSON.stringify({ choices: [{ index: 0, delta: {}, finish_reason: finish }] })}\n\ndata: [DONE]\n\n`);
  } else {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ choices: [{ index: 0, message: choice, finish_reason: finish }], usage: { prompt_tokens: 1, completion_tokens: 1 } }));
  }
}
const mock = http.createServer(async (req, res) => {
  try {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw);
    // Title/auxiliary calls do not consume a scripted agent action.
    if (!scenario || !body.tools?.some((t) => t.function?.name === "finalize_response")) {
      reply(res, body, { text: "Lab browser fixture" }); return;
    }
    const last = body.messages.filter((m) => m.role === "tool").at(-1);
    // A refused tool or finalize returns "[Tool error …]" text, not JSON.
    // Keep it readable so the step script's own assertion names the failure.
    let result = null;
    if (last) {
      const text = String(last.content).replace(/^\[Tool: [^\]]+\]\s*/, "");
      try { result = JSON.parse(text); } catch { result = { error: text }; }
    }
    const step = scenario.steps.shift();
    assert.ok(step, "agent called LLM after script completion (completion refused?)");
    const action = step(result);
    if (action.hold) {
      heldReplies.add(res); scenario.waiting.resolve();
      res.on("close", () => heldReplies.delete(res));
      return;
    }
    assert.ok(body.tools.some((t) => t.function?.name === action.tool), `${action.tool} absent from native tool catalog`);
    scenario.calls.push(action.tool);
    reply(res, body, action);
  } catch (error) {
    mockErrors.push(error.message);
    res.writeHead(500); res.end("Lab fixture failed");
  }
});
await new Promise((resolve) => mock.listen(0, "127.0.0.1", resolve));
const mockPort = mock.address().port;
const browser = await chromium.launch({
  headless: true, executablePath: process.env.OPENAI4S_BROWSER_EXECUTABLE || undefined,
});
const page = await browser.newPage({ viewport: { width: 1440, height: 1050 }, acceptDownloads: true });
// A cold ChemGymRL open may take up to 180 s (provider deadline); toy is fast.
page.setDefaultTimeout(real ? 240000 : 30000);
// W4-B adds a confirmation to the existing .lab-stop control.
page.on("dialog", (dialog) => dialog.type() === "confirm" ? dialog.accept() : dialog.dismiss());
await page.addInitScript(() => localStorage.setItem("os-lang", "en"));
let disconnect = false;
const liveSockets = new Set();
let socketCount = 0;
await page.routeWebSocket("**/api/v1/ws", (socket) => {
  socketCount++;
  if (disconnect) { socket.close(); return; }
  const server = socket.connectToServer();
  liveSockets.add(socket);
  socket.onClose(() => { liveSockets.delete(socket); server.close(); });
  server.onMessage((message) => {
    if (dropUpdates && JSON.parse(String(message)).type === "lab_update") return;
    socket.send(message);
  });
});
const api = async (endpoint, data, method = data === undefined ? "GET" : "POST") => {
  const response = await page.request.fetch(new URL(`/api/v1${endpoint}`, baseUrl).toString(), {
    method, data, timeout: 240000,
  });
  assert.ok(response.ok(), `${method} ${endpoint}: ${response.status()} ${redactSecrets(await response.text())}`);
  return response.json();
};
let projectId, modelId, oldConfig;
const pane = page.locator("#dock-lab");
const frames = [];
const runPath = (fid, rid) => `/frames/${fid}/lab/runs/${rid}`;
const command = (run, value = actionValue) => real
  ? { operation: "settle_model", parameters: { duration: { value, unit: "model_time" } }, expected_revision: run.revision }
  : { operation: "transfer_liquid", source: "beaker_1", target: "extraction_vessel",
    parameters: { volume: { value, unit: "mL" } }, expected_revision: run.revision };
const tool = (name, args) => ({ tool: name, args });
// A finished workflow is not a verified scientific goal. This remains partial
// under the Lab completion gate as well as the ordinary completion contract.
const report = (summary) => tool("finalize_response", { summary, task_status: "partial",
  completion_bullets: ["Reviewed the confirmed simulation receipts"] });
async function labTab() {
  if (await page.locator("#rightdock.collapsed").count()) await page.locator(".nb-tray").click();
  await page.locator("#dock-tabs .dock-tab").filter({ hasText: /^Lab$/ }).click();
  await pane.waitFor({ state: "visible" });
}
async function open(fid) {
  await page.evaluate((id) => window.openConversation(id), fid);
  await labTab();
  await pane.locator(".lab-connection").filter({ hasText: /^Connected$/ }).waitFor();
}
async function newFrame() {
  const frame = await api("/frames", { project_id: projectId });
  const fid = frame.id || frame.frame_id; frames.push(fid); return fid;
}
async function revision(n) {
  await pane.locator(".lab-metrics span").filter({ hasText: new RegExp(`^Revision ${n}$`) }).waitFor();
}
async function createManual(fid) {
  await open(fid);
  await pane.getByLabel(/^Device/).selectOption(deviceId);
  await pane.getByLabel(/^Profile/).selectOption(profile);
  await pane.getByLabel("Seed (optional)", { exact: true }).fill("7");
  await pane.getByRole("button", { name: "Create experiment", exact: true }).click();
  await revision(0);
  const index = await api(`/frames/${fid}/lab`);
  assert.equal(index.runs.length, 1);
  assert.equal(index.runs[0].status, "ready");
  return index.runs[0];
}
async function approve(allow) {
  const card = page.locator(".perm-card:not(.resolved)").filter({ hasText: /Simulation/ }).last();
  await card.waitFor();
  const resolved = page.locator(allow ? ".perm-card.resolved.allowed" : ".perm-card.resolved.denied");
  const previous = await resolved.count();
  await card.locator(".perm-scope .perm-seg").first().click(); // once, not remembered for the run
  const decision = page.waitForResponse((r) => r.request().method() === "POST" &&
    new URL(r.url()).pathname.endsWith("/decision"));
  await card.locator(allow ? ".perm-allow" : ".perm-deny").click();
  const response = await decision;
  assert.equal(response.request().postDataJSON().allow, allow);
  assert.equal(response.request().postDataJSON().scope, "once");
  assert.ok(response.ok(), `decision HTTP ${response.status()}`);
  const result = await response.json();
  assert.ok(result.ok === true || result.code === "decision_resolving");
  // Do not let the next approval bind this card during its resolving interval.
  await resolved.nth(previous).waitFor();
}
async function agentOwner(fid) {
  const owner = (await api(`/frames/${fid}/execution-queue`)).owner;
  assert.equal(owner?.owner.kind, "agent");
  assert.equal(owner.status, "running");
  return owner;
}
async function stopAgent(fid) {
  const owner = await agentOwner(fid);
  const cancelled = page.waitForResponse((r) => r.request().method() === "POST" &&
    r.url().endsWith(`/frames/${fid}/cancel`));
  await page.locator("#cancel-btn").click();
  const response = await cancelled;
  assert.ok(response.ok());
  const request = response.request().postDataJSON();
  assert.equal(request.execution_id, owner.execution_id);
  assert.deepEqual(request.owner, owner.owner);
  const result = await response.json();
  assert.equal(result.ok, true);
  assert.equal(result.execution_id, owner.execution_id);
  assert.equal(result.root_frame_id, fid);
  // Close the fixture only after the daemon has accepted the exact cancel.
  for (const res of heldReplies) res.destroy();
  await waitUntil("cancelled agent releases its execution", async () => {
    const queue = await api(`/frames/${fid}/execution-queue`);
    return ![queue.owner, ...(queue.queue || [])].some((entry) => entry?.execution_id === owner.execution_id);
  });
  await page.locator("#cancel-btn").waitFor({ state: "hidden" });
}
async function begin(goal, steps) {
  assert.equal(mockErrors.length, 0, mockErrors.join("; "));
  scenario = { steps: [...steps], serial: 0, calls: [], waiting: deferred() };
  await page.locator("#composer").fill(goal);
  await page.locator("#send-btn").click();
}
async function finished(text) {
  await page.locator("#messages").getByText(text, { exact: false }).last().waitFor();
  await page.locator("#cancel-btn").waitFor({ state: "hidden" });
  assert.equal(scenario.steps.length, 0);
  assert.equal(mockErrors.length, 0, mockErrors.join("; "));
}
async function screenshot(name) {
  if (evidenceDir) await page.screenshot({ path: path.join(evidenceDir, `${name}.png`), fullPage: true });
}
try {
  await authenticate(page, baseUrl);
  oldConfig = await api("/config/llm");
  assert.equal(oldConfig.has_api_key, false, "the Lab harness needs a daemon without credentials");
  const saved = await api("/model-profiles", { name: "Lab browser local fixture", provider: "ark",
    base_url: `http://localtest.me:${mockPort}/v1`, model: "lab-browser-fixture", api_key: "lab-browser-fixture" });
  modelId = saved.id;
  await api(`/model-profiles/${modelId}/activate`, {});
  const project = await api("/projects", { name: "Lab browser acceptance" });
  projectId = project.project_id || project.id;
  const fid = await newFrame();
  let run = await createManual(fid);
  const rid = run.run_id;
  passed("device list and manual experiment creation");

  await pane.getByLabel(/^Operation/).selectOption(actionId);
  await pane.getByLabel(real ? /^duration \(model_time\)/ : /^volume \(mL\)/).selectOption(String(actionValue));
  let posts = 0;
  const gate = deferred(), received = deferred();
  const commandUrl = `**/api/v1${runPath(fid, rid)}/commands`;
  await page.route(commandUrl, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    posts++; received.resolve(); await gate.promise; await route.continue();
  });
  // Two actual click events, with the first POST held until the second event.
  try {
    await pane.getByRole("button", { name: "Run one step", exact: true }).dblclick();
    await bounded(received.promise, "manual command POST");
  } finally { gate.resolve(); }
  await revision(1);
  await page.unroute(commandUrl);
  let detail = await api(runPath(fid, rid));
  assert.equal(posts, 1); assert.equal(detail.run.step_count, 1); assert.equal(detail.commands.length, 1);
  assert.equal(detail.commands[0].origin, "manual_ui");
  passed("double-click dispatches and applies exactly one manual step");

  await begin("In the simulation lab, take one more allowed step if I approve it.", [
    () => tool("lab_execute", { run_id: rid, ...command(detail.run) }),
    (r) => { assert.ok(r.error); return report("Lab denial confirmed; no additional step was applied."); },
  ]);
  await approve(false);
  await finished("Lab denial confirmed");
  detail = await api(runPath(fid, rid));
  assert.equal(detail.run.revision, 1); assert.equal(detail.commands.length, 1);
  passed("native agent Lab command Deny leaves the experiment unchanged");

  await begin("In the simulation lab, take one allowed step after approval and report the confirmed receipt.", [
    () => tool("lab_execute", { run_id: rid, ...command(detail.run) }),
    (r) => { assert.equal(r.command.state, "succeeded"); return report("Lab approved step confirmed by the receipt."); },
  ]);
  await approve(true); await finished("Lab approved step confirmed"); await revision(2);
  detail = await api(runPath(fid, rid));
  assert.equal(detail.commands.at(-1).origin, "agent_tool");
  passed("native agent Lab command Allow advances the real provider");

  // No refresh button/navigation/agent terminal event can rescue this check:
  // an independent HTTP mutation must reach this idle page via lab_update.
  await page.waitForLoadState("networkidle");
  await api(`${runPath(fid, rid)}/commands`, { ...command(detail.run), idempotency_key: `ws-${rid}` });
  await revision(3);
  passed("unsolicited lab_update refreshes an idle visible Lab pane");

  disconnect = true;
  for (const socket of liveSockets) socket.close();
  await pane.locator(".lab-connection").filter({ hasText: /^Disconnected/ }).waitFor();
  detail = await api(runPath(fid, rid));
  await api(`${runPath(fid, rid)}/commands`, { ...command(detail.run), idempotency_key: `offline-${rid}` });
  assert.match(await pane.locator(".lab-metrics").innerText(), /Revision 3/);
  const beforeReconnect = socketCount; disconnect = false;
  await pane.locator(".lab-connection").filter({ hasText: /^Connected$/ }).waitFor();
  await revision(4); assert.ok(socketCount > beforeReconnect);
  passed("WebSocket disconnect shows last confirmed state and reconnect reads current state");

  const fidB = await newFrame();
  const runB = await createManual(fidB);
  const held = deferred(), read = deferred(), delivered = deferred();
  let captured = false;
  const indexUrl = `**/api/v1/frames/${fid}/lab`;
  await page.route(indexUrl, async (route) => {
    if (captured) return route.continue();
    captured = true;
    const response = await route.fetch(); read.resolve(); await held.promise;
    await route.fulfill({ response });
    delivered.resolve();
  });
  try {
    await page.evaluate((id) => { void window.openConversation(id); }, fid);
    await bounded(read.promise, "late session A read");
    await open(fidB); await revision(0);
    // Record every run the B pane shows from here on, including transient
    // states a later refresh would repair (an assertion alone races them).
    await page.evaluate(() => {
      const root = document.querySelector("#dock-lab");
      const seen = (window.__labSeen = []);
      const record = () => {
        for (const option of root.querySelectorAll("option")) seen.push(option.value);
        seen.push(root.querySelector(".lab-overview")?.textContent || "");
      };
      record();
      new MutationObserver(record).observe(root, { subtree: true, childList: true, characterData: true, attributes: true });
    });
  } finally { held.resolve(); }
  await bounded(delivered.promise, "late session A response delivered");
  await page.unroute(indexUrl);
  // A stale write follows the delivered read with its own detail fetches;
  // give it far longer than those take before judging what B ever showed.
  await page.waitForTimeout(2000);
  const seen = await page.evaluate(() => window.__labSeen);
  assert.ok(seen.length > 0);
  assert.ok(!seen.some((value) => value.includes(rid)), `session B showed session A's run ${rid}`);
  assert.equal(await pane.getByLabel(/^Run/).inputValue(), runB.run_id);
  assert.equal(await pane.locator(".lab-command").count(), 0);
  await open(fid); await revision(4);
  passed("late Lab response from session A never appears in session B");

  const checkpoint = await api(`/frames/${fid}/branches/checkpoints`, { reason: "lab-browser" });
  const branchState = await api(`/frames/${fid}/branches`);
  const originalBranch = branchState.current_branch_id || branchState.branch_id;
  const fork = await api(`/frames/${fid}/branches/fork`, { from_checkpoint_id: checkpoint.checkpoint_id, name: "Lab browser fork" });
  const forkId = fork.branch_id || fork.branch?.branch_id;
  assert.ok(originalBranch && forkId);
  for (const branchId of [forkId, originalBranch, forkId]) {
    await page.locator("#dock-tabs .dock-tab").filter({ hasText: /^Action Timeline$/ }).click();
    const response = page.waitForResponse((r) => r.request().method() === "POST" &&
      r.url().endsWith(`/branches/${branchId}/activate`));
    await page.locator(`[data-focus-key="branch-activate:${branchId}"]`).click();
    assert.equal((await (await response).json()).current_branch_id, branchId);
    await page.locator(`[data-focus-key="branch-activate:${branchId}"]`).waitFor({ state: "detached" });
  }
  await open(fid); await revision(4);
  assert.equal((await api(runPath(fid, rid))).run.command_count, 4);
  passed("branch activations preserve session Lab history without replaying commands");
  await page.reload({ waitUntil: "networkidle" }); await labTab(); await revision(4);
  assert.equal(await pane.getByLabel(/^Run/).inputValue(), rid);
  await pane.locator(".lab-command").nth(3).waitFor();
  assert.equal(await pane.locator(".lab-command").count(), 4);
  await pane.getByText("Observation sequence 4", { exact: false }).first().waitFor();
  passed("page reload restores the persisted run, its four receipts and the latest observation");

  const holdSteps = [() => tool("lab_observe", { run_id: rid }), () => ({ hold: true })];
  await begin("In the simulation lab, keep planning until I stop the agent.", holdSteps);
  await bounded(scenario.waiting.promise, "agent reaches held model response");
  await stopAgent(fid);
  assert.equal((await api(runPath(fid, rid))).run.status, "ready");
  passed("agent Stop targets owner=agent and leaves the Lab experiment ready");

  await begin("In the simulation lab, keep planning while I stop the experiment separately.", holdSteps);
  await bounded(scenario.waiting.promise, "second agent reaches held model response");
  const planning = await agentOwner(fid);
  await pane.locator(".lab-stop").click();
  await pane.getByText("End reason: stopped", { exact: true }).waitFor();
  assert.equal(await page.locator("#cancel-btn").isVisible(), true);
  assert.equal((await agentOwner(fid)).execution_id, planning.execution_id);
  await stopAgent(fid);
  assert.equal((await api(runPath(fid, rid))).run.end_reason, "stopped");
  passed("Lab Stop ends its provider without stopping the agent turn");
  await screenshot("independent-stops");
  await api(`${runPath(fidB, runB.run_id)}/stop`, {});

  const exportFid = await newFrame();
  const exportRun = await createManual(exportFid);
  await pane.getByLabel(/^Operation/).selectOption(actionId);
  await pane.getByLabel(real ? /^duration \(model_time\)/ : /^volume \(mL\)/).selectOption(String(actionValue));
  await pane.getByRole("button", { name: "Run one step", exact: true }).click();
  await revision(1);
  const results = pane.locator(".lab-results");
  const exportedResponse = page.waitForResponse((r) => r.request().method() === "POST" && r.url().endsWith("/export"));
  await results.getByRole("button", { name: "Export recorded evidence", exact: true }).click();
  const exported = await (await exportedResponse).json();
  assert.deepEqual(exported.artifacts.map((a) => a.kind).sort(), ["actions", "observations_csv", "observations_json", "report"]);
  assert.equal(exported.ground_truth, undefined);
  await pane.locator(".lab-export-links a").nth(3).waitFor();
  const bound = await api(`${runPath(exportFid, exportRun.run_id)}/observations`);
  const observationVersion = exported.artifacts.find((a) => a.kind === "observations_json").version_id;
  assert.ok(bound.observations.every((o) => o.artifact_version_id === observationVersion));
  passed("export records four exact Artifact versions and binds the observations");

  await results.getByLabel(/ground truth/i).check();
  const truthResponse = page.waitForResponse((r) => r.request().method() === "POST" && r.url().endsWith("/export"));
  const download = page.waitForEvent("download");
  await results.getByRole("button", { name: "Export recorded evidence", exact: true }).click();
  const truthExport = await (await truthResponse).json();
  const truthFile = await download;
  assert.match(truthFile.suggestedFilename(), /-simulation-ground-truth\.json$/);
  const truthText = fs.readFileSync(await truthFile.path(), "utf8");
  assert.match(truthText, /Simulation ground truth/);
  assert.ok(!truthExport.artifacts.some((a) => /ground-truth/.test(a.filename)));
  // The second export adds new versions of the same four Artifacts, and
  // nothing else: the session holds no ground-truth Artifact at all.
  const byKind = (result) => Object.fromEntries(result.artifacts.map((a) => [a.kind, a]));
  const [first, second] = [byKind(exported), byKind(truthExport)];
  for (const kind of Object.keys(first)) {
    assert.equal(second[kind].artifact_id, first[kind].artifact_id);
    assert.notEqual(second[kind].version_id, first[kind].version_id);
  }
  const stored = await api(`/frames/${exportFid}/artifacts`);
  assert.deepEqual(stored.map((a) => a.id ?? a.artifact_id).sort(), Object.values(first).map((a) => a.artifact_id).sort());
  assert.ok(!stored.some((a) => /ground.truth/.test(JSON.stringify(a))));
  await results.getByRole("status").filter({ hasText: "Simulation ground truth was downloaded to this computer" }).waitFor();
  passed("opted-in ground truth downloads to this computer and never becomes a session Artifact");

  const writes = [];
  const trackWrites = (request) => { if (request.method() !== "GET") writes.push(request.url()); };
  page.on("request", trackWrites);
  await results.getByRole("button", { name: "Replay recorded history", exact: true }).click();
  const slider = pane.locator(".lab-replay input[type=range]");
  await slider.waitFor();
  await slider.fill("1"); await slider.fill("0");
  page.off("request", trackWrites);
  assert.deepEqual(writes, []);
  assert.equal((await api(runPath(exportFid, exportRun.run_id))).run.revision, 1);
  passed("replay moves through recorded history without any request but reads");

  await pane.getByRole("button", { name: "End experiment", exact: true }).click();
  await pane.getByText("End reason: end_action", { exact: true }).waitFor();
  assert.equal((await api(runPath(exportFid, exportRun.run_id))).run.end_reason, "end_action");
  passed("End experiment executes end_experiment and records end_action, unlike Stop");

  if (!real) {
    // The completion gate in both directions, through a real turn. Only the
    // toy profile declares a goal (a CI goal, not chemistry): a 200 mL
    // transfer and end_experiment meet it, so a completed claim declaring
    // that run is accepted; the same claim about a stopped run is refused
    // back to the model, which then reports honestly.
    const goalFid = await newFrame(); await open(goalFid);
    let goalRun;
    await begin("In the simulation lab, run the toy extraction to its goal, end it, and report completion with the run declared.", [
      () => tool("lab_create", { device_id: deviceId, profile, seed: 7 }),
      (r) => { goalRun = r.run; return tool("lab_execute", { run_id: goalRun.run_id, ...command(goalRun) }); },
      (r) => { assert.equal(r.command.state, "succeeded"); return tool("lab_execute", { run_id: goalRun.run_id, operation: "end_experiment", expected_revision: r.run.revision }); },
      (r) => { assert.equal(r.run.end_reason, "end_action"); return tool("finalize_response", {
        summary: `Toy goal report: run ${goalRun.run_id} ended with end_action; the host verified its declared toy CI goal.`,
        completion_bullets: ["Declared the ended toy run"],
        lab_runs: [{ run_id: goalRun.run_id, status: "completed" }] }); },
    ]);
    await approve(true); await approve(true); await approve(true);
    await finished("Toy goal report:");
    passed("a completed claim declaring an ended toy run passes the Lab completion gate");

    const stopFid = await newFrame(); await open(stopFid);
    let stopRun, refusal = "";
    await begin("In the simulation lab, run the toy extraction, stop it, and report.", [
      () => tool("lab_create", { device_id: deviceId, profile, seed: 7 }),
      (r) => { stopRun = r.run; return tool("lab_execute", { run_id: stopRun.run_id, ...command(stopRun) }); },
      (r) => { assert.equal(r.command.state, "succeeded"); return tool("lab_stop", { run_id: stopRun.run_id }); },
      (r) => { assert.equal(r.run.end_reason, "stopped"); return tool("finalize_response", {
        summary: `Toy stop claim: run ${stopRun.run_id} reached its goal.`,
        completion_bullets: ["Declared the stopped toy run"],
        lab_runs: [{ run_id: stopRun.run_id, status: "completed" }] }); },
      (r) => { refusal = String(r?.error || ""); return report(`Honest stop report: run ${stopRun.run_id} was stopped after one confirmed transfer and did not end normally, so no goal is claimed.`); },
    ]);
    await approve(true); await approve(true);
    await finished("Honest stop report:");
    assert.match(refusal, /not ended normally/);
    assert.doesNotMatch(refusal, /reward|ground_truth|moles|purity|\d+\.\d/);
    assert.equal(await page.locator("#messages").getByText("Toy stop claim:", { exact: false }).count(), 0);
    passed("the same claim after Stop is refused value-free and the honest report is accepted");
    await screenshot("completion-gate");
  }

  // Full goal -> inspection -> refusal -> adjustment -> end -> report loop.
  const loopFid = await newFrame(); await open(loopFid);
  let loopRun;
  await begin("In the simulation lab, create an extraction experiment, inspect its feedback, adjust an unsupported action to an allowed setting, then end the experiment and report only confirmed observations.", [
    () => tool("lab_create", { device_id: deviceId, profile, seed: 7 }),
    (r) => { loopRun = r.run; return tool("lab_observe", { run_id: loopRun.run_id }); },
    (r) => { assert.equal(r.observation.sequence, 0); return tool("lab_execute", { run_id: loopRun.run_id, ...command(loopRun, 123) }); },
    (r) => { assert.equal(r.command.state, "rejected"); assert.equal(r.command.error_code, "unsupported_action"); return tool("lab_execute", { run_id: loopRun.run_id, ...command(r.run) }); },
    (r) => { assert.equal(r.command.state, "succeeded"); return tool("lab_observe", { run_id: loopRun.run_id }); },
    (r) => { assert.equal(r.observation.sequence, 1); return tool("lab_execute", { run_id: loopRun.run_id, operation: "end_experiment", expected_revision: r.run.revision }); },
    (r) => { assert.equal(r.run.end_reason, "end_action"); return tool("lab_status", { run_id: loopRun.run_id }); },
    (r) => { assert.equal(r.run.status, "ended"); return report(`Lab simulation report: run ${loopRun.run_id} ended with end_action. The unsupported setting was rejected; the adjusted action and end action have confirmed receipts. Sensor readings are simulated; composition and experimental success remain unknown.`); },
  ]);
  await approve(true); // create
  await approve(true); // unsupported action still crosses the permission boundary
  await approve(true); // adjusted transfer
  await approve(true); // normal end action
  await finished("Lab simulation report:");
  await pane.getByText("End reason: end_action", { exact: true }).waitFor();
  const loopDetail = await api(runPath(loopFid, loopRun.run_id));
  assert.deepEqual(loopDetail.commands.map((c) => c.state), ["rejected", "succeeded", "succeeded"]);
  assert.deepEqual(loopDetail.commands.map((c) => c.origin), ["agent_tool", "agent_tool", "agent_tool"]);
  assert.equal(loopDetail.run.revision, 2); assert.equal(loopDetail.observation.sequence, 2);
  const safeText = JSON.stringify(loopDetail);
  assert.doesNotMatch(safeText, /"(?:evaluation|ground_truth|reward|provider_action|moles)"/);
  evidence.runs.push(await api(runPath(fid, rid)), loopDetail);
  evidence.native_calls = scenario.calls;
  passed("natural-language goal, feedback, adjustment, normal end and receipt-grounded report");
  await screenshot("complete-report");
  await page.reload({ waitUntil: "networkidle" }); await labTab();
  await page.locator("#messages").getByText("Lab simulation report:", { exact: false }).last().waitFor();
  await pane.getByText("End reason: end_action", { exact: true }).waitFor();
  passed("completed report and experiment survive reload");
  await screenshot("reloaded-report");
} catch (error) {
  evidence.error = redactSecrets(error.stack || String(error));
  await screenshot("failure").catch(() => {});
  console.error(evidence.error);
  if (mockErrors.length) console.error(mockErrors.join("; "));
  process.exitCode = 1;
} finally {
  disconnect = false;
  for (const res of heldReplies) res.destroy();
  // Cleanup exactly our project/profile, even on a failed assertion.
  try {
    for (const fid of frames) await api(`/frames/${fid}/cancel`, {}).catch(() => {});
    if (projectId) await api(`/projects/${projectId}`, undefined, "DELETE");
    if (modelId) await api(`/model-profiles/${modelId}`, undefined, "DELETE");
    if (oldConfig && !oldConfig.has_api_key) await api("/config/llm", { provider: oldConfig.provider,
      model: oldConfig.model, base_url: oldConfig.base_url, clear_api_key: true }, "PUT");
  } catch (error) { console.error(`cleanup failed: ${redactSecrets(error.message)}`); process.exitCode = 1; }
  if (evidenceDir) fs.writeFileSync(path.join(evidenceDir, "evidence.json"), JSON.stringify(evidence, null, 2) + "\n");
  await browser.close();
  mock.closeAllConnections(); await new Promise((resolve) => mock.close(resolve));
}
console.log(`Lab browser: ${checks.length} checks; backend=${evidence.backend}; ${process.exitCode ? "FAILED" : "PASS"}`);
