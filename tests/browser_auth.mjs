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
    // Let the startup fetches settle first. Reloading with requests in flight
    // aborts them, and WebKit surfaces an aborted fetch as a page-level error
    // while Chromium and Firefox drop it silently -- the same abort
    // browser_matrix.mjs already warns about above its own `authenticate`
    // call, reintroduced here by this reload rather than by a second goto.
    await page.waitForLoadState("networkidle").catch(() => {});
    await page.reload({ waitUntil: "domcontentloaded" });
  }
  const wizard = page.locator("#onboarding");
  if (await wizard.isVisible().catch(() => false)) {
    await wizard.locator("button.outline-btn").first().click();
    await wizard.waitFor({ state: "hidden", timeout: 10000 });
  }
}

/**
 * Poll `predicate` until it returns a truthy value. Every browser script needs
 * this: the workbench CSP has no 'unsafe-eval', so page.waitForFunction is
 * refused and every wait has to be a locator or an evaluate poll.
 */
export async function waitUntil(label, predicate, timeoutMs = 20000, intervalMs = 60) {
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

/**
 * A daemon spawned by a browser gate gets a pinned, minimal environment: the
 * developer's shell must not drive it (a real provider key, a team mode, an
 * enforced kernel sandbox or an approval policy would each change what the
 * gate measures). Only locale, temp and executable lookup pass through.
 */
export function minimalChildEnvironment(extra = {}) {
  const environment = {
    PYTHONDONTWRITEBYTECODE: "1",
    PYTHONUTF8: "1",
    OPENAI4S_SKIP_DOTENV: "1",
    OPENAI4S_SECRET_STORE: "plaintext",
    OPENAI4S_UNATTENDED_APPROVAL: "deny",
    ...extra,
  };
  for (const key of ["PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT", "WINDIR"]) {
    if (process.env[key]) environment[key] = process.env[key];
  }
  return environment;
}

/** Keep the tail of a child's stream so a failure can quote the daemon. */
export function boundedLogCollector(stream, limit = 64 * 1024) {
  let value = "";
  stream?.setEncoding("utf8");
  stream?.on("data", (chunk) => {
    value = (value + chunk).slice(-limit);
  });
  return () => value;
}
