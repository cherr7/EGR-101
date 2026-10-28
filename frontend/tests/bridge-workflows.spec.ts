import { test, expect } from '@playwright/test'
import type { APIRequestContext, Page } from '@playwright/test'
import { randomUUID } from 'node:crypto'

async function createStack(request: APIRequestContext, count = 10) {
  const project = '00-DEMO-BRIDGE-' + randomUUID().slice(0, 8)
  const response = await request.post('/api/prep-drafts', { data: { rows: [{ project_number: project, material_ref: 'DEMO bridge fabric', test_code: 'D5034', preconditioning_code: 'AR → 5L', specimen_count: count }] } })
  expect(response.ok(), await response.text()).toBeTruthy()
  const draft = await response.json(), committed = await request.post('/api/prep-drafts/' + draft.id + '/commit', { data: {} })
  expect(committed.ok(), await committed.text()).toBeTruthy()
  const result = await committed.json()
  return (await request.get('/api/stacks/' + result.stack_ids[0])).json()
}
async function current(request: APIRequestContext, id: string) { return (await request.get('/api/stacks/' + id)).json() }

/** Only the Pi transport is simulated. Lookups, movements, session history and Undo use PostgreSQL through the real API. */
async function installBridge(page: Page, options: { scanner?: string; autoOpen?: boolean; pages?: number; failStatus?: boolean; clockAhead?: boolean } = {}) {
  await page.addInitScript(settings => {
    const control: any = {
      session_id: 'demo-process-a', scanner: settings.scanner || 'ok', scanner_name: 'DEMO Eyoyo USB', scanner_error: '', nfc: 'off', nfc_error: '', pages: settings.pages ?? 1,
      autoOpen: settings.autoOpen !== false, failStatus: settings.failStatus === true, delayStatus: 0, clockOffset: 900000, sources: [], sequence: 0,
      open() { const source = this.sources.at(-1); source.readyState = 1; source.onopen?.(new Event('open')) },
      disconnect() { const source = this.sources.at(-1); source.readyState = 0; source.onerror?.(new Event('error')) },
      send(message: Record<string, unknown>) { const source = this.sources.at(-1); if (!source.closed) source.onmessage?.(new MessageEvent('message', { data: JSON.stringify(message) })) },
    }
    ;(window as any).__bridge = control
    ;(window as any).fabricStationConfig = { stationId: 'shared-door', roomId: 'shared', roomName: 'Shared conditioning room', inputBridge: true, fixed: false }
    const nativeFetch = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/station/input/status')) return nativeFetch(input, init)
      if (control.failStatus) throw new TypeError('DEMO stopped local input service')
      if (control.delayStatus) await new Promise<void>((resolve, reject) => { const timer = setTimeout(resolve, control.delayStatus); init?.signal?.addEventListener('abort', () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')) }, { once: true }) })
      return new Response(JSON.stringify({ session_id: control.session_id, now_ms: performance.now() + control.clockOffset, scanner: control.scanner, scanner_name: control.scanner_name, scanner_error: control.scanner_error, nfc: control.nfc, nfc_error: control.nfc_error, pages: control.pages }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }
    ;(window as any).EventSource = class {
      onopen: ((event: Event) => void) | null = null
      onerror: ((event: Event) => void) | null = null
      onmessage: ((event: MessageEvent) => void) | null = null
      readyState = 0; closed = false
      constructor(public url: string) { control.sources.push(this); if (control.autoOpen) queueMicrotask(() => control.open()) }
      close() { this.closed = true; this.readyState = 2 }
    }
    if (settings.clockAhead) { const original = Date.now.bind(Date); Date.now = () => original() + 72 * 3600000 }
  }, options)
}
async function openKiosk(page: Page, ready = true) {
  await page.goto('/#kiosk')
  await expect(page.getByText('Online', { exact: true })).toBeVisible()
  if (ready) await expect(page.locator('.readiness-area')).toHaveAttribute('data-input-ready', 'true')
}
async function frame(page: Page, value: string, overrides: Record<string, unknown> = {}) {
  await page.evaluate(({ value, overrides }) => {
    const bridge = (window as any).__bridge
    bridge.send({ kind: 'scan', session_id: bridge.session_id, id: ++bridge.sequence, at_ms: performance.now() + bridge.clockOffset, code: value, ...overrides })
  }, { value, overrides })
}
async function status(page: Page, updates: Record<string, unknown>) {
  await page.evaluate(updates => { const bridge = (window as any).__bridge; Object.assign(bridge, updates); bridge.send({ kind: 'status', ...bridge, sources: undefined, now_ms: performance.now() + bridge.clockOffset }) }, updates)
}

// These are separate from keyboard-wedge regressions so the production Pi path is exercised directly.
test('direct USB stream records an unfocused visible 20-piece stack, repeat and exact one-tap Undo', async ({ page, request }) => {
  const stack = await createStack(request, 20)
  await installBridge(page); await openKiosk(page)
  await page.evaluate(() => { Object.defineProperty(document, 'hasFocus', { configurable: true, value: () => false }); window.dispatchEvent(new Event('blur')) })
  await frame(page, stack.specimens[8].barcode_value)
  await expect(page.getByRole('heading', { name: 'IN confirmed', exact: true })).toBeVisible()
  await expect(page.locator('.readiness-description')).toContainText('20 specimens')
  const entered = await current(request, stack.id)
  await frame(page, stack.specimens[0].barcode_value); await expect(page.getByRole('heading', { name: 'Already recorded', exact: true })).toBeVisible()
  expect((await current(request, stack.id)).specimens.map((s: any) => s.ENTRY)).toEqual(entered.specimens.map((s: any) => s.ENTRY))
  await page.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Transaction undone', exact: true })).toBeVisible()
  const result = await current(request, stack.id)
  expect(result.inside_count).toBe(0); expect(result.events.filter((event: any) => event.kind === 'undo')).toHaveLength(1)
})

test('direct stream preserves numbered partial checkout, early override and remaining-stack checkout', async ({ page, request }) => {
  const stack = await createStack(request)
  await installBridge(page); await openKiosk(page); await frame(page, stack.specimens[0].barcode_value)
  await expect(page.getByRole('heading', { name: 'IN confirmed', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'OUT', exact: true }).click(); await page.getByRole('button', { name: 'Individual' }).click()
  await frame(page, stack.specimens[0].barcode_value)
  const selection = page.getByRole('dialog', { name: 'Choose the specimens moving' })
  await expect(selection).toBeVisible(); await selection.getByRole('checkbox', { name: /^03/ }).check(); await selection.getByRole('checkbox', { name: /^05/ }).check()
  await frame(page, stack.specimens[9].barcode_value); expect((await current(request, stack.id)).inside_count).toBe(10)
  await selection.getByRole('button', { name: 'Record 3 OUT' }).click()
  const early = page.getByRole('dialog', { name: 'Early checkout' }); await expect(early).toBeVisible()
  await frame(page, stack.specimens[9].barcode_value); expect((await current(request, stack.id)).inside_count).toBe(10)
  await early.getByRole('button', { name: 'Override & record OUT' }).click(); await expect(page.locator('.readiness-description')).toContainText('3 specimens')
  expect((await current(request, stack.id)).specimens.filter((s: any) => s.EXIT).map((s: any) => s.number)).toEqual([1, 3, 5])
  await expect(page.getByRole('button', { name: 'Individual', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('button', { name: 'Stack', exact: true }).click()
  await frame(page, stack.specimens[0].barcode_value); await expect(early).toBeVisible(); await early.getByRole('button', { name: 'Override & record OUT' }).click()
  await expect(page.locator('.readiness-description')).toContainText('7 specimens'); expect((await current(request, stack.id)).finished_count).toBe(10)
  await page.getByRole('button', { name: 'IN', exact: true }).click(); await frame(page, stack.specimens[0].barcode_value)
  await expect(page.getByRole('heading', { name: 'Already recorded', exact: true })).toBeVisible()
  expect((await current(request, stack.id)).specimens.every((s: any) => s.sessions.length === 1)).toBe(true)
})

test('lost direct-scan response reconciles after reload and accepts no intervening stream input', async ({ page, request }) => {
  const stack = await createStack(request, 3), other = await createStack(request, 2)
  let hold = true, lost = false
  await page.route('**/api/movements', async route => { if (lost) return route.continue(); lost = true; const response = await route.fetch(); expect(response.ok()).toBeTruthy(); await route.abort('failed') })
  await page.route('**/api/movement-requests/*', async route => { if (hold) return route.abort('failed'); await route.continue() })
  await installBridge(page); await openKiosk(page); await frame(page, stack.specimens[0].barcode_value)
  await expect(page.getByRole('heading', { name: 'Confirming scan…' })).toBeVisible(); await frame(page, other.specimens[0].barcode_value)
  expect((await current(request, other.id)).inside_count).toBe(0)
  hold = false; await page.reload(); await expect(page.getByRole('heading', { name: 'IN confirmed', exact: true })).toBeVisible()
  expect((await current(request, stack.id)).events.filter((event: any) => event.kind === 'movement')).toHaveLength(1)
})

test('lost direct-input Undo survives reload and does not repeat the original transaction', async ({ page, request }) => {
  const stack = await createStack(request, 4); let hold = false, undoPosts = 0
  await installBridge(page); await openKiosk(page); await frame(page, stack.specimens[0].barcode_value)
  await expect(page.getByRole('heading', { name: 'IN confirmed', exact: true })).toBeVisible()
  await page.route('**/api/transactions/*/undo', async route => { undoPosts++; const response = await route.fetch(); expect(response.ok()).toBeTruthy(); hold = true; await route.abort('failed') })
  await page.route(/\/api\/transactions\/[0-9a-f-]+$/, async route => { if (hold) return route.abort('failed'); await route.continue() })
  await page.getByRole('button', { name: 'Undo' }).click(); await expect(page.getByRole('heading', { name: 'Confirming undo…' })).toBeVisible()
  await expect.poll(() => hold).toBe(true)
  hold = false; await page.reload(); await expect(page.getByRole('heading', { name: 'Transaction undone', exact: true })).toBeVisible()
  const result = await current(request, stack.id); expect(result.inside_count).toBe(0); expect(result.events.filter((event: any) => event.kind === 'undo')).toHaveLength(1); expect(undoPosts).toBe(1)
})

test('monotonic live frames ignore wall-clock skew, wrong process, duplicate sequence, stale/future and malformed payloads', async ({ page, request }) => {
  const first = await createStack(request, 2), second = await createStack(request, 2)
  await installBridge(page, { clockAhead: true }); await openKiosk(page)
  let lookups = 0; page.on('request', event => { if (event.method() === 'POST' && event.url().endsWith('/api/lookup')) lookups++ })
  await frame(page, first.specimens[0].barcode_value, { session_id: 'previous-process' })
  await frame(page, first.specimens[0].barcode_value, { at_ms: -900000 })
  await frame(page, first.specimens[0].barcode_value, { at_ms: 999999999999 })
  await frame(page, '0x1234'); await frame(page, '0b111'); await frame(page, '12345-6')
  expect((await current(request, first.id)).inside_count).toBe(0); expect(lookups).toBe(0)
  await frame(page, ']d1' + first.specimens[0].barcode_value)
  await expect(page.getByRole('heading', { name: 'IN confirmed', exact: true })).toBeVisible()
  const used = await page.evaluate(() => (window as any).__bridge.sequence)
  await frame(page, second.specimens[0].barcode_value, { id: used }); expect((await current(request, second.id)).inside_count).toBe(0); expect(lookups).toBe(1)
  await status(page, { session_id: 'demo-process-b' }); await expect(page.locator('.readiness-area')).toHaveAttribute('data-input-ready', 'true')
  await frame(page, second.specimens[0].barcode_value, { id: 1 }); await expect(page.locator('.last-stack')).toContainText(second.project_number)
  await expect.poll(async () => (await current(request, second.id)).inside_count).toBe(2)
})

test('reader loss is separate from database health and another active page cannot use keyboard fallback', async ({ page, request }) => {
  const stack = await createStack(request, 2)
  await installBridge(page); await openKiosk(page)
  await status(page, { scanner: 'missing', scanner_error: 'DEMO scanner unplugged' })
  await expect(page.getByRole('heading', { name: 'Connect scanner', exact: true })).toBeVisible(); await expect(page.getByText('Online', { exact: true })).toBeVisible()
  await frame(page, stack.specimens[0].barcode_value); expect((await current(request, stack.id)).inside_count).toBe(0)
  await page.evaluate(() => { const bridge = (window as any).__bridge; bridge.pages = 1; bridge.disconnect() })
  await expect(page.getByRole('heading', { name: 'Scanner in use', exact: true })).toBeVisible()
  await page.keyboard.type(stack.specimens[0].barcode_value); await page.keyboard.press('Enter'); expect((await current(request, stack.id)).inside_count).toBe(0)
  // A subsequent failed status request must not forget already known consumer ownership.
  await page.evaluate(() => { const bridge = (window as any).__bridge; bridge.failStatus = true; bridge.disconnect() })
  await expect(page.getByRole('heading', { name: 'Scanner in use', exact: true })).toBeVisible()
  await page.keyboard.type(stack.specimens[0].barcode_value); await page.keyboard.press('Enter'); expect((await current(request, stack.id)).inside_count).toBe(0)
  await page.evaluate(() => { const bridge = (window as any).__bridge; bridge.failStatus = false; bridge.scanner = 'ok'; bridge.open() })
  await expect(page.locator('.readiness-area')).toHaveAttribute('data-input-ready', 'true'); await frame(page, stack.specimens[0].barcode_value); await expect(page.getByRole('heading', { name: 'IN confirmed', exact: true })).toBeVisible()
})

test('hidden direct-input page discards scans without later replay', async ({ page, request }) => {
  const stack = await createStack(request, 2)
  await installBridge(page); await openKiosk(page)
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange')) })
  await frame(page, stack.specimens[0].barcode_value); expect((await current(request, stack.id)).inside_count).toBe(0)
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, get: () => false }); document.dispatchEvent(new Event('visibilitychange')) })
  expect((await current(request, stack.id)).inside_count).toBe(0)
  await frame(page, stack.specimens[0].barcode_value); await expect(page.getByRole('heading', { name: 'IN confirmed', exact: true })).toBeVisible()
})

test('database outage accepts no direct input and reconnect requires a fresh scan', async ({ page, request }) => {
  const stack = await createStack(request, 2)
  await installBridge(page); await openKiosk(page)
  await page.route('**/api/health', route => route.abort('failed'))
  await expect(page.getByRole('heading', { name: 'Offline', exact: true })).toBeVisible(); await frame(page, stack.specimens[0].barcode_value)
  expect((await current(request, stack.id)).inside_count).toBe(0); await expect(page.locator('.readiness-area.confirmed')).toHaveCount(0)
  await page.unroute('**/api/health'); await expect(page.getByText('Online', { exact: true })).toBeVisible()
  expect((await current(request, stack.id)).inside_count).toBe(0)
  await frame(page, stack.specimens[0].barcode_value); await expect(page.getByRole('heading', { name: 'IN confirmed', exact: true })).toBeVisible()
})

test('unreachable service permits focused keyboard fallback and calibration failures recover automatically', async ({ page, request }) => {
  const stack = await createStack(request, 2), second = await createStack(request, 2)
  await installBridge(page, { autoOpen: false, failStatus: true, pages: 0 }); await openKiosk(page, false)
  await page.evaluate(() => (window as any).__bridge.disconnect())
  await expect(page.locator('.readiness-area')).toHaveAttribute('data-input-ready', 'true')
  await page.keyboard.type(stack.specimens[0].barcode_value); await page.keyboard.press('Enter'); await expect(page.getByRole('heading', { name: 'IN confirmed', exact: true })).toBeVisible()
  await page.evaluate(() => { const bridge = (window as any).__bridge; bridge.failStatus = false; bridge.delayStatus = 1100; bridge.open() })
  await expect(page.getByRole('heading', { name: 'Connect scanner', exact: true })).toBeVisible()
  await frame(page, second.specimens[0].barcode_value); expect((await current(request, second.id)).inside_count).toBe(0)
  await expect(page.locator('.readiness-description')).toHaveText('Check the USB connection')
  await page.evaluate(() => { (window as any).__bridge.delayStatus = 0 })
  await expect(page.locator('.readiness-area')).toHaveAttribute('data-input-ready', 'true')
  await frame(page, second.specimens[0].barcode_value); await expect(page.locator('.last-stack')).toContainText(second.project_number); expect((await current(request, second.id)).inside_count).toBe(2)
})

test('keyboard fallback discards a frame begun in blocked direct mode or before a direction change', async ({ page, request }) => {
  const stack = await createStack(request, 2), payload = stack.specimens[0].barcode_value
  await installBridge(page); await openKiosk(page)
  await page.keyboard.type(payload.slice(0, 18))
  await page.evaluate(() => { const bridge = (window as any).__bridge; bridge.failStatus = true; bridge.pages = 0; bridge.disconnect() })
  await expect(page.locator('.readiness-area')).toHaveAttribute('data-input-ready', 'true')
  await page.keyboard.type(payload.slice(18)); await page.keyboard.press('Enter')
  expect((await current(request, stack.id)).inside_count).toBe(0)
  await page.keyboard.type(payload.slice(0, 18)); await page.getByRole('button', { name: 'OUT', exact: true }).click()
  await page.keyboard.type(payload.slice(18)); await page.keyboard.press('Enter')
  const unchanged = await current(request, stack.id); expect(unchanged.inside_count).toBe(0); expect(unchanged.events.filter((event: any) => event.kind === 'movement')).toHaveLength(0)
  await page.getByRole('button', { name: 'IN', exact: true }).click(); await page.keyboard.type(payload); await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: 'IN confirmed', exact: true })).toBeVisible()
})

test('native EventSource retries HTTP unavailable and occupied responses without reloading or replaying input', async ({ page }) => {
  const { createServer } = await import('node:http')
  const streams = new Set<import('node:http').ServerResponse>()
  let attempts = 0, available = false
  const server = createServer((request, response) => {
    response.setHeader('Access-Control-Allow-Origin', '*')
    response.setHeader('Cache-Control', 'no-store')
    const snapshot = { session_id: 'native-process', now_ms: performance.now(), scanner: 'ok', scanner_name: 'TEST native stream', nfc: 'off', pages: 1 }
    if (request.url === '/status') {
      response.writeHead(200, { 'Content-Type': 'application/json' }); response.end(JSON.stringify(snapshot))
      if (available) setTimeout(() => { for (const stream of streams) stream.write('data: ' + JSON.stringify({ kind: 'scan', session_id: 'native-process', id: 1, at_ms: performance.now(), code: '42' }) + '\n\n') }, 50)
      return
    }
    if (request.url !== '/events') { response.writeHead(404); response.end(); return }
    attempts++
    if (attempts === 1 || !available) { response.writeHead(attempts === 1 ? 503 : 409); response.end(); return }
    response.writeHead(200, { 'Content-Type': 'text/event-stream' })
    response.write('data: ' + JSON.stringify({ kind: 'status', ...snapshot }) + '\n\n')
    streams.add(response); response.on('close', () => streams.delete(response))
  })
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
  const origin = 'http://127.0.0.1:' + (server.address() as import('node:net').AddressInfo).port
  try {
    await page.goto('/#overview')
    await page.evaluate(async origin => {
      const { attachInputBridge } = await import('/src/inputBridge.ts')
      const state = window as any
      state.__nativeScans = []; state.__nativeInput = null
      state.__nativeDispose = attachInputBridge({ url: origin + '/events', statusUrl: origin + '/status', enabled: () => true,
        onScan: (value: string) => state.__nativeScans.push(value), onReject: () => {}, onBlocked: () => {},
        onStatus: (value: unknown) => { state.__nativeInput = value } })
    }, origin)
    await expect.poll(() => attempts).toBeGreaterThanOrEqual(2)
    await expect.poll(() => page.evaluate(() => (window as any).__nativeInput?.busy)).toBe(true)
    expect(await page.evaluate(() => (window as any).__nativeScans)).toEqual([])
    available = true
    await expect.poll(() => page.evaluate(() => (window as any).__nativeInput?.ready)).toBe(true)
    await expect.poll(() => page.evaluate(() => (window as any).__nativeScans)).toEqual(['42'])
  } finally {
    await page.evaluate(() => (window as any).__nativeDispose?.()).catch(() => {})
    for (const stream of streams) stream.end()
    await new Promise<void>(resolve => { server.close(() => resolve()); server.closeAllConnections() })
  }
})
