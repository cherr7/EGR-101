/** Headless smoke check of the real local bundle with the Mac API unavailable. */
import assert from 'node:assert/strict';
import { readFile, stat } from 'node:fs/promises';
import { createServer } from 'node:http';
import { dirname, extname, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '../../frontend/node_modules/playwright/index.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../');
const folder = resolve(process.argv[2] || resolve(root, 'output/station-ui'));
const manifest = JSON.parse(await readFile(resolve(folder, 'station-package.json'), 'utf8'));
assert.equal(manifest.format, 'fabric-conditioning-station');
const heartbeats = [], apiCalls = [], failures = [], errors = [];
const types = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json' };
const server = createServer(async (request, response) => {
  try {
    const url = new URL(request.url, 'http://127.0.0.1');
    if (url.pathname === '/station/heartbeat') {
      let body = '';
      for await (const chunk of request) body += chunk;
      heartbeats.push(JSON.parse(body));
      response.writeHead(204); response.end(); return;
    }
    if (url.pathname.startsWith('/api/')) {
      apiCalls.push({ method: request.method, path: url.pathname });
      response.writeHead(503, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ detail: 'Simulated unavailable Mac API.' })); return;
    }
    const path = resolve(folder, '.' + decodeURIComponent(url.pathname === '/' ? '/index.html' : url.pathname));
    if (!path.startsWith(folder + sep) || !(await stat(path)).isFile()) throw new Error('Not found');
    response.writeHead(200, { 'Content-Type': types[extname(path)] || 'application/octet-stream', 'Cache-Control': 'no-store' });
    response.end(await readFile(path));
  } catch {
    response.writeHead(404); response.end('Not found');
  }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  page.on('pageerror', error => errors.push(error.message));
  page.on('requestfailed', request => {
    // Unload/focus heartbeats can be canceled by the deliberate reload below.
    if (request.url().endsWith('/station/heartbeat') && request.failure()?.errorText === 'net::ERR_ABORTED') return;
    failures.push(request.url());
  });
  await page.goto(origin + '/station.html?station=wrong-room#overview');
  await page.bringToFront();
  await page.waitForFunction(() => window.fabricScannerCaptureReady === true);
  await page.waitForFunction(() => document.querySelector('.readiness-area')?.textContent?.includes('NOT AVAILABLE'));
  const station = await page.evaluate(() => window.fabricStationConfig);
  assert.equal(new URL(page.url()).hash, '#kiosk');
  assert.equal(new URL(page.url()).searchParams.get('station'), station.stationId);
  assert.equal(station.fixed, false);
  assert.ok((await page.locator('.kiosk-header h1').innerText()).includes(station.roomName));
  assert.equal(await page.getByRole('navigation', { name: 'Conditioning room' }).getByRole('button').count(), 3);
  await page.getByRole('button', { name: 'PPE', exact: true }).click();
  assert.equal(await page.getByRole('button', { name: 'PPE', exact: true }).getAttribute('aria-pressed'), 'true');
  assert.ok((await page.locator('.kiosk-header h1').innerText()).includes('PPE'));
  assert.equal(await page.locator('.sidebar').count(), 0);
  assert.ok(await page.locator('.kiosk-dock').isVisible());
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth && document.querySelector('.kiosk-shell').scrollHeight <= innerHeight));
  await page.keyboard.type('340282366920938463463374607431768211455');
  await page.keyboard.press('Enter');
  assert.equal(apiCalls.filter(call => call.method === 'POST' && call.path === '/api/movements').length, 0);
  await page.waitForTimeout(2200);
  assert.ok(heartbeats.some(value => value.capture_ready === true && value.focused === true && value.visible === true));
  assert.ok(heartbeats.every(value => Object.keys(value).sort().join(',') === 'capture_ready,focused,visible'));
  await page.evaluate(() => { location.hash = 'records'; });
  await page.waitForFunction(() => location.hash === '#kiosk');
  await page.reload();
  await page.waitForFunction(() => window.fabricScannerCaptureReady === true && document.querySelector('.readiness-area')?.textContent?.includes('NOT AVAILABLE'));
  assert.equal(new URL(page.url()).searchParams.get('station'), 'ppe-door');
  assert.equal(await page.getByRole('button', { name: 'PPE', exact: true }).getAttribute('aria-pressed'), 'true');
  assert.equal(await page.locator('.readiness-area.confirmed').count(), 0);
  assert.deepEqual(errors, []);
  assert.deepEqual(failures, []);
  console.log(JSON.stringify({ status: 'passed', display: '1280x720', default_station: station.stationId, configurable_room: true,
    local_assets: true, api_unavailable: true, capture_ready_offline: true, watchdog_heartbeat: true,
    automatic_scanner_route: true, reload: true, offline_movements: 0, page_errors: 0 }, null, 2));
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
