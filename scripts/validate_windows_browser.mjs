import assert from 'node:assert/strict';
import fs from 'node:fs';
import { chromium } from 'playwright';
import { authenticate, skipFirstRunWizard, redactSecrets } from '../tests/browser_auth.mjs';

const token = process.env.OPENAI4S_TOKEN;
const base = 'http://127.0.0.1:18760/';
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(redactSecrets(error.message, token)));
  assert(await authenticate(page, base, token));
  await skipFirstRunWizard(page, base);
  await page.locator('#dashboard').waitFor({ state: 'visible', timeout: 30000 });
  await page.locator('#dash-projects .dash-empty, #dash-projects .d-row:not(.skeleton-row)').first().waitFor({ state: 'visible' });
  await page.locator('#dashboard .skeleton-row').waitFor({ state: 'hidden' });
  assert.equal(await page.locator('#dash-projects-retry').count(), 0);
  assert.equal((await page.request.get(base + 'api/v1/projects')).status(), 200);
  await page.screenshot({ path: 'platform-evidence/windows-workbench.png', fullPage: true });
  assert.deepEqual(errors, []);
  fs.writeFileSync('platform-evidence/windows-browser.json', JSON.stringify({ passed: true, browser: 'Windows Chromium', authenticated_dashboard: true, page_errors: errors }, null, 2));
} catch (error) {
  throw new Error(redactSecrets(error.message, token));
} finally {
  await browser.close();
}
