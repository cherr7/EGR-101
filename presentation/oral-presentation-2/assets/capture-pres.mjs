// Presentation captures against the isolated scratch API on 8121 only (synthetic data).
import { chromium } from '../frontend/node_modules/playwright/index.mjs';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const origin = 'http://127.0.0.1:8121';
const folder = resolve(process.argv[2] || 'output/pres');
await mkdir(folder, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: 'chrome' });
const context = await browser.newContext({ baseURL: origin, viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
const request = context.request;
async function json(r) { if (!r.ok()) throw new Error(r.url() + ' ' + await r.text()); return r.json(); }
const sheetBody = rows => ({  // the prep-sheet draft layout: one worksheet per project
  headers: Object.fromEntries(rows.map(r => [r.project_number, { project_number: r.project_number }])),
  rows: rows.map(r => ({ sheet: r.project_number, test: r.test_code, conditions: r.preconditioning_code, conditioning: String(r.specimen_count), sample: r.material_ref })),
});
async function requestId(request, client = 'shared-door') {  // '<station>-<n>', reserved like the kiosk does
  const block = await (await request.post('/api/request-ids', { data: { client, count: 1 } })).json();
  return `${client}-${block.first}`;
}
const tag = String(Date.now()).slice(-4);
const rows = [
  { project_number: '4900000' + tag + '1', material_ref: 'Outer shell fabric', test_code: 'D4966', preconditioning_code: 'AR', specimen_count: 20 },
  { project_number: '4900000' + tag + '2', material_ref: 'Moisture barrier', test_code: 'D3786', preconditioning_code: '5L', specimen_count: 10 },
  { project_number: '4900000' + tag + '3', material_ref: 'Thermal liner', test_code: 'D3884', preconditioning_code: '50L', specimen_count: 12 },
];
const files = [];
const page = await context.newPage();
const errors = [];
page.on('pageerror', e => errors.push(e.message));
async function cap(name) { await page.screenshot({ path: resolve(folder, name), animations: 'disabled' }); files.push(name); }
try {
  const draft = await json(await request.post('/api/prep-drafts', { data: sheetBody(rows) }));
  const reg = await json(await request.post('/api/prep-drafts/' + draft.id + '/commit', { data: {} }));
  const stacks = [];
  for (const id of reg.stack_ids) stacks.push(await json(await request.get('/api/stacks/' + id)));
  const [ready, fresh, waiting] = stacks;
  const code = s => s.specimens[0].barcode_value;
  // Stack 1 entered 25 h ago (backdated through the normal correction route).
  const entered = await json(await request.post('/api/movements', { data: { station_id: 'shared-door', code: code(ready), scope: 'stack', request_id: await requestId(request), direction: 'IN' } }));
  await json(await request.patch('/api/transactions/' + entered.event_id, { data: { occurred_at: new Date(Date.now() - 25 * 3600000).toISOString(), expected_version: entered.version } }));
  const pdf = await request.get('/api/stacks/' + fresh.id + '/labels.pdf');
  await writeFile(resolve(folder, 'labels-stack.pdf'), await pdf.body());

  // Staff: preparation screen with rows typed in.
  await page.goto('/#prep');
  await page.getByText('Database connected', { exact: true }).waitFor();
  await page.getByRole('button', { name: '＋ New prep sheet' }).click();
  await page.getByLabel('Oracle project').fill('490000000204');
  await page.getByLabel('Test, row 1').fill('D5034');
  await page.getByLabel('Conditions, row 1').fill('5L');
  await page.getByLabel('Specimens per sample for Conditioning, row 1').fill('15');
  await page.getByLabel('Sample Number, row 1').fill('Outer shell fabric');
  await cap('01-staff-prep.png');

  // Scanner flow at the Pi Touch Display 2 resolution.
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/#kiosk');
  await page.getByText('Online', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Shared', exact: true }).click();
  await page.getByRole('button', { name: 'Stack', exact: true }).click();
  await page.getByRole('button', { name: 'IN', exact: true }).click();
  await page.waitForTimeout(400);
  await cap('02-scanner-ready-in.png');
  await page.keyboard.type(code(fresh)); await page.keyboard.press('Enter');
  await page.getByRole('heading', { name: 'IN confirmed', exact: true }).waitFor();
  await cap('03-scanner-in-confirmed.png');
  await page.getByRole('button', { name: 'OUT', exact: true }).click();
  await page.waitForTimeout(4500);
  await page.keyboard.type(code(fresh)); await page.keyboard.press('Enter');
  await page.getByRole('dialog', { name: 'Early checkout' }).waitFor();
  await page.waitForTimeout(300);
  await cap('04-scanner-early-checkout.png');
  await page.getByRole('button', { name: 'Keep in room' }).click();
  await page.getByRole('button', { name: 'Individual', exact: true }).click();
  await page.keyboard.type(code(ready)); await page.keyboard.press('Enter');
  const chooser = page.getByRole('dialog', { name: 'Choose the specimens moving' });
  await chooser.waitFor();
  for (const n of ['01', '02', '03']) await chooser.getByRole('checkbox', { name: new RegExp('^' + n) }).check().catch(async () => { await chooser.getByText(n, { exact: true }).click(); });
  await page.waitForTimeout(300);
  await cap('05-scanner-partial-out.png');
  await chooser.getByRole('button', { name: /^Record 3 OUT/ }).click();
  await page.getByRole('heading', { name: 'OUT confirmed', exact: true }).waitFor();
  await cap('06-scanner-out-confirmed.png');
  await page.getByRole('button', { name: 'Stack', exact: true }).click();
  await page.waitForTimeout(13000);
  await page.getByRole('button', { name: 'Inventory', exact: true }).click();
  await page.getByRole('dialog', { name: 'Inventory' }).waitFor();
  await page.waitForTimeout(1500);
  await cap('07-scanner-inventory.png');
  await page.getByRole('dialog', { name: 'Inventory' }).getByRole('button', { name: 'Close', exact: true }).first().click().catch(() => page.keyboard.press('Escape'));
  // Offline: the health check fails, so the station disables writes.
  await page.route('**/api/health', r => r.fulfill({ status: 503, body: '{}' }));
  await page.getByText('Offline', { exact: true }).first().waitFor({ timeout: 20000 });
  await page.waitForTimeout(500);
  await cap('08-scanner-offline.png');
  await page.unroute('**/api/health');

  // Staff: overview, records, transactions.
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/#overview');
  await page.getByText('Database connected', { exact: true }).waitFor();
  await page.getByLabel('Search stacks').fill('4900000' + tag);
  await page.locator('.overview-table tbody tr').nth(2).waitFor();
  await page.waitForTimeout(500);
  await cap('09-staff-stacks.png');
  await page.goto('/#transactions');
  await page.getByText('Database connected', { exact: true }).waitFor();
  await page.waitForTimeout(1500);
  await cap('10-staff-transactions.png');
  const events = await json(await request.get('/api/transactions?limit=20'));
  const list = Array.isArray(events) ? events : (events.items || events.transactions || []);
  const partial = list.find(e => e.direction === 'OUT');
  if (partial) {
    await page.goto('/#transactions?stack=' + ready.id + '&event=' + partial.id);
    await page.getByRole('dialog', { name: 'Edit OUT transaction' }).waitFor();
    await page.waitForTimeout(500);
    await cap('11-staff-correction.png');
  }
  await page.goto('/#records');
  await page.getByLabel('Search records').fill('4900000' + tag + '1');
  await page.waitForTimeout(1500);
  await cap('12-staff-records.png');
  if (errors.length) console.error('page errors:', errors);
  await writeFile(resolve(folder, 'manifest.json'), JSON.stringify({ captured_at: new Date().toISOString(), source: origin, data: 'Isolated synthetic records (project numbers 4900000' + tag + 'x)', files, page_errors: errors }, null, 2));
  console.log(JSON.stringify({ files, errors }, null, 2));
} finally { await context.close(); await browser.close(); }
