// Shared credential lookup for the browser harnesses.
//
// The daemon requires an access token by default, on loopback too. Both
// harnesses navigated to `/` with no credential and drove `/api/v1/*` with a
// bare `page.request.fetch`, so after the gate was turned on every check in
// both files would have failed on a 401 -- not because the thing under test
// broke, but because the test never learned to log in.
//
// It reads the token the way the CLI does, from the same file, so the two
// cannot drift: a harness holding a stale copy of a credential authenticates
// against nothing and reports it as a product failure.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export function daemonToken() {
  // The override exists for the same reason the CLI has one: the token file is
  // owner-only, and a daemon started under another account writes a file the
  // harness cannot read.
  const fromEnv = (process.env.OPENAI4S_TOKEN || "").trim();
  if (fromEnv) return fromEnv;
  const dataDir = process.env.OPENAI4S_DATA_DIR || path.join(os.homedir(), ".openai4s");
  try {
    return fs.readFileSync(path.join(dataDir, "access-token"), "utf8").trim() || null;
  } catch {
    // No token file means the daemon runs with the gate off
    // (OPENAI4S_REQUIRE_TOKEN=0). That is a supported configuration for one
    // more minor release, so it is not an error here -- the navigation below
    // simply carries no token and the daemon does not ask for one.
    return null;
  }
}

// Authenticate a browser context by doing what a user does: open the printed
// URL once. The daemon answers 303, sets an HttpOnly cookie and redirects to
// the same path with the token stripped, after which every request from this
// context -- including `page.request.fetch`, which shares the context's cookie
// jar -- carries it.
//
// Deliberately the query-string bootstrap rather than injecting a header on
// every call: this is the path a real browser takes, so the harness exercises
// the 303 and the cookie hand-off instead of routing around them.
export async function authenticate(page, baseUrl, explicitToken = undefined) {
  // Security-sensitive harnesses may validate and open a particular token
  // file themselves, then pass the captured value here. In that mode never
  // perform a second environment/default-directory lookup: doing so would
  // reopen a TOCTOU window after the caller's validation.
  const token =
    explicitToken === undefined
      ? daemonToken()
      : String(explicitToken || "").trim() || null;
  if (!token) return null;
  const url = new URL(baseUrl);
  url.searchParams.set("token", token);
  await page.goto(url.toString(), { waitUntil: "domcontentloaded" });
  const landed = new URL(page.url());
  if (landed.searchParams.has("token")) {
    throw new Error(
      `the token bootstrap did not strip the credential from the URL: ${page.url()}`
    );
  }
  await skipFirstRunWizard(page, baseUrl);
  return token;
}

export async function skipFirstRunWizard(page, baseUrl) {
  // F-23: the default workbench is the Vite shell, which mounts the first-run
  // wizard on a fresh data dir. Workbench E2E files drive dock/composer
  // locators; a modal on top is not the product under test here. Skip is a
  // documented first-run path (`POST {skip:true}`, zero provider calls).
  const completeUrl = new URL("/api/v1/onboarding/complete", baseUrl);
  const complete = await page.request.post(completeUrl.toString(), {
    data: { skip: true },
    headers: { "content-type": "application/json" },
  });
  if (complete.ok()) {
    await page.reload({ waitUntil: "domcontentloaded" });
  }
  const wizard = page.locator("#onboarding");
  if (await wizard.isVisible().catch(() => false)) {
    await wizard.locator("button.outline-btn").first().click();
    await wizard.waitFor({ state: "hidden", timeout: 10000 });
  }
}
