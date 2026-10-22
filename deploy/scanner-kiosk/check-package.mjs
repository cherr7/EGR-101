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
const heartbeats = [], apiCalls = [], failures = [], errors = [], streams = new Set();
let bridgeMode = false, apiUp = false;
const movements = [];
// A minimal stand-in for the Mac API, used only for the final bridge-to-confirmation check.
const specimen = (id, number) => ({ id, number, state: 'OUT', room_id: null, entered_at: null, due_at: null, revision: 0, sessions: [] });
const stack = { id: 'stack-1', version: 1, created_at: new Date().toISOString(), is_demo: true, project_number: 'PACKAGE-CHECK', material_ref: 'M1', test_code: 'FLM', preconditioning_code: 'AR', specimen_count: 2, specimens: [specimen('a', 1), specimen('b', 2)] };
function mockApi(method, path, body) {
  if (path === '/api/health') return { ok: true };
  if (path === '/api/config') return { rooms: [{ id: 'shared', name: 'Shared conditioning room', temperature_c: 23, relative_humidity: 65, required_hours: 24 }], stations: [{ id: 'shared-door', name: 'Shared conditioning room door', room_id: 'shared' }], treatments: [] };
  if (path === '/api/events') return { items: [] };
  if (method === 'POST' && path === '/api/lookup') return { ...stack, scanned_specimen_id: 'a' };
  if (method === 'POST' && path === '/api/movements') { movements.push(JSON.parse(body)); return { status: 'committed', message: 'Recorded.', stack_id: stack.id, affected_count: 2, recorded_at: new Date().toISOString(), event_id: 'event-1', version: 2 }; }
  if (path === '/api/stacks/' + stack.id) return stack;
  return null;
}
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
    if (url.pathname === '/station/input/events') {
      // Stands in for input-bridge.py behind the Pi's nginx.
      response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store' });
      response.write('retry: 1000\n\ndata: ' + JSON.stringify({ kind: 'status', scanner: 'ok', scanner_name: 'Test scanner', nfc: 'ok' }) + '\n\n');
      streams.add(response); request.on('close', () => streams.delete(response)); return;
    }
    if (bridgeMode && url.pathname === '/station-config.js') {
      // The Pi installer enables the bridge in the station configuration.
      const config = (await readFile(resolve(folder, 'station-config.js'), 'utf8')).replace(/}\s*;\s*$/, ', "inputBridge": true};\n');
      response.writeHead(200, { 'Content-Type': 'application/javascript', 'Cache-Control': 'no-store' });
      response.end(config); return;
    }
    if (url.pathname.startsWith('/api/')) {
      apiCalls.push({ method: request.method, path: url.pathname });
      let body = '';
      for await (const chunk of request) body += chunk;
      const value = apiUp && mockApi(request.method, url.pathname, body);
      if (value) { response.writeHead(200, { 'Content-Type': 'application/json' }); response.end(JSON.stringify(value)); return; }
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
  assert.equal(station.fixed, true);
  assert.ok((await page.locator('.kiosk-header h1').innerText()).includes(station.roomName));
  assert.equal(await page.getByLabel('Station', { exact: true }).isDisabled(), true);
  assert.equal(await page.locator('.sidebar').count(), 0);
  assert.ok(await page.locator('.kiosk-dock').isVisible());
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth && document.querySelector('.kiosk-shell').scrollHeight <= innerHeight));
  await page.keyboard.type('340282366920938463463374607431768211455');
  await page.keyboard.press('Enter');
  assert.equal(apiCalls.filter(call => call.method === 'POST' && call.path === '/api/movements').length, 0);
  await page.waitForTimeout(2200);
  assert.ok(heartbeats.some(value => value.capture_ready === true && value.focused === true && value.visible === true));
  assert.ok(heartbeats.every(value => Object.keys(value).sort().join(',') === 'capture_ready,focused,input_bridge,visible'));
  assert.ok(heartbeats.every(value => value.input_bridge === false));
  assert.equal(streams.size, 0);
  await page.evaluate(() => { location.hash = 'records'; });
  await page.waitForFunction(() => location.hash === '#kiosk');
  await page.reload();
  await page.waitForFunction(() => window.fabricScannerCaptureReady === true && document.querySelector('.readiness-area')?.textContent?.includes('NOT AVAILABLE'));
  assert.equal(new URL(page.url()).searchParams.get('station'), station.stationId);
  assert.equal(await page.locator('.readiness-area.confirmed').count(), 0);
  // Installed Pi: scans arrive from input-bridge.py over SSE, with the same gating as keyboard capture.
  bridgeMode = true;
  await page.reload();
  await page.waitForFunction(() => window.fabricInputBridgeConnected === true);
  await page.getByText('Scanner ready · NFC ready').waitFor();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth && document.querySelector('.kiosk-shell').scrollHeight <= innerHeight));
  if (process.env.SCREENSHOT) await page.screenshot({ path: process.env.SCREENSHOT });
  const send = message => { for (const stream of streams) stream.write('data: ' + JSON.stringify({ id: 1, source: 'barcode', at: Date.now() / 1000, ...message }) + '\n\n'); };
  const alert = text => page.waitForFunction(value => document.querySelector('.kiosk-message[role="alert"]')?.textContent?.includes(value), text);
  send({ kind: 'scan', code: '340282366920938463463374607431768211455' });
  send({ kind: 'scan', source: 'nfc', code: 'UL-FAB-0001' });
  await alert('Unrecognized scanner input');
  send({ kind: 'scan', code: '42', at: Date.now() / 1000 - 10 });
  await alert('arrived late');
  send({ kind: 'reject', message: 'This NFC tag does not hold a specimen ID.' });
  await alert('does not hold a specimen ID');
  send({ kind: 'status', scanner: 'missing', scanner_name: '', nfc: 'off' });
  await page.getByText('Scanner not detected').waitFor();
  await page.waitForTimeout(2200);
  assert.ok(heartbeats.some(value => value.input_bridge === true && value.capture_ready === true));
  assert.equal(apiCalls.filter(call => call.method === 'POST').length, 0);
  assert.equal(await page.locator('.readiness-area.confirmed').count(), 0);
  // With the API reachable, a bridge scan follows the normal lookup -> movement -> green path.
  apiUp = true;
  await page.getByText('Database connected').waitFor({ timeout: 10000 });
  send({ kind: 'status', scanner: 'ok', scanner_name: 'Test scanner', nfc: 'ok' });
  send({ kind: 'scan', code: '000000000000000000000000000000000000042' });
  await page.getByRole('heading', { name: 'IN confirmed', exact: true }).waitFor();
  assert.equal(movements.length, 1);
  assert.equal(movements[0].code, '000000000000000000000000000000000000042');
  assert.equal(movements[0].station_id, station.stationId);
  assert.equal(movements[0].direction, 'IN');
  assert.equal(await page.locator('.readiness-area.confirmed').count(), 1);
  assert.deepEqual(errors, []);
  assert.deepEqual(failures, []);
  console.log(JSON.stringify({ status: 'passed', display: '1280x720', fixed_station: station.stationId,
    local_assets: true, api_unavailable: true, capture_ready_offline: true, watchdog_heartbeat: true,
    automatic_scanner_route: true, reload: true, input_bridge_stream: true, bridge_scan_confirmed: true, offline_movements: 0, page_errors: 0 }, null, 2));
} finally {
  await browser.close();
  for (const stream of streams) stream.end();
  await new Promise(resolve => server.close(resolve));
}
