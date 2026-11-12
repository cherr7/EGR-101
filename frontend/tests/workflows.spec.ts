import { test, expect } from '@playwright/test'
import { tag, sheet, reserveId } from './helpers'
import type { APIRequestContext, Page } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

async function createStack(request: APIRequestContext, count = 10) {
  const project = `00-DEMO-E2E-${tag()}`
  const response = await request.post('/api/prep-drafts', { data: sheet(project, `DEMO fabric ${project}`, 'D5034', 'AR → 5L', count) }); expect(response.ok(), await response.text()).toBeTruthy(); const draft = await response.json()
  const registered = await request.post(`/api/prep-drafts/${draft.id}/commit`, { data: {} }); expect(registered.ok(), await registered.text()).toBeTruthy(); const value = await registered.json(); return { ...(await (await request.get(`/api/stacks/${value.stack_ids[0]}`)).json()), draft_id: draft.id }
}
async function openKiosk(page: Page) { await page.goto('/#kiosk'); await expect(page.getByText('Online', { exact: true })).toBeVisible(); await expect.poll(() => page.evaluate(() => (window as unknown as { fabricScannerCaptureReady: boolean }).fabricScannerCaptureReady)).toBe(true) }
async function scan(page: Page, value: string) { await page.keyboard.type(value); await page.keyboard.press('Enter') }
async function current(request: APIRequestContext, id: string) { return (await request.get(`/api/stacks/${id}`)).json() }
function eastern(value: Date) { return new Intl.DateTimeFormat('sv-SE', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(value).replace(' ', 'T').replace(/:00$/, '') }   // Chrome stores 10:30:00 as 10:30

test('Excel import shows the prep sheet as printed and keeps the same labels on reprint', async ({ page, request }) => {
  const previews: { selected_specimen_ids: number[] }[] = []
  page.on('request', value => { if (value.method() === 'POST' && value.url().endsWith('/api/labels/preview.png')) previews.push(value.postDataJSON()) })
  await page.goto('/#batch/new'); await expect(page.getByText('Online')).toBeVisible()
  await page.getByLabel('Import prep workbook').setInputFiles({ name: 'DEMO-prep.xlsx', mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buffer: Buffer.concat([await readFile(fileURLToPath(new URL('./fixtures/DEMO-prep.xlsx', import.meta.url))), Buffer.from(tag())]) })
  // Same columns in the same order as the sheet; one row per sheet row; header values once.
  await expect(page.locator('.prep-sheet-table thead th')).toHaveText(['Test', 'Total Number of Specimens to be Tested', 'Conditions',
    'Specimens per sample for Conditioning', 'Sample Number', 'Sample Type', 'Row tools'])
  await expect(page.locator('.prep-sheet-table tbody tr')).toHaveCount(2)
  await expect(page.getByLabel('Oracle project')).toHaveValue('00073-LAB'); await expect(page.getByLabel('LabWare #')).toHaveValue('LW-0009')
  await expect(page.getByLabel('Test, row 1')).toHaveValue('Flame'); await expect(page.getByLabel('Test, row 2')).toHaveValue('')
  await expect(page.getByLabel('Specimens per sample for Conditioning, row 1')).toHaveValue('6 Warp, 6 Fill')
  await expect(page.locator('tbody tr').first()).toContainText('12 pieces')
  await expect(page.getByRole('button', { name: /^Register \d+ stacks?$/ })).toBeDisabled()
  await page.getByRole('button', { name: 'Source, row 1' }).click(); const source = page.getByRole('dialog', { name: 'Source' }); await expect(source).toContainText('D5'); await expect(source).toContainText('6 Warp, 6 Fill'); await source.getByRole('button', { name: 'Close', exact: true }).click()
  const project = `0007-DEMO-XLSX-${tag()}`
  await page.getByLabel('Oracle project').fill(project)   // typed once for the whole sheet
  await page.getByLabel('Sample Number, row 1').fill('DEMO-S1'); await page.getByLabel('Sample Number, row 2').fill('DEMO-S2')
  await expect(page.locator('.summary-count')).toHaveText('2 sheet rows · 2 stacks · 20 pieces')
  await page.getByRole('button', { name: 'Register 2 stacks' }).click(); await expect(page.getByRole('heading', { name: 'Print labels', exact: true })).toBeVisible()
  const labels = page.locator('.label-preview-image img')
  await expect(labels).toHaveAttribute('src', /^blob:/)
  const before = previews.at(-1)!
  expect(before.selected_specimen_ids).toHaveLength(20)
  await page.getByRole('button', { name: 'Print later' }).click()
  await expect(page.getByRole('heading', { name: /Track this batch/ })).toBeVisible()
  await expect(page.getByText('20 pieces still need labels.')).toBeVisible()
  await page.reload()
  await page.getByRole('link', { name: 'Print labels' }).click()  // a permanent reprint keeps the same specimen IDs
  await expect(labels).toHaveAttribute('src', /^blob:/)
  expect(previews.at(-1)!.selected_specimen_ids).toEqual(before.selected_specimen_ids)
  const stacks = (await (await request.get(`/api/stacks?project_number=${project}`)).json()).items
  expect(stacks.map((s: { material_ref: string; test_code: string; preconditioning_code: string; specimen_count: number }) => [s.material_ref, s.test_code, s.preconditioning_code, s.specimen_count]).sort())
    .toEqual([['DEMO-S1', 'Flame', 'AR', 12], ['DEMO-S2', 'Flame', '5L->CH', 8]])
})

test('automatic decimal HID scanning works after touching controls, repeats preserve time, Undo is entire last transaction', async ({ page, request }, info) => {
  const stack = await createStack(request, 20); await openKiosk(page); await expect(page.locator('input')).toHaveCount(0)
  await page.getByRole('button', { name: 'IN', exact: true }).click(); await page.keyboard.type(stack.specimens[0].barcode_value); const start = performance.now(); await page.keyboard.press('Enter'); await expect(page.getByRole('heading', { name: 'IN confirmed', exact: true })).toBeVisible(); await expect(page.locator('.readiness-description')).toContainText('20 specimens')
  const milliseconds = Math.round(performance.now() - start); await info.attach('confirmed-scan-latency', { body: JSON.stringify({ milliseconds, target_ms: 1000, specimens: 20 }), contentType: 'application/json' }); console.info(`Confirmed 20-specimen scan, Enter to green: ${milliseconds} ms`); expect(milliseconds).toBeLessThan(1000)
  const entered = await current(request, stack.id); await scan(page, stack.specimens[7].barcode_value); await expect(page.getByRole('heading', { name: 'Already recorded' })).toBeVisible(); const repeated = await current(request, stack.id); expect(repeated.specimens.map((s: { ENTRY: string }) => s.ENTRY)).toEqual(entered.specimens.map((s: { ENTRY: string }) => s.ENTRY)); expect(repeated.specimens.every((s: { sessions: unknown[] }) => s.sessions.length === 1)).toBeTruthy()
  await page.getByRole('button', { name: 'Undo' }).focus(); await page.keyboard.press('Enter'); expect((await current(request, stack.id)).inside_count).toBe(20)
  await page.getByRole('button', { name: 'Undo' }).click(); await expect(page.getByRole('dialog')).toHaveCount(0); await expect(page.getByRole('heading', { name: 'Transaction undone', exact: true })).toBeVisible(); const undone = await current(request, stack.id); expect(undone.inside_count).toBe(0); expect(undone.specimens.every((s: { ENTRY: string | null }) => s.ENTRY === null)).toBeTruthy(); expect(undone.events.some((e: { kind: string }) => e.kind === 'undo')).toBeTruthy()
})

test('numbered partial checkout, popup scan rejection, automatic remaining-stack checkout and one conditioning period', async ({ page, request }) => {
  const stack = await createStack(request); await openKiosk(page); await scan(page, stack.specimens[0].barcode_value); await expect(page.getByRole('heading', { name: 'IN confirmed', exact: true })).toBeVisible(); await page.getByRole('button', { name: 'OUT', exact: true }).click(); await page.getByRole('button', { name: 'Individual' }).click(); await scan(page, stack.specimens[0].barcode_value)
  const select = page.getByRole('dialog', { name: 'Choose pieces' }); await expect(select).toBeVisible(); await expect(select.getByRole('checkbox', { name: /^01/ })).toBeChecked(); await select.getByRole('checkbox', { name: /^03/ }).check(); await select.getByRole('checkbox', { name: /^05/ }).check(); await scan(page, stack.specimens[0].barcode_value); expect((await current(request, stack.id)).inside_count).toBe(10)
  await select.getByRole('button', { name: 'Record 3 OUT' }).click(); const early = page.getByRole('dialog', { name: 'Early checkout' }); await expect(early).toBeVisible(); await page.keyboard.press('Enter'); expect((await current(request, stack.id)).inside_count).toBe(10); await early.getByRole('button', { name: 'Take out anyway' }).click(); await expect(page.getByRole('heading', { name: 'OUT confirmed', exact: true })).toBeVisible(); await expect(page.locator('.readiness-description')).toContainText('3 specimens'); const partial = await current(request, stack.id); expect(partial.specimens.filter((s: { EXIT: string | null }) => !!s.EXIT).map((s: { number: number }) => s.number)).toEqual([1, 3, 5]); await expect(page.getByRole('button', { name: 'Individual' })).toHaveAttribute('aria-pressed', 'true'); await page.getByRole('button', { name: 'Stack', exact: true }).click()
  await scan(page, stack.specimens[0].barcode_value); await expect(early).toBeVisible(); await expect(page.getByRole('dialog', { name: 'Choose pieces' })).toHaveCount(0); await early.getByRole('button', { name: 'Take out anyway' }).click(); await expect(page.locator('.readiness-description')).toContainText('7 specimens'); expect((await current(request, stack.id)).finished_count).toBe(10)
  await page.getByRole('button', { name: 'IN', exact: true }).click(); await scan(page, stack.specimens[0].barcode_value); await expect(page.getByRole('heading', { name: 'Already recorded' })).toBeVisible(); expect((await current(request, stack.id)).specimens.every((s: { sessions: unknown[] }) => s.sessions.length === 1)).toBeTruthy()
})

test('lost committed response survives reload and reconciles the unchanged request exactly once', async ({ page, request }) => {
  const stack = await createStack(request, 3); let lost = false; await page.route('**/api/movements', async route => { if (lost) return route.continue(); lost = true; const response = await route.fetch(); expect(response.ok()).toBeTruthy(); await route.abort('failed') })
  await openKiosk(page); await scan(page, stack.specimens[0].barcode_value); await expect(page.getByRole('heading', { name: 'Confirming scan…' })).toBeVisible(); const saved = await page.evaluate(() => localStorage.getItem('fabric-tracker.pending-movement.v3')); expect(saved).not.toBeNull(); await page.reload(); await expect(page.getByRole('heading', { name: 'IN confirmed', exact: true })).toBeVisible(); const result = await current(request, stack.id); expect(result.events.filter((e: { kind: string }) => e.kind === 'movement')).toHaveLength(1); expect(await page.evaluate(() => localStorage.getItem('fabric-tracker.pending-movement.v3'))).toBeNull()
})

test('lost Undo response stays pending across reload and reconciles the same original transaction once', async ({ page, request }) => {
  const stack = await createStack(request, 6), other = await createStack(request, 2)
  await openKiosk(page); await scan(page, stack.specimens[0].barcode_value); await expect(page.getByRole('heading', { name: 'IN confirmed', exact: true })).toBeVisible()
  let undoPosts = 0, holdReconciliation = false
  await page.route('**/api/transactions/*/undo', async route => { undoPosts++; const response = await route.fetch(); expect(response.ok()).toBeTruthy(); holdReconciliation = true; await route.abort('failed') })
  await page.route(/\/api\/transactions\/[0-9a-f-]+$/, async route => { if (holdReconciliation) await route.abort('failed'); else await route.continue() })
  await page.getByRole('button', { name: 'Undo' }).click(); await expect(page.getByRole('heading', { name: 'Confirming undo…' })).toBeVisible()
  await expect.poll(() => holdReconciliation).toBe(true); const intent = await page.evaluate(() => localStorage.getItem('fabric-tracker.pending-undo.v2')); expect(intent).not.toBeNull(); await scan(page, other.specimens[0].barcode_value); expect((await current(request, other.id)).inside_count).toBe(0)
  holdReconciliation = false; await page.reload(); await expect(page.getByRole('heading', { name: 'Transaction undone', exact: true })).toBeVisible(); expect(await page.evaluate(() => localStorage.getItem('fabric-tracker.pending-undo.v2'))).toBeNull()
  const final = await current(request, stack.id); expect(final.inside_count).toBe(0); expect(final.events.filter((e: { kind: string }) => e.kind === 'undo')).toHaveLength(1); expect(undoPosts).toBe(1); await expect(page.locator('.kiosk-message[role="alert"]')).toHaveCount(0)
})

test('scanner rejects incomplete and malformed frames and Enter or Space cannot activate an override', async ({ page, request }) => {
  const stack = await createStack(request, 2); await openKiosk(page)
  await page.keyboard.type(stack.specimens[0].barcode_value.slice(0, 15)); await page.waitForTimeout(410); await page.keyboard.type(stack.specimens[0].barcode_value.slice(15)); await page.keyboard.press('Enter'); expect((await current(request, stack.id)).inside_count).toBe(0)
  await scan(page, `!${stack.specimens[0].barcode_value}`); expect((await current(request, stack.id)).inside_count).toBe(0)
  await scan(page, stack.specimens[0].barcode_value); await expect(page.getByRole('heading', { name: 'IN confirmed', exact: true })).toBeVisible(); await page.getByRole('button', { name: 'OUT', exact: true }).click(); await scan(page, stack.specimens[0].barcode_value); const early = page.getByRole('dialog', { name: 'Early checkout' }); await expect(early).toBeVisible(); await early.getByRole('button', { name: 'Take out anyway' }).focus(); await page.keyboard.press('Enter'); await page.keyboard.press('Space'); await expect(early).toBeVisible(); expect((await current(request, stack.id)).inside_count).toBe(2)
})

test('1280 by 720 scanner keeps readiness and dock fixed while only recent activity scrolls', async ({ page, request }, info) => {
  await page.setViewportSize({ width: 1280, height: 720 }); const stack = await createStack(request, 20); await openKiosk(page)
  const before = { dock: await page.locator('.kiosk-dock').boundingBox(), ready: await page.locator('.readiness-area').boundingBox() }; await page.locator('.recent-scroll').evaluate(el => { el.scrollTop = el.scrollHeight }); expect(await page.locator('.kiosk-dock').boundingBox()).toEqual(before.dock); expect(await page.locator('.readiness-area').boundingBox()).toEqual(before.ready); expect(await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight)).toBeTruthy()
  await scan(page, stack.specimens[0].barcode_value); await expect(page.getByRole('heading', { name: 'IN confirmed', exact: true })).toBeVisible(); await page.screenshot({ animations: 'disabled', path: info.outputPath('scanner-confirmed-1280x720.png') }); await page.getByRole('button', { name: 'OUT', exact: true }).click(); await page.getByRole('button', { name: 'Individual' }).click(); await scan(page, stack.specimens[0].barcode_value); const popup = page.getByRole('dialog', { name: 'Choose pieces' }); await expect(popup).toBeVisible(); const actions = await popup.locator('.modal-actions').boundingBox(); expect(actions!.y + actions!.height).toBeLessThanOrEqual(720); expect(await popup.evaluate(el => el.scrollHeight <= el.clientHeight)).toBeTruthy(); await page.screenshot({ animations: 'disabled', path: info.outputPath('scanner-partial-1280x720.png') })
})

