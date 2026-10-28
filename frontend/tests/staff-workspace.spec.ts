import { test, expect } from '@playwright/test'
import type { APIRequestContext } from '@playwright/test'
import { randomUUID } from 'node:crypto'

async function stack(request: APIRequestContext, count = 5, project = 'DEMO-AUDIT-' + randomUUID().slice(0, 8), material = 'DEMO reviewed fabric') {
  const draft = await (await request.post('/api/prep-drafts', { data: { rows: [{
    project_number: project, material_ref: material, test_code: 'D5034', preconditioning_code: 'AR', specimen_count: count,
  }] } })).json()
  const commit = await request.post('/api/prep-drafts/' + draft.id + '/commit', { data: {} })
  expect(commit.ok()).toBe(true)
  return (await request.get('/api/stacks/' + (await commit.json()).stack_ids[0])).json()
}
async function move(request: APIRequestContext, value: { specimens: { id: string }[] }, direction = 'IN', station = 'shared-door') {
  const current = await (await request.post('/api/lookup', { data: { code: value.specimens[0].barcode_value } })).json()
  const response = await request.post('/api/movements', { data: {
    request_id: randomUUID(), station_id: station, code: value.specimens[0].barcode_value, direction, scope: 'stack', acknowledge_early: true, expected_version: current.version,
  } })
  expect(response.ok()).toBe(true)
  const result = await response.json()
  expect(result.status, result.message).toBe('committed')
  return result
}

test('prep edits survive reload and navigation without an extra Save step', async ({ page }) => {
  await page.goto('/#prep')
  await expect(page.getByText('Database connected', { exact: true })).toBeVisible()
  await page.getByLabel('project_number row 1').fill('00073-DEMO-REVIEW')
  await page.getByLabel('material_ref row 1').fill('DEMO material edit to preserve')
  await page.getByLabel('specimen_count row 1').fill('17')
  await page.reload()
  await expect(page.getByLabel('material_ref row 1')).toHaveValue('DEMO material edit to preserve')
  await page.getByRole('tab', { name: 'Registered specimens', exact: true }).click()
  await page.getByRole('tab', { name: 'Prepare / import', exact: true }).click()
  await expect(page.getByLabel('project_number row 1')).toHaveValue('00073-DEMO-REVIEW')
  await expect(page.getByLabel('specimen_count row 1')).toHaveValue('17')
})

