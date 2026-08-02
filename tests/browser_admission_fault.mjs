// The admission fault case, in a real browser.
//
// Everything else about exactly-once admission can be shown from Python. This
// one cannot: the property is that the *page* survives losing its own answer,
// and that depends on when the browser writes the id relative to dispatch —
// which only a browser can demonstrate.
//
// The fault is deliberately not "make the request fail". The POST must reach
// the daemon and succeed, so the turn really is accepted and the pins really
// are consumed; only the response is destroyed on its way back to the page.
// That is the shape of a dropped connection or a tab closed mid-flight, and it
// is the one case where a client that guesses is wrong either way: "assume
// sent" silently loses the user's comments, "assume open" offers them a
// comment a running turn is already carrying.
//
// Run against a daemon already serving on 127.0.0.1:8760.

import { chromium } from "playwright";

import { authenticate } from "./browser_auth.mjs";

const baseUrl = process.env.OPENAI4S_BROWSER_URL || "http://127.0.0.1:8760/";
const failures = [];
const note = (ok, what, detail = "") => {
  if (!ok) failures.push(`${what}${detail ? `: ${detail}` : ""}`);
  console.log(`${ok ? "ok  " : "FAIL"}  ${what}${detail ? `  ${detail}` : ""}`);
};

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const token = await authenticate(page, baseUrl);
const api = async (path, init = {}) => {
  const response = await page.request.fetch(new URL(path, baseUrl).toString(), {
    headers: { "X-OpenAI4S-Token": token, "content-type": "application/json" },
    ...init,
  });
  return { status: response.status(), body: await response.json().catch(() => null) };
};