test('offline state is red, submits nothing, then recovers with page-wide capture', async ({ page, request }) => {
  const stack = await createStack(request, 2); await openKiosk(page); await page.route('**/api/health', route => route.abort('failed')); await expect(page.getByRole('heading', { name: 'Offline', exact: true })).toBeVisible(); await expect(page.locator('.readiness-area')).toHaveClass(/unavailable/); await scan(page, stack.specimens[0].barcode_value); expect((await current(request, stack.id)).inside_count).toBe(0); await expect(page.locator('.readiness-area.confirmed')).toHaveCount(0); await page.unroute('**/api/health'); await expect(page.getByText('Online', { exact: true })).toBeVisible(); await scan(page, stack.specimens[0].barcode_value); await expect(page.getByRole('heading', { name: 'IN confirmed', exact: true })).toBeVisible()
})

test('popup transaction editor reduces an OUT from ten to three and preserves originals without permissions', async ({ page, request }) => {
  const stack = await createStack(request); const base = { station_id: 'shared-door', code: stack.specimens[0].barcode_value, scope: 'stack' }; const entered = await (await request.post('/api/movements', { data: { ...base, request_id: await reserveId(request), direction: 'IN' } })).json(); const out = await (await request.post('/api/movements', { data: { ...base, request_id: await reserveId(request), direction: 'OUT', acknowledge_early: true, expected_version: entered.version } })).json()
  await page.goto('/#transactions'); await expect(page.getByText('Online')).toBeVisible(); await page.getByLabel('Search transactions').fill(stack.project_number); await page.getByRole('button', { name: `Edit transaction ${out.event_id}` }).click(); const editor = page.getByRole('dialog', { name: 'Edit OUT transaction' }); await expect(editor).toBeVisible(); for (let n = 4; n <= 10; n++) await editor.getByRole('checkbox', { name: new RegExp(`^${String(n).padStart(2, '0')}`) }).uncheck(); await editor.getByRole('button', { name: 'Save change', exact: true }).click(); await expect(editor).toBeHidden(); await expect(page.getByText('Saved. The original stays in the history.')).toBeVisible(); const changed = await current(request, stack.id); expect(changed.inside_count).toBe(7); expect(changed.finished_count).toBe(3); const original = changed.events.find((e: { id: string }) => e.id === out.event_id); expect(original.affected_count).toBe(10); expect(original.effective_count).toBe(3); expect(original.modified).toBe(true); expect(changed.events.some((e: { kind: string }) => e.kind === 'modification')).toBeTruthy()
  await page.getByRole('button', { name: `Edit transaction ${out.event_id}` }).click(); await editor.getByRole('button', { name: 'Undo entire transaction' }).click(); const undo = page.getByRole('dialog', { name: 'Undo the entire transaction?' }); await undo.getByRole('button', { name: 'Undo transaction', exact: true }).click(); await expect(undo).toBeHidden(); expect((await current(request, stack.id)).inside_count).toBe(10)
})

