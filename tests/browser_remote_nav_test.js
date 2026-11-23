// The Remote Access tab, followed in a REAL browser against the REAL server.
//
// Why this exists. The fake-DOM suites (remote_nav_live_test.js and friends) execute adminnav.js and
// settings.js in a sandbox with hand-written element stubs. They proved the modules' logic and still
// let a defect through, because they cannot see what only a browser and a running server show:
//
//   * turning Remote Access ON reads the network adapters afresh, a Windows PowerShell round trip
//     that measured 9-11 s, during which the toggle went grey and said nothing -- a person who sees
//     no change for ten seconds refreshes, and calls the tab "static until refreshed";
//   * a page restored or left in the background keeps the answer it was served with.
//
// This drives the shipped pages through the shipped server: Remote Access off -> on -> off with no
// reload, from the Device Info page and from the Remote Access page itself, and asserts what is on
// screen at each step.
//
// It needs a browser and a network adapter. Where there is no Playwright-compatible module or the
// machine offers no adapter to listen on, it says SKIPPED and exits 0 -- an honest "not run", never
// a pass. Set OMNI_REQUIRE_BROWSER=1 to make that a failure (the release checklist does).
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');
const {spawn} = require('child_process');

const ROOT = path.join(__dirname, '..');
const SERVER = path.join(ROOT, 'OmniMatrix_upgrade_server_v7_6y.py');

function skip(why) {
  console.log('SKIPPED  ' + why);
  process.exit(process.env.OMNI_REQUIRE_BROWSER === '1' ? 1 : 0);
}

function findBrowserModule() {
  const tries = [process.env.OMNI_BROWSER_MODULE, 'playwright', 'patchright'].filter(Boolean);
  const ext = path.join(os.homedir(), '.vscode', 'extensions');
  try {
    fs.readdirSync(ext).forEach((dir) => {
      tries.push(path.join(ext, dir, 'standalone', 'node_modules', 'patchright'));
    });
  } catch (err) { /* no extensions folder */ }
  for (const name of tries) {
    try { return require(name); } catch (err) { /* next */ }
  }
  return null;
}

// A full Chromium the module can drive, when the headless shell it expects is not installed.
function findChromium() {
  if (process.env.OMNI_BROWSER_EXE) return process.env.OMNI_BROWSER_EXE;
  const home = process.env.PLAYWRIGHT_BROWSERS_PATH
    || path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), '.cache'), 'ms-playwright');
  try {
    const builds = fs.readdirSync(home).filter((d) => /^chromium-\d+$/.test(d)).sort().reverse();
    for (const build of builds) {
      for (const rel of [['chrome-win64', 'chrome.exe'], ['chrome-linux', 'chrome'],
                         ['chrome-mac', 'Chromium.app', 'Contents', 'MacOS', 'Chromium']]) {
        const candidate = path.join(home, build, ...rel);
        if (fs.existsSync(candidate)) return candidate;
      }
    }
  } catch (err) { /* none installed */ }
  return undefined;
}

