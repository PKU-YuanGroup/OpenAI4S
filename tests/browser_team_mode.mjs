/* Team-mode (M1+M2) end to end in a real browser.
 *
 * Unlike the other browser gates, this one needs a daemon started with
 * OPENAI4S_TEAM_MODE=1 and two seeded accounts, because the whole point is
 * what a *person* sees after logging in — the login redirect, the identity
 * chip, the admin governance panel, the guest's replay-only world, and the
 * cross-user refusals that a unit test can only assert at the socket.
 *
 * Setup (the harness does not seed for you — passwords must not ride argv):
 *   OPENAI4S_DATA_DIR=<dir> OPENAI4S_TEAM_MODE=1 OPENAI4S_PORT=8794 \
 *     openai4s serve &
 *   printf '<admin-pw>\n'  | openai4s user add erika   --role admin --password-stdin
 *   printf '<member-pw>\n' | openai4s user add mallory --password-stdin
 *   OPENAI4S_TEAM_ADMIN_PW=<admin-pw> OPENAI4S_TEAM_MEMBER_PW=<member-pw> \
 *     OPENAI4S_BROWSER_URL=http://127.0.0.1:8794 node tests/browser_team_mode.mjs
 */
import { skipFirstRunWizard } from "./browser_auth.mjs";

let playwright;
try {
  playwright = await import("playwright");
} catch (error) {
  const fallback = process.env.OPENAI4S_PLAYWRIGHT_MODULE;
  if (!fallback) throw error;
  playwright = await import(fallback);
}
const { chromium } = playwright;

const BASE = (process.env.OPENAI4S_BROWSER_URL || "http://127.0.0.1:8794").replace(
  /\/$/,
  "",
);
const ADMIN = {
  u: process.env.OPENAI4S_TEAM_ADMIN_USER || "erika",
  p: process.env.OPENAI4S_TEAM_ADMIN_PW,
};
const MEMBER = {
  u: process.env.OPENAI4S_TEAM_MEMBER_USER || "mallory",
  p: process.env.OPENAI4S_TEAM_MEMBER_PW,
};
if (!ADMIN.p || !MEMBER.p) {
  console.error(
    "set OPENAI4S_TEAM_ADMIN_PW and OPENAI4S_TEAM_MEMBER_PW to the seeded " +
      "accounts' passwords (see the header comment)",
  );
  process.exit(2);
}

const executablePath = process.env.OPENAI4S_BROWSER_EXECUTABLE || undefined;
const browser = await chromium.launch({ headless: true, executablePath });
let failures = 0;
const check = (ok, label) => {
  console.log((ok ? "  PASS " : "  FAIL ") + label);
  if (!ok) failures++;
};

async function login(page, who) {
  await page.goto(BASE + "/login", { waitUntil: "domcontentloaded" });
  await page.fill("#u", who.u);
  await page.fill("#p", who.p);
  await page.click("#b");
  await page.waitForURL(BASE + "/", { timeout: 10000 });
  await skipFirstRunWizard(page, BASE + "/");
}

// -- 1. the login gate is what an anonymous visitor meets --------------------
console.log("1. login, identity chip, admin affordance");
const adminCtx = await browser.newContext();
const admin = await adminCtx.newPage();
await admin.goto(BASE + "/", { waitUntil: "domcontentloaded" });
await admin.waitForURL("**/login", { timeout: 10000 });
check(true, "anonymous root redirects to /login");
await login(admin, ADMIN);
await admin.waitForSelector("#team-user", { state: "visible", timeout: 10000 });
const chip = await admin.textContent("#team-user");
check(chip.includes(ADMIN.u) && chip.includes("admin"), "chip names the admin");
// The chip is a real, clickable control in the dashboard header (TEAM-21):
// an ordinary click -- no force, no scroll -- opens the user menu.
await admin.click("#team-user");
const adminEntry = admin.locator(".ctx-menu .ctx-item", { hasText: "Team admin" });
await adminEntry.waitFor({ state: "visible", timeout: 5000 });
check(true, "admin sees the Team admin entry in the user menu");
check(
  (await admin.locator(".ctx-menu .ctx-item", { hasText: "Sign out" }).count()) === 1,
  "the user menu offers Sign out",
);