test('missed-entry time is distinct from recording time and the dashboard surfaces the ready specimens', async ({ page, request }) => {
  const stack = await createStack(request, 4); await page.goto('/#transactions'); await expect(page.getByText('Online')).toBeVisible(); await page.getByRole('button', { name: 'Add missed scan' }).click(); const dialog = page.getByRole('dialog', { name: 'Record a missed scan' }); await dialog.getByLabel('Missed stack').selectOption(String(stack.id)); await dialog.getByLabel('Missed movement time').fill(eastern(new Date(Date.now() - 26 * 3600000))); await dialog.getByRole('checkbox', { name: /^01/ }).check(); await dialog.getByRole('checkbox', { name: /^02/ }).check(); await dialog.getByRole('button', { name: 'Record missed IN' }).click(); await expect(dialog).toBeHidden(); const result = await current(request, stack.id); expect(result.ready_count).toBe(2); expect(Date.parse(result.specimens[0].sessions[0].recorded_at)).toBeGreaterThan(Date.parse(result.specimens[0].ENTRY)); await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: 'Dashboard', exact: true }).click(); await page.getByLabel('Search dashboard').fill(stack.project_number); const room = page.getByTestId('ready-shared'); await expect(room).toContainText('1 stack · 2 pieces ready'); await expect(room).toContainText('oldest waiting 2 h'); await room.locator('summary').click(); await expect(room.getByText('2 of 2 inside')).toBeVisible(); await expect(page.getByTestId('needs-labels-' + stack.id)).toContainText('2 of 4'); await room.getByRole('button', { name: 'Details' }).click(); await expect(page.getByRole('dialog', { name: 'Stack' })).toContainText('Ready')
})

