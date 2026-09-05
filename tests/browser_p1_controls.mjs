// The P1-A and P1-B controls, in a real browser.
//
// The plan asks for browser evidence behind every user-visible capability. The
// three existing `browser_*.mjs` files scored zero keyword hits on all eight of
// these — `before_seq`, `newest_first`, chip, profile, attachment, delegation,
// steer, memory — so the whole group rested on one manual walkthrough taken 43
// commits before the audit.
//
// Each check below drives real product code in a real, logged-in page. Two
// shapes, chosen per capability rather than uniformly:
//
//   * the paging checks call the client's own fetch helpers against the live
//     route, because their entire content is which query string goes on the
//     wire — a DOM assertion could not see it;
//   * the rendering checks call the real render function into a detached node,
//     the idiom `browser_smoke.mjs` already uses for `renderSheet`. These
//     functions need a real DOM and a loaded `S`, and the pre-fix baseline for
//     several of them is that the element does not exist at all.
//
// What this file deliberately does NOT do is assert on the daemon's own
// seeded data beyond the example session's two messages: a check whose fixture
// is whatever the developer's database happens to hold passes for reasons that
// have nothing to do with the code.

let playwright;
try {
  playwright = await import("playwright");
} catch (error) {
  const fallback = process.env.OPENAI4S_PLAYWRIGHT_MODULE;
  if (!fallback) throw error;
  playwright = await import(fallback);
}
const { chromium } = playwright;
import { authenticate, waitUntil } from "./browser_auth.mjs";

const baseUrl = process.env.OPENAI4S_BROWSER_URL || "http://127.0.0.1:8760/";
const executablePath = process.env.OPENAI4S_BROWSER_EXECUTABLE || undefined;
const browser = await chromium.launch({ headless: true, executablePath });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
await authenticate(page, baseUrl);

const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(String(error)));

const failures = [];
function check(label, condition, detail) {
  if (!condition) failures.push(`${label}${detail ? `: ${detail}` : ""}`);
}

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

