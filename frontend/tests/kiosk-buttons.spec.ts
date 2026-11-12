/**
 * Kiosk button suite: drives the scanner screen at the Pi's 1280 x 720 touch display and
 * taps every control. Each button must
 *   - say what it does (an accessible name),
 *   - be reachable by touch: at least 44 x 44 px, on screen, and not covered,
 *   - look disabled when it is disabled (dimmed, not-allowed cursor) and look enabled otherwise,
 *   - do what its name says when tapped.
 * It also covers Undo / remove ("−") when one stack, or one specimen, has several scans in Activity.
 */
import { test, expect } from '@playwright/test'
import type { APIRequestContext, Locator, Page } from '@playwright/test'
import { createStack, move, reserveId, scan, tag } from './helpers'

test.use({ viewport: { width: 1280, height: 720 }, hasTouch: true })
const MIN_TOUCH = 44

type Audit = { name: string; disabled: boolean }
/** Check every visible button inside `root` and return what was found. */
async function auditButtons(page: Page, root: Locator): Promise<Audit[]> {
  const found: Audit[] = []
  const buttons = root.getByRole('button')
  for (let i = 0; i < await buttons.count(); i++) {
    const button = buttons.nth(i)
    if (!await button.isVisible()) continue
    await button.scrollIntoViewIfNeeded()
    const facts = await button.evaluate(element => {
      const box = element.getBoundingClientRect(), style = getComputedStyle(element)
      let opacity = 1
      for (let node: Element | null = element; node; node = node.parentElement) opacity *= Number(getComputedStyle(node).opacity)
      const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)
      return {
        name: (element.getAttribute('aria-label') || element.textContent || element.getAttribute('title') || '').trim(),
        width: box.width, height: box.height, x: box.x, y: box.y,
        onScreen: box.x >= 0 && box.y >= 0 && box.right <= innerWidth && box.bottom <= innerHeight,
        reachable: !!hit && (hit === element || element.contains(hit)),
        disabled: (element as HTMLButtonElement).disabled, opacity, cursor: style.cursor,
      }
    })
    const label = `button "${facts.name}"`
    expect(facts.name, 'every button needs a name').not.toBe('')
    expect(facts.width, `${label} width`).toBeGreaterThanOrEqual(MIN_TOUCH)
    expect(facts.height, `${label} height`).toBeGreaterThanOrEqual(MIN_TOUCH)
    expect(facts.onScreen, `${label} must be on screen`).toBe(true)
    expect(facts.reachable, `${label} must not be covered`).toBe(true)
    if (facts.disabled) {
      expect(facts.opacity, `${label} must look disabled`).toBeLessThanOrEqual(0.6)
      expect(facts.cursor, `${label} disabled cursor`).toBe('not-allowed')
    } else expect(facts.opacity, `${label} must look enabled`).toBeGreaterThan(0.9)
    found.push({ name: facts.name, disabled: facts.disabled })
  }
  return found
}
const names = (audit: Audit[], disabled?: boolean) => audit.filter(b => disabled === undefined || b.disabled === disabled).map(b => b.name)

