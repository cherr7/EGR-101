import { test, expect } from '@playwright/test'
import type { APIRequestContext, Page } from '@playwright/test'

async function createStack(request: APIRequestContext, count = 20) {
  const project = `00E2E-${Date.now()}-${Math.random().toString(16).slice(2, 7)}`
  const response = await request.post('/api/prep-drafts', { data: { rows: [{ project_number: project, material_ref: `Browser-${project}`, test_code: 'TENSILE', preconditioning_code: 'AR', specimen_count: count }] } })
  expect(response.ok(), await response.text()).toBeTruthy()
  const draft = await response.json()
  const registration = await request.post(`/api/prep-drafts/${draft.id}/commit`, { data: {} })
  expect(registration.ok()).toBeTruthy()
  const { stack_ids } = await registration.json()
  return (await request.get(`/api/stacks/${stack_ids[0]}`)).json()
}
async function openKiosk(page: Page) {
  await page.goto('/#kiosk')
  await expect(page.getByText('Database connected')).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Scan code' })).toBeFocused()
}
async function scan(page: Page, code: string) {
  await page.getByRole('textbox', { name: 'Scan code' }).fill(code)
  await page.getByRole('textbox', { name: 'Scan code' }).press('Enter')
}

test('prep registers a leading-zero project, labels keep identities across reload', async ({ page, request }) => {
  await page.goto('/#prep')
  await expect(page.getByText('Database connected')).toBeVisible()
  const project = `0007-BROWSER-${Date.now()}`
  await page.getByLabel('project_number row 1').fill(project)
  await page.getByLabel('material_ref row 1').fill('Canvas review batch')
  await page.getByLabel('test_code row 1').fill('TENSILE')
  await page.getByRole('button', { name: 'Register specimens →' }).click()
  await expect(page.getByText('Registered 1 stack successfully.')).toBeVisible()
  const before = await page.getByRole('link', { name: 'PDF ↗', exact: true }).getAttribute('href')
  await page.reload()
  await expect(page.getByText('Registered 1 stack successfully.')).toBeVisible()
  expect(await page.getByRole('link', { name: 'PDF ↗', exact: true }).getAttribute('href')).toBe(before)
  const items = (await (await request.get(`/api/stacks?project_number=${encodeURIComponent(project)}`)).json()).items
  expect(items).toHaveLength(1)
  expect(items[0].project_number).toBe(project)
  expect(items[0].specimens).toHaveLength(20)
})

test('CSV import supports editable review before registration', async ({ page, request }) => {
  const project = `00CSV-${Date.now()}`
  await page.goto('/#prep')
  await expect(page.getByText('Database connected')).toBeVisible()
  await page.getByLabel('Import CSV').setInputFiles({ name: 'review.csv', mimeType: 'text/csv', buffer: Buffer.from(`project_number,material_ref,test_code,preconditioning_code,specimen_count\n${project},Canvas A,TENSILE,AR,2\n${project},Canvas B,TENSILE,3L,3\n`) })
  await expect(page.getByLabel('material_ref row 2')).toHaveValue('Canvas B')
  await page.getByLabel('material_ref row 1').fill('Canvas A reviewed')
  await page.getByRole('button', { name: 'Register specimens →' }).click()
  await expect(page.getByText('Registered 2 stacks successfully.')).toBeVisible()
  const items = (await (await request.get(`/api/stacks?project_number=${encodeURIComponent(project)}`)).json()).items
  expect(items).toHaveLength(2)
  expect(items.map((item: { material_ref: string }) => item.material_ref).sort()).toEqual(['Canvas A reviewed', 'Canvas B'])
  expect(new Set(items.flatMap((item: { specimens: { id: string }[] }) => item.specimens.map(s => s.id))).size).toBe(5)
})

