import { expect } from '@playwright/test'
import type { APIRequestContext, Page } from '@playwright/test'

let serial = 0
/** A short label that is unique within a test run, for project names (no UUIDs). */
export const tag = () => Date.now().toString(36).slice(-6) + (++serial).toString(36)

/** A one-row prep sheet in the draft's layout: header once, then the sheet's columns. */
export const sheet = (project: string, material: string, test: string, treatment: string, count: number) => ({
  headers: { Preparation: { project_number: project } },
  rows: [{ sheet: 'Preparation', test, conditions: treatment, conditioning: String(count), sample: material }],
})

/** Reserve one retry ID '<client>-<n>' the way the kiosk (station id) and label dialog ('print') do. */
export async function reserveId(request: APIRequestContext, client = 'shared-door') {
  const response = await request.post('/api/request-ids', { data: { client, count: 1 } })
  expect(response.ok(), await response.text()).toBe(true)
  return `${client}-${(await response.json()).first}`
}

export async function createStack(request: APIRequestContext, count: number, material = 'DEMO touch-screen fabric', project = 'DEMO-' + tag(), treatment = '5L') {
  const draft = await (await request.post('/api/prep-drafts', { data: sheet(project, material, 'D5034', treatment, count) })).json()
  const result = await (await request.post('/api/prep-drafts/' + draft.id + '/commit', { data: {} })).json()
  return (await request.get('/api/stacks/' + result.stack_ids[0])).json()
}
/** Records the whole stack IN at the Shared door through the API, outside the kiosk page. */
export async function move(request: APIRequestContext, stack: { specimens: { barcode_value: string }[] }, direction = 'IN', station = 'shared-door', extra: Record<string, unknown> = {}) {
  const response = await request.post('/api/movements', { data: { request_id: await reserveId(request, station), station_id: station, code: stack.specimens[0].barcode_value, direction, scope: 'stack', ...extra } })
  expect(response.ok()).toBe(true)
  const result = await response.json()
  expect(result.status, result.message).toBe('committed')
  return result
}
export async function openKiosk(page: Page) {
  await page.goto('/#kiosk')
  await expect(page.getByText('Online', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Shared', exact: true }).click()
  await page.getByRole('button', { name: 'IN', exact: true }).click()
  await page.bringToFront()
}
export async function scan(page: Page, code: string) { await page.keyboard.type(code); await page.keyboard.press('Enter') }
