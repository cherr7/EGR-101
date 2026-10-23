import { test, expect } from '@playwright/test'
import type { APIRequestContext, Page } from '@playwright/test'
import { randomUUID } from 'node:crypto'

const code = (id: string) => BigInt('0x' + id.replaceAll('-', '')).toString().padStart(39, '0')
async function specimenStack(request: APIRequestContext, count: number) {
  const draft = await (await request.post('/api/prep-drafts', { data: { rows: [{
    project_number: 'DEMO-ACTIVITY-' + randomUUID().slice(0, 8), material_ref: 'DEMO touch-screen fabric',
    test_code: 'D5034', preconditioning_code: '5L', specimen_count: count,
  }] } })).json()
  const registered = await (await request.post(`/api/prep-drafts/${draft.id}/commit`, { data: {} })).json()
  return (await request.get('/api/stacks/' + registered.stack_ids[0])).json()
}
async function open(page: Page) {
  await page.goto('/#kiosk')
  await expect(page.getByText('Online', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Shared', exact: true }).click()
  await page.getByRole('button', { name: 'IN', exact: true }).click()
  await page.bringToFront()
}
async function scan(page: Page, id: string) {
  await page.keyboard.type(code(id))
  await page.keyboard.press('Enter')
}

test('Individual survives early checkout, Undo and reload until explicitly changed', async ({ page, request }) => {
  const stack = await specimenStack(request, 3)
  await open(page)
  await scan(page, stack.specimens[0].id)
  await expect(page.getByRole('heading', { name: 'IN confirmed', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'OUT', exact: true }).click()
  const individual = page.getByRole('button', { name: 'Individual', exact: true })
  await individual.click()
  await scan(page, stack.specimens[0].id)
  const selection = page.getByRole('dialog', { name: 'Choose the specimens moving' })
  await selection.getByRole('button', { name: 'Record 1 OUT', exact: true }).click()
  await page.getByRole('dialog', { name: 'Early checkout' }).getByRole('button', { name: 'Override & record OUT', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'OUT confirmed', exact: true })).toBeVisible()
  await expect(individual).toHaveAttribute('aria-pressed', 'true')
  const updated = await (await request.get('/api/stacks/' + stack.id)).json()
  expect(updated.inside_count).toBe(2)
  expect(updated.finished_count).toBe(1)
  await page.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Transaction undone', exact: true })).toBeVisible()
  await expect(individual).toHaveAttribute('aria-pressed', 'true')
  await page.reload()
  await expect(page.getByText('Online', { exact: true })).toBeVisible()
  await expect(individual).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('button', { name: 'OUT', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await scan(page, stack.specimens[1].id)
  await expect(selection).toBeVisible()
  await expect(selection.getByRole('checkbox', { name: /^02/ })).toBeChecked()
  await selection.getByRole('button', { name: 'Cancel', exact: true }).click()
  await page.getByRole('button', { name: 'Stack', exact: true }).click()
  await page.reload()
  await expect(page.getByRole('button', { name: 'Stack', exact: true })).toHaveAttribute('aria-pressed', 'true')
})

test('confirmed receipt and exact Undo do not wait for activity or detail refreshes', async ({ page, request }) => {
  const stack = await specimenStack(request, 4)
  await page.setViewportSize({ width: 1280, height: 720 })
  await open(page)
  await page.route('**/api/events?**', route => route.abort('failed'))
  await page.route('**/api/stacks/' + stack.id, route => route.abort('failed'))
  await scan(page, stack.specimens[0].id)
  await expect(page.getByRole('heading', { name: 'IN confirmed', exact: true })).toBeVisible()
  const row = page.locator('.recent-row').first()
  await expect(row).toContainText(stack.project_number)
  await expect(row).toContainText('4 specimens')
  await expect(row).toHaveAttribute('data-undo-target', 'true')
  await expect(page.locator('.last-stack')).toContainText('4 inside')
  const eventId = await row.getAttribute('data-event-id')
  const undo = page.getByRole('button', { name: 'Undo', exact: true })
  await expect(undo).toHaveAttribute('aria-describedby', 'activity-' + eventId)
  await undo.click()
  await expect(page.getByRole('heading', { name: 'Transaction undone', exact: true })).toBeVisible()
  await expect(row).toHaveAttribute('data-event-id', eventId!)
  await expect(row).toContainText('Undone')
  await expect(undo).toBeDisabled()
  expect((await (await request.get('/api/stacks/' + stack.id)).json()).inside_count).toBe(0)
})

test('a late history snapshot cannot erase the newly confirmed top Activity row', async ({ page, request }) => {
  const stack = await specimenStack(request, 2)
  await page.clock.install()
  await open(page)
  let release: (() => void) | undefined
  let held = false
  await page.route('**/api/events?**', async route => {
    if (held) { await route.abort('failed'); return }
    held = true
    const response = await route.fetch()
    await new Promise<void>(resolve => { release = resolve })
    await route.fulfill({ response })
  })
  await page.clock.fastForward(5001)
  await expect.poll(() => !!release).toBe(true)
  await scan(page, stack.specimens[0].id)
  await expect(page.getByRole('heading', { name: 'IN confirmed', exact: true })).toBeVisible()
  const row = page.locator('.recent-row').first()
  await expect(row).toContainText(stack.project_number)
  const lateResponse = page.waitForResponse(response => response.url().includes('/api/events?'))
  release!()
  await lateResponse
  await expect(row).toContainText(stack.project_number)
  await expect(page.getByRole('button', { name: 'Undo', exact: true })).toBeEnabled()
})

test('Undo follows the newest visible scan and never steps backward after an Undo', async ({ page, request }) => {
  const first = await specimenStack(request, 2), second = await specimenStack(request, 3)
  await page.clock.install()
  await open(page)
  await scan(page, first.specimens[0].id)
  await expect(page.getByRole('heading', { name: 'IN confirmed', exact: true })).toBeVisible()
  const newer = await (await request.post('/api/movements', { data: {
    request_id: randomUUID(), station_id: 'shared-door', code: code(second.specimens[0].id), direction: 'IN', scope: 'stack',
  } })).json()
  expect(newer.status).toBe('committed')
  await page.clock.fastForward(5001)
  await expect(page.locator('.recent-row').first()).toContainText(second.project_number)
  await page.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(page.locator('.recent-row').first()).toContainText('Undone')
  await expect(page.getByRole('button', { name: 'Undo', exact: true })).toBeDisabled()
  expect((await (await request.get('/api/stacks/' + first.id)).json()).inside_count).toBe(2)
  expect((await (await request.get('/api/stacks/' + second.id)).json()).inside_count).toBe(0)
})

test('ordinary scan errors clear after four seconds and a new scan can interrupt them', async ({ page, request }) => {
  const stack = await specimenStack(request, 3)
  await page.clock.install()
  await open(page)
  await page.clock.pauseAt(new Date(Date.now() + 1000))
  await page.keyboard.type('NOT-A-LABEL'); await page.keyboard.press('Enter')
  await expect(page.getByRole('alert')).toBeVisible()
  await page.clock.fastForward(3999)
  await expect(page.getByRole('alert')).toBeVisible()
  await page.clock.fastForward(2)
  await expect(page.getByRole('alert')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Ready for IN', exact: true })).toBeVisible()
  await page.keyboard.type('NOT-A-LABEL'); await page.keyboard.press('Enter')
  await expect(page.getByRole('alert')).toBeVisible()
  await scan(page, stack.specimens[0].id)
  await expect(page.getByRole('heading', { name: 'IN confirmed', exact: true })).toBeVisible()
  await expect(page.getByRole('alert')).toHaveCount(0)
  expect((await (await request.get('/api/stacks/' + stack.id)).json()).inside_count).toBe(3)
})