test('lost registration response recovers the committed draft and original labels on reload', async ({ page, request }) => {
  const project = `00COMMIT-${Date.now()}`
  await page.route('**/api/prep-drafts/*/commit', async route => {
    const response = await route.fetch()
    expect(response.ok()).toBeTruthy()
    await route.abort('failed')
  })
  await page.goto('/#prep')
  await expect(page.getByText('Database connected')).toBeVisible()
  await page.getByLabel('project_number row 1').fill(project)
  await page.getByLabel('material_ref row 1').fill('Response lost batch')
  await page.getByLabel('test_code row 1').fill('TENSILE')
  await page.getByRole('button', { name: 'Register specimens →' }).click()
  await expect(page.getByRole('alert')).toBeVisible()
  const draftId = await page.evaluate(() => JSON.parse(localStorage.getItem('fabric-tracker.prep-draft.v1')!).id)
  const draft = await (await request.get(`/api/prep-drafts/${draftId}`)).json()
  expect(draft.committed).toBe(true)
  await page.reload()
  await expect(page.getByText('Registered 1 stack successfully.')).toBeVisible()
  await expect(page.getByRole('link', { name: 'PDF ↗', exact: true })).toHaveAttribute('href', new RegExp(draft.stack_ids[0]))
  const items = (await (await request.get(`/api/stacks?project_number=${encodeURIComponent(project)}`)).json()).items
  expect(items).toHaveLength(1)
})

test('whole-stack scans, no-op repeats, early partial checkout, mixed selection and scope reset', async ({ page, request }) => {
  const stack = await createStack(request)
  const code = `UL1:P:${stack.specimens[2].id}`
  await openKiosk(page)
  const scanStarted = performance.now()
  await scan(page, code)
  await expect(page.getByText('IN confirmed · 20 specimens')).toBeVisible()
  const confirmedLatencyMs = performance.now() - scanStarted
  await test.info().attach('confirmed-scan-latency', {
    body: JSON.stringify({ specimen_count: 20, confirmed_latency_ms: Math.round(confirmedLatencyMs), target_ms: 1000 }),
    contentType: 'application/json',
  })
  expect(confirmedLatencyMs, 'Scan submission to visible database confirmation').toBeLessThan(1000)
  const entered = await (await request.get(`/api/stacks/${stack.id}`)).json()
  expect(entered.specimens.every((s: { state: string }) => s.state === 'IN')).toBeTruthy()
  await scan(page, code)
  await expect(page.getByText('Already recorded', { exact: true })).toBeVisible()
  const repeated = await (await request.get(`/api/stacks/${stack.id}`)).json()
  expect(repeated.specimens.map((s: { entered_at: string }) => s.entered_at)).toEqual(entered.specimens.map((s: { entered_at: string }) => s.entered_at))
  expect(repeated.specimens.every((s: { sessions: unknown[] }) => s.sessions.length === 1)).toBeTruthy()
  await page.getByRole('button', { name: 'OUT', exact: true }).click()
  await expect(page.getByRole('textbox', { name: 'Scan code' })).toBeFocused()
  await page.getByRole('button', { name: 'Choose specimens', exact: true }).click()
  const selection = page.getByRole('dialog', { name: 'Select the specimens moving' })
  await selection.getByRole('checkbox', { name: /^02/ }).check()
  await selection.getByRole('checkbox', { name: /^05/ }).check()
  await selection.getByRole('button', { name: 'Confirm OUT' }).click()
  const early = page.getByRole('dialog', { name: 'Conditioning is not complete' })
  await expect(early).toBeVisible()
  const unchanged = await (await request.get(`/api/stacks/${stack.id}`)).json()
  expect(unchanged.specimens.every((s: { state: string }) => s.state === 'IN')).toBeTruthy()
  await early.getByRole('button', { name: 'Acknowledge & record OUT' }).click()
  await expect(page.getByText('OUT confirmed · 2 specimens')).toBeVisible()
  const partial = await (await request.get(`/api/stacks/${stack.id}`)).json()
  expect(partial.specimens.filter((s: { state: string }) => s.state === 'OUT').map((s: { number: number }) => s.number)).toEqual([2, 5])
  await expect(page.getByRole('button', { name: /Whole stack/ })).toHaveAttribute('aria-pressed', 'true')
  await scan(page, code)
  await expect(page.getByRole('dialog', { name: 'Select the specimens moving' })).toBeVisible()
  await page.getByRole('button', { name: 'Close dialog' }).click()
  await page.getByRole('button', { name: /Individual specimens/ }).click()
  await scan(page, code)
  await page.getByRole('dialog').getByRole('button', { name: 'Acknowledge & record OUT' }).click()
  await expect(page.getByText('OUT confirmed · 1 specimens')).toBeVisible()
  await expect(page.getByRole('button', { name: /Whole stack/ })).toHaveAttribute('aria-pressed', 'true')
})

