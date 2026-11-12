import { validScannerCode } from './scannerCapture'

/** The Pi reads USB/NFC locally; only this page's active stream may use those scans. */
export type InputBridgeStatus = {
  connected: boolean; ready: boolean; fallback: boolean; busy: boolean
  scanner: string; scannerName: string; scannerError: string; nfc: string; nfcError: string; message: string; localIps: string[]
}
type Message = { kind?: string; session_id?: unknown; id?: unknown; code?: unknown; message?: unknown; at_ms?: unknown; now_ms?: unknown; scanner?: unknown; scanner_name?: unknown; scanner_error?: unknown; nfc?: unknown; nfc_error?: unknown; pages?: unknown; local_ips?: unknown }
const MAX_AGE_MS = 3000, MAX_CALIBRATION_RTT_MS = 1000, FUTURE_TOLERANCE_MS = 100
export const INITIAL_INPUT_STATUS: InputBridgeStatus = { connected: false, ready: false, fallback: false, busy: false, scanner: 'starting', scannerName: '', scannerError: '', nfc: 'off', nfcError: '', message: 'Connecting to scanner input…', localIps: [] }

export function attachInputBridge(options: { url: string; statusUrl?: string; enabled: () => boolean; onScan: (value: string) => void; onReject: (message: string) => void; onBlocked: () => void; onStatus: (status: InputBridgeStatus) => void }) {
  let status = { ...INITIAL_INPUT_STATUS }, alive = true, open = false, generation = 0
  let calibration: { session: string; offset: number; uncertainty: number } | null = null
  const seen = new Map<string, number>()
  let controller: AbortController | null = null
  const update = (changes: Partial<InputBridgeStatus>) => {
    status = { ...status, ...changes }
    status.ready = status.connected && (status.scanner === 'ok' || status.nfc === 'ok')
    if (alive) options.onStatus(status)
  }
  const sensors = (message: Message) => ({ scanner: typeof message.scanner === 'string' ? message.scanner : 'unknown', scannerName: typeof message.scanner_name === 'string' ? message.scanner_name : '', scannerError: typeof message.scanner_error === 'string' ? message.scanner_error : '', nfc: typeof message.nfc === 'string' ? message.nfc : 'off', nfcError: typeof message.nfc_error === 'string' ? message.nfc_error : '', localIps: Array.isArray(message.local_ips) ? message.local_ips.filter((ip): ip is string => typeof ip === 'string') : status.localIps })
  let source = new EventSource(options.url)
  const statusUrl = options.statusUrl || options.url.replace(/\/events(?:\?.*)?$/, '/status')
  async function synchronize(connected: boolean) {
    const current = ++generation
    controller?.abort(); const requestController = new AbortController(); controller = requestController
    clearTimeout(retry)
    const timeout = setTimeout(() => requestController.abort(), MAX_CALIBRATION_RTT_MS)
    const start = performance.now()
    try {
      const response = await fetch(statusUrl, { cache: 'no-store', signal: requestController.signal })
      if (!response.ok) throw new Error('Scanner service is unavailable.')
      const message = await response.json() as Message, end = performance.now()
      if (!alive || current !== generation) return
      if (!connected && message.pages === 1) update({ busy: true })
      if (typeof message.session_id !== 'string' || typeof message.now_ms !== 'number' || !Number.isFinite(message.now_ms) || end - start > MAX_CALIBRATION_RTT_MS) throw new Error('Scanner timing could not be verified.')
      const busy = !connected && message.pages === 1
      if (connected && open) {
        calibration = { session: message.session_id, offset: (start + end) / 2 - message.now_ms, uncertainty: (end - start) / 2 }
        // Each bridge start has a new session ID (bridge-1, bridge-2, ...). Keep only a few former IDs so reconnects cannot replay scans.
        if (!seen.has(message.session_id)) seen.set(message.session_id, 0)
        if (seen.size > 8) seen.delete(seen.keys().next().value!)
      }
      update({ ...sensors(message), connected: connected && open, fallback: false, busy, message: busy ? 'Scanner input is active in another page. Close that page to resume here.' : connected && open ? '' : 'Reconnecting to scanner input…' })
    } catch {
      if (!alive || current !== generation) return
      calibration = null
      // Deliberate keyboard fallback only when the local service is unreachable, never while another stream owns it.
      update({ connected: false, fallback: !connected && !status.busy, message: connected ? 'Scanner timing could not be verified. Reconnecting…' : status.busy ? 'Scanner input is active in another page. Close that page to resume here.' : 'Direct input unavailable. Focus this screen for keyboard scanner input.' })
      if (connected) retry = setTimeout(() => { if (alive && open) void synchronize(true) }, 1000)
    } finally { clearTimeout(timeout) }
  }
  let retry: ReturnType<typeof setTimeout> | undefined, reconnectTimer: ReturnType<typeof setTimeout> | undefined
  function installHandlers() {
    const connection = source
    connection.onopen = () => {
      if (!alive || connection !== source) return
      clearTimeout(reconnectTimer)
      open = true; calibration = null
      update({ connected: false, fallback: false, busy: false, message: 'Checking scanner input…' })
      void synchronize(true)
    }
    connection.onerror = () => {
      if (!alive || connection !== source) return
      open = false; calibration = null
      update({ connected: false, fallback: false, message: 'Reconnecting to scanner input…' })
      void synchronize(false)
      // HTTP 409/502/503 permanently close native EventSource. Socket failures
      // reconnect natively, but a CLOSED stream needs a fresh instance.
      if (connection.readyState === 2) {
        clearTimeout(reconnectTimer)
        reconnectTimer = setTimeout(() => {
          if (!alive || connection !== source) return
          generation++; controller?.abort(); calibration = null
          update({ connected: false, fallback: false, message: 'Reconnecting to scanner input…' })
          connection.close(); source = new EventSource(options.url); installHandlers()
        }, 1000)
      }
    }
    connection.onmessage = event => {
      if (!alive || connection !== source) return
      let message: Message; try { message = JSON.parse(event.data) as Message } catch { return }
      if (calibration && message.kind === 'status' && message.session_id !== calibration.session) { calibration = null; update({ connected: false, fallback: false, message: 'Scanner service restarted. Checking live input…' }); void synchronize(true); return }
      if (!calibration || message.session_id !== calibration.session) return
      if (message.kind === 'status') { update({ ...sensors(message), connected: open, fallback: false, busy: false }); return }
      if (message.kind === 'consumer_overflow') { calibration = null; update({ connected: false, message: 'Scanner delivery stalled. Reconnecting; rescan the label.' }); options.onReject(status.message); return }
      if (!['scan', 'reject', 'delivery_error'].includes(message.kind || '')) return
      if (typeof message.id !== 'number' || !Number.isSafeInteger(message.id) || message.id <= (seen.get(calibration.session) || 0)) return
      seen.set(calibration.session, message.id)
      if (typeof message.at_ms !== 'number' || !Number.isFinite(message.at_ms)) return
      const age = performance.now() - (message.at_ms + calibration.offset)
      if (age + calibration.uncertainty > MAX_AGE_MS || age < -FUTURE_TOLERANCE_MS - calibration.uncertainty) { options.onReject('That scan arrived outside the live input window and was not used. Rescan the label.'); return }
      if (!status.ready || !options.enabled()) { options.onBlocked(); return }
      if (message.kind !== 'scan') { options.onReject(typeof message.message === 'string' ? message.message : 'Unrecognized scanner input. Rescan the complete label.'); return }
      if (typeof message.code !== 'string' || !validScannerCode(message.code)) { options.onReject('Unrecognized scanner input. Rescan the complete label.'); return }
      options.onScan(message.code)
    }
  }
  installHandlers()
  options.onStatus(status)
  return () => { alive = false; generation++; controller?.abort(); clearTimeout(retry); clearTimeout(reconnectTimer); source.close() }
}