try {
  // A profile pointing at a name that cannot resolve. `.invalid` is reserved by
  // RFC 2606, so the panel check below can assert the row states the endpoint
  // it would call without that endpoint existing.
  const profile = await api("/model-profiles", {
    method: "POST",
    data: {
      name: "P1 coverage profile",
      provider: "chatgpt",
      base_url: "https://api.p1-coverage.invalid/v1",
      model: "p1-coverage-model",
      api_key: "sk-not-a-real-key",
    },
  });
  const profileId = profile.id || (profile.profile || {}).id;
  if (!profileId) throw new Error("profile creation did not return an id");

  const project = await api("/projects", {
    method: "POST",
    data: { name: "P1 control coverage", description: "CI-only workbench state" },
  });
  const projectId = project.project_id || project.id;
  const frame = await api("/frames", { method: "POST", data: { project_id: projectId } });
  const frameId = frame.id || frame.frame_id;

  const deepLink = new URL(
    `projects/${encodeURIComponent(projectId)}/frames/${encodeURIComponent(frameId)}`,
    baseUrl,
  ).toString();
  const response = await page.goto(deepLink, { waitUntil: "networkidle" });
  if (!response || !response.ok()) {
    throw new Error(`workspace deep link returned HTTP ${response?.status() ?? "unknown"}`);
  }
  await page.locator("#workspace:not(.hidden)").waitFor({ state: "visible" });

  // ---- P1-B: newest-first paging and the keyset cursor --------------------
  // Every message fetch used to send `?from=0&limit=N` — the OLDEST N — so a
  // 640-message session opened on messages 0..299 and the work the reader came
  // back for was off the end. `newest_first` / `before_seq` / `next_before_seq`
  // went in through the store, the repository and the route, and `app.js`
  // contained neither string. Two messages are enough to tell the two clients
  // apart: the fixed client asks for seq 1, the broken one for seq 0.
  //
  // The fixture is built here rather than borrowed. The first version of this
  // check seeded the product's example session, which passed on the author's
  // machine — where that session already existed, so the route short-circuited
  // — and failed in CI, where a fresh data directory made `POST /example/session`
  // demand `{"confirm": true}` and then run six cells against two external
  // APIs. That is exactly the "fixture is whatever the developer's database
  // holds" trap this file's own header warns about.
  //
  // What replaces it is one turn that cannot succeed. `run_message` writes the
  // user row *before* the model is called and the failure path writes the
  // terminal row, so a turn that dies on its first LLM call leaves exactly seq 0
  // and seq 1 — no kernel, no network, ~0.2 s.
  //
  // THE INVARIANT THIS DEPENDS ON, stated rather than assumed: the daemon has
  // no usable LLM credential. That is the CI posture — `ci.yml`'s browser job
  // sets no `OPENAI4S_*_API_KEY` — and it is checked below instead of hoped
  // for. On a machine that *does* hold a working credential this turn really
  // runs, and the throw says so in as many words rather than hanging or
  // quietly passing. (`model` on the frame does not change that: the override
  // reaching dispatch comes from the message body, not the frame column, so a
  // bogus model name with a valid key still reaches a live provider.)
  const pagingFrame = await api("/frames", {
    method: "POST",
    data: { project_id: projectId },
  });
  const pagingFrameId = pagingFrame.id || pagingFrame.frame_id;
  // `request`, not `content`: the route reads `input_data.request` or
  // `request` and never `content`, so the earlier spelling stored an empty
  // user row that still counted as two messages.
  const turn = await api(`/frames/${encodeURIComponent(pagingFrameId)}/message`, {
    method: "POST",
    data: { request: "paging fixture", wait: true },
  });
  if (turn.status !== "failed") {
    throw new Error(
      `the paging fixture needs a turn that cannot succeed, and this one reported ` +
        `"${turn.status}". This daemon holds a usable LLM credential, so the ` +
        `fixture's stated invariant does not hold here — run this file against a ` +
        `daemon with no model credential configured, as ci.yml does.`,
    );
  }
  const seeded = await api(
    `/frames/${encodeURIComponent(pagingFrameId)}/messages?limit=20`,
  );
  if ((seeded.messages || []).length !== 2) {
    throw new Error(
      `the fixture turn wrote ${(seeded.messages || []).length} messages, not 2; ` +
        "every assertion below depends on exactly seq 0 and seq 1 existing",
    );
  }

  // What only a browser can show for this defect is which query string the
  // client puts on the wire — the route's side is covered by
  // tests/test_session_and_message_paging.py. Recorded from the real requests
  // rather than inferred from the rows, so a client that returned the right
  // rows by accident still fails.
  const messageRequests = [];
  const recordRequest = (request) => {
    const url = request.url();
    if (/\/api\/v1\/frames\/[^/]+\/messages/.test(url)) messageRequests.push(url);
  };
  page.on("request", recordRequest);

  const paging = await page.evaluate(async (fid) => {
    const newest = await fetchRecentMessages(fid, 1);
    const older = newest.next_before_seq == null
      ? null
      : await fetchOlderMessages(fid, newest.next_before_seq, 5);
    const whole = await fetchAllMessages(fid);
    return {
      newestSeqs: (newest.messages || []).map((m) => m.seq),
      cursor: newest.next_before_seq,
      hasEarlier: !!newest.has_earlier,
      olderSeqs: older ? (older.messages || []).map((m) => m.seq) : null,
      wholeSeqs: (whole.messages || []).map((m) => m.seq),
      wholeComplete: !!whole.complete,
    };
  }, pagingFrameId);
  page.off("request", recordRequest);

  check(
    "newest-first paging",
    paging.newestSeqs.length === 1 && paging.newestSeqs[0] === 1,
    `asked for one message and got seq ${JSON.stringify(paging.newestSeqs)}; ` +
      `seq 0 means the client dropped newest_first and fetched the oldest page`,
  );
  check("paging cursor", paging.cursor === 1, `next_before_seq was ${paging.cursor}`);
  check("paging has_earlier", paging.hasEarlier === true, "the route said there was nothing earlier");
  check(
    "before_seq is a keyset bound",
    Array.isArray(paging.olderSeqs) && paging.olderSeqs.every((s) => s < paging.cursor),
    `older page returned ${JSON.stringify(paging.olderSeqs)}, not strictly before ${paging.cursor}`,
  );
  check(
    "the walk returns the whole conversation, oldest first",
    JSON.stringify(paging.wholeSeqs) === JSON.stringify([0, 1]) && paging.wholeComplete,
    `walk produced ${JSON.stringify(paging.wholeSeqs)} complete=${paging.wholeComplete}`,
  );

  // The query strings themselves. A client that dropped `newest_first` and got
  // the right answer anyway — because the session is short enough that the
  // oldest page and the newest page coincide — would satisfy every assertion
  // above. This is the one that cannot be satisfied by accident.
  check(
    "the client asks the route for the newest page",
    messageRequests.some((url) => url.includes("newest_first=1")),
    `no request carried newest_first=1; observed ${JSON.stringify(messageRequests)}`,
  );
  check(
    "and pages backwards with a keyset cursor, not an offset",
    messageRequests.some((url) => url.includes("before_seq=")) &&
      !messageRequests.some((url) => /[?&]from=/.test(url)),
    `observed ${JSON.stringify(messageRequests)}`,
  );

  // ---- P1-A: the message reference chip ----------------------------------
  // The reference used to exist only as `@name#v-id` inside the message text,
  // so it was there to read and not to see. The chip draws the stored record —
  // version, digest head, and the arrow when the file was copied in from
  // another session — and `truncated`/`sent_bytes` now ride the same record.
  const chips = await page.evaluate(() => {
    const host = document.createElement("div");
    renderMessageRefChips(host, [
      { display_name: "counts.csv", version_id: "v-aaaaaaaaaaaa", sha256: "abcdef0123456789", artifact_id: "a-1", materialized_target: null, sent_bytes: 12 },
      { display_name: "big.csv", version_id: "v-bbbbbbbbbbbb", sha256: "fedcba9876543210", artifact_id: "a-2", materialized_target: "v-cccccccccccc", source_session: "f-otherssssss", sent_bytes: 200000, truncated: true },
      { display_name: "", version_id: "v-dddddddddddd" },
    ]);
    const drawn = [...host.querySelectorAll(".msg-ref-chip")];
    return {
      count: drawn.length,
      titles: drawn.map((c) => c.title),
      texts: drawn.map((c) => c.textContent),
    };
  });
  check("ref chips drawn", chips.count === 2, `drew ${chips.count} chips for 2 nameable refs`);
  check(
    "the chip states the version it sent",
    chips.titles[0].includes("v-aaaaaaaaaaaa") && chips.titles[0].includes("sha256:abcdef012345"),
    chips.titles[0],
  );
  check(
    "a materialised chip names the session it came from",
    chips.titles[1].includes("↗") && chips.titles[1].includes("f-otherssss"),
    chips.titles[1],
  );
  check("an unnamed ref draws nothing", !chips.texts.some((x) => x === ""), JSON.stringify(chips.texts));

  // ---- P1-A: the composer chip, drawn before the send --------------------
  // A token typed by hand, or one whose artifact was deleted, looked exactly
  // like one that resolves. Unresolved is drawn rather than dropped, because
  // "this will not do what you think" is the entire reason to show it early.
  const composerChips = await page.evaluate(() => {
    S.artifacts = [{ artifact_id: "a-9", id: "a-9", filename: "known.csv", version_id: "v-known000000", checksum: "0f0f0f0f0f0f0f0f", root_frame_id: S.currentId }];
    const box = document.querySelector("#composer");
    const previous = box.value;
    box.value = "compare @known.csv#v-known000000 with @ghost.csv";
    renderComposerRefChips();
    const host = document.querySelector("#composer-refs");
    const result = {
      total: host.querySelectorAll(".msg-ref-chip").length,
      unresolved: host.querySelectorAll(".msg-ref-chip.unresolved").length,
      hidden: host.classList.contains("hidden"),
    };
    box.value = previous;
    renderComposerRefChips();
    return result;
  });
  check("composer chips", composerChips.total === 2, `drew ${composerChips.total}`);
  check(
    "an unresolvable token is shown, not dropped",
    composerChips.unresolved === 1,
    `${composerChips.unresolved} unresolved chips`,
  );

  // The three blocks below reach `UPLOAD_STATE`, `turnDone`, `grow` and
  // `newSession` as page globals. `app.js` has them as globals; the Vite
  // workbench (the default shell -- only OPENAI4S_WEBUI=legacy serves app.js)
  // carries the same upload barrier, failure latch and New-session single
  // flight but keeps them module-scoped, so the same lookups resolve to
  // `undefined` there. Asserting through globals that do not exist is not a
  // test, it is a guaranteed failure: scope the blocks to the shell that
  // exposes them, and say loudly when they did not run -- a silent pass here
  // would read as coverage of the default shell, which is exactly what this
  // file's header warns against.
  const legacyUploadShell = await page.evaluate(() =>
    typeof UPLOAD_STATE !== "undefined" && typeof turnDone === "function");
  if (!legacyUploadShell) {
    console.log(
      "SKIP upload-barrier / failed-upload-latch / New-session state checks: these " +
      "assert on UPLOAD_STATE and turnDone, which app.js has as globals and the " +
      "workbench keeps module-scoped. The workbench now carries the same fixes " +
      "(frontend/src/features/chrome/upload.ts, features/send/send.ts), and its " +
      "cancel path is covered shell-agnostically below; run with OPENAI4S_WEBUI=legacy " +
      "to exercise these particular assertions.",
    );
  }
  if (legacyUploadShell) {
  // ---- Upload/send barrier: bytes are admitted before the turn -----------
  // The old FileReader callback returned immediately, so pressing Enter after
  // choosing a file could start list_dir while /uploads was still pending.
  // The upload was then refused as trusted_capture_busy and the truthful but
  // premature tool result was "0 items". Hold the real page request here and
  // prove that no optimistic bubble or message POST can cross it.
  const uploadRoute = "**/api/v1/uploads";
  const sendRoute = `**/api/v1/frames/${frameId}/message`;
  const order = [];
  let releaseUpload;
  let signalUploadStarted;
  let signalMessageStarted;
  const uploadGate = new Promise((resolve) => { releaseUpload = resolve; });
  const uploadStarted = new Promise((resolve) => { signalUploadStarted = resolve; });
  const messageStarted = new Promise((resolve) => { signalMessageStarted = resolve; });
  let messageCount = 0;
  await page.route(uploadRoute, async (route) => {
    order.push("upload-start"); signalUploadStarted();
    await uploadGate;
    order.push("upload-finish");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ artifact_id: "a-upload-order", filename: "upload-order.txt" }),
    });
  });
  await page.route(sendRoute, async (route) => {
    messageCount += 1; order.push("message"); signalMessageStarted();
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({
        status: "accepted", request_id: "req-upload-order",
        execution_id: "exec-upload-order", queue_position: 0,
        owner: { kind: "agent", id: "job-upload-order" },
      }),
    });
  });
  const uploadPrompt = "__upload_barrier_list_files__";
  const beforeUploadRelease = await page.evaluate((prompt) => {
    const box = document.querySelector("#composer");
    window.__uploadBarrierSavedDraft = box.value;
    box.value = prompt;
    const file = new File(["alpha,beta\n1,2\n"], "upload-order.txt", { type: "text/plain" });
    window.__uploadBarrier = {
      upload: uploadFiles([file]),
      send: send(prompt),
      duplicate: send(prompt),
    };
    return {
      draft: box.value,
      running: S.running,
      preparing: !!S._sendPreparing,
      bubbles: [...document.querySelectorAll(".msg.user .bubble")]
        .filter((node) => node.textContent === prompt).length,
    };
  }, uploadPrompt);
  await Promise.race([
    uploadStarted,
    new Promise((_, reject) => setTimeout(() => reject(new Error("upload request did not start")), 5000)),
  ]);
  check(
    "send waits behind an in-flight upload",
    beforeUploadRelease.draft === uploadPrompt
      && beforeUploadRelease.running === false
      && beforeUploadRelease.preparing === true
      && beforeUploadRelease.bubbles === 0
      && messageCount === 0,
    JSON.stringify({ beforeUploadRelease, messageCount, order }),
  );
  releaseUpload();
  await Promise.race([
    messageStarted,
    new Promise((_, reject) => setTimeout(() => reject(new Error("message request did not start")), 5000)),
  ]);
  await page.evaluate(async () => {
    await Promise.all([
      window.__uploadBarrier.upload,
      window.__uploadBarrier.send,
      window.__uploadBarrier.duplicate,
    ]);
  });
  const afterUploadRelease = await page.evaluate((prompt) => ({
    draft: document.querySelector("#composer").value,
    bubbles: [...document.querySelectorAll(".msg.user .bubble")]
      .filter((node) => node.textContent === prompt).length,
  }), uploadPrompt);
  check(
    "one successful upload releases exactly one message",
    JSON.stringify(order) === JSON.stringify(["upload-start", "upload-finish", "message"])
      && messageCount === 1
      && afterUploadRelease.draft === ""
      && afterUploadRelease.bubbles === 1,
    JSON.stringify({ order, messageCount, afterUploadRelease }),
  );
  await page.evaluate((prompt) => {
    document.querySelectorAll(".msg.user").forEach((node) => {
      const bubble = node.querySelector(".bubble");
      if (bubble && bubble.textContent === prompt) node.remove();
    });
    turnDone("cancelled");
    const box = document.querySelector("#composer");
    box.value = window.__uploadBarrierSavedDraft || "";
    delete window.__uploadBarrier; delete window.__uploadBarrierSavedDraft;
    grow(); renderComposerRefChips(); hint("");
  }, uploadPrompt);
  await page.unroute(sendRoute);
  await page.unroute(uploadRoute);

  // A failed upload refuses the Enter that raced it: that Enter must preserve
  // the draft and must not run list_dir against bytes that never arrived. The
  // refusal is consumed when it is reported, though -- latching it left the
  // composer dead for the rest of the session, with re-selecting a file in that
  // same conversation as the only escape.
  let failedMessageCount = 0;
  await page.route(uploadRoute, (route) => route.fulfill({
    status: 500,
    contentType: "application/json",
    body: JSON.stringify({ error: "injected upload failure" }),
  }));
  await page.route(sendRoute, (route) => {
    failedMessageCount += 1;
    return route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ status: "accepted", request_id: "must-not-send", queue_position: 0 }),
    });
  });
  const failedPrompt = "__failed_upload_must_not_list__";
  const failedUpload = await page.evaluate(async (prompt) => {
    const box = document.querySelector("#composer");
    window.__failedUploadSavedDraft = box.value;
    box.value = prompt;
    const file = new File(["not admitted"], "failed-upload.txt", { type: "text/plain" });
    await Promise.all([uploadFiles([file]), send(prompt)]);
    return {
      draft: box.value,
      running: S.running,
      failures: UPLOAD_STATE.failures.size,
      bubbles: [...document.querySelectorAll(".msg.user .bubble")]
        .filter((node) => node.textContent === prompt).length,
    };
  }, failedPrompt);
  check(
    // No `failures > 0` here: the record is consumed by the very report this
    // Enter triggers, so it is already gone by the time this reads it. The
    // refusal itself is the guarantee -- no POST, draft intact, no bubble --
    // and `afterWarnedUpload` below covers the consume.
    "a failed upload keeps the Enter that raced it from listing an empty workspace",
    failedMessageCount === 0
      && failedUpload.draft === failedPrompt
      && failedUpload.running === false
      && failedUpload.bubbles === 0,
    JSON.stringify({ failedMessageCount, failedUpload }),
  );
  const afterWarnedUpload = await page.evaluate(async (prompt) => {
    await send(prompt);
    return {
      draft: document.querySelector("#composer").value,
      failures: UPLOAD_STATE.failures.size,
      bubbles: [...document.querySelectorAll(".msg.user .bubble")]
        .filter((node) => node.textContent === prompt).length,
    };
  }, failedPrompt);
  check(
    "a reported upload failure does not brick the composer for the session",
    failedMessageCount === 1
      && afterWarnedUpload.draft === ""
      && afterWarnedUpload.failures === 0
      && afterWarnedUpload.bubbles === 1,
    JSON.stringify({ failedMessageCount, afterWarnedUpload }),
  );
  await page.evaluate((prompt) => {
    document.querySelectorAll(".msg.user").forEach((node) => {
      const bubble = node.querySelector(".bubble");
      if (bubble && bubble.textContent === prompt) node.remove();
    });
    turnDone("cancelled");
    UPLOAD_STATE.failures.clear(); S._sendPreparing = null;
    const box = document.querySelector("#composer");
    box.value = window.__failedUploadSavedDraft || "";
    delete window.__failedUploadSavedDraft;
    grow(); renderComposerRefChips(); hint("");
  }, failedPrompt);
  await page.unroute(sendRoute);
  await page.unroute(uploadRoute);
  }

  // ---- Stop sends a SCOPED cancel, on whichever shell is serving ---------
  // Observable through the wire, not through globals, so this is the one
  // upload/cancel check that covers the default workbench too. The workbench
  // reached its cancel through `callLane("scopedExecutionRequest", ...)`, a
  // name nothing ever assigned; `callLane` answers a missing name with
  // `undefined` rather than throwing, so Stop resolved to undefined, no POST
  // was ever sent, and nothing said so. An interrupt that names no execution
  // is the other half: it can land on whatever started after the one the user
  // meant to stop.
  const cancelPosts = [];
  const queueRoute = "**/api/v1/frames/*/execution-queue";
  const cancelRoute = "**/api/v1/frames/*/cancel";
  await page.route(queueRoute, (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      owner: {
        execution_id: "exec-scoped-cancel",
        status: "running",
        owner: { kind: "agent", id: "job-scoped-cancel" },
      },
      queue: [],
    }),
  }));
  await page.route(cancelRoute, async (route) => {
    cancelPosts.push({ url: route.request().url(), body: route.request().postDataJSON() });
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, scope: "running" }),
    });
  });
  await page.evaluate(() => { S.running = true; });
  const stopBtn = page.locator("#cancel-btn");
  await stopBtn.evaluate((node) => node.classList.remove("hidden"));
  await stopBtn.click();
  await waitUntil("the scoped cancel to reach the wire", () => cancelPosts.length > 0, 5000);
  const cancelBody = cancelPosts[0] ? cancelPosts[0].body : {};
  check(
    "Stop sends one cancel naming the exact execution it means to stop",
    cancelPosts.length === 1
      && cancelPosts[0].url.includes(`/frames/${frameId}/cancel`)
      && cancelBody.execution_id === "exec-scoped-cancel"
      && cancelBody.owner_id === "job-scoped-cancel"
      && !!cancelBody.reason,
    JSON.stringify(cancelPosts),
  );
  await page.unroute(cancelRoute);
  await page.unroute(queueRoute);
  await page.evaluate(() => {
    S.running = false;
    document.querySelector("#cancel-btn")?.classList.add("hidden");
  });

  // ---- P1-A: the two problem cards, which are deliberately not one -------
  // Ref problems carry {ref, code, message} and the server owns the wording;
  // attachment problems carry {name, reason, limit, bytes} and the client owns
  // it. Merging them is the tempting refactor and it loses one of the two.
  const problemCards = await page.evaluate(() => {
    const messages = document.querySelector("#messages");
    const before = messages.innerHTML;
    renderRefProblems([
      { ref: "big.csv#v-1", code: "ref_truncated", message: "big.csv was sent only in part: the first 200,000 of 205,000 bytes." },
    ]);
    renderAttachmentProblems([
      { name: "plot.png", reason: "too_large", bytes: 9_000_000, limit: 4_000_000 },
      { name: "old.png", reason: "version_changed" },
    ]);
    const cards = [...messages.querySelectorAll(".ref-problems")];
    const result = {
      cards: cards.length,
      refText: cards[0] ? cards[0].textContent : "",
      attachText: cards[1] ? cards[1].textContent : "",
    };
    messages.innerHTML = before;
    return result;
  });
  check("both problem cards render", problemCards.cards === 2, `${problemCards.cards} cards`);
  check(
    "the ref card prints the server's own sentence",
    problemCards.refText.includes("200,000 of 205,000"),
    problemCards.refText,
  );
  check(
    "the attachment card translates its closed set of reasons",
    !problemCards.attachText.includes("version_changed") && problemCards.attachText.includes("plot.png"),
    problemCards.attachText,
  );

  // ---- P1-B: the delegation panel and the steer control ------------------
  // The controls are offered only on a child that is actually going. After a
  // daemon restart every child here is finished, and offering stop/steer on one
  // invites a 409 the user cannot act on.
  const delegation = await page.evaluate(() => {
    const previous = S.delegationState;
    S.delegationState = {
      budget: { spawned: 3, limit: 48, active: 1 },
      stats: { running: 1 },
      children: [
        { child_id: "c-running0001", name: "structure-scout", status: "running", depth: 0, progress: { turn_boundary: 2, max_turns: 8 }, steering: { queued: 1, delivered: 0 } },
        { child_id: "c-done000002", name: "assay-reader", status: "done", depth: 2, overrides: { model: "deepseek-chat" } },
      ],
    };
    const panel = renderDelegationPanel();
    S.delegationState = previous;
    const rows = [...panel.querySelectorAll(".delegation-child")];
    return {
      rows: rows.length,
      statuses: rows.map((r) => r.className),
      indents: rows.map((r) => r.style.getPropertyValue("--delegation-indent")),
      controls: rows.map((r) => r.querySelectorAll(".delegation-child-controls button").length),
      pills: panel.textContent,
    };
  });
  check("delegation rows", delegation.rows === 2, `${delegation.rows} rows`);
  check(
    "depth is drawn as indentation",
    delegation.indents[0] === "0px" && delegation.indents[1] === "20px",
    JSON.stringify(delegation.indents),
  );
  check(
    "stop and steer are offered on a running child",
    delegation.controls[0] === 2,
    `${delegation.controls[0]} controls on the running child`,
  );
  check(
    "and on a finished one they are not",
    delegation.controls[1] === 0,
    `${delegation.controls[1]} controls on the finished child`,
  );
  check(
    "the queued steering count is shown",
    /\b1\b/.test(delegation.pills) && delegation.statuses[0].includes("status-running"),
    delegation.pills.slice(0, 200),
  );

  // The steer control has to reach the child's own route. A button that posts
  // to the session instead would look identical and steer nothing.
  const steerTarget = await page.evaluate(() => String(steerDelegationChild));
  check(
    "steer posts to the child's steer route",
    /delegations\/\$\{encodeURIComponent\([^)]+\)\}\/steer/.test(steerTarget),
    steerTarget.slice(0, 200),
  );

  // ---- D9: the structured, truthful delegate step card --------------------
  // Every terminal shape the server can emit, rendered through the real
  // buildStepCard. Green is reserved for completed; partial/blocked/stopped/
  // max_turns render amber "warning"; failed/malformed/transport render red.
  // The default card must be human-readable — raw JSON only behind the
  // collapsed "Show details" reveal.
  const delegateCards = await page.evaluate(() => {
    const bigOutput = "R".repeat(12000);
    const envelope = (over) => Object.assign({
      name: "assay-reader", child_id: "c-1", frame_id: "f-child",
      task_status: "completed", stop_reason: "submitted", status: null,
      turns: 3, max_turns: 8,
      environment: { python: "/envs/sci/bin/python", env_name: "sci-env" },
      summary: "wrote the report", limitations: [], artifacts: ["report.md"],
      raw: JSON.stringify({ child_id: "c-1", output: bigOutput }),
    }, over || {});
    const build = (status, output) => buildStepCard({
      step_id: "s-" + Math.random().toString(16).slice(2),
      kind: "delegate", title: "Delegating to assay-reader",
      input: { specialist: "assay-reader", request: "do the thing" },
      status, output,
    });
    const describe = (handle) => {
      const card = handle.card;
      const json = card.querySelector(".s-json");
      const clone = card.cloneNode(true);
      clone.querySelectorAll(".s-json").forEach((node) => node.remove());
      const chips = [...card.querySelectorAll(".dlg-chip")].map((chip) => chip.className);
      return {
        classes: card.className,
        meta: card.querySelector(".s-meta").textContent,
        chips,
        visibleText: clone.textContent,
        jsonHidden: json ? json.style.display === "none" : null,
        hasToggle: !!card.querySelector(".s-out-tgl"),
        envShown: clone.textContent.includes("sci-env"),
        artifactShown: clone.textContent.includes("report.md"),
      };
    };
    return {
      completed: describe(build("done", envelope())),
      blocked: describe(build("warning", envelope({ task_status: "blocked", limitations: ["needs credentials"] }))),
      partial: describe(build("warning", envelope({ task_status: "partial" }))),
      maxTurns: describe(build("warning", envelope({ task_status: "partial", stop_reason: "max_turns", error: "max_turns exhausted before completion" }))),
      cancelled: describe(build("warning", { name: "assay-reader", child_id: "c-1", frame_id: null, task_status: null, stop_reason: "stopped", status: null, turns: null, max_turns: 8, environment: null, summary: "", limitations: [], artifacts: [], raw: "{}" })),
      runtimeError: describe(build("error", { error: "RuntimeError: kernel died" })),
      malformed: describe(build("error", { raw: "42" })),
      legacy: describe(build("done", { result: JSON.stringify({ old: "flattened", stop_reason: "submitted", output: { summary: "an old stored step" } }, null, 1) })),
      bigOutputLeak: bigOutput.slice(0, 64),
    };
  });
  check("completed card is green-path done", !delegateCards.completed.classes.includes("warn") && !delegateCards.completed.classes.includes("err"), delegateCards.completed.classes);
  check("completed chip says completed", delegateCards.completed.chips.some((c) => c.includes("completed")), JSON.stringify(delegateCards.completed.chips));
  check("completed card shows env + artifacts", delegateCards.completed.envShown && delegateCards.completed.artifactShown, delegateCards.completed.visibleText.slice(0, 200));
  check(
    "the default card contains NO raw JSON blob",
    delegateCards.completed.jsonHidden === true && !delegateCards.completed.visibleText.includes('"child_id"'),
    `jsonHidden=${delegateCards.completed.jsonHidden}`,
  );
  check("10k+ output stays behind details", delegateCards.completed.hasToggle && !delegateCards.completed.visibleText.includes(delegateCards.bigOutputLeak), "raw output leaked into the default card");
  check("blocked renders amber warning", delegateCards.blocked.classes.includes("warn") && delegateCards.blocked.chips.some((c) => c.includes("warning")), delegateCards.blocked.classes);
  check("blocked card lists its limitations", delegateCards.blocked.visibleText.includes("needs credentials"), delegateCards.blocked.visibleText.slice(0, 200));
  check("partial renders amber warning", delegateCards.partial.classes.includes("warn"), delegateCards.partial.classes);
  check("max_turns stays a structured warning, not a bare error dump", delegateCards.maxTurns.classes.includes("warn") && delegateCards.maxTurns.visibleText.includes("max_turns exhausted"), delegateCards.maxTurns.visibleText.slice(0, 200));
  check("cancelled renders amber, chip says stopped", delegateCards.cancelled.classes.includes("warn") && delegateCards.cancelled.chips.some((c) => c.includes("warning")), JSON.stringify(delegateCards.cancelled.chips));
  check("a runtime error renders red", delegateCards.runtimeError.classes.includes("err"), delegateCards.runtimeError.classes);
  check("a malformed result renders red with its summary word", delegateCards.malformed.classes.includes("err"), delegateCards.malformed.classes);
  check("a legacy flattened stored step still renders (graceful degrade)", delegateCards.legacy.visibleText.includes("flattened"), delegateCards.legacy.visibleText.slice(0, 120));

  // A step forwarded from a child (input.delegation decoration) renders nested
  // with the child tag — child activity never masquerades as the root's.
  const childStep = await page.evaluate(() => {
    const handle = buildStepCard({
      step_id: "s-child-x", kind: "skill", title: "Loading a skill",
      input: { name: "x", delegation: { delegation_child_id: "c-1", child_frame_id: "f-child", child_name: "structure-scout", depth: 2 } },
      status: "done", output: {},
    });
    return {
      isChild: handle.card.className.includes("step-child"),
      tag: (handle.card.querySelector(".s-child-tag") || {}).textContent || "",
      indent: handle.card.style.getPropertyValue("--step-child-indent"),
    };
  });
  check("a forwarded child step renders as nested", childStep.isChild, childStep.tag);
  check("the child tag names the child", childStep.tag.includes("structure-scout"), childStep.tag);
  check("depth scales the nesting indent", childStep.indent === "24px", childStep.indent);

  // ---- D8: the live delegation_child_event stream updates the panel -------
  const liveEvent = await page.evaluate(() => {
    const previous = S.delegationState;
    S.delegationState = { root_frame_id: "f-root", initialized: true, budget: null, stats: { total: 0, pending: 0, running: 0, done: 0, failed: 0, stopped: 0 }, children: [] };
    mergeDelegationChildEvent({
      type: "delegation_child_event", event: "running", root_frame_id: "f-root",
      child: { child_id: "c-live", name: "live-child", status: "running", task_status: "", depth: 1, frame_id: "f-child-live", progress: { turn_boundary: 1, max_turns: 6 }, steering: { queued: 0, delivered: 0 } },
    });
    mergeDelegationChildEvent({
      type: "delegation_child_event", event: "done", root_frame_id: "f-root",
      child: { child_id: "c-live", name: "live-child", status: "done", task_status: "partial", depth: 1, frame_id: "f-child-live", progress: { turn_boundary: 6, max_turns: 6 }, steering: { queued: 0, delivered: 0 } },
    });
    const panel = renderDelegationPanel();
    const stats = S.delegationState.stats;
    S.delegationState = previous;
    const row = panel.querySelector(".delegation-child");
    return {
      rows: panel.querySelectorAll(".delegation-child").length,
      rowClass: row ? row.className : "",
      chips: row ? [...row.querySelectorAll(".dlg-chip")].map((chip) => chip.className) : [],
      frameRef: row ? (row.querySelector(".dlg-frame-ref") || {}).title || "" : "",
      stats,
    };
  });
  check("a live child event stream upserts one panel row", liveEvent.rows === 1, `${liveEvent.rows} rows`);
  check("the panel row reaches the terminal status", liveEvent.rowClass.includes("status-done"), liveEvent.rowClass);
  check("the panel row surfaces task_status", liveEvent.chips.some((c) => c.includes("warning")), JSON.stringify(liveEvent.chips));
  check("the panel row references the child frame", liveEvent.frameRef === "f-child-live", liveEvent.frameRef);
  check("live stats recount from the merged children", liveEvent.stats.total === 1 && liveEvent.stats.done === 1, JSON.stringify(liveEvent.stats));
  const eventHandler = await page.evaluate(() => {
    const previous = S.delegationState;
    const fid = S.currentId;
    S.delegationState = {
      root_frame_id: fid,
      initialized: true,
      budget: null,
      stats: { total: 0, pending: 0, running: 0, done: 0, failed: 0, stopped: 0 },
      children: [],
    };
    onEvent({
      type: "delegation_child_event",
      event: "running",
      root_frame_id: fid,
      frame_id: fid,
      child: {
        child_id: "c-route",
        name: "routed-child",
        status: "running",
        task_status: "",
        depth: 1,
        frame_id: "f-child-route",
      },
    });
    const routed = (S.delegationState.children || []).some((c) => c.child_id === "c-route");
    S.delegationState = previous;
    return {
      routed,
      onEventIsFn: typeof onEvent === "function",
      mergeIsFn: typeof mergeDelegationChildEvent === "function",
    };
  });
  check(
    "the delegation_child_event handler feeds the live merge and the timeline",
    eventHandler.routed && eventHandler.onEventIsFn && eventHandler.mergeIsFn,
    "onEvent does not route delegation_child_event through the live merge",
  );

  // ---- D10: the Executed code surface (execution history, root + children)
  // Render-level, injected state, through the real build/render functions: the
  // navigator must show child frames, the per-frame cell list must render a
  // failed cell in true order, and the surface must label itself as execution
  // history distinct from Artifacts/deliverables.
  const executedCode = await page.evaluate(() => {
    const state = {
      open: true, loading: false, error: "", request: 1,
      data: {
        root_frame_id: "f-exec-root", truncated: false,
        frames: [
          { frame_id: "f-exec-root", parent_id: null, root_frame_id: "f-exec-root", name: "analysis", kind: "turn", depth: 0, status: "ready", order: 0, counts: { cells: 1, ok: 1, error: 0, interrupted: 0 }, cells: [] },
          { frame_id: "f-exec-child", parent_id: "f-exec-root", root_frame_id: "f-exec-root", name: "assay-reader", kind: "delegate", depth: 1, status: "done", order: 1, counts: { cells: 2, ok: 1, error: 1, interrupted: 0 }, cells: [] },
        ],
      },
      selected: "f-exec-child",
      cells: {
        "f-exec-child": [
          { producing_cell_id: "cell-c1", cell_index: 1, state_revision: 1, kernel_id: "python", language: "python", origin: "delegate", source: "data = load('assay.csv')", stdout: "", stderr: "", error: "", status: "ok", figures: [], files_written: [], files_read: [] },
          { producing_cell_id: "cell-c2", cell_index: 2, state_revision: 2, kernel_id: "python", language: "python", origin: "delegate", source: "raise ValueError('bad assay')", stdout: "", stderr: "", error: "ValueError: bad assay", status: "error", figures: [], files_written: [], files_read: [] },
        ],
      },
    };
    const view = buildExecutedCodeView(state);
    const frames = [...view.querySelectorAll(".nb-exec-frame")];
    const cellsRendered = [...view.querySelectorAll(".notebook-cell")];
    const failed = cellsRendered[1] || null;
    return {
      frameRows: frames.length,
      frameNames: frames.map((f) => f.textContent),
      frameIds: frames.map((f) => f.getAttribute("data-frame")),
      indents: frames.map((f) => f.style.getPropertyValue("--exec-indent")),
      selectedIsChild: frames[1] ? frames[1].className.includes("on") : false,
      cellCount: cellsRendered.length,
      cellOrder: cellsRendered.map((c) => c.getAttribute("data-producing-cell")),
      failedHasError: failed ? !!failed.querySelector(".nbc-error") : false,
      failedText: failed ? failed.textContent : "",
      title: (view.querySelector(".nb-exec-title") || {}).textContent || "",
      note: (view.querySelector(".nb-exec-note") || {}).textContent || "",
      artifactsTabLabel: t("dock.files.heading"),
    };
  });
  check("executed-code navigator shows root and child frames", executedCode.frameRows === 2, `${executedCode.frameRows} rows`);
  check(
    "the child frame is listed by name and the root by its label",
    executedCode.frameNames[1].includes("assay-reader") && executedCode.frameIds[0] === "f-exec-root",
    JSON.stringify(executedCode.frameNames),
  );
  check(
    "child depth is drawn as indentation",
    executedCode.indents[0] === "0px" && executedCode.indents[1] === "14px",
    JSON.stringify(executedCode.indents),
  );
  check("the selected child frame is marked", executedCode.selectedIsChild, "child row lacks .on");
  check(
    "the child cell list renders both cells in true order",
    executedCode.cellCount === 2 && executedCode.cellOrder[0] === "cell-c1" && executedCode.cellOrder[1] === "cell-c2",
    JSON.stringify(executedCode.cellOrder),
  );
  check(
    "the failed cell renders with its error, not dropped",
    executedCode.failedHasError && executedCode.failedText.includes("bad assay"),
    executedCode.failedText.slice(0, 160),
  );
  check(
    "the surface labels itself as execution history",
    executedCode.title.length > 0 && executedCode.note.length > 0,
    `${executedCode.title} / ${executedCode.note.slice(0, 80)}`,
  );
  check(
    "the label is distinct from the Artifacts surface and says so",
    executedCode.title !== executedCode.artifactsTabLabel && /Artifacts/i.test(executedCode.note),
    executedCode.note.slice(0, 160),
  );

  // The download split-button must carry the sources.zip entry, pointing at
  // the execution-sources export route (not the notebook export).
  const exportMenu = await page.evaluate(() => {
    const link = notebookExportLink("f-exec-root");
    const items = [...link.querySelectorAll(".prov-dlitem")];
    const sources = items.find((a) => (a.getAttribute("href") || "").includes("/execution-sources/export"));
    return {
      items: items.length,
      hasSources: !!sources,
      href: sources ? sources.getAttribute("href") : "",
      downloadName: sources ? sources.getAttribute("download") : "",
      label: sources ? sources.textContent : "",
    };
  });
  check("the download menu carries the sources.zip entry", exportMenu.hasSources, `${exportMenu.items} items`);
  check(
    "the sources entry targets the execution-sources export route",
    exportMenu.href.includes("/frames/f-exec-root/execution-sources/export"),
    exportMenu.href,
  );
  check(
    "the sources entry downloads as sources.zip with its own label",
    exportMenu.downloadName.endsWith("sources.zip") && exportMenu.label.length > 0,
    `${exportMenu.downloadName} / ${exportMenu.label}`,
  );

  // A failed per-frame execution-log fetch must not be cached as an empty
  // cell list: the pre-fix behavior pinned `[]` for that frame until the
  // session was reopened, so the retry (the next click on the frame row)
  // could never actually retry. Drives the real selectExecFrame through the
  // page's own api()/fetch, with the route intercepted to fail once and then
  // to answer -- the client `api` is a top-level const, so interception is
  // the only seam that exercises the real call path.
  const execLogRoute = "**/api/v1/frames/f-exec-retry/execution-log*";
  await page.route(execLogRoute, (route) => route.fulfill({
    status: 500,
    contentType: "application/json",
    body: JSON.stringify({ error: "injected execution-log failure" }),
  }));
  const execFail = await page.evaluate(async () => {
    const st = execSourcesState();
    window.__execRetrySaved = {
      open: st.open, selected: st.selected, cells: st.cells,
      error: st.error, cellRequest: st.cellRequest,
    };
    st.cells = {}; st.error = "";
    await selectExecFrame("f-exec-retry");
    return {
      cachedAfterFailure: Object.prototype.hasOwnProperty.call(st.cells, "f-exec-retry"),
      errorAfterFailure: String(st.error || ""),
    };
  });
  await page.unroute(execLogRoute);
  await page.route(execLogRoute, (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ kernels: ["python"], entries: [
      { producing_cell_id: "cell-retry-1", cell_index: 1, state_revision: 1, kernel_id: "python", language: "python", origin: "delegate", source: "x = 1", stdout: "", stderr: "", error: "", status: "ok", figures: [], files_written: [], files_read: [] },
    ] }),
  }));
  const execRecover = await page.evaluate(async () => {
    const st = execSourcesState();
    await selectExecFrame("f-exec-retry");
    const out = {
      recoveredCells: (st.cells["f-exec-retry"] || []).length,
      errorCleared: !st.error,
    };
    const saved = window.__execRetrySaved;
    delete window.__execRetrySaved;
    st.open = saved.open; st.selected = saved.selected; st.cells = saved.cells;
    st.error = saved.error; st.cellRequest = saved.cellRequest;
    return out;
  });
  await page.unroute(execLogRoute);
  check(
    "a failed execution-log fetch is not cached as an empty frame",
    execFail.cachedAfterFailure === false && execFail.errorAfterFailure.length > 0,
    JSON.stringify(execFail),
  );
  check(
    "re-selecting the frame retries, recovers, and clears the error",
    execRecover.recoveredCells === 1 && execRecover.errorCleared,
    JSON.stringify(execRecover),
  );

  // ---- P1-A: model profiles, and P1-B: memory -- both Customize panels ----
  // Driven through the tray a user actually clicks, not by calling the render
  // function: these two are reached by a tab id, and a tab that stopped
  // rendering would leave the function passing and the panel empty.
  // Reuses the profile created at the top. The fixture is made by this file
  // rather than assumed: a daemon with no profiles configured renders an empty
  // panel *correctly*, and a check that passed on whatever the developer's
  // database held would be measuring the database.
  for (const entry of ["#customize-btn", "#settings-gear"]) {
    await page.locator(entry).click();
    await page.locator("#cust:not(.hidden)").waitFor({ state: "visible" });
    // The rendered heading is the shell-agnostic signal that a renderer ran.
    // `aria-busy` is app.js's own settled marker (this PR added it); the
    // workbench never sets it, so waiting on it there waits forever.
    await page.locator("#cust-content .cust-h").first().waitFor({ state: "visible" });
    if (legacyUploadShell) {
      await page.locator('#cust-content[aria-busy="false"]').waitFor({ state: "attached" });
    }
    const opened = await page.evaluate(() => ({
      active: document.querySelector(".cust-tab.active")?.dataset.tab || "",
      heading: document.querySelector("#cust-content .cust-h")?.textContent || "",
      content: document.querySelector("#cust-content")?.textContent || "",
    }));
    check(
      `${entry} opens the General settings panel instead of forwarding its click event`,
      opened.active === "general" && opened.heading.length > 0 && !/^(Loading|加载中)/.test(opened.content.trim()),
      JSON.stringify(opened),
    );
    await page.locator("#cust-close").click();
  }

  // A tab owns the DOM node it started with.  Hold the Skills response, move
  // to Models, then release the stale response: the late renderer must only
  // update its detached node and cannot replace the visible Models panel.
  const skillsCatalogRoute = "**/api/v1/skills/catalog";
  let releaseSkills;
  let signalSkillsStarted;
  let signalSkillsFulfilled;
  const skillsGate = new Promise((resolve) => { releaseSkills = resolve; });
  const skillsStarted = new Promise((resolve) => { signalSkillsStarted = resolve; });
  const skillsFulfilled = new Promise((resolve) => { signalSkillsFulfilled = resolve; });
  await page.route(skillsCatalogRoute, async (route) => {
    signalSkillsStarted();
    await skillsGate;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ skills: [{ name: "__stale_skill__", displayName: "__stale_skill__", description: "must stay detached" }] }),
    });
    signalSkillsFulfilled();
  });
  await page.locator("#customize-btn").click();
  await page.locator('.cust-tab[data-tab="skills"]').click();
  await Promise.race([
    skillsStarted,
    new Promise((_, reject) => setTimeout(() => reject(new Error("Skills request did not start")), 5000)),
  ]);
  await page.locator('.cust-tab[data-tab="models"]').click();
  await page.locator("#cust-content .prof-row").first().waitFor({ state: "visible" });
  releaseSkills();
  await skillsFulfilled;
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const afterStaleSkills = await page.evaluate(() => ({
    active: document.querySelector(".cust-tab.active")?.dataset.tab || "",
    profiles: document.querySelectorAll("#cust-content .prof-row").length,
    staleSkillVisible: (document.querySelector("#cust-content")?.textContent || "").includes("__stale_skill__"),
  }));
  check(
    "a late Settings response cannot overwrite the newer tab",
    afterStaleSkills.active === "models" && afterStaleSkills.profiles > 0 && !afterStaleSkills.staleSkillVisible,
    JSON.stringify(afterStaleSkills),
  );
  await page.unroute(skillsCatalogRoute);
  await page.locator("#cust-close").click();

  {
    await page.evaluate(() => openCust("models"));
    await page.locator("#cust:not(.hidden)").waitFor({ state: "visible" });
    const profiles = await page.evaluate(async (name) => {
      const deadline = Date.now() + 8000;
      while (Date.now() < deadline) {
        if (document.querySelectorAll("#cust .prof-row").length) break;
        await new Promise((r) => setTimeout(r, 60));
      }
      const rows = [...document.querySelectorAll("#cust .prof-row")];
      return {
        rows: rows.length,
        named: rows.some((r) => r.textContent.includes(name)),
        // The endpoint is shown on the row. It must be the stored, normalised
        // one -- a profile created with credentials in the URL must not put
        // them back on screen.
        text: rows.map((r) => r.textContent).join(" | "),
      };
    }, "P1 coverage profile");
    check("the model profile panel renders its rows", profiles.rows > 0, `${profiles.rows} rows`);
    check("the profile just created is listed", profiles.named, profiles.text.slice(0, 200));
    check(
      "the row states the endpoint it will call",
      profiles.text.includes("api.p1-coverage.invalid"),
      profiles.text.slice(0, 200),
    );
  }
  // Tidied here rather than in a `finally`: the profile is the paging fixture
  // too, so it has to outlive every check above.
  await api(`/model-profiles/${encodeURIComponent(profileId)}`, { method: "DELETE" }).catch(
    () => {},
  );

  await page.evaluate(() => custTab("memory"));
  const memory = await page.evaluate(async () => {
    const deadline = Date.now() + 8000;
    while (Date.now() < deadline) {
      if (document.querySelectorAll("#cust .cust-row").length) break;
      await new Promise((r) => setTimeout(r, 60));
    }
    return {
      rows: document.querySelectorAll("#cust .cust-row").length,
      toggles: document.querySelectorAll("#cust .toggle").length,
    };
  });
  check("the memory panel renders rows", memory.rows > 0, `${memory.rows} rows`);
  check("the memory master switch is drawn", memory.toggles > 0, `${memory.toggles} toggles`);

  // Both New session controls are real onclick entry points.  A parameterized
  // newSession() must not mistake the PointerEvent for a project id; each click
  // should POST the active project's scalar id, never a serialized event.
  await page.locator("#cust-close").click();
  const frameCreateRoute = "**/api/v1/frames";
  let signalFrameCreate = null;
  await page.route(frameCreateRoute, async (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      if (signalFrameCreate) signalFrameCreate(body);
    }
    await route.continue();
  });
  for (const entry of ["#new-session", "#tab-new"]) {
    const before = await page.evaluate(() => S.currentId);
    const posted = new Promise((resolve) => { signalFrameCreate = resolve; });
    await page.locator(entry).click();
    const body = await Promise.race([
      posted,
      new Promise((_, reject) => setTimeout(() => reject(new Error(`${entry} did not create a frame`)), 5000)),
    ]);
    signalFrameCreate = null;
    check(
      `${entry} creates a session in the active project instead of forwarding its click event`,
      body.project_id === projectId,
      JSON.stringify(body),
    );
    // onclick does not await its async handler.  An explicit New session opts
    // out of the per-project single flight, so both clicks do POST -- but the
    // second one still has to observe the first one's frame before it can be
    // told apart from it, so let this click land before issuing the next.
    //
    // Polled with page.evaluate rather than page.waitForFunction: the shell
    // CSP carries no 'unsafe-eval', so waitForFunction is refused outright
    // (browser_smoke.mjs makes the same note at both of its wait sites).
    await waitUntil(
      "the New session single-flight creation to drain",
      () => page.evaluate(
        (beforeId) => S.currentId !== beforeId
          && (typeof UPLOAD_STATE === "undefined" || UPLOAD_STATE.creations.size === 0),
        before,
      ),
      8000,
    );
  }
  await page.unroute(frameCreateRoute);

  if (pageErrors.length) failures.push(`page errors: ${pageErrors.join(" | ")}`);
  if (failures.length) {
    throw new Error(`P1 control coverage failed:\n  - ${failures.join("\n  - ")}`);
  }
  console.log("OpenAI4S P1 control coverage passed");
} finally {
  await browser.close();
}
