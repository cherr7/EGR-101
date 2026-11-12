// Capture only the disposable local browser-test deployment, never the lab dataset.
import { chromium } from '../frontend/node_modules/playwright/index.mjs';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const origin = process.env.PREVIEW_URL || 'http://127.0.0.1:8121';
if (!/^http:\/\/(?:127\.0\.0\.1|localhost):8121$/.test(origin)) {
  throw new Error('Captures require the isolated local browser-test API on port 8121.');
}
const folder = resolve('output/screenshots');
await mkdir(folder, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: 'chrome' });
const context = await browser.newContext({ baseURL: origin, viewport: { width: 1440, height: 1000 } });
const request = context.request;
async function json(response) {
  if (!response.ok()) throw new Error(await response.text());
  return response.json();
}
const sheetBody = rows => ({  // the prep-sheet draft layout: one worksheet per project
  headers: Object.fromEntries(rows.map(r => [r.project_number, { project_number: r.project_number }])),
  rows: rows.map(r => ({ sheet: r.project_number, test: r.test_code, conditions: r.preconditioning_code, conditioning: String(r.specimen_count), sample: r.material_ref })),
});
async function requestId(request, client = 'shared-door') {  // '<station>-<n>', reserved like the kiosk does
  const block = await (await request.post('/api/request-ids', { data: { client, count: 1 } })).json();
  return `${client}-${block.first}`;
}
const project = 'DEMO-UI-' + Date.now().toString(36).slice(-7);
const rows = [
  { project_number: project, material_ref: 'DEMO Outer shell A', test_code: 'Flame resistance', preconditioning_code: 'AR', specimen_count: 20 },
  { project_number: project, material_ref: 'DEMO Outer shell A', test_code: 'Flame resistance', preconditioning_code: '5L', specimen_count: 10 },
];
const files = [];
try {
  const draft = await json(await request.post('/api/prep-drafts', { data: sheetBody(rows) }));
  const registered = await json(await request.post('/api/prep-drafts/' + draft.id + '/commit', { data: {} }));
  const first = await json(await request.get('/api/stacks/' + registered.stack_ids[0]));
  const second = await json(await request.get('/api/stacks/' + registered.stack_ids[1]));
  const base = { station_id: 'shared-door', code: first.specimens[0].barcode_value, scope: 'stack' };
  const entered = await json(await request.post('/api/movements', { data: { ...base, request_id: await requestId(request), direction: 'IN' } }));
  await json(await request.patch('/api/transactions/' + entered.event_id, {
    data: { occurred_at: new Date(Date.now() - 25 * 3600000).toISOString(), expected_version: entered.version },
  }));
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  async function capture(name) {
    await page.screenshot({ path: resolve(folder, name), animations: 'disabled' });
    files.push(name);
  }
  await page.goto('/#overview');
  await page.getByText('Database connected', { exact: true }).waitFor();
  await page.getByLabel('Search stacks').fill(project);
  await page.locator('.overview-table tbody tr').first().waitFor();
  await capture('staff-overview.png');
  await page.goto('/#records');
  await page.getByLabel('Search records').fill(project);
  await page.getByLabel('Select all visible specimen labels').waitFor();
  await page.getByRole('checkbox', { name: /^Select label / }).nth(0).check();
  await page.getByRole('checkbox', { name: /^Select label / }).nth(1).check();
  await capture('staff-specimens.png');
  await page.getByRole('button', { name: 'Preview / reprint selected' }).click();
  await page.locator('iframe[title="Actual label PDF preview"]').waitFor();
  await page.waitForTimeout(1000); // Allow Chrome's built-in PDF viewer to paint before a visual capture.
  await capture('staff-labels.png');
  await page.getByRole('dialog').getByRole('button', { name: 'Close', exact: true }).click();
  const removed = await json(await request.post('/api/movements', { data: { ...base, request_id: await requestId(request), direction: 'OUT' } }));
  await page.goto('/#transactions?stack=' + first.id + '&event=' + removed.event_id);
  const editor = page.getByRole('dialog', { name: 'Edit OUT transaction' });
  await editor.waitFor();
  for (let number = 4; number <= 20; number++) {
    await editor.getByRole('checkbox', { name: new RegExp('^' + String(number).padStart(2, '0')) }).uncheck();
  }
  await capture('staff-correction.png');
  await page.goto('/#prep');
  await page.getByRole('button', { name: '＋ New prep sheet' }).click();
  await page.getByLabel('Oracle project').fill(rows[0].project_number);
  await page.getByLabel('Test, row 1').fill(rows[0].test_code);
  await page.getByLabel('Conditions, row 1').fill(rows[0].preconditioning_code);
  await page.getByLabel('Specimens per sample for Conditioning, row 1').fill('20');
  await page.getByLabel('Sample Number, row 1').fill(rows[0].material_ref);
  await capture('staff-preparation.png');

  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/#kiosk');
  await page.getByText('Online', { exact: true }).waitFor();
  await page.bringToFront();
  await page.getByRole('button', { name: 'Shared', exact: true }).click();
  await page.getByRole('button', { name: 'IN', exact: true }).click();
  await capture('scanner-ready.png');
  await page.keyboard.type(second.specimens[0].barcode_value);
  await page.keyboard.press('Enter');
  await page.getByRole('heading', { name: 'IN confirmed', exact: true }).waitFor();
  await capture('scanner-confirmed.png');
  await page.getByRole('button', { name: 'OUT', exact: true }).click();
  await page.getByRole('button', { name: 'Individual' }).click();
  await page.keyboard.type(second.specimens[0].barcode_value);
  await page.keyboard.press('Enter');
  await page.getByRole('dialog', { name: 'Choose the specimens moving' }).waitFor();
  await capture('scanner-partial.png');
  if (errors.length) throw new Error(errors.join('\n'));
  await writeFile(resolve(folder, 'manifest.json'), JSON.stringify({
    captured_at: new Date().toISOString(), source: origin, data: 'Isolated synthetic DEMO records', project, files,
  }, null, 2));
  console.log(JSON.stringify({ files, folder, page_errors: errors.length }, null, 2));
} finally {
  await context.close();
  await browser.close();
}