// -- 2. the governance panel renders live data ------------------------------
console.log("2. governance panel");
await adminEntry.click();
await admin.waitForSelector("#team-admin-modal:not(.hidden)", { timeout: 5000 });
await admin.waitForSelector(".team-admin-table", { timeout: 8000 });
const panel = await admin.textContent("#team-admin-body");
check(
  panel.includes(ADMIN.u) && panel.includes(MEMBER.u),
  "user table lists the seeded accounts",
);
check(
  /Users[\s\S]*Usage[\s\S]*Quotas[\s\S]*Invites[\s\S]*Audit/.test(panel),
  "all five governance sections render",
);
await admin.click("#team-admin-close");

// -- 3. a member's own session ----------------------------------------------
console.log("3. member session");
const memberCtx = await browser.newContext();
const member = await memberCtx.newPage();
await login(member, MEMBER);
const created = await member.evaluate(async () => {
  const projects = await (await fetch("/api/v1/projects")).json();
  let pid = (projects.projects[0] || {}).project_id;
  if (!pid) {
    const made = await (
      await fetch("/api/v1/projects", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name: "team-mode-e2e" }),
      })
    ).json();
    pid = made.project_id || made.id;
  }
  const frame = await (
    await fetch("/api/v1/frames", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ project_id: pid }),
    })
  ).json();
  return { pid, fid: frame.frame_id || frame.id };
});
check(!!created.fid, "member created a session");

// -- 3b. a session link opened signed-out comes back after the login ---------
// TEAM-9: the 303 used to drop the target and every login landed on "/".
console.log("3b. deep link through the login");
const linkCtx = await browser.newContext();
const linkPage = await linkCtx.newPage();
const deepLink = `/projects/${encodeURIComponent(created.pid)}/frames/${encodeURIComponent(created.fid)}`;
await linkPage.goto(BASE + deepLink, { waitUntil: "domcontentloaded" });
await linkPage.waitForURL("**/login?next=**", { timeout: 10000 });
await linkPage.fill("#u", MEMBER.u);
await linkPage.fill("#p", MEMBER.p);
await linkPage.click("#b");
await linkPage.waitForURL(BASE + deepLink, { timeout: 10000 });
check(true, "login returns to the session link it was opened from");
// A hand-written next= that leaves the origin is ignored, not followed.
await linkPage.evaluate(() => fetch("/api/v1/auth/logout", { method: "POST" }));
await linkPage.goto(BASE + "/login?next=" + encodeURIComponent("//example.invalid/x"), {
  waitUntil: "domcontentloaded",
});
await linkPage.fill("#u", MEMBER.u);
await linkPage.fill("#p", MEMBER.p);
await linkPage.click("#b");
await linkPage.waitForURL(BASE + "/", { timeout: 10000 });
check(true, "an off-origin next= lands on the home page instead");
await linkCtx.close();

// -- 4. governance is admin-only --------------------------------------------
console.log("4. governance is admin-only");
const memberAdminStatus = await member.evaluate(
  async () => (await fetch("/api/v1/team/users")).status,
);
check(memberAdminStatus === 403, `member gets 403 on /team/users (${memberAdminStatus})`);
await member.waitForSelector("#team-user", { state: "visible", timeout: 10000 });
await member.click("#team-user");
await member
  .locator(".ctx-menu .ctx-item", { hasText: "Sign out" })
  .waitFor({ state: "visible", timeout: 5000 });
check(
  (await member.locator(".ctx-menu .ctx-item", { hasText: "Team admin" }).count()) === 0,
  "member's user menu has no Team admin entry",
);
await member.keyboard.press("Escape");

