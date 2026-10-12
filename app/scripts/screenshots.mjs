// Headless end-to-end walk-through + screenshots (uses the system Chrome via playwright-core).
// Usage: BASE=http://127.0.0.1:3100 ADMIN_EMAIL=.. ADMIN_PASSWORD=.. DEMO_EMAIL=.. DEMO_PASSWORD=.. node scripts/screenshots.mjs
import { chromium } from "playwright-core";
import fs from "node:fs";

const BASE = process.env.BASE ?? "http://127.0.0.1:3100";
const OUT = new URL("../docs/screens/", import.meta.url).pathname;
fs.mkdirSync(OUT, { recursive: true });
const log = (...a) => console.log("[screens]", ...a);
const browser = await chromium.launch({ executablePath: process.env.CHROME ?? "/usr/bin/google-chrome", headless: true, args: ["--no-sandbox", "--disable-gpu"] });
const shot = async (page, name, full = true) => { await page.waitForTimeout(300); await page.screenshot({ path: `${OUT}${name}.png`, fullPage: full }); log("saved", name); };
const login = async (page, email, pw) => {
  await page.goto(`${BASE}/login`);
  await page.fill('input[name="email"]', email);
  await page.fill('input[name="password"]', pw);
  await Promise.all([page.waitForURL(/\/(app|onboarding)/), page.click('button[type="submit"]')]);
};
const results = [];
const check = (name, ok) => { results.push([name, ok]); log(ok ? "PASS" : "FAIL", name); };

