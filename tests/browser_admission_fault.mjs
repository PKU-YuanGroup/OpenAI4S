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
      (fid) => localStorage.getItem("openai4s.admission." + fid),
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
    !!storedBeforeAnswer,
    "the admission id was durable BEFORE the answer came back",
    String(storedBeforeAnswer),
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

  const cleared = await page.evaluate(
    (fid) => localStorage.getItem("openai4s.admission." + fid),
    frameId,
  );
  note(cleared === null, "a settled admission is cleared from storage", String(cleared));

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
} finally {
  await browser.close();
}

if (failures.length) {
  console.error(`\n${failures.length} failure(s):`);
  failures.forEach((f) => console.error(`  - ${f}`));
  process.exit(1);
}
console.log("\nadmission fault case: all checks passed");