// -- 5. admin visibility -----------------------------------------------------
console.log("5. admin visibility");
check(
  (await admin.evaluate(
    async (fid) => (await fetch("/api/v1/frames/" + fid)).status,
    created.fid,
  )) === 200,
  "admin can read the member's session",
);

// -- 6. read-only replay -----------------------------------------------------
console.log("6. read-only replay");
await member.goto(BASE + "/replay#" + created.fid, { waitUntil: "domcontentloaded" });
await member.waitForTimeout(1500);
check(!(await member.textContent("#err")), "owner's replay loads without error");
check((await member.textContent("#meta")).length > 0, "replay renders metadata");

// -- 7. a guest invite yields a replay-only world ---------------------------
console.log("7. guest invite");
const token = await admin.evaluate(async (pid) => {
  const r = await fetch("/api/v1/team/invites", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ project_id: pid }),
  });
  return (await r.json()).token;
}, created.pid);
check(!!token, "admin minted an invite");
const guestCtx = await browser.newContext();
const guest = await guestCtx.newPage();
await guest.goto(BASE + "/login", { waitUntil: "domcontentloaded" });
const redeem = await guest.evaluate(async (t) => {
  const r = await fetch("/api/v1/auth/redeem-invite", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      token: t,
      username: "visitor-e2e-" + Math.floor(Date.now() / 1000),
      password: "fake-guest-pw-not-real",
    }),
  });
  return r.status;
}, token);
check(redeem === 201, `invite redeemed into a guest account (${redeem})`);
// TEAM-28: the server sends a guest's page load to the viewer -- the
// workbench (and its run of 403s) never loads -- and the viewer lists what
// the guest may read, with who is signed in and a way out.
const guestApi = [];
guest.on("request", (req) => {
  if (req.url().includes("/api/v1/")) guestApi.push(new URL(req.url()).pathname);
});
await guest.goto(BASE + "/", { waitUntil: "domcontentloaded" });
await guest.waitForURL("**/replay", { timeout: 10000 });
check(true, "a guest landing on / is sent to /replay");
await guest
  .locator(`#list a[data-session="${created.fid}"]`)
  .waitFor({ state: "visible", timeout: 10000 });
check(true, "the guest's replay page lists the project's session");
check(
  (await guest.locator("#who button").count()) === 1,
  "the guest's replay page names the guest and offers Sign out",
);
check(
  guestApi.every((path) => path.startsWith("/api/v1/auth/")),
  `a guest's landing reads only its own auth routes (${[...new Set(guestApi)].join(", ")})`,
);
await guest.goto(BASE + deepLink, { waitUntil: "domcontentloaded" });
await guest.waitForURL(`**/replay#${created.fid}`, { timeout: 10000 });
check(true, "a session link opens that session in the guest's viewer");
const guestBlocked = await guest.evaluate(async () => ({
  frames: (await fetch("/api/v1/frames")).status,
  files: (await fetch("/api/v1/files")).status,
}));
check(
  guestBlocked.frames === 403 && guestBlocked.files === 403,
  "a guest's API surface is closed (D3)",
);

// -- 8. the isolation holes stay closed --------------------------------------
console.log("8. isolation");
const otherProject = await member.evaluate(
  async () => (await fetch("/api/v1/projects/proj_example")).status,
);
check(otherProject === 404, `non-participant refused a project (${otherProject})`);
const guestPreview = await guest.evaluate(
  async () => (await fetch("/preview/anything")).status,
);
check(
  guestPreview === 403 || guestPreview === 404,
  "a guest cannot pull artifact bytes through /preview/",
);

// -- 9. sign out --------------------------------------------------------------
console.log("9. sign out");
const loggedOut = await admin.evaluate(async () => {
  await fetch("/api/v1/auth/logout", { method: "POST" });
  return (await fetch("/api/v1/frames")).status;
});
check(loggedOut === 401, `the API refuses after logout (${loggedOut})`);

await browser.close();
if (failures) {
  console.error(`\nteam-mode browser gate FAILED (${failures})`);
  process.exit(1);
}
console.log("\nteam-mode browser gate passed");
