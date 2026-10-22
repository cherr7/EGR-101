import { validScannerCode } from './scannerCapture'

/** Scans read on the Pi by input-bridge.py (evdev scanner, optional NFC hat), streamed over same-origin SSE. */
export type InputBridgeStatus = { connected: boolean; scanner: string; scannerName: string; nfc: string }
type Message = { kind?: string; code?: unknown; message?: unknown; at?: unknown; scanner?: unknown; scanner_name?: unknown; nfc?: unknown }
// A scan that sat in a stalled connection is no longer the operator's current action.
const MAX_AGE_MS = 3000

export function attachInputBridge(options: { url: string; enabled: () => boolean; onScan: (value: string) => void; onReject: (message: string) => void; onBlocked: () => void; onStatus: (status: InputBridgeStatus) => void }) {
  let status: InputBridgeStatus = { connected: false, scanner: 'unknown', scannerName: '', nfc: 'off' }
  const update = (changes: Partial<InputBridgeStatus>) => { status = { ...status, ...changes }; options.onStatus(status) }
  const source = new EventSource(options.url)
  source.onopen = () => update({ connected: true })
  source.onerror = () => update({ connected: false }) // EventSource reconnects on its own.
  source.onmessage = event => {
    let message: Message; try { message = JSON.parse(event.data) as Message } catch { return }
    if (message.kind === 'status') { update({ connected: true, scanner: String(message.scanner || 'unknown'), scannerName: String(message.scanner_name || ''), nfc: String(message.nfc || 'off') }); return }
    if (message.kind === 'reject') { options.onReject(typeof message.message === 'string' ? message.message : 'Unrecognized scanner input. Rescan the complete label.'); return }
    if (message.kind !== 'scan' || typeof message.code !== 'string' || typeof message.at !== 'number') return
    if (Date.now() - message.at * 1000 > MAX_AGE_MS) { options.onReject('That scan arrived late and was not used. Rescan the label.'); return }
    if (!validScannerCode(message.code)) { options.onReject('Unrecognized scanner input. Rescan the complete label.'); return }
    if (!options.enabled()) { options.onBlocked(); return }
    options.onScan(message.code)
  }
  return () => source.close()
}
