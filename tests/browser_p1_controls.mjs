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
import { authenticate } from "./browser_auth.mjs";

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
    /delegations\/\$\{encodeURIComponent\(childId\)\}\/steer/.test(steerTarget),
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
  const eventHandler = await page.evaluate(() => String(onEvent));
  check(
    "the delegation_child_event handler feeds the live merge and the timeline",
    eventHandler.includes("mergeDelegationChildEvent") && eventHandler.includes("delegation_child_event"),
    "onEvent does not route delegation_child_event through the live merge",
  );

  // ---- P1-A: model profiles, and P1-B: memory -- both Customize panels ----
  // Driven through the tray a user actually clicks, not by calling the render
  // function: these two are reached by a tab id, and a tab that stopped
  // rendering would leave the function passing and the panel empty.
  // Reuses the profile created at the top. The fixture is made by this file
  // rather than assumed: a daemon with no profiles configured renders an empty
  // panel *correctly*, and a check that passed on whatever the developer's
  // database held would be measuring the database.
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

  if (pageErrors.length) failures.push(`page errors: ${pageErrors.join(" | ")}`);
  if (failures.length) {
    throw new Error(`P1 control coverage failed:\n  - ${failures.join("\n  - ")}`);
  }
  console.log("OpenAI4S P1 control coverage passed");
} finally {
  await browser.close();
}