async function openKiosk(page: Page) {
  await page.goto('/#kiosk')
  await expect(page.getByText('Online', { exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => (window as unknown as { fabricScannerCaptureReady: boolean }).fabricScannerCaptureReady)).toBe(true)
  await page.getByRole('button', { name: 'Shared', exact: true }).tap()
  await page.getByRole('button', { name: 'IN', exact: true }).tap()
  await page.getByRole('button', { name: 'Stack', exact: true }).tap()
}
const heading = (page: Page) => page.locator('.readiness-area h2')
const dock = (page: Page, name: string) => page.locator('.kiosk-dock').getByRole('button', { name, exact: true })
const stackState = async (request: APIRequestContext, id: number) => (await request.get('/api/stacks/' + id)).json()
async function rows(page: Page) {
  await page.getByRole('button', { name: 'Expand Activity', exact: true }).tap()
  return page.getByRole('dialog', { name: 'Activity', exact: true })
}
const minus = (dialog: Locator, id: number) => dialog.getByRole('button', { name: 'Undo transaction ' + id, exact: true })

test('main screen: every control is named, touch-sized, uncovered and visibly enabled or disabled', async ({ page, request }) => {
  const stack = await createStack(request, 3, 'Main screen audit')
  await openKiosk(page)
  const shell = page.locator('.kiosk-shell')
  let audit = await auditButtons(page, shell)
  expect(names(audit)).toEqual(expect.arrayContaining(['PPE', 'Shared', 'Footwear', 'Stack', 'Individual', 'Scanner setup',
    'Inventory', 'Expand Activity', 'IN', 'OUT', 'Undo']))
  // Nothing scanned at this station yet in this test's view of Undo: it is disabled and says so visually.
  const undo = dock(page, 'Undo')
  if (await undo.isDisabled()) expect(names(audit, true)).toContain('Undo')

  // Room, scope and direction buttons are toggles that report their state.
  for (const room of ['PPE', 'Footwear', 'Shared']) {
    await page.getByRole('button', { name: room, exact: true }).tap()
    await expect(page.getByRole('button', { name: room, exact: true })).toHaveAttribute('aria-pressed', 'true')
    await expect(page).toHaveURL(new RegExp('station=' + room.toLowerCase()))
  }
  for (const scope of ['Individual', 'Stack']) {
    await page.getByRole('button', { name: scope, exact: true }).tap()
    await expect(page.getByRole('button', { name: scope, exact: true })).toHaveAttribute('aria-pressed', 'true')
  }
  for (const direction of ['OUT', 'IN']) {
    await dock(page, direction).tap()
    await expect(dock(page, direction)).toHaveAttribute('aria-pressed', 'true')
    await expect(heading(page)).toHaveText('Ready for ' + direction)
  }

  // A scan enables Undo, which reverses exactly that scan.
  await scan(page, stack.specimens[0].barcode_value)
  await expect(heading(page)).toHaveText('IN confirmed')
  await expect(undo).toBeEnabled()
  audit = await auditButtons(page, shell)
  expect(names(audit, false)).toContain('Undo')
  await undo.tap()
  await expect(heading(page)).toHaveText('Transaction undone')
  await expect(undo).toBeDisabled()
  expect((await stackState(request, stack.id)).inside_count).toBe(0)
  audit = await auditButtons(page, shell)
  expect(names(audit, true)).toContain('Undo')
})

test('panels: setup, inventory and activity buttons work by touch and block scanning while open', async ({ page, request }) => {
  const stack = await createStack(request, 2, 'Panel audit ' + tag())
  await move(request, stack)
  await openKiosk(page)

  await page.getByRole('button', { name: 'Scanner setup', exact: true }).tap()
  const setup = page.getByRole('dialog', { name: 'Scanner setup' })
  await expect(setup).toBeVisible()
  await auditButtons(page, setup)
  // While a panel is open the header and dock are disabled, and they look it.
  const behind = await auditButtons(page, page.locator('.kiosk-dock'))
  expect(names(behind, true)).toEqual(expect.arrayContaining(['IN', 'OUT', 'Undo']))
  await setup.getByRole('button', { name: 'Done', exact: true }).tap()
  await expect(setup).toBeHidden()

  await page.getByRole('button', { name: 'Inventory', exact: true }).tap()
  const inventory = page.getByRole('dialog', { name: 'Inventory', exact: true })
  await expect(inventory.locator(`[data-stack-id="${stack.id}"]`)).toHaveCount(0)   // 'Ready' filter: nothing has done 24 h
  for (const filter of ['Inside', 'Next 2h', 'Ready']) {
    const button = inventory.getByRole('button', { name: new RegExp('^' + filter) })
    await button.tap()
    await expect(button).toHaveAttribute('aria-pressed', 'true')
  }
  await inventory.getByRole('button', { name: /^Inside/ }).tap()
  const item = inventory.locator(`[data-stack-id="${stack.id}"]`).getByRole('button')
  await item.tap()
  await expect(item).toHaveAttribute('aria-expanded', 'true')
  await expect(inventory).toContainText('Conditioning')
  await auditButtons(page, inventory)
  await scan(page, stack.specimens[0].barcode_value)
  await expect(inventory.getByRole('alert')).toContainText('Close this panel')
  await inventory.getByRole('button', { name: 'Close dialog', exact: true }).tap()
  await expect(inventory).toBeHidden()

  const activity = await rows(page)
  for (const filter of ['IN', 'OUT', 'All']) {
    await activity.getByRole('button', { name: filter, exact: true }).tap()
    await expect(activity.getByRole('button', { name: filter, exact: true })).toHaveAttribute('aria-pressed', 'true')
  }
  await auditButtons(page, activity)
  await activity.getByRole('button', { name: 'Close dialog', exact: true }).tap()
  await expect(activity).toBeHidden()
})

test('popups: individual selection and early checkout buttons are touch-sized and do what they say', async ({ page, request }) => {
  const stack = await createStack(request, 4, 'Popup audit')
  await openKiosk(page)
  await scan(page, stack.specimens[0].barcode_value)
  await expect(heading(page)).toHaveText('IN confirmed')
  await dock(page, 'OUT').tap()
  await page.getByRole('button', { name: 'Individual', exact: true }).tap()
  await scan(page, stack.specimens[1].barcode_value)
  const selection = page.getByRole('dialog', { name: 'Choose the specimens moving' })
  await expect(selection.getByRole('checkbox', { name: /^02/ })).toBeChecked()
  await auditButtons(page, selection)
  await selection.getByRole('button', { name: 'Clear', exact: true }).tap()
  await expect(selection.getByRole('button', { name: 'Record 0 OUT', exact: true })).toBeDisabled()
  await selection.getByRole('button', { name: /^Select all eligible/ }).tap()
  await expect(selection.getByRole('button', { name: 'Record 4 OUT', exact: true })).toBeEnabled()
  for (const n of ['01', '03', '04']) await selection.locator('label', { hasText: new RegExp('^' + n) }).tap()
  await selection.getByRole('button', { name: 'Record 1 OUT', exact: true }).tap()
  const early = page.getByRole('dialog', { name: 'Early checkout' })
  await expect(early).toContainText('Specimen 02')
  await auditButtons(page, early)
  await early.getByRole('button', { name: 'Keep in room', exact: true }).tap()
  await expect(early).toBeHidden()
  expect((await stackState(request, stack.id)).inside_count).toBe(4)
  await scan(page, stack.specimens[1].barcode_value)
  await selection.getByRole('button', { name: 'Cancel', exact: true }).tap()
  await expect(selection).toBeHidden()
  await scan(page, stack.specimens[1].barcode_value)
  await selection.getByRole('button', { name: 'Record 1 OUT', exact: true }).tap()
  await early.getByRole('button', { name: 'Override & record OUT', exact: true }).tap()
  await expect(heading(page)).toHaveText('OUT confirmed')
  expect((await stackState(request, stack.id)).inside_count).toBe(3)
})

test('pending scan: the recovery button is touch-sized and everything else is visibly disabled until it resolves', async ({ page, request }) => {
  const stack = await createStack(request, 2, 'Pending audit')
  await openKiosk(page)
  let hold = true
  await page.route('**/api/movements', async route => { const response = await route.fetch(); if (hold) await route.abort('failed'); else await route.fulfill({ response }) })
  await page.route('**/api/movement-requests/**', async route => { if (hold) await route.abort('failed'); else await route.continue() })
  await scan(page, stack.specimens[0].barcode_value)
  await expect(page.getByRole('button', { name: 'Check scan result', exact: true })).toBeVisible()
  const audit = await auditButtons(page, page.locator('.kiosk-shell'))
  expect(names(audit, true)).toEqual(expect.arrayContaining(['PPE', 'Shared', 'Footwear', 'Stack', 'Individual', 'IN', 'OUT', 'Undo', 'Inventory', 'Expand Activity']))
  hold = false
  await page.getByRole('button', { name: 'Check scan result', exact: true }).tap()
  await expect(heading(page)).toHaveText('IN confirmed')
  expect((await stackState(request, stack.id)).inside_count).toBe(2)
})

test('several scans of one stack: only scans with nothing later can be undone, and undoing the newest reopens the one before', async ({ page, request }) => {
  const stack = await createStack(request, 5, 'Multi-scan stack')
  await openKiosk(page)
  await scan(page, stack.specimens[0].barcode_value)                         // whole stack IN
  await expect(heading(page)).toHaveText('IN confirmed')
  await dock(page, 'OUT').tap()
  await page.getByRole('button', { name: 'Individual', exact: true }).tap()
  for (const at of [2, 3]) {                                                  // two early individual OUTs
    await scan(page, stack.specimens[at].barcode_value)
    await page.getByRole('dialog', { name: 'Choose the specimens moving' }).getByRole('button', { name: 'Record 1 OUT', exact: true }).tap()
    await page.getByRole('dialog', { name: 'Early checkout' }).getByRole('button', { name: 'Override & record OUT', exact: true }).tap()
    await expect(heading(page)).toHaveText('OUT confirmed')
  }
  const events = (await (await request.get(`/api/events?stack_id=${stack.id}&kind=movement`)).json()).items
  const [outFour, outThree, wholeIn] = events.map((e: { id: number }) => e.id)
  let activity = await rows(page)
  // OUT #04 and OUT #03 touch different specimens, so both are undoable; the whole-stack IN is not.
  await expect(minus(activity, outFour)).toBeEnabled()
  await expect(minus(activity, outThree)).toBeEnabled()
  await expect(minus(activity, wholeIn)).toBeDisabled()
  await expect(minus(activity, wholeIn)).toHaveAttribute('title', 'Later activity prevents Undo')
  await auditButtons(page, activity)
  // Undo the older OUT first: the newer one is unaffected.
  await minus(activity, outThree).tap()
  await expect(activity.locator(`[data-event-id="${outThree}"]`)).toContainText('Undone')
  await expect(minus(activity, outThree)).toBeDisabled()
  await expect(minus(activity, outFour)).toBeEnabled()
  await expect(minus(activity, wholeIn)).toBeDisabled()
  await minus(activity, outFour).tap()
  await expect(activity.locator(`[data-event-id="${outFour}"]`)).toContainText('Undone')
  // With both OUTs undone, the whole-stack IN is the latest live change again.
  await activity.getByRole('button', { name: 'Close dialog', exact: true }).tap()
  activity = await rows(page)
  await expect(minus(activity, wholeIn)).toBeEnabled()
  await minus(activity, wholeIn).tap()
  await expect(activity.locator(`[data-event-id="${wholeIn}"]`)).toContainText('Undone')
  const final = await stackState(request, stack.id)
  expect(final.inside_count).toBe(0); expect(final.finished_count).toBe(0); expect(final.waiting_count).toBe(5)
  await activity.getByRole('button', { name: 'Close dialog', exact: true }).tap()
  // The dock's Undo only ever targets the newest row; that row is undone, so it stays disabled rather than jump to another scan.
  await expect(dock(page, 'Undo')).toBeDisabled()
})

test('the same specimen scanned again and interleaved stacks: Undo targets the right transaction', async ({ page, request }) => {
  const a = await createStack(request, 3, 'Interleaved A'), b = await createStack(request, 2, 'Interleaved B')
  await openKiosk(page)
  await scan(page, a.specimens[0].barcode_value); await expect(heading(page)).toHaveText('IN confirmed')
  await scan(page, b.specimens[0].barcode_value); await expect(heading(page)).toHaveText('IN confirmed')
  // Scanning the same specimen again records nothing new and adds no Activity row.
  await scan(page, a.specimens[1].barcode_value); await expect(heading(page)).toHaveText('Already recorded')
  const ids = (await (await request.get('/api/events?kind=movement&room_id=shared&limit=5')).json()).items
    .filter((e: { stack_id: number }) => [a.id, b.id].includes(e.stack_id)).map((e: { id: number }) => e.id)
  expect(ids).toHaveLength(2)
  const [bIn, aIn] = ids
  // Another station scans A out: A's IN now has later activity; B's IN still doesn't.
  const out = await request.post('/api/movements', { data: { request_id: await reserveId(request, 'shared-door'), station_id: 'shared-door', code: a.specimens[2].barcode_value,
    direction: 'OUT', scope: 'stack', acknowledge_early: true, expected_version: (await stackState(request, a.id)).version } })
  expect((await out.json()).status).toBe('committed')
  const activity = await rows(page)
  await expect(minus(activity, aIn)).toBeDisabled()
  await expect(minus(activity, bIn)).toBeEnabled()
  await minus(activity, bIn).tap()
  await expect(activity.locator(`[data-event-id="${bIn}"]`)).toContainText('Undone')
  expect((await stackState(request, b.id)).inside_count).toBe(0)
  expect((await stackState(request, a.id)).finished_count).toBe(3)
})
