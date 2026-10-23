import { test, expect } from '@playwright/test'
import type { APIRequestContext, Page } from '@playwright/test'
import { randomUUID } from 'node:crypto'

const code = (id: string) => BigInt('0x' + id.replaceAll('-', '')).toString().padStart(39, '0')
async function create(request: APIRequestContext, count: number, material: string, project = 'DEMO-PANELS-' + randomUUID().slice(0, 8)) {
  const draft = await (await request.post('/api/prep-drafts', { data: { rows: [{ project_number: project, material_ref: material, test_code: 'D5034', preconditioning_code: '5L', specimen_count: count }] } })).json()
  const result = await (await request.post('/api/prep-drafts/' + draft.id + '/commit', { data: {} })).json()
  return (await request.get('/api/stacks/' + result.stack_ids[0])).json()
}
async function move(request: APIRequestContext, stack: any) {
  const response = await request.post('/api/movements', { data: { request_id: randomUUID(), station_id: 'shared-door', code: code(stack.specimens[0].id), direction: 'IN', scope: 'stack' } })
  expect(response.ok()).toBe(true)
  return response.json()
}
async function kiosk(page: Page) {
  await page.setViewportSize({ width: 1280, height: 720 })
  await page.goto('/#kiosk')
  await expect(page.getByText('Online', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Shared', exact: true }).click()
  await page.getByRole('button', { name: 'IN', exact: true }).click()
  await page.bringToFront()
}
async function scan(page: Page, id: string) { await page.keyboard.type(code(id)); await page.keyboard.press('Enter') }

test('50/50 layout and expanded Activity undo the chosen older transaction despite a lost response', async ({ page, request }) => {
  const first = await create(request, 3, 'Older transaction'), second = await create(request, 2, 'Newest transaction'), untouched = await create(request, 1, 'Blocked scan')
  const entered = await move(request, first); await move(request, second)
  await kiosk(page)
  const left = await page.locator('.readiness-area').boundingBox(), right = await page.locator('.recent-panel').boundingBox()
  expect(Math.abs(left!.width - right!.width)).toBeLessThan(1)
  await page.getByRole('button', { name: 'Expand Activity', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Activity', exact: true })
  const row = dialog.locator('[data-event-id="' + entered.event_id + '"]')
  await expect(row).toContainText('Older transaction')
  await expect(row).toContainText('01, 02, 03')
  await scan(page, untouched.specimens[0].id)
  expect((await (await request.get('/api/stacks/' + untouched.id)).json()).inside_count).toBe(0)
  let posts = 0
  await page.route('**/api/transactions/' + entered.event_id + '/undo', async route => { posts++; const response = await route.fetch(); expect(response.ok()).toBe(true); await route.abort('failed') })
  const minus = row.getByRole('button', { name: 'Undo transaction ' + entered.event_id, exact: true })
  await minus.click()
  await expect(row).toContainText('Undone')
  await expect(minus).toBeDisabled()
  expect(posts).toBe(1)
  const a = await (await request.get('/api/stacks/' + first.id)).json(), b = await (await request.get('/api/stacks/' + second.id)).json()
  expect(a.inside_count).toBe(0); expect(b.inside_count).toBe(2)
  expect(a.events.filter((e: any) => e.kind === 'undo')).toHaveLength(1)
})

test('room inventory prioritizes ready pieces, filters project and due-soon, and blocks scans', async ({ page, request }) => {
  const project = 'DEMO-INVENTORY-' + randomUUID().slice(0, 8)
  const mixed = await create(request, 3, 'Mixed readiness', project), otherRoom = await create(request, 2, 'Other room', project), untouched = await create(request, 1, 'Not entered', project)
  async function enter(stack: any, ids: string[], hours: number, room: string) {
    const current = await (await request.get('/api/stacks/' + stack.id)).json()
    const response = await request.post('/api/transactions/missed', { data: { stack_id: stack.id, specimen_ids: ids, direction: 'IN', occurred_at: new Date(Date.now() - hours * 3600000).toISOString(), room_id: room, expected_version: current.version } })
    expect(response.ok()).toBe(true)
  }
  await enter(mixed, mixed.specimens.slice(0, 2).map((s: any) => s.id), 26, 'shared')
  await enter(mixed, [mixed.specimens[2].id], 23.5, 'shared')
  await enter(otherRoom, otherRoom.specimens.map((s: any) => s.id), 26, 'ppe')
  await kiosk(page)
  await page.getByRole('button', { name: 'Inventory', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Inventory', exact: true })
  await dialog.getByLabel('Inventory project').selectOption(project)
  await expect(dialog.getByRole('button', { name: 'Ready 2', exact: true })).toHaveAttribute('aria-pressed', 'true')
  const row = dialog.locator('[data-stack-id="' + mixed.id + '"]')
  await expect(row).toContainText('2 ready'); await expect(row).toContainText('3 inside')
  await expect(dialog.getByText('Other room', { exact: true })).toHaveCount(0)
  await row.getByRole('button').click()
  await expect(row.locator('.inventory-pieces')).toContainText('01, 02')
  await expect(row.locator('.inventory-pieces')).toContainText('03')
  await scan(page, untouched.specimens[0].id)
  expect((await (await request.get('/api/stacks/' + untouched.id)).json()).inside_count).toBe(0)
  await dialog.getByRole('button', { name: 'Next 2h', exact: true }).click()
  await expect(row).toBeVisible()
  await dialog.getByRole('button', { name: 'Inside 3', exact: true }).click()
  await expect(row).toBeVisible()
  const actions = await dialog.getByRole('button', { name: 'Close dialog', exact: true }).boundingBox()
  expect(actions!.y + actions!.height).toBeLessThanOrEqual(720)
  expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
  await dialog.getByRole('button', { name: 'Close dialog', exact: true }).click()
  await page.getByRole('button', { name: 'PPE', exact: true }).click()
  await page.getByRole('button', { name: 'Inventory', exact: true }).click()
  await dialog.getByLabel('Inventory project').selectOption(project)
  await expect(dialog.locator('[data-stack-id="' + otherRoom.id + '"]')).toBeVisible()
  await expect(dialog.locator('[data-stack-id="' + mixed.id + '"]')).toHaveCount(0)
})