try {
  // --- a session with one pinned comment, built through the real API -------
  const project = (
    await api("/api/v1/projects", { method: "POST", data: { name: "admission-fault" } })
  ).body;
  const projectId = project && (project.project_id || project.id);
  const frame = (
    await api("/api/v1/frames", { method: "POST", data: { project_id: projectId } })
  ).body;
  const frameId = frame && (frame.id || frame.frame_id);
  note(!!frameId, "created a session", String(frameId));

  const uploaded = (
    await api("/api/v1/uploads", {
      method: "POST",
      data: {
        filename: "plot.png",
        content_base64: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
        frame_id: frameId,
        project_id: projectId,
      },
    })
  ).body;
  const artifactId = uploaded && uploaded.artifact_id;

  let annotationId = null;
  if (artifactId) {
    const created = await api(`/api/v1/frames/${frameId}/annotations`, {
      method: "POST",
      data: { artifact_id: artifactId, body: "look at this peak", x: 0.5, y: 0.5 },
    });
    annotationId = created.body && created.body.annotation && created.body.annotation.annotation_id;
  }
  note(!!annotationId, "pinned a comment", String(annotationId));
  if (!annotationId) throw new Error("cannot exercise admission without a pin");

  // --- the fault: the POST completes, the page never sees the answer -------
  //
  // `route.fetch` performs the real request against the daemon, so the server
  // side is genuinely done. `route.abort` then destroys the response, so the
  // page's promise rejects exactly as it would on a dropped connection.
  let dispatched = 0;
  let storedBeforeAnswer = null;
  await page.route(`**/api/v1/frames/${frameId}/message`, async (route) => {
    dispatched += 1;
    await route.fetch();
    storedBeforeAnswer = await page.evaluate(
      (fid) => outstandingAdmissions(fid),
      frameId,
    );
    await route.abort("connectionfailed");
  });

  await page.goto(baseUrl);
  await page.waitForTimeout(1200);
  // Open the conversation through the app's own entry point, so `S.currentId`
  // and the loaded annotations are the ones the send path reads.
  await page.evaluate(async (fid) => {
    if (typeof openConversation === "function") await openConversation(fid);
  }, frameId);
  await page.waitForTimeout(1500);

  // Send through the page's own composer path, so the id is minted by the
  // client code under test rather than by this harness.
  const sent = await page.evaluate(async () => {
    const box = document.querySelector("#composer");
    if (box) {
      box.value = "reconcile me";
      box.dispatchEvent(new Event("input", { bubbles: true }));
    }
    const pins = typeof openAnnotations === "function" ? openAnnotations() : [];
    try { await send("reconcile me"); } catch {}
    return { pins: pins.length };
  });
  await page.waitForTimeout(2500);
  note(sent.pins > 0, "the composer saw the pinned comment", JSON.stringify(sent));

  note(dispatched === 1, "the turn was dispatched exactly once", `dispatched=${dispatched}`);
  note(
    Array.isArray(storedBeforeAnswer) && storedBeforeAnswer.length === 1,
    "the admission id was durable BEFORE the answer came back",
    JSON.stringify(storedBeforeAnswer),
  );

  // --- reload: the page must reconcile, not resend and not reopen ----------
  await page.unroute(`**/api/v1/frames/${frameId}/message`);
  let resent = 0;
  await page.route(`**/api/v1/frames/${frameId}/message`, async (route) => {
    resent += 1;
    await route.continue();
  });
  // Counted from the PAGE, not issued by this harness. Querying the endpoint
  // with `page.request` proves the endpoint works and says nothing about
  // whether the app ever calls it -- the earlier version of this check passed
  // whether or not `reconcileLastAdmission` ran at all.
  let pageReconciles = 0;
  let reconcileBody = null;
  page.on("response", async (response) => {
    if (!/\/admissions\//.test(response.url())) return;
    pageReconciles += 1;
    reconcileBody = await response.json().catch(() => null);
  });

  await page.goto(baseUrl);
  await page.waitForTimeout(1200);
  await page.evaluate(async (fid) => {
    if (typeof openConversation === "function") await openConversation(fid);
  }, frameId);
  await page.waitForTimeout(2500);

  note(resent === 0, "the reloaded page did not resend the turn", `resent=${resent}`);
  note(
    pageReconciles >= 1,
    "the PAGE asked the server what happened",
    `requests=${pageReconciles}`,
  );
  note(
    reconcileBody && reconcileBody.state === "sent",
    "the reconcile the page received names the real outcome",
    JSON.stringify(reconcileBody),
  );

  const cleared = await page.evaluate((fid) => outstandingAdmissions(fid), frameId);
  note(cleared.length === 0, "a settled admission is cleared from storage", JSON.stringify(cleared));

  const annotations = (await api(`/api/v1/frames/${frameId}/annotations`)).body;
  const row = ((annotations && annotations.annotations) || []).find(
    (a) => a.annotation_id === annotationId,
  );
  note(
    !!row && row.status === "sent",
    "the pin was not reopened by the lost response",
    row ? row.status : "missing",
  );

  // --- the released path, on a FRESH pin ----------------------------------
  //
  // Reusing the already-sent pin proved nothing: it was `sent` before the call
  // and `sent` after it, whatever the release did.
  const secondPin = (
    await api(`/api/v1/frames/${frameId}/annotations`, {
      method: "POST",
      data: { artifact_id: artifactId, body: "second pin", x: 0.4, y: 0.4 },
    })
  ).body.annotation.annotation_id;

  const refused = await api(`/api/v1/frames/${frameId}/message`, {
    method: "POST",
    data: {
      request: "x".repeat(9 * 1024 * 1024),
      annotation_ids: [secondPin],
      wait: false,
    },
  });
  note(refused.status === 413, "an oversized turn is refused synchronously", String(refused.status));

  const afterRefusal = (await api(`/api/v1/frames/${frameId}/annotations`)).body;
  const secondRow = ((afterRefusal && afterRefusal.annotations) || []).find(
    (a) => a.annotation_id === secondPin,
  );
  note(
    secondRow && secondRow.status === "open",
    "a synchronous refusal leaves the pin exactly open",
    secondRow ? secondRow.status : "missing",
  );

  // --- many outstanding admissions, each independently reconcilable -------
  //
  // The storage this drives is the page's own. Two designs failed here before:
  // a scalar, which a second send overwrote, and a capped JSON list, which
  // evicted the oldest *unresolved* id to stay under its bound and lost it
  // between two tabs by read-modify-write.
  const storage = await page.evaluate(async (fid) => {
    const mint = (n) => "resv-" + String(n).padStart(4, "0") + "a".repeat(20);

    // 65 = the coordinator's queue cap (64) plus the running turn. This is the
    // number of sends a user can genuinely have outstanding, and every id has
    // to survive it -- the capped list dropped everything past its own bound.
    const CAPACITY = 65;
    for (let i = 0; i < CAPACITY; i++) rememberAdmission(fid, mint(i));
    const all = outstandingAdmissions(fid);

    // Settling one must not disturb any other. Under the list design this was
    // a read-modify-write of a shared value, so it could not be true across
    // tabs and was only accidentally true within one.
    forgetAdmission(fid, mint(7));
    const afterOne = outstandingAdmissions(fid);

    // A concurrent tab's write, simulated at the only level it is observable:
    // an independent key set while this tab holds its own view of the world.
    // With a container under one key, this `setItem` would have destroyed
    // every id above.
    localStorage.setItem("openai4s.admission." + fid + ".resv-fromothertab000000", "1");
    const afterOther = outstandingAdmissions(fid);

    for (const id of outstandingAdmissions(fid)) forgetAdmission(fid, id);
    const drained = outstandingAdmissions(fid);

    // The scalar a tab reloading into this build can still be holding.
    localStorage.setItem("openai4s.admission." + fid, "resv-legacyscalar00000000");
    const migrated = outstandingAdmissions(fid);
    const legacyGone = localStorage.getItem("openai4s.admission." + fid) === null;
    for (const id of outstandingAdmissions(fid)) forgetAdmission(fid, id);

    return {
      capacity: CAPACITY,
      remembered: all.length,
      firstKept: all[0],
      lastKept: all[all.length - 1],
      afterOne: afterOne.length,
      forgottenGone: !afterOne.includes(mint(7)),
      neighboursKept: afterOne.includes(mint(6)) && afterOne.includes(mint(8)),
      afterOther: afterOther.length,
      otherTabVisible: afterOther.includes("resv-fromothertab000000"),
      drained: drained.length,
      migrated,
      legacyGone,
      settled: [
        admissionSettled("sent"),
        admissionSettled("released"),
        admissionSettled("none"),
        admissionSettled("pending"),
        admissionSettled("something-new"),
        admissionSettled(undefined),
      ],
    };
  }, frameId);

  note(
    storage.remembered === storage.capacity,
    "every outstanding admission up to the real queue capacity is kept",
    `${storage.remembered}/${storage.capacity}, oldest=${storage.firstKept}`,
  );
  note(
    storage.firstKept === "resv-0000" + "a".repeat(20) &&
      storage.lastKept === "resv-0064" + "a".repeat(20),
    "the OLDEST unresolved id -- the one a bound used to evict -- is still there, and first",
    `${storage.firstKept} .. ${storage.lastKept}`,
  );
  note(
    storage.afterOne === storage.capacity - 1 && storage.forgottenGone && storage.neighboursKept,
    "settling one admission removes exactly that one",
    JSON.stringify({ after: storage.afterOne, gone: storage.forgottenGone, kept: storage.neighboursKept }),
  );
  note(
    storage.afterOther === storage.capacity && storage.otherTabVisible,
    "a concurrent tab's admission joins the set instead of replacing it",
    JSON.stringify({ after: storage.afterOther, visible: storage.otherTabVisible }),
  );
  note(storage.drained === 0, "settled admissions leave nothing behind", String(storage.drained));
  note(
    storage.migrated.length === 1 &&
      storage.migrated[0] === "resv-legacyscalar00000000" &&
      storage.legacyGone,
    "a tab reloading with the old scalar keeps its outstanding id",
    JSON.stringify(storage.migrated) + " legacyGone=" + storage.legacyGone,
  );
  note(
    JSON.stringify(storage.settled) === JSON.stringify([true, true, true, false, false, false]),
    "only a decided answer settles an admission",
    JSON.stringify(storage.settled),
  );

  // --- two real sends whose answers are lost, reconciled independently -----
  //
  // Through the composer, so the ids are minted and stored by the code under
  // test. The first request reaches the daemon and its response is destroyed;
  // the second never leaves the browser. Two genuinely different outcomes --
  // one `sent`, one that the server has never heard of -- from two ids that
  // must both survive to the reload.
  const pinA = (
    await api(`/api/v1/frames/${frameId}/annotations`, {
      method: "POST",
      data: { artifact_id: artifactId, body: "first of two", x: 0.6, y: 0.6 },
    })
  ).body.annotation.annotation_id;

  await page.unroute(`**/api/v1/frames/${frameId}/message`);
  let leg = 0;
  await page.route(`**/api/v1/frames/${frameId}/message`, async (route) => {
    leg += 1;
    if (leg === 1) {
      await route.fetch();               // the server really runs this one
      await route.abort("connectionfailed");
    } else {
      await route.abort("connectionfailed");   // this one never leaves
    }
  });

  await page.goto(baseUrl);
  await page.waitForTimeout(1200);
  await page.evaluate(async (fid) => {
    if (typeof openConversation === "function") await openConversation(fid);
  }, frameId);
  await page.waitForTimeout(1500);
  const firstSend = await page.evaluate(async () => {
    try { await send("first of two"); } catch {}
    return (typeof openAnnotations === "function" ? openAnnotations() : []).length;
  });
  await page.waitForTimeout(2000);

  const pinB = (
    await api(`/api/v1/frames/${frameId}/annotations`, {
      method: "POST",
      data: { artifact_id: artifactId, body: "second of two", x: 0.7, y: 0.7 },
    })
  ).body.annotation.annotation_id;
  const twoOutstanding = await page.evaluate(async (fid) => {
    await loadAnnotations(fid);
    try { await send("second of two"); } catch {}
    return outstandingAdmissions(fid);
  }, frameId);
  await page.waitForTimeout(2000);

  note(
    twoOutstanding.length === 2,
    "two lost sends leave TWO outstanding ids, not one overwriting the other",
    JSON.stringify(twoOutstanding) + ` legs=${leg} pins=${firstSend}`,
  );

  await page.unroute(`**/api/v1/frames/${frameId}/message`);
  let resentAfterTwo = 0;
  await page.route(`**/api/v1/frames/${frameId}/message`, async (route) => {
    resentAfterTwo += 1;
    await route.continue();
  });
  const asked = [];
  page.on("response", async (response) => {
    const m = /\/admissions\/([^/?]+)/.exec(response.url());
    if (m) asked.push([decodeURIComponent(m[1]), response.status()]);
  });

  await page.goto(baseUrl);
  await page.waitForTimeout(1200);
  await page.evaluate(async (fid) => {
    if (typeof openConversation === "function") await openConversation(fid);
  }, frameId);
  await page.waitForTimeout(3000);

  const askedIds = asked.map(pair => pair[0]);
  note(
    twoOutstanding.every(id => askedIds.includes(id)),
    "the reloaded page asked about BOTH outstanding admissions",
    JSON.stringify(asked),
  );
  note(
    asked.some(pair => pair[1] === 200) && asked.some(pair => pair[1] === 404),
    "the two admissions were answered independently, one known and one never sent",
    JSON.stringify(asked.map(pair => pair[1])),
  );
  note(resentAfterTwo === 0, "neither turn was resent", `resent=${resentAfterTwo}`);

  // The settled one goes; the one the server has never heard of STAYS, because
  // it is still inside its dispatch lease. A 404 answers "not yet" and "never"
  // with the same status, and deleting on the first is how a concurrent tab
  // destroys the only handle on a send that is still in flight.
  const leftover = await page.evaluate((fid) => outstandingAdmissions(fid), frameId);
  note(
    leftover.length === 1 && leftover[0] === twoOutstanding[1],
    "the settled admission cleared and the fresh never-sent one was kept",
    JSON.stringify(leftover),
  );

  // Aged past the lease, the same 404 is taken at face value -- so a request
  // that really never left cannot accumulate forever.
  const aged = await page.evaluate(async (args) => {
    const [fid, id] = args;
    localStorage.setItem(
      "openai4s.admission." + fid + "." + id,
      String(Date.now() - 10 * 60 * 1000),
    );
    await reconcileLastAdmission(fid);
    return outstandingAdmissions(fid);
  }, [frameId, twoOutstanding[1]]);
  note(aged.length === 0, "an aged never-sent admission is finally dropped", JSON.stringify(aged));

  // A corrupt or future stamp is not a lease either: trusting one would let a
  // bad value protect a key permanently.
  const bogus = await page.evaluate(async (fid) => {
    const out = {};
    for (const [label, value] of [
      ["garbage", "not-a-number"],
      ["future", String(Date.now() + 10 * 60 * 1000)],
      ["zero", "0"],
    ]) {
      localStorage.setItem("openai4s.admission." + fid + ".resv-bogus" + label + "0000000", value);
      await reconcileLastAdmission(fid);
      out[label] = outstandingAdmissions(fid).length;
    }
    return out;
  }, frameId);
  note(
    bogus.garbage === 0 && bogus.future === 0 && bogus.zero === 0,
    "a corrupt or future stamp confers no lease",
    JSON.stringify(bogus),
  );

  const afterTwo = (await api(`/api/v1/frames/${frameId}/annotations`)).body;
  const rowA = ((afterTwo && afterTwo.annotations) || []).find(a => a.annotation_id === pinA);
  const rowB = ((afterTwo && afterTwo.annotations) || []).find(a => a.annotation_id === pinB);
  note(
    rowA && rowA.status === "sent",
    "the pin on the turn that really ran stayed sent",
    rowA ? rowA.status : "missing",
  );
  note(
    rowB && rowB.status === "open",
    "the pin on the turn that never left is the user's again",
    rowB ? rowB.status : "missing",
  );

  // --- two real tabs, one localStorage, one pre-dispatch barrier ----------
  //
  // The race a single page cannot show. Tab A writes its admission key and has
  // not sent the POST yet; tab B opens the same session and asks the server
  // about that id. The server has genuinely never heard of it, so B gets 404 --
  // a true answer. Acting on it was still wrong: deleting the key there, and
  // then letting A's POST succeed with its response lost, leaves the comments
  // unreconcilable from either tab.
  //
  // One BrowserContext, because that is what makes the two pages share
  // localStorage; two contexts would be two browsers and would prove nothing.
  const shared = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  try {
    const pageA = await shared.newPage();
    await authenticate(pageA, baseUrl);
    const pageB = await shared.newPage();

    const raceFrame = (
      await api("/api/v1/frames", { method: "POST", data: { project_id: projectId } })
    ).body;
    const raceId = raceFrame && (raceFrame.id || raceFrame.frame_id);
    const racePin = (
      await api(`/api/v1/frames/${raceId}/annotations`, {
        method: "POST",
        data: { artifact_id: artifactId, body: "pinned in tab A", x: 0.3, y: 0.3 },
      })
    ).body.annotation.annotation_id;

    // A's POST is held BEFORE it is forwarded, so while it is paused the
    // daemon has no row for the id and B's 404 is genuine.
    let held = null;
    let posts = 0;
    let releaseA = null;
    const heldUntil = new Promise((resolve) => { releaseA = resolve; });
    await pageA.route(`**/api/v1/frames/${raceId}/message`, async (route) => {
      posts += 1;
      held = true;
      await heldUntil;
      await route.fetch();               // the server really runs it...
      await route.abort("connectionfailed");  // ...and A never sees the answer
    });

    await pageA.goto(baseUrl);
    await pageA.waitForTimeout(1200);
    await pageA.evaluate(async (fid) => {
      if (typeof openConversation === "function") await openConversation(fid);
    }, raceId);
    await pageA.waitForTimeout(1200);
    pageA.evaluate(async () => { try { await send("from tab A"); } catch {} });

    // Wait for the key to be durable and the POST to be in flight.
    let mintedId = null;
    for (let i = 0; i < 60 && !(held && mintedId); i++) {
      await pageA.waitForTimeout(100);
      const ids = await pageA.evaluate((fid) => outstandingAdmissions(fid), raceId);
      mintedId = ids[0] || null;
    }
    note(!!mintedId, "tab A stored its admission before dispatch", String(mintedId));
    note(held === true, "tab A's POST is paused in flight", `posts=${posts}`);

    // B now asks about it and is told 404, truthfully.
    let bSaw404 = false;
    pageB.on("response", (response) => {
      if (/\/admissions\//.test(response.url()) && response.status() === 404) bSaw404 = true;
    });
    await pageB.goto(baseUrl);
    await pageB.waitForTimeout(1200);
    await pageB.evaluate(async (fid) => {
      if (typeof openConversation === "function") await openConversation(fid);
    }, raceId);
    await pageB.waitForTimeout(2500);
    note(bSaw404, "tab B was told 404 for an id the server has not seen yet", String(bSaw404));

    const afterB = await pageB.evaluate((fid) => outstandingAdmissions(fid), raceId);
    note(
      afterB.includes(mintedId),
      "tab B's 404 did NOT evict tab A's in-flight admission",
      JSON.stringify(afterB),
    );

    // Watch BOTH tabs from here on. Either may be the one that settles it --
    // A's own in-lease retry fires on a timer, and B re-asks on reload. The
    // contract is that the id survives to be reconciled and then settles, not
    // that one particular tab is the one to do it.
    const answered = [];
    const collect = (response) => {
      const m = /\/admissions\/([^/?]+)/.exec(response.url());
      if (m) answered.push([decodeURIComponent(m[1]), response.status()]);
    };
    pageA.on("response", collect);
    pageB.on("response", collect);

    // Let A through: the server takes the turn, A never learns.
    releaseA();
    await pageA.waitForTimeout(6000);

    await pageB.goto(baseUrl);
    await pageB.waitForTimeout(1200);
    await pageB.evaluate(async (fid) => {
      if (typeof openConversation === "function") await openConversation(fid);
    }, raceId);
    await pageB.waitForTimeout(3000);

    note(
      answered.some((pair) => pair[0] === mintedId && pair[1] === 200),
      "after the POST landed, the surviving id reconciles 200 in a real tab",
      JSON.stringify(answered),
    );
    const settled = await pageB.evaluate((fid) => outstandingAdmissions(fid), raceId);
    note(settled.length === 0, "and the settled key is cleared", JSON.stringify(settled));
    note(posts === 1, "the turn was sent exactly once across both tabs", `posts=${posts}`);

    const raceRow = ((await api(`/api/v1/frames/${raceId}/annotations`)).body.annotations || [])
      .find((a) => a.annotation_id === racePin);
    note(
      raceRow && raceRow.status === "sent",
      "the pin the surviving admission named was really consumed",
      raceRow ? raceRow.status : "missing",
    );
  } finally {
    await shared.close();
  }

  // --- a genuinely held pin refuses edit and delete with 409 --------------
  // Unique per run. A fixed id collides with the ledger row a previous run
  // left behind and comes back 409 -- which is the replay guard working
  // correctly and the test asserting the wrong thing.
  const holdId =
    "resv-" +
    [...crypto.getRandomValues(new Uint8Array(16))]
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
  const holding = await api(`/api/v1/frames/${frameId}/message`, {
    method: "POST",
    data: {
      request: "hold it",
      annotation_ids: [secondPin],
      annotation_reservation_id: holdId,
      wait: false,
    },
  });
  note(holding.status === 202, "a second turn accepted the pin", String(holding.status));

  // The 409 pair is asserted in `tests/test_followup_admission.py`
  // (`test_the_public_routes_refuse_a_held_pin_over_http`), through the real
  // Handler and the real Store, because a *hold* cannot be produced
  // deterministically over HTTP alone: the turn above finalises within
  // milliseconds, and `pending` only arises from an injected finalize fault.
  // An earlier version of this file asserted the 409 here anyway and raced it,
  // which is how it came to claim the assertions in a comment while the calls
  // were never written at all.
  //
  // What the browser *can* prove, and Python cannot, is what the page shows.
  // The whole failure mode is a user being offered a comment that is already
  // on its way, so the rendering is driven with a genuinely `reserved` row
  // through the page's own code paths.
  await page.goto(baseUrl);
  await page.waitForTimeout(1200);
  await page.evaluate(async (fid) => {
    if (typeof openConversation === "function") await openConversation(fid);
  }, frameId);
  await page.waitForTimeout(2000);

  const rendered = await page.evaluate(() => {
    // A held row, rendered by `renderPins` itself rather than by a copy of it.
    const held = {
      annotation_id: "an-held-probe",
      id: "an-held-probe",
      artifact_id: (S.artifacts && S.artifacts[0] && S.artifacts[0].id) || "a-probe",
      number: 99,
      body: "held by a turn",
      status: "reserved",
      x: 0.2,
      y: 0.2,
    };
    S.annotations = [...(S.annotations || []), held];
    // A stage, built to the shape `renderPins`/`openPinPop` read: they need a
    // `.annot-layer` child and nothing else. Constructed here because the
    // image viewer is only open when a user has opened it, and the property
    // under test is the rendering, not the route into it.
    let stage = document.querySelector(".annot-layer")?.parentElement;
    if (!stage) {
      stage = document.createElement("div");
      const layer = document.createElement("div");
      layer.className = "annot-layer";
      stage.appendChild(layer);
      document.body.appendChild(stage);
    }
    stage._artId = held.artifact_id;
    renderPins(stage, { id: held.artifact_id });
    const pin = [...document.querySelectorAll(".annot-pin[data-annotation-status]")].find(
      (n) => n.textContent === "99",
    );
    openPinPop(stage, { id: held.artifact_id }, held);
    const del = document.querySelector(".annot-pop .annot-btn.danger");
    const deleteDisabled = del ? !!del.disabled && del.dataset.heldByTurn === "1" : null;
    const status = document.querySelector(".annot-pop-status[data-annotation-status]");
    return {
      pinStatus: pin ? pin.dataset.annotationStatus : null,
      popStatus: status ? status.dataset.annotationStatus : null,
      deleteDisabled,
      offeredToNextTurn: (openAnnotations() || []).some(
        (a) => (a.annotation_id || a.id) === "an-held-probe",
      ),
    };
  });
  note(
    rendered.pinStatus === "pending",
    "a held pin renders as pending, not open",
    JSON.stringify(rendered),
  );
  note(
    rendered.popStatus === "pending",
    "the popover says pending",
    String(rendered.popStatus),
  );
  note(
    rendered.deleteDisabled === true,
    "Delete is disabled while a turn holds the pin",
    String(rendered.deleteDisabled),
  );
  note(
    rendered.offeredToNextTurn === false,
    "a held pin is not offered to the next turn",
    String(rendered.offeredToNextTurn),
  );

  // An unrecognised status is `unknown`, not silently `open`: "I do not know"
  // and "you may edit this" are different answers.
  const classification = await page.evaluate(() => ({
    unknown: annotationStatus({ status: "something-new" }),
    unknownHeld: annotationIsHeld({ status: "something-new" }),
    reserved: annotationStatus({ status: "reserved" }),
    open: annotationStatus({ status: "open" }),
    openHeld: annotationIsHeld({ status: "open" }),
  }));
  note(
    classification.unknown === "unknown" &&
      classification.unknownHeld === true &&
      classification.reserved === "pending" &&
      classification.open === "open" &&
      classification.openHeld === false,
    "an unrecognised status is unknown and held, never open",
    JSON.stringify(classification),
  );

  // The Delete control the popover offers for a held pin.
  const deleteState = await page.evaluate(() => {
    const foot = document.createElement("div");
    // Build the popover's footer decision the same way `openPinPop` does.
    return {
      heldDisabled: annotationIsHeld({ status: "reserved" }),
      openEnabled: !annotationIsHeld({ status: "open" }),
      unknownDisabled: annotationIsHeld({ status: "wat" }),
      _: !!foot,
    };
  });
  note(
    deleteState.heldDisabled && deleteState.openEnabled && deleteState.unknownDisabled,
    "Delete is withheld exactly for held and unknown pins",
    JSON.stringify(deleteState),
  );
} finally {
  await browser.close();
}

if (failures.length) {
  console.error(`\n${failures.length} failure(s):`);
  failures.forEach((f) => console.error(`  - ${f}`));
  process.exit(1);
}
console.log("\nadmission fault case: all checks passed");