test('registration recovers a saved prep review after its PUT response is lost', async ({ page, request }) => {
  const original = { project_number: 'DEMO-LOST-REVIEW-' + randomUUID().slice(0, 8), material_ref: 'DEMO original',
    test_code: 'D5034', preconditioning_code: 'AR', specimen_count: 5 }
  const draftResponse = await request.post('/api/prep-drafts', { data: { rows: [original] } })
  expect(draftResponse.ok()).toBe(true)
  const draft = await draftResponse.json()
  await page.goto('/#overview')
  await page.evaluate(value => localStorage.setItem('fabric-tracker.prep-review.v3', JSON.stringify({ draft: value, rows: value.rows })), draft)
  let lost = false
  await page.route('**/api/prep-drafts/' + draft.id, async route => {
    if (route.request().method() === 'PUT' && !lost) {
      lost = true
      const response = await route.fetch()
      expect(response.ok()).toBe(true)
      await route.abort('failed')
    } else await route.continue()
  })
  await page.goto('/#prep')
  await page.getByLabel('material_ref row 1').fill('DEMO reviewed and saved')
  await page.getByRole('button', { name: 'Register & preview labels →', exact: true }).click()
  await expect(page.getByRole('alert')).toBeVisible()
  expect(lost).toBe(true)
  await page.reload()
  await expect(page.getByLabel('material_ref row 1')).toHaveValue('DEMO reviewed and saved')
  await page.getByRole('button', { name: 'Register & preview labels →', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Preview & print labels' })).toBeVisible()
  const registered = await (await request.get('/api/prep-drafts/' + draft.id)).json()
  expect(registered.committed).toBe(true)
  expect(registered.stack_ids).toHaveLength(1)
  const result = await (await request.get('/api/stacks/' + registered.stack_ids[0])).json()
  expect(result.material_ref).toBe('DEMO reviewed and saved')
  expect(result.specimens).toHaveLength(5)
})

test('correction opens fresh values and rejects a second concurrent edit', async ({ page, request }) => {
  const value = await stack(request, 10)
  await move(request, value)
  const out = await move(request, value, 'OUT')
  await page.goto('/#transactions')
  await page.getByLabel('Search transactions').fill(value.project_number)
  const edit = page.getByRole('button', { name: 'Edit transaction ' + out.event_id })
  await expect(edit).toBeVisible()
  await request.patch('/api/transactions/' + out.event_id, { data: {
    specimen_ids: value.specimens.slice(0, 3).map((s: { id: string }) => s.id), note: 'Other staff saved this first',
  } })
  await edit.click()
  const dialog = page.getByRole('dialog', { name: 'Edit OUT transaction' })
  await expect(dialog.locator('input[type=checkbox]:checked')).toHaveCount(3)
  await expect(dialog.getByLabel('Transaction notes')).toHaveValue('Other staff saved this first')
  await expect(dialog.getByLabel('Correction result preview')).toContainText('3 specimens')
  await request.patch('/api/transactions/' + out.event_id, { data: {
    specimen_ids: value.specimens.slice(0, 2).map((s: { id: string }) => s.id),
  } })
  await dialog.getByRole('button', { name: 'Save change', exact: true }).click()
  await expect(dialog.getByRole('alert')).toBeVisible()
  const result = await (await request.get('/api/stacks/' + value.id)).json()
  expect(result.finished_count).toBe(2)
  await dialog.getByRole('button', { name: 'Reload latest values' }).click()
  await expect(dialog.locator('input[type=checkbox]:checked')).toHaveCount(2)
})

test('replacement preview and lost print retry keep the exact specimen and request ID', async ({ page, request }) => {
  const value = await stack(request)
  const previews: { selected_specimen_ids: string[] }[] = []
  const printJobs: { request_id: string; selected_specimen_ids: string[] }[] = []
  page.on('request', event => {
    if (event.method() === 'POST' && event.url().endsWith('/api/labels/preview')) previews.push(event.postDataJSON())
  })
  await page.route('**/api/printers', route => route.fulfill({ json: {
    available: true, enabled: true, configured_queue: 'TEST_QUEUE', message: 'Simulated printer, no physical jobs',
    printers: [{ name: 'TEST_QUEUE', is_default: true }],
  } }))
  // All printer submissions are intercepted: this test cannot reach CUPS.
  await page.route('**/api/print-jobs/*', route => route.fulfill({ status: 404, json: { detail: 'Not yet claimed' } }))
  await page.route('**/api/print-jobs', async route => {
    const body = route.request().postDataJSON()
    printJobs.push(body)
    if (printJobs.length === 1) await route.abort('failed')
    else await route.fulfill({ json: { request_id: body.request_id, status: 'submitted' } })
  })
  await page.goto('/#records')
  await page.getByLabel('Search records').fill(value.project_number)
  await page.getByRole('checkbox', { name: 'Select label 3 ' + value.material_ref, exact: true }).check()
  await page.getByRole('button', { name: 'Preview / reprint selected' }).click()
  const dialog = page.getByRole('dialog', { name: 'Preview & print labels' })
  await expect(dialog.locator('iframe')).toHaveAttribute('src', /^blob:/)
  expect(previews.at(-1)?.selected_specimen_ids).toEqual([value.specimens[2].id])
  await expect(dialog.getByLabel('Label width', { exact: true })).toHaveCount(0)
  await expect(dialog.getByLabel('Label height', { exact: true })).toHaveCount(0)
  await dialog.getByRole('button', { name: 'Print 1 label', exact: true }).click()
  await expect(dialog.getByText('Sent to printer.', { exact: true })).toBeVisible()
  expect(printJobs).toHaveLength(2)
  expect(printJobs[0].request_id).toBe(printJobs[1].request_id)
  expect(printJobs[1].selected_specimen_ids).toEqual([value.specimens[2].id])
})

test('specimen history opens only that piece and offers its existing label', async ({ page, request }) => {
  const value = await stack(request)
  await move(request, value)
  await page.goto('/#records')
  await page.getByLabel('Search records').fill(value.project_number)
  await page.getByRole('button', { name: 'History specimen 3 ' + value.material_ref, exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Specimen 03 · history' })
  await expect(dialog).toContainText(value.specimens[2].barcode_value)
  await expect(dialog).toContainText('This specimen’s observations & changes')
  await dialog.getByRole('button', { name: 'Reprint this specimen’s label' }).click()
  await expect(page.getByTestId('label-scope')).toHaveText('1 selected')
})

test('overview counts and remaining time follow the selected room and project', async ({ page, request }) => {
  const project = 'DEMO-ROOM-' + randomUUID().slice(0, 8)
  const shared = await stack(request, 2, project, 'DEMO Shared pieces')
  const ppe = await stack(request, 3, project, 'DEMO PPE pieces')
  await move(request, shared)
  await move(request, ppe, 'IN', 'ppe-door')
  await page.goto('/#overview')
  await page.getByLabel('Search stacks').fill(project)
  const inside = page.locator('.metric').filter({ hasText: 'Inside specimens' }).locator('b')
  await expect(inside).toHaveText('5')
  await page.getByLabel('Overview room').selectOption('ppe')
  await expect(inside).toHaveText('3')
  await expect(page.locator('.overview-table')).toContainText('DEMO PPE pieces')
  await expect(page.locator('.overview-table')).not.toContainText('DEMO Shared pieces')
  await expect(page.locator('.overview-table')).toContainText('Ready in')
})

test('backup page surfaces overdue scheduling and failed copies', async ({ page }) => {
  await page.route('**/api/backups', route => route.fulfill({ json: {
    items: [{ id: 'a'.repeat(32), created_at: '2026-10-21T06:00:00Z', filename: 'DEMO-checkpoint.zip',
      size: 100, sha256: '0'.repeat(64), verified: true, cloud_copy_state: 'copy_failed' }],
    settings: { destination: '/TEST/synced-folder', retention_days: 30, daily_hour: 2, enabled: true },
    scheduler: { enabled: true, overdue: true, last_success_at: '2026-10-21T06:00:00Z', last_error: 'Simulated failure' },
  } }))
  await page.goto('/#backups')
  await expect(page.getByLabel('Backup health')).toContainText('Checkpoint overdue')
  await expect(page.getByLabel('Backup health')).toContainText('Latest copy failed')
  await expect(page.getByRole('alert')).toContainText('scheduled backup could not finish')
})

test('a committed restore with a lost response reconciles after reload without another replacement', async ({ page, request }) => {
  const backupResponse = await request.post('/api/backups', { data: {} })
  expect(backupResponse.ok()).toBe(true)
  const backup = await backupResponse.json()
  const download = await request.get('/api/backups/' + backup.id + '/download')
  let replacements = 0, holdStatus = true
  await page.route('**/api/backups/imports/*/restore', async route => {
    replacements++
    const response = await route.fetch()
    expect(response.ok()).toBe(true)
    await route.abort('failed')
  })
  await page.route(/\/api\/backups\/imports\/[0-9a-f]{32}$/, route =>
    holdStatus ? route.abort('failed') : route.continue())
  await page.goto('/#backups')
  await page.getByLabel('Import backup archive').setInputFiles({
    name: backup.filename, mimeType: 'application/zip', buffer: await download.body(),
  })
  const dialog = page.getByRole('dialog', { name: 'Restore validated backup?' })
  await dialog.getByLabel('Restore confirmation').fill('RESTORE')
  await dialog.getByRole('button', { name: 'Restore backup', exact: true }).click()
  await expect(page.getByRole('alert').filter({ hasText: /Restore result is not confirmed|still unconfirmed/ }).first()).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('fabric-tracker.pending-restore.v1'))).not.toBeNull()
  holdStatus = false
  await page.reload()
  await expect(page.getByText('Backup restored. Refresh the other pages to load the restored records.', { exact: true })).toBeVisible()
  expect(replacements).toBe(1)
  expect(await page.evaluate(() => localStorage.getItem('fabric-tracker.pending-restore.v1'))).toBeNull()
})
