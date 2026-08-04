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
  // The example session is the one fixture every daemon has, it is seeded by
  // the product rather than by this file, and its two messages are enough to
  // separate "newest page" from "oldest page" — which is the whole defect.
  const seeded = await api("/example/session", { method: "POST", data: {} });
  const exampleFrame = seeded.frame_id;
  if (!exampleFrame) throw new Error("the example session did not seed a frame");

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
  }, exampleFrame);

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

  // ---- P1-A: model profiles, and P1-B: memory -- both Customize panels ----
  // Driven through the tray a user actually clicks, not by calling the render
  // function: these two are reached by a tab id, and a tab that stopped
  // rendering would leave the function passing and the panel empty.
  // The fixture is created here rather than assumed: a daemon with no profiles
  // configured renders an empty panel *correctly*, and a check that passed on
  // whatever the developer's database held would be measuring the database.
  const profile = await api("/model-profiles", {
    method: "POST",
    data: {
      name: "P1 coverage profile",
      provider: "chatgpt",
      base_url: "https://api.p1-coverage.invalid/v1",
      model: "p1-coverage-model",
    },
  });
  const profileId = profile.id || (profile.profile || {}).id;
  if (!profileId) throw new Error("profile creation did not return an id");
  try {
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
  } finally {
    await api(`/model-profiles/${encodeURIComponent(profileId)}`, { method: "DELETE" }).catch(() => {});
  }

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
