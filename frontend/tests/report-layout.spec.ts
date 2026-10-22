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
    await expect(page.getByText('Database connected')).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
    await expect(page.getByRole('textbox', { name: 'Scan code' })).toBeEnabled()
  }
})