test('server readiness and elapsed time remain correct when a staff computer clock is two days ahead', async ({ page, request }) => {
  const stack = await createStack(request, 2); const entered = await request.post('/api/movements', { data: { request_id: await reserveId(request, 'shared-door'), station_id: 'shared-door', code: stack.specimens[0].barcode_value, direction: 'IN', scope: 'stack' } }); expect(entered.ok()).toBeTruthy()
  await page.addInitScript(() => { const realNow = Date.now.bind(Date); Date.now = () => realNow() + 48 * 3600000 })
  const waiting = await createStack(request, 1)
  await page.goto('/#dashboard?q=' + stack.project_number); await expect(page.getByText('Online')).toBeVisible()
  await expect(page.getByTestId('ready-shared')).toHaveCount(0); await expect(page.locator('.ready-room.empty').filter({ hasText: 'Shared' })).toContainText('nothing ready')
  await expect(page.getByTestId('batch-' + stack.draft_id)).toBeVisible()
  await page.goto('/#dashboard?q=' + waiting.project_number); await expect(page.getByTestId('needs-labels-' + waiting.id).locator('td').nth(2)).toContainText(/^[0-9] min ago/)
})

test('a locally packaged Pi names its default room even when the Mac is unreachable after cold start', async ({ page }) => {
  await page.addInitScript(() => { (window as unknown as { fabricStationConfig: unknown }).fabricStationConfig = { stationId: 'shared-door', stationName: 'Shared conditioning room door', roomName: 'Shared conditioning room', fixed: true } })
  await page.route('**/api/health', route => route.abort('failed')); await page.goto('/#kiosk'); await expect(page.getByRole('heading', { name: 'Offline', exact: true })).toBeVisible(); await expect(page.getByTestId('current-room')).toContainText('Shared'); await page.getByRole('button', { name: 'Station settings' }).click(); await expect(page.getByRole('group', { name: 'Conditioning room' }).getByRole('button')).toHaveCount(3); await expect(page.getByRole('button', { name: 'Shared', exact: true })).toHaveAttribute('aria-pressed', 'true'); await page.getByRole('button', { name: 'Done', exact: true }).click(); expect(await page.evaluate(() => (window as unknown as { fabricScannerCaptureReady: boolean }).fabricScannerCaptureReady)).toBe(true)
})

