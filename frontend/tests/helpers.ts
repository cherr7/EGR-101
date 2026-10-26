import { expect } from '@playwright/test'
import type { APIRequestContext, Page } from '@playwright/test'
import { randomUUID } from 'node:crypto'

export const code = (id: string) => BigInt('0x' + id.replaceAll('-', '')).toString().padStart(39, '0')
export async function createStack(request: APIRequestContext, count: number, material = 'DEMO touch-screen fabric', project = 'DEMO-' + randomUUID().slice(0, 8)) {
  const draft = await (await request.post('/api/prep-drafts', { data: { rows: [{ project_number: project, material_ref: material, test_code: 'D5034', preconditioning_code: '5L', specimen_count: count }] } })).json()
  const result = await (await request.post('/api/prep-drafts/' + draft.id + '/commit', { data: {} })).json()
  return (await request.get('/api/stacks/' + result.stack_ids[0])).json()
}
/** Records the whole stack IN at the Shared door through the API, outside the kiosk page. */
export async function move(request: APIRequestContext, stack: { specimens: { id: string }[] }) {
  const response = await request.post('/api/movements', { data: { request_id: randomUUID(), station_id: 'shared-door', code: code(stack.specimens[0].id), direction: 'IN', scope: 'stack' } })
  expect(response.ok()).toBe(true)
  const result = await response.json()
  expect(result.status).toBe('committed')
  return result
}
export async function openKiosk(page: Page) {
  await page.goto('/#kiosk')
  await expect(page.getByText('Online', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Shared', exact: true }).click()
  await page.getByRole('button', { name: 'IN', exact: true }).click()
  await page.bringToFront()
}
export async function scan(page: Page, id: string) { await page.keyboard.type(code(id)); await page.keyboard.press('Enter') }