test('lost committed response reconciles the same request, including after reload', async ({ page, request }) => {
  const stack = await createStack(request, 3)
  let lost = false
  await page.route('**/api/movements', async route => {
    if (lost) { await route.continue(); return }
    lost = true
    const response = await route.fetch()
    expect(response.ok()).toBeTruthy()
    await route.abort('failed')
  })
  await openKiosk(page)
  await scan(page, `UL1:P:${stack.specimens[0].id}`)
  await expect(page.getByText('Awaiting database confirmation.')).toBeVisible()
  await page.reload()
  await expect(page.getByText('IN confirmed · 3 specimens')).toBeVisible()
  const detail = await (await request.get(`/api/stacks/${stack.id}`)).json()
  expect(detail.specimens.every((s: { sessions: unknown[] }) => s.sessions.length === 1)).toBeTruthy()
  expect(detail.events.filter((e: { kind: string }) => e.kind === 'movement')).toHaveLength(1)
  expect(await page.evaluate(() => localStorage.getItem('fabric-tracker.pending-movement.v1'))).toBeNull()
})

test('server unavailable disables scanning and shows no success', async ({ page, request }) => {
  const stack = await createStack(request, 2)
  await openKiosk(page)
  await page.route('**/api/health', route => route.abort('failed'))
  await expect(page.getByRole('alert').filter({ hasText: 'NOT AVAILABLE' })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Scan code' })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Simulate specimen scan →' })).toBeDisabled()
  await expect(page.locator('.scan-result.success')).toHaveCount(0)
  const unchanged = await (await request.get(`/api/stacks/${stack.id}`)).json()
  expect(unchanged.specimens.every((s: { sessions: unknown[] }) => s.sessions.length === 0)).toBeTruthy()
})

test('supervisor records a missed entry with a reason and visible audit provenance', async ({ page, request }) => {
  const stack = await createStack(request, 2)
  await page.goto('/#supervisor')
  await expect(page.getByText('Database connected')).toBeVisible()
  await page.getByLabel('Supervisor password').fill(process.env.SUPERVISOR_PASSWORD || 'classroom-demo')
  await page.getByRole('button', { name: 'Sign in →' }).click()
  await expect(page.getByText('Document a correction')).toBeVisible()
  await page.getByLabel('Correction stack').selectOption(stack.id)
  await page.getByRole('button', { name: 'Record missed entry' }).click()
  await page.locator('.correction-card').getByRole('checkbox', { name: /^01/ }).check()
  const earlier = new Date(Date.now() - 26 * 3600000)
  const input = `${earlier.getFullYear()}-${String(earlier.getMonth() + 1).padStart(2, '0')}-${String(earlier.getDate()).padStart(2, '0')}T${String(earlier.getHours()).padStart(2, '0')}:${String(earlier.getMinutes()).padStart(2, '0')}`
  await page.getByLabel('Effective entry time', { exact: true }).fill(input)
  await page.getByLabel('Correction reason').fill('Operator forgot to scan entry; verified from room log.')
  await page.getByRole('button', { name: 'Save correction →' }).click()
  await expect(page.getByText('Correction saved. The original observation remains in the audit history.')).toBeVisible()
  await expect(page.getByText('Operator forgot to scan entry; verified from room log.')).toBeVisible()
  const detail = await (await request.get(`/api/stacks/${stack.id}`)).json()
  expect(detail.specimens[0].state).toBe('IN')
  expect(detail.specimens[1].state).toBe('OUT')
  expect(Date.parse(detail.specimens[0].sessions[0].recorded_at)).toBeGreaterThan(Date.parse(detail.specimens[0].sessions[0].entered_at))
})