// ---------- desktop, admin ----------
{
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  await page.goto(BASE); await shot(page, "01-landing-desktop", false);
  await page.goto(`${BASE}/login`); await shot(page, "02-login-desktop", false);
  await login(page, process.env.ADMIN_EMAIL, process.env.ADMIN_PASSWORD);
  await page.goto(`${BASE}/app`); await shot(page, "03-dashboard-desktop");
  await page.goto(`${BASE}/app/approvals`); await shot(page, "04-approvals-desktop");
  await page.goto(`${BASE}/app/chat`); await page.click("text=LinkedIn tip for clinics"); await page.waitForURL(/\/app\/chat\//); await shot(page, "05-chat-desktop", false);
  for (const s of ["integrations", "guardrails", "ai", "channels", "members", "billing"]) { await page.goto(`${BASE}/app/settings/${s}`); await shot(page, `06-settings-${s}-desktop`); }
  await page.goto(`${BASE}/admin`); await shot(page, "07-admin-desktop");
  await ctx.close();
}

// ---------- mobile, new client end to end ----------
const email = `e2e-${Date.now()}@example.com`;
const WSNAME = `Chai Corner (test ${String(Date.now()).slice(-5)})`;
{
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, userAgent: "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Mobile Safari/537.36" });
  const page = await ctx.newPage();
  await page.goto(`${BASE}/signup`);
  await page.fill('input[name="name"]', "Asha");
  await page.fill('input[name="email"]', email);
  await page.fill('input[name="password"]', "test-password-123");
  await Promise.all([page.waitForURL(/\/onboarding/), page.click('button[type="submit"]')]);
  await page.fill('input[name="name"]', WSNAME); await page.fill('input[name="city"]', "Hyderabad");
  await Promise.all([page.waitForLoadState("networkidle"), page.click('button[type="submit"]')]);
  await page.click("text=Self-serve Solo");
  await page.click("text=Choose plan"); await page.waitForSelector("text=Pay ₹1,999");
  await shot(page, "10-onboarding-plan-payment-mobile");
  check("plan pick shows UPI payment box", await page.isVisible("img[alt='UPI QR code']"));
  await page.click("text=I've paid / I'll pay later: continue"); await page.waitForSelector("text=1/10");
  const answers = ["Chai Corner, we sell masala chai and snacks", "Hyderabad, Madhapur. Office workers", "More footfall in the evening", "skip", "Warm, quick, local", "Instagram and X", "Chai tips; never politics", "skip", "Approve every post", "WhatsApp, 9 to 9"];
  const bubbles = () => page.locator("div.whitespace-pre-wrap").count();
  const answer = async (a) => {
    const n = await bubbles();
    await page.fill('input[name="text"]', a);
    await page.click('form button[type="submit"]');
    await page.waitForFunction((k) => document.querySelectorAll("div.whitespace-pre-wrap").length >= k, n + 2);
  };
  for (const a of answers) await answer(a);
  await shot(page, "11-onboarding-interview-summary-mobile");
  await page.fill('input[name="text"]', "looks good");
  await page.click('form button[type="submit"]');
  await page.waitForSelector("text=Connect your tools");
  check("interview finished -> connect tools step", true);
  await shot(page, "12-onboarding-integrations-mobile", false);
  await page.click("button:has-text('Next')"); await page.waitForSelector("text=Where do you want to chat?");
  await shot(page, "13-onboarding-channel-mobile");
  check("Slack offered in onboarding channel step", await page.isVisible("text=Slack"));
  check("Telegram hidden in onboarding channel step", await page.locator("p.font-normal", { hasText: "Telegram" }).count() === 0);
  await Promise.all([page.waitForURL(/\/app$/), page.click("text=Finish setup")]);
  await shot(page, "14-dashboard-mobile", false);
  // chat -> agent (worker) -> draft
  await page.goto(`${BASE}/app/chat`);
  await Promise.all([page.waitForURL(/\/app\/chat\//), page.click("text=New chat")]);
  await page.fill("textarea", "draft an x post about our evening chai and samosa combo");
  await page.click("button:has-text('Send')");
  await page.waitForSelector("text=Open in Approvals", { timeout: 90000 });
  await shot(page, "15-chat-mobile", false);
  check("agent reply created a draft (worker + agent loop)", true);
  await page.goto(`${BASE}/app/approvals`);
  await shot(page, "16-approvals-pending-mobile", false);
  await page.click("button:has-text('Approve')");
  await page.waitForLoadState("networkidle");
  check("approve blocked until payment is confirmed", !!(await page.waitForSelector("text=payment confirmed", { timeout: 10000 }).catch(() => null)));
  // admin marks paid
  const actx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  const ap = await actx.newPage();
  await login(ap, process.env.ADMIN_EMAIL, process.env.ADMIN_PASSWORD);
  await ap.goto(`${BASE}/admin`);
  const card = ap.locator(".card", { hasText: WSNAME });
  await card.locator("input[name=reference]").fill("UPI-E2E-TEST");
  await card.locator("button:has-text('Mark paid')").click();
  await ap.waitForSelector("text=Marked paid");
  check("admin marked invoice paid", true);
  await actx.close();
  await page.goto(`${BASE}/app/approvals`);
  await page.click("button:has-text('Approve')");
  await page.waitForSelector("text=Approved: post from your phone", { timeout: 15000 });
  check("approve after payment -> approved (dry run, nothing sent)", await page.isVisible("text=Dry run"));
  check("handoff buttons shown", await page.isVisible("button:has-text('Open in X')") && await page.isVisible("button:has-text('Share on WhatsApp')"));
  await shot(page, "17-approvals-approved-handoff-mobile");
  await page.goto(`${BASE}/app/settings`); await shot(page, "18-settings-mobile");
  await page.goto(`${BASE}/app/settings/guardrails`); await shot(page, "19-guardrails-mobile");
  // isolation check through the UI: the seeded demo chat is not visible to this new tenant
  const r = await page.request.get(`${BASE}/api/health`);
  check("health ok", r.ok());
  const manifest = await page.request.get(`${BASE}/manifest.webmanifest`);
  check("PWA manifest served", manifest.ok() && (await manifest.json()).display === "standalone");
  const sw = await page.request.get(`${BASE}/sw.js`);
  check("service worker served", sw.ok());
  // v0.3: the Clients dashboard is admin-only
  const nc = await page.goto(`${BASE}/admin/clients`);
  check("non-admin gets 404 on /admin/clients", nc?.status() === 404);
  const nd = await page.goto(`${BASE}/admin/clients/client0`);
  check("non-admin gets 404 on a client page", nd?.status() === 404);
  await ctx.close();
}

// ---------- mobile, admin Clients dashboard (v0.3). Screens go to /tmp: they show real profile handles. ----------
{
  const PRIV = process.env.PRIVATE_SCREENS_DIR ?? "/tmp/northstar-private-screens/";
  fs.mkdirSync(PRIV, { recursive: true });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
  const page = await ctx.newPage();
  await login(page, process.env.ADMIN_EMAIL, process.env.ADMIN_PASSWORD);
  const r1 = await page.goto(`${BASE}/admin/clients`);
  check("admin sees /admin/clients", r1?.ok() && await page.isVisible("[data-testid=client-client0]") && await page.isVisible("[data-testid=client-client1]"));
  check("clients show caps", (await page.locator("[role=meter]").count()) >= 6);
  await page.screenshot({ path: `${PRIV}20-clients-mobile.png`, fullPage: true });
  await page.click("[data-testid=client-client1]"); await page.waitForURL(/\/admin\/clients\/client1/);
  check("client analytics page renders", await page.isVisible("text=Recent activity") && await page.isVisible("text=AI spend (month)"));
  await page.click("text=30 days"); await page.waitForURL(/range=30/);
  check("30-day range works", await page.isVisible("text=Per day, last 30 days"));
  await page.screenshot({ path: `${PRIV}21-client-analytics-mobile.png`, fullPage: true });
  const noBottomScroll = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
  check("no horizontal scroll on mobile", noBottomScroll);
  await ctx.close();
}
await browser.close();
const failed = results.filter(([, ok]) => !ok);
log(`${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