test('portable backup is created, downloaded and validated before restore becomes available', async ({ page, request }) => {
  await createStack(request, 2); await page.goto('/#backups'); await expect(page.getByText('Online')).toBeVisible(); await page.getByRole('button', { name: 'Back up now' }).click(); await expect(page.getByText('Backup created.', { exact: true })).toBeVisible(); const all = await (await request.get('/api/backups')).json(); expect(all.items.length).toBeGreaterThan(0); const backup = all.items[0]; const download = await request.get(`/api/backups/${backup.id}/download`); expect(download.ok()).toBeTruthy(); await page.getByLabel('Import backup archive').setInputFiles({ name: backup.filename, mimeType: 'application/zip', buffer: await download.body() }); const preview = page.getByRole('dialog', { name: 'Restore backup?' }); await expect(preview).toBeVisible(); await expect(preview).toContainText('Backup file is valid'); await expect(preview.getByRole('button', { name: 'Restore backup', exact: true })).toBeDisabled(); await preview.getByLabel('Restore confirmation').fill('RESTORE'); await expect(preview.getByRole('button', { name: 'Restore backup', exact: true })).toBeEnabled(); await preview.getByRole('button', { name: 'Cancel', exact: true }).click(); await expect(preview).toBeHidden()
})


test('room chosen in Station settings persists configuration and recent activity belongs to the selected room', async ({ page, request }) => {
  const shared = await createStack(request, 2), ppe = await createStack(request, 3)
  await request.post('/api/movements', { data: { request_id: await reserveId(request, 'shared-door'), station_id: 'shared-door', code: shared.specimens[0].barcode_value, direction: 'IN', scope: 'stack' } })
  await openKiosk(page)
  await page.getByRole('button', { name: 'Station settings' }).click()
  await page.getByRole('button', { name: 'PPE', exact: true }).click()
  await expect(page.getByRole('button', { name: 'PPE', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('button', { name: 'Done', exact: true }).click()
  await expect(page.getByTestId('current-room')).toContainText('PPE')
  await expect(page.locator('.recent-scroll')).not.toContainText(shared.project_number)
  await scan(page, ppe.specimens[1].barcode_value)
  await expect(page.getByRole('heading', { name: 'IN confirmed', exact: true })).toBeVisible()
  expect((await current(request, ppe.id)).specimens.every((s: { room_id: string }) => s.room_id === 'ppe')).toBe(true)
  await expect(page.locator('.recent-scroll')).toContainText(ppe.project_number)
  await page.reload()
  await expect(page.getByTestId('current-room')).toContainText('PPE')
  await expect(page.locator('.recent-scroll')).not.toContainText(shared.project_number)
  await expect(page.locator('.recent-scroll')).toContainText(ppe.project_number)
})

test('success remains green for twelve seconds without preventing the next scan', async ({ page, request }) => {
  const first = await createStack(request, 2), second = await createStack(request, 3)
  await page.clock.install()
  await openKiosk(page); await scan(page, first.specimens[0].barcode_value)
  await expect(page.getByRole('heading', { name: 'IN confirmed', exact: true })).toBeVisible()
  await page.clock.fastForward(10000)
  await expect(page.getByRole('heading', { name: 'IN confirmed', exact: true })).toBeVisible()
  await scan(page, second.specimens[0].barcode_value)
  await expect(page.locator('.readiness-description')).toContainText('3 specimens')
  expect((await current(request, second.id)).inside_count).toBe(3)
  await page.clock.fastForward(12001)
  await expect(page.getByRole('heading', { name: 'Ready for IN', exact: true })).toBeVisible()
  await expect(page.locator('.recent-row').first()).toContainText('3 specimens')
})