function request(port, method, url, body) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const req = http.request({host: '127.0.0.1', port, path: url, method,
      headers: data ? {'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data)} : {}},
      (res) => {
        let text = '';
        res.on('data', (c) => { text += c; });
        res.on('end', () => { try { resolve({status: res.statusCode, body: JSON.parse(text)}); }
                                catch (e) { resolve({status: res.statusCode, body: text}); } });
      });
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms, step) {
  const end = Date.now() + ms;
  for (;;) {
    const value = await fn();
    if (value) return value;
    if (Date.now() > end) return false;
    await wait(step || 200);
  }
}

(async () => {
  const pw = findBrowserModule();
  if (!pw || !pw.chromium) skip('no Playwright-compatible module is installed');

  const port = 20000 + Math.floor(Math.random() * 20000);
  const data = fs.mkdtempSync(path.join(os.tmpdir(), 'omni-nav-'));
  fs.writeFileSync(path.join(data, 'config.json'), JSON.stringify({
    username: 'admin', password: 'x', fallback_password: 'y', ws_port: 80, timeout: 2,
    concurrency: 2, firmware_path: ''}));
  const python = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  const server = spawn(python, [SERVER], {
    cwd: ROOT, stdio: 'ignore',
    env: Object.assign({}, process.env, {OMNI_DATA_DIR: data, OMNI_PORT: String(port),
                                         OMNI_FIRMWARE_CHECK: '0'}),
  });
  const results = [];
  let failures = 0;
  const check = (name, ok, detail) => {
    if (!ok) failures++;
    results.push((ok ? 'ok   ' : 'FAIL ') + name + (ok || !detail ? '' : ': ' + detail));
  };
  let browser = null;
  try {
    const up = await until(async () => {
      try { return (await request(port, 'GET', '/api/remote_access')).status === 200; }
      catch (err) { return false; }
    }, 60000, 500);
    if (!up) skip('the server did not start');
    // Enumerating adapters is the slow part on Windows; give it time before concluding there are none.
    let state = {};
    await until(async () => {
      state = (await request(port, 'GET', '/api/remote_access?refresh=1')).body || {};
      return (state.adapters || []).length > 0;
    }, 60000, 1000);
    if (!(state.adapters || []).length) skip('this machine offers no network adapter to listen on');
    const adapter = state.adapters[0];
    await request(port, 'POST', '/api/bootstrap/admin',
      {username: 'boss', password: 'longpassword1', confirm: 'longpassword1'});

    browser = await pw.chromium.launch({headless: true, executablePath: findChromium()});
    const page = await browser.newPage();
    const tab = () => page.evaluate(() => !!document.querySelector('a[data-nav="remote"]'));
    const base = 'http://127.0.0.1:' + port;

    // ---- Device Info: off -> on, with no reload
    await page.goto(base + '/');
    check('starts with Remote Access off and no tab', (await tab()) === false);
    // A first run shows the network-access notice over the page; the operator continues past it.
    const notice = page.locator('#notice_continue');
    if (await notice.isVisible().catch(() => false)) await notice.click();
    await page.click('#header_gear');
    await page.waitForSelector('#cfg_remote_toggle', {state: 'visible'});
    await page.selectOption('#cfg_remote_adapter', {value: adapter.id || adapter.address});
    await page.selectOption('#cfg_remote_transport', 'http');
    await page.click('#cfg_remote_toggle');
    await page.locator('button', {hasText: /^Turn on$/}).first().click();
    // The slow part is the server's: say so at once, in the control and in the status line.
    const pending = await until(async () => /Turning on/.test(
      await page.textContent('#cfg_remote_toggle')), 2000, 50);
    check('turning on says so immediately, not after the network read', pending);
    check('the status line explains the wait',
      /several seconds/.test(await page.textContent('#cfg_remote_status')));
    const appeared = await until(tab, 60000, 250);
    check('the Remote Access tab appears after a successful enable, with no reload', appeared);
    check('the toggle offers turning it off again',
      /Turn off/.test(await page.textContent('#cfg_remote_toggle')));

    // ---- Device Info: on -> off, with no reload
    await page.click('#cfg_remote_toggle');
    const gone = await until(async () => !(await tab()), 15000, 150);
    check('the Remote Access tab disappears after a successful disable, with no reload', gone);

    // ---- a page that is not on screen is brought in line when it returns
    await page.goto(base + '/matrix');
    check('a freshly served page agrees (off)', (await tab()) === false);
    await request(port, 'POST', '/api/remote_access', {
      enabled: true, adapter_id: adapter.id || '', address: adapter.address,
      transport: 'http', accept_insecure: true});
    await page.evaluate(() => { document.dispatchEvent(new Event('visibilitychange')); window.dispatchEvent(new Event('focus')); });
    const followed = await until(tab, 10000, 150);
    check('a page that missed the change follows it when it is shown again', followed);

    // ---- the Remote Access page itself: turning it off there leaves the page
    await page.goto(base + '/admin/users');
    if (await page.locator('#notice_continue').isVisible().catch(() => false)) await page.locator('#notice_continue').click();
    await page.click('#header_gear');
    await page.waitForSelector('#cfg_remote_toggle', {state: 'visible'});
    check('the page that IS the entry keeps it while Remote Access is on', (await tab()) === true);
    await page.click('#cfg_remote_toggle');
    const left = await until(async () => new URL(page.url()).pathname === '/', 15000, 150);
    check('turning Remote Access off on its own page leaves for the application', left);
    check('and the tab is gone there too', (await tab()) === false);

    // ---- pages the browser already holds from before the change (the 304 defect)
    //
    // Every page except Device Info used to answer with the file's ETag and `no-cache`. The Remote
    // Access state stamped into the body is not part of that ETag, so a page visited while Remote
    // Access was off was revalidated after it was switched on, answered 304, and shown from the
    // browser's copy -- tab missing until a hard refresh. Visit first, change it, visit again.
    const enableApi = (https) => request(port, 'POST', '/api/remote_access', {
      enabled: true, adapter_id: adapter.id || '', address: adapter.address,
      transport: https ? 'https' : 'http', accept_insecure: !https});
    for (const https of [false, true]) {
      const label = https ? ' (HTTPS)' : ' (HTTP)';
      await request(port, 'POST', '/api/remote_access', {enabled: false});
      if (https) await request(port, 'POST', '/api/admin/certificate', {action: 'generate', address: adapter.address});
      for (const p of ['/matrix/usb', '/matrix/configure', '/matrix/multiview']) await page.goto(base + p);
      const on = await enableApi(https);
      check('Remote Access enables' + label, on.status === 200 && on.body && on.body.ok !== false, JSON.stringify(on.body).slice(0, 120));
      for (const p of ['/matrix/usb', '/matrix/configure', '/matrix/multiview']) {
        await page.goto(base + p);
        check(p + ' shows the tab after it was switched on' + label, (await tab()) === true);
      }
      await request(port, 'POST', '/api/remote_access', {enabled: false});
      for (const p of ['/matrix/usb', '/matrix/configure']) {
        await page.goto(base + p);
        check(p + ' drops the tab after it was switched off' + label, (await tab()) === false);
      }
    }
  } catch (err) {
    failures++;
    results.push('FAIL ' + String(err && err.message ? err.message : err).replace(/\s+/g, ' ').slice(0, 500));
  } finally {
    try { if (browser) await browser.close(); } catch (err) { /* closing */ }
    try { await request(port, 'POST', '/api/remote_access', {enabled: false}); } catch (err) { /* gone */ }
    server.kill();
    try { fs.rmSync(data, {recursive: true, force: true}); } catch (err) { /* temp */ }
  }
  results.forEach((line) => console.log(line));
  console.log(failures ? failures + ' FAILED' : 'browser remote-nav test: all checks passed');
  process.exit(failures ? 1 : 0);
})();
