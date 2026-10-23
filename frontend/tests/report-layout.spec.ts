import { test, expect } from '@playwright/test'
import { randomUUID } from 'node:crypto'

test('printable report preserves identity and readable before/after observations', async ({ page, request }, testInfo) => {
  const project = `PRINT-QA-${randomUUID().slice(0, 8)}`
  const draft = await (await request.post('/api/prep-drafts', { data: { rows: [{ project_number: project, material_ref: 'Thermal barrier', test_code: 'FLAME', preconditioning_code: '50L', specimen_count: 3 }] } })).json()
  const registered = await (await request.post(`/api/prep-drafts/${draft.id}/commit`)).json()
  const id = registered.stack_ids[0]
  const base = { station_id: 'shared-door', code: `UL1:S:${id}`, scope: 'stack' }
  const entered = await (await request.post('/api/movements', { data: { ...base, request_id: randomUUID(), direction: 'IN' } })).json()
  expect(entered.status).toBe('committed')
  const removed = await (await request.post('/api/movements', { data: { ...base, request_id: randomUUID(), direction: 'OUT', acknowledge_early: true, expected_version: entered.version } })).json()
  expect(removed.status).toBe('committed')
  await page.goto(`/api/exports/report?project_number=${project}`)
  await expect(page.getByRole('heading', { name: 'Fabric conditioning audit report' })).toBeVisible()
  await expect(page.getByText(`Stack ${id} · 3 specimens`)).toBeVisible()
  await expect(page.getByText('Previous record', { exact: true })).toHaveCount(2)
  await expect(page.getByText('Recorded result', { exact: true })).toHaveCount(2)
  await expect(page.getByText(`Event ${entered.event_id}`, { exact: true })).toBeVisible()
  await expect(page.getByText('Incomplete conditioning: early exit acknowledged.', { exact: true })).toBeVisible()
  await expect(page.locator('pre')).toHaveCount(0)
  await page.pdf({ path: testInfo.outputPath('audit-report.pdf'), preferCSSPageSize: true, printBackground: true })
})

test('station layout has no horizontal page overflow on touch and mobile displays', async ({ page }) => {
  for (const viewport of [{ width: 1280, height: 720 }, { width: 800, height: 480 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport)
    await page.goto('/#kiosk')
    await expect(page.getByText('Online', { exact: true })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
    await expect(page.locator('.kiosk-dock')).toBeVisible()
    await expect(page.getByRole('textbox', { name: 'Scan code' })).toHaveCount(0)
    expect(await page.locator('.kiosk-shell').evaluate(el => el.scrollHeight <= window.innerHeight)).toBeTruthy()
    const buttons = await page.locator('.kiosk-dock button').all()
    const boxes = await Promise.all(buttons.map(button => button.boundingBox()))
    expect(boxes).toHaveLength(3)
    expect(Math.abs(boxes[0]!.width - boxes[1]!.width)).toBeLessThan(1)
    expect(Math.abs(boxes[1]!.width - boxes[2]!.width)).toBeLessThan(1)
    expect(boxes.every(box => box!.y === boxes[0]!.y && box!.y + box!.height <= viewport.height)).toBe(true)
  }
})

test('staff records and label-preview actions fit shorter laptop browser viewports', async ({ page, request }) => {
  const project = 'DEMO-LAPTOP-' + randomUUID().slice(0, 8)
  const draft = await (await request.post('/api/prep-drafts', { data: { rows: [{
    project_number: project, material_ref: 'DEMO laptop layout', test_code: 'D5034', preconditioning_code: '5L', specimen_count: 3,
  }] } })).json()
  await request.post('/api/prep-drafts/' + draft.id + '/commit', { data: {} })
  // 1408×760 leaves room for browser/OS chrome below the Neo's reported 1408×881 logical desktop.
  for (const viewport of [{ width: 1204, height: 650 }, { width: 1280, height: 720 }, { width: 1408, height: 760 }, { width: 1440, height: 800 }]) {
    await page.setViewportSize(viewport)
    await page.goto('/#records')
    await expect(page.getByText('Database connected', { exact: true })).toBeVisible()
    await page.getByLabel('Search records').fill(project)
    await page.getByRole('checkbox', { name: /^Select label / }).first().check()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.getByRole('button', { name: 'Preview / reprint selected' }).click()
    const dialog = page.getByRole('dialog', { name: 'Preview & print labels' })
    await expect(dialog).toBeVisible()
    const actions = await dialog.locator('.modal-actions').boundingBox()
    expect(actions!.y + actions!.height).toBeLessThanOrEqual(viewport.height)
    expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
    await dialog.getByRole('button', { name: 'Close', exact: true }).click()
  }
})
