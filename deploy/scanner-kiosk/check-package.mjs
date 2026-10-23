/** Headless smoke check of the real local bundle with the Mac API unavailable. */
import assert from 'node:assert/strict';
import { readFile, stat } from 'node:fs/promises';
import { createServer } from 'node:http';
import { dirname, extname, resolve, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { chromium } from '../../frontend/node_modules/playwright/index.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../');
const folder = resolve(process.argv[2] || resolve(root, 'output/station-ui'));
const manifest = JSON.parse(await readFile(resolve(folder, 'station-package.json'), 'utf8'));
assert.equal(manifest.format, 'fabric-conditioning-station');
const heartbeats = [], apiCalls = [], failures = [], errors = [], streams = new Set();
let bridgeMode = false, apiUp = false;
const movements = [];
const sessionId = 'package-check-session';
let sequence = 0;
let devices = { scanner: 'ok', scanner_name: 'Test scanner', scanner_error: '', nfc: 'ok', nfc_error: '' };
const clock = () => performance.now();
const snapshot = () => ({ kind: 'status', ...devices, session_id: sessionId, now_ms: clock(), pages: streams.size });
// A minimal stand-in for the Mac API, used only for the final bridge-to-confirmation check.
const specimen = (id, number) => ({ id, number, state: 'OUT', room_id: null, entered_at: null, due_at: null, revision: 0, sessions: [] });
const stack = { id: 'stack-1', version: 1, created_at: new Date().toISOString(), is_demo: true, project_number: 'PACKAGE-CHECK', material_ref: 'M1', test_code: 'FLM', preconditioning_code: 'AR', specimen_count: 2, specimens: [specimen('a', 1), specimen('b', 2)] };
function mockApi(method, path, body) {
  if (path === '/api/health') return { ok: true };
  if (path === '/api/config') return { rooms: ['ppe','shared','footwear'].map(id => ({ id, name: ({ppe:'PPE', shared:'Shared', footwear:'Footwear'})[id]+' conditioning room', temperature_c: 23, relative_humidity: 65, required_hours: 24 })), stations: ['ppe','shared','footwear'].map(id => ({ id:id+'-door', name:id+' door', room_id:id })), treatments: [] };
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
    if (url.pathname === '/station/input/status') {
      response.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
      response.end(JSON.stringify(snapshot())); return;
    }
    if (url.pathname === '/station/input/events') {
      // First active page owns delivery; another page must never receive this scan.
      if (streams.size) { response.writeHead(409); response.end('Input already has an active page'); return; }
      response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store' });
      streams.add(response);
      response.write('retry: 1000\n\ndata: ' + JSON.stringify(snapshot()) + '\n\n');
      const keepalive = setInterval(() => response.write(': keepalive\n\n'), 1000);
      request.on('close', () => { streams.delete(response); clearInterval(keepalive); }); return;
    }
    if (url.pathname === '/station-config.js') {
      // Both modes use exactly the same real package, with one explicit test override.
      const config = (await readFile(resolve(folder, 'station-config.js'), 'utf8')).replace(/"inputBridge":\s*(?:true|false)/, '"inputBridge": '+bridgeMode);
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
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  page.on('requestfailed', request => {
    // Unload/focus heartbeats can be canceled by the deliberate reload below.
    if (/\/station\/(?:heartbeat|input\/)/.test(request.url()) && request.failure()?.errorText === 'net::ERR_ABORTED') return;
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
  assert.ok(heartbeats.every(value => Object.keys(value).sort().join(',') === 'capture_ready,focused,input_bridge,visible'));
  assert.ok(heartbeats.every(value => value.input_bridge === false));
  assert.equal(streams.size, 0);
  await page.evaluate(() => { location.hash = 'records'; });
  await page.waitForFunction(() => location.hash === '#kiosk');
  await page.reload();
  await page.waitForFunction(() => window.fabricScannerCaptureReady === true && document.querySelector('.readiness-area')?.textContent?.includes('NOT AVAILABLE'));
  assert.equal(new URL(page.url()).searchParams.get('station'), 'ppe-door');
  assert.equal(await page.getByRole('button', { name: 'PPE', exact: true }).getAttribute('aria-pressed'), 'true');
  assert.equal(await page.locator('.readiness-area.confirmed').count(), 0);
  // Installed Pi: scans arrive from input-bridge.py over SSE, with the same gating as keyboard capture.
  bridgeMode = true;
  await page.reload();
  await page.waitForFunction(() => window.fabricInputBridgeReady === true);
  await page.getByText('Scanner ready', { exact:true }).waitFor();
  assert.equal(streams.size, 1);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth && document.querySelector('.kiosk-shell').scrollHeight <= innerHeight));
  if (process.env.SCREENSHOT) await page.screenshot({ path: process.env.SCREENSHOT });
  const send = message => { for (const stream of streams) stream.write('data: ' + JSON.stringify({ id: ++sequence, session_id: sessionId, source: 'barcode', at_ms: clock(), now_ms: clock(), ...message }) + '\n\n'); };
  const alert = text => page.waitForFunction(value => document.querySelector('.kiosk-message[role="alert"]')?.textContent?.includes(value), text);
  send({ kind: 'scan', code: '340282366920938463463374607431768211455' });
  await page.waitForTimeout(100);
  assert.equal(movements.length, 0);
  apiUp = true;
  await page.getByText('Database connected').waitFor({ timeout: 10000 });
  send({ kind: 'scan', source: 'nfc', code: 'UL-FAB-0001' });
  await alert('Unrecognized scanner input');
  send({ kind: 'scan', code: '42', at_ms: clock() - 10000 });
  await alert('outside the live input window');
  send({ kind: 'reject', message: 'This NFC tag does not hold a specimen ID.' });
  await alert('does not hold a specimen ID');
  devices = { ...devices, scanner: 'missing', scanner_name: '', nfc: 'off' };
  send({ kind: 'status', ...devices });
  await page.getByText('SCANNER NOT READY', { exact:true }).first().waitFor();
  await page.waitForFunction(() => window.fabricInputBridgeReady === false);
  await page.waitForTimeout(2200);
  assert.ok(heartbeats.slice(-1).every(value => value.input_bridge === false && value.capture_ready === true));
  assert.equal(apiCalls.filter(call => call.method === 'POST').length, 0);
  assert.equal(await page.locator('.readiness-area.confirmed').count(), 0);
  // With the API reachable, a bridge scan follows the normal lookup -> movement -> green path.
  apiUp = true;
  await page.getByText('Database connected').waitFor({ timeout: 10000 });
  devices = { ...devices, scanner: 'ok', scanner_name: 'Test scanner', nfc: 'ok' };
  send({ kind: 'status', ...devices });
  await page.waitForFunction(() => window.fabricInputBridgeReady === true);
  await page.waitForTimeout(2200);
  assert.ok(heartbeats.some(value => value.input_bridge === true && value.capture_ready === true));
  // A second page sees the occupied state and cannot steal input or fall back to keys.
  const second = await page.context().newPage();
  await second.goto(origin + '/station.html?station=ppe-door#kiosk');
  await second.getByText('Scanner in another page', { exact:true }).waitFor();
  assert.equal(await second.evaluate(() => window.fabricInputBridgeReady), false);
  assert.equal(streams.size, 1);
  await second.close();
  await page.bringToFront();
  send({ kind: 'scan', code: '000000000000000000000000000000000000042' });
  await page.getByRole('heading', { name: 'IN confirmed', exact: true }).waitFor();
  assert.equal(movements.length, 1);
  assert.equal(movements[0].code, '000000000000000000000000000000000000042');
  assert.equal(movements[0].station_id, 'ppe-door');
  assert.equal(await page.getByRole('button', { name:'PPE', exact:true }).getAttribute('aria-pressed'), 'true');
  assert.equal(movements[0].direction, 'IN');
  assert.equal(await page.locator('.readiness-area.confirmed').count(), 1);
  // Exercise the self-contained file:// screen while the local UI is unavailable.
  const starting = await browser.newPage();
  let localReady = false, readyChecks = 0;
  await starting.route('http://127.0.0.1:8080/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/station/ready') {
      readyChecks++;
      await route.fulfill({ status: localReady ? 200 : 503, headers: { 'Access-Control-Allow-Origin':'null', 'Cache-Control':'no-store' }, body:'local readiness' });
    } else if (url.pathname === '/station.html') await route.fulfill({ contentType:'text/html', body:'<h1>Local station opened</h1>' });
    else throw new Error('The starting screen must only contact local UI readiness');
  });
  const startupFile = pathToFileURL(resolve(root, 'deploy/scanner-kiosk/startup.html')).href;
  const target = 'http://127.0.0.1:8080/station.html?station=ppe-door#kiosk';
  await starting.goto(startupFile+'#'+encodeURIComponent(target));
  await starting.getByRole('heading', { name:'Starting station…' }).waitFor();
  await starting.waitForFunction(() => document.getElementById('detail')?.textContent?.includes('Waiting for the local station'));
  assert.ok(readyChecks>0);
  localReady = true;
  await starting.getByRole('heading', { name:'Local station opened' }).waitFor();
  assert.equal(starting.url(), target);
  for (const invalid of ['https://example.invalid/station.html?station=ppe-door#kiosk', 'http://a:b@127.0.0.1:8080/station.html?station=ppe-door#kiosk', 'http://127.0.0.1:8080/station.html?station=ppe-door&other=1#kiosk']) {
    await starting.goto(startupFile+'#'+encodeURIComponent(invalid));
    await starting.reload(); // A fragment-only navigation does not rerun the file's guard.
    await starting.getByText('Station configuration needs attention.').waitFor();
  }
  await starting.close();
  assert.deepEqual(errors, []);
  assert.deepEqual(failures, []);
  console.log(JSON.stringify({ status: 'passed', display: '1280x720', default_station: station.stationId, configurable_room: true,
    local_assets: true, api_unavailable: true, capture_ready_offline: true, watchdog_heartbeat: true,
    automatic_scanner_route: true, reload: true, input_bridge_stream: true, bridge_scan_confirmed: true, active_consumer_exclusive: true, usable_reader_heartbeat: true, startup_feedback: true, offline_movements: 0, page_errors: 0 }, null, 2));
} finally {
  await browser.close();
  for (const stream of streams) stream.end();
  await new Promise(resolve => server.close(resolve));
}
