import { chromium } from "playwright";

const baseUrl = process.env.OPENAI4S_BROWSER_URL || "http://127.0.0.1:8760/";
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(String(error)));

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

  const projectsResponse = await page.request.get(`${baseUrl}api/projects`);
  if (!projectsResponse.ok()) {
    throw new Error(`projects API returned HTTP ${projectsResponse.status()}`);
  }
  const projects = await projectsResponse.json();
  if (!Array.isArray(projects.projects)) {
    throw new Error("projects API did not return a projects array");
  }
  if (pageErrors.length) {
    throw new Error(`browser page errors: ${pageErrors.join(" | ")}`);
  }
} finally {
  await browser.close();
}
