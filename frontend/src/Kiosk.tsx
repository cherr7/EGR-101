import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError, elapsed, entryOf, exitOf, formatTime, isReady, post, requestId } from './api'
import type { AuditEvent, Config, Movement, MovementResult, Stack } from './types'
import { Modal } from './App'
import { attachScanner } from './scannerCapture'
import './kiosk.css'

const PENDING_KEY = 'fabric-tracker.pending-movement.v2', LAST_KEY = 'fabric-tracker.last-movement.v2', UNDO_KEY = 'fabric-tracker.pending-undo.v1'
function stored<T>(key: string): T | null { try { return JSON.parse(localStorage.getItem(key) || 'null') as T | null } catch { return null } }
type Last = { event_id: string; direction: string; count: number; stack_id: string; version?: number; recorded_at?: string; station_id?: string; project_number?: string; material_ref?: string }
type PendingUndo = Last & { body: { expected_version?: number }; submitted_at: string }
type Selection = { stack: Stack; code: string; direction: 'IN' | 'OUT'; ids: string[] }
type Acknowledgment = { stack: Stack; request: Movement; result: MovementResult }

const RESULT_HOLD_MS = 12000
const ROOM_CHOICES = [
  { stationId: 'ppe-door', roomId: 'ppe', label: 'PPE', name: 'PPE conditioning room' },
  { stationId: 'shared-door', roomId: 'shared', label: 'Shared', name: 'Shared conditioning room' },
  { stationId: 'footwear-door', roomId: 'footwear', label: 'Footwear', name: 'Footwear conditioning room' },
]
type RecentEvent = AuditEvent & { project_number?: string; material_ref?: string; test_code?: string; preconditioning_code?: string }

export default function Kiosk({ config, healthy }: { config: Config; healthy: boolean }) {
  const stationConfig = (window as unknown as { fabricStationConfig?: { stationId?: string; fixed?: boolean; stationName?: string; roomId?: string; roomName?: string } }).fabricStationConfig
  const [station, setStation] = useState(() => stationConfig ? localStorage.getItem('fabric-tracker.station') || stationConfig.stationId || 'shared-door' : new URLSearchParams(location.search).get('station') || localStorage.getItem('fabric-tracker.station') || 'shared-door')
  const [direction, setDirection] = useState<'IN' | 'OUT'>(() => localStorage.getItem('fabric-tracker.direction') === 'OUT' ? 'OUT' : 'IN')
  const [scope, setScope] = useState<'stack' | 'specimen'>('stack'), [busy, setBusy] = useState(false), [pending, setPending] = useState<Movement | null>(() => stored<Movement>(PENDING_KEY))
  const [pendingUndo, setPendingUndo] = useState<PendingUndo | null>(() => stored<PendingUndo>(UNDO_KEY)), [undoMessage, setUndoMessage] = useState('')
  const [selection, setSelection] = useState<Selection | null>(null), [acknowledge, setAcknowledge] = useState<Acknowledgment | null>(null)
  const [sound, setSound] = useState(() => localStorage.getItem('fabric-tracker.sound') === 'on')
  const audio = useRef<AudioContext | null>(null)
  const [detail, setDetail] = useState<Stack | null>(null), [events, setEvents] = useState<AuditEvent[]>([]), [last, setLast] = useState<Last | null>(() => stored<Last>(LAST_KEY))
  const [result, setResult] = useState<MovementResult | null>(null), [resultDirection, setResultDirection] = useState<'IN' | 'OUT'>('IN'), [green, setGreen] = useState(false), [error, setError] = useState(''), [focused, setFocused] = useState(document.hasFocus()), [latency, setLatency] = useState<number | null>(null)
  const inFlight = useRef(false), active = useRef(true), greenTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined), historyRef = useRef<HTMLDivElement>(null)
  const currentRoomId = config.stations.find(s => s.id === station)?.room_id || ROOM_CHOICES.find(r => r.stationId === station)?.roomId || stationConfig?.roomId
  const room = config.rooms.find(r => r.id === currentRoomId)
  const roomChoice = ROOM_CHOICES.find(r => r.stationId === station)
  const eventRoom = useRef(currentRoomId); eventRoom.current = currentRoomId
  const blocked = busy || !!pendingUndo || !!pending || !!selection || !!acknowledge
  useEffect(() => { active.current = true; return () => { active.current = false; clearTimeout(greenTimer.current) } }, [])
  useEffect(() => { if (config.stations.length && !config.stations.some(s => s.id === station)) setStation(config.stations.find(s => s.id === 'shared-door')?.id || config.stations[0].id) }, [config.stations, station])
  useEffect(() => { localStorage.setItem('fabric-tracker.station', station) }, [station]); useEffect(() => { localStorage.setItem('fabric-tracker.direction', direction) }, [direction])
  const refreshEvents = useCallback(async () => {
    if (!currentRoomId) return
    try {
      const value = await api<{ items: RecentEvent[] }>('/events?limit=40&room_id=' + encodeURIComponent(currentRoomId))
      if (active.current && eventRoom.current === currentRoomId) setEvents(value.items)
    } catch { /* Availability is owned by the health check. */ }
  }, [currentRoomId])
  useEffect(() => {
    setEvents([])
    if (!healthy) return
    void refreshEvents()
    const timer = setInterval(() => void refreshEvents(), 5000)
    return () => clearInterval(timer)
  }, [healthy, refreshEvents])
  const tone = useCallback((warning = false) => {
    if (!sound) return
    try {
      if (!audio.current) audio.current = new AudioContext()
      const context = audio.current
      void context.resume().then(() => {
        if (context.state !== 'running') return
        const start = context.currentTime
        for (const [index, frequency] of (warning ? [330, 247] : [659, 880]).entries()) {
          const oscillator = context.createOscillator(), volume = context.createGain()
          oscillator.frequency.value = frequency
          oscillator.connect(volume); volume.connect(context.destination)
          volume.gain.setValueAtTime(0.0001, start + index * 0.09)
          volume.gain.exponentialRampToValueAtTime(0.09, start + index * 0.09 + 0.015)
          volume.gain.exponentialRampToValueAtTime(0.0001, start + index * 0.09 + 0.2)
          oscillator.start(start + index * 0.09); oscillator.stop(start + index * 0.09 + 0.21)
        }
      }).catch(() => {})
    } catch { /* Audio is optional; it never changes a movement result. */ }
  }, [sound])
  const accept = useCallback(async (value: MovementResult, request: Movement, loaded?: Stack) => {
    localStorage.removeItem(PENDING_KEY); setPending(null); setResult(value); setResultDirection(request.direction); setError('')
    let current = loaded
    if (value.stack_id) try { current = await api<Stack>(`/stacks/${value.stack_id}`) } catch { /* The movement response itself is authoritative. */ }
    if (!active.current) return
    if (current) setDetail(current)
    if (value.status === 'selection_required' && current) { setSelection({ stack: current, code: request.code, direction: request.direction, ids: [] }); setAcknowledge(null) }
    else if (value.status === 'acknowledgment_required' && current) { setAcknowledge({ request, stack: current, result: value }); tone(true); setSelection(null) }
    else { setSelection(null); setAcknowledge(null); if (value.status === 'committed') { const transaction = value.event_id ? { event_id: value.event_id, direction: request.direction, count: value.affected_count, stack_id: value.stack_id, version: value.version, recorded_at: value.recorded_at, station_id: request.station_id, project_number: current?.project_number, material_ref: current?.material_ref } : null; setLast(transaction); if (transaction) localStorage.setItem(LAST_KEY, JSON.stringify(transaction)); setScope('stack'); setGreen(true); tone(); clearTimeout(greenTimer.current); greenTimer.current = setTimeout(() => { if (active.current) { setGreen(false); setResult(null) } }, RESULT_HOLD_MS); void refreshEvents() } else if (value.status === 'already_recorded') { setScope('stack'); clearTimeout(greenTimer.current); greenTimer.current = setTimeout(() => { if (active.current) setResult(null) }, RESULT_HOLD_MS) } else if (value.status === 'rejected') setError(value.message) }
  }, [refreshEvents, tone])
  async function submit(request: Movement, loaded?: Stack) {
    if (inFlight.current || !healthy || stored<Movement>(PENDING_KEY) || stored<PendingUndo>(UNDO_KEY)) return
    clearTimeout(greenTimer.current)
    inFlight.current = true; setBusy(true); setGreen(false); setUndoMessage(''); setError(''); setResult(null); setLatency(null)
    try { localStorage.setItem(PENDING_KEY, JSON.stringify(request)); setPending(request) } catch { inFlight.current = false; setBusy(false); setError('This browser cannot preserve a pending request. Enable local storage before scanning.'); return }
    const start = performance.now()
    try { const value = await post<MovementResult>('/movements', request); if (active.current) { setLatency(Math.round(performance.now() - start)); await accept(value, request, loaded) } }
    catch (err) { if (!active.current) return; if (err instanceof ApiError && err.status < 500) { localStorage.removeItem(PENDING_KEY); setPending(null); setSelection(null); setAcknowledge(null); setError(err.message) } else setError('Response interrupted. This same request must be confirmed before another scan.') }
    finally { inFlight.current = false; if (active.current) setBusy(false) }
  }
  const recover = useCallback(async () => { const request = stored<Movement>(PENDING_KEY); if (!request || !healthy || inFlight.current) return; inFlight.current = true; setBusy(true); try { let value: MovementResult; try { value = await api<MovementResult>(`/movement-requests/${request.request_id}`, {}, 4500) } catch (err) { if (err instanceof ApiError && err.status === 404) value = await post<MovementResult>('/movements', request); else throw err } if (active.current) await accept(value, request) } catch { if (active.current) setError('Confirmation pending. The same request will be checked again when connected.') } finally { inFlight.current = false; if (active.current) setBusy(false) } }, [healthy, accept])
  useEffect(() => { if (!pending || !healthy) return; void recover(); const timer = setInterval(() => void recover(), 5000); return () => clearInterval(timer) }, [pending, healthy, recover])
  async function scan(code: string) {
    if (!healthy || blocked || inFlight.current) return
    clearTimeout(greenTimer.current)
    inFlight.current = true; setBusy(true); setError(''); setUndoMessage(''); setResult(null); setGreen(false)
    try { const stack = await post<Stack>('/lookup', { code }); if (!active.current) return; setDetail(stack); if (scope === 'specimen') { const scanned = stack.specimens.find(s => s.id === stack.scanned_specimen_id); const eligible = scanned && (direction === 'IN' ? !entryOf(scanned) : scanned.state === 'IN'); setSelection({ stack, code, direction, ids: eligible ? [scanned!.id] : [] }); return } inFlight.current = false; await submit({ request_id: requestId(), station_id: station, code, direction, scope: 'stack', expected_version: stack.version }, stack) }
    catch (err) { if (active.current) setError((err as Error).message) } finally { inFlight.current = false; if (active.current) setBusy(false) }
  }
  const scanRef = useRef(scan), stateRef = useRef({ healthy, blocked }); scanRef.current = scan; stateRef.current = { healthy, blocked }
  useEffect(() => { const dispose = attachScanner({ enabled: () => stateRef.current.healthy && !stateRef.current.blocked && !inFlight.current, onScan: code => void scanRef.current(code), onReject: message => { if (!stateRef.current.blocked) setError(message) }, onBlocked: () => { /* Reject scans while a popup or pending transaction is open; never queue them. */ }, onFocusChange: () => setFocused(document.hasFocus() && !document.hidden) }); (window as unknown as { fabricScannerCaptureReady: boolean }).fabricScannerCaptureReady = true; return () => { dispose(); (window as unknown as { fabricScannerCaptureReady: boolean }).fabricScannerCaptureReady = false } }, [])
  const finishUndo = useCallback(async (intent: PendingUndo) => {
    localStorage.removeItem(UNDO_KEY); localStorage.removeItem(LAST_KEY); setPendingUndo(null); setLast(null); setGreen(false); setResult(null); setError(''); setUndoMessage(`Undo confirmed · ${intent.count} specimens restored.`)
    try { const stack = await api<Stack>(`/stacks/${intent.stack_id}`); if (active.current) setDetail(stack) } catch { /* The undo response or persisted audit event already confirmed the operation. */ }
    void refreshEvents()
  }, [refreshEvents])
  const undoStatus = async (intent: PendingUndo) => { const value = await api<{ items: AuditEvent[] }>(`/transactions?stack_id=${encodeURIComponent(intent.stack_id)}`, {}, 4500); const target = value.items.find(event => event.id === intent.event_id); if (!target) throw new ApiError(404, 'The original transaction could not be found.'); return target }
  const reconcileUndo = useCallback(async () => {
    const intent = stored<PendingUndo>(UNDO_KEY); if (!intent || !healthy || inFlight.current) return
    inFlight.current = true; setBusy(true)
    const reject = (message: string) => { localStorage.removeItem(UNDO_KEY); setPendingUndo(null); setError(message) }
    try {
      const status = await undoStatus(intent)
      if (status.undone) { await finishUndo(intent); return }
      if (status.can_undo === false) { reject('Undo was not applied: a specimen has intervening activity. Review the transaction log.'); return }
      await post(`/transactions/${intent.event_id}/undo`, intent.body)
      if (active.current) await finishUndo(intent)
    } catch (err) {
      if (!active.current) return
      if (err instanceof ApiError && err.status < 500) {
        try { const status = await undoStatus(intent); if (status.undone) await finishUndo(intent); else reject(err.message) }
        catch (checkError) { if (checkError instanceof ApiError && checkError.status === 404) reject(checkError.message); else setError('Undo confirmation is pending. This transaction will be checked again before scanning resumes.') }
      } else setError('Undo confirmation is pending. This transaction will be checked again before scanning resumes.')
    } finally { inFlight.current = false; if (active.current) setBusy(false) }
  }, [healthy, finishUndo])
  useEffect(() => { if (!pendingUndo || !healthy) return; void reconcileUndo(); const timer = setInterval(() => void reconcileUndo(), 5000); return () => clearInterval(timer) }, [pendingUndo, healthy, reconcileUndo])
  async function undo() {
    if (!last || !healthy || inFlight.current || stored<PendingUndo>(UNDO_KEY)) return
    const intent: PendingUndo = { ...last, body: { expected_version: last.version }, submitted_at: new Date().toISOString() }
    try { localStorage.setItem(UNDO_KEY, JSON.stringify(intent)) } catch { setError('This browser cannot preserve a pending undo. Enable local storage before continuing.'); return }
    setPendingUndo(intent); setGreen(false); setResult(null); setUndoMessage(''); setError('')
    await reconcileUndo()
  }
  const title = !healthy ? 'NOT AVAILABLE' : !focused ? 'Tap this screen to resume' : pendingUndo ? 'Confirming undo…' : pending ? 'Confirming transaction…' : busy ? 'Reading label…' : green && result ? `${resultDirection} confirmed` : result?.status === 'already_recorded' ? 'Already recorded' : undoMessage ? 'Transaction undone' : error ? 'Check the message below' : `Ready for ${direction}`
  const eligible = selection?.stack.specimens.filter(s => selection.direction === 'IN' ? !entryOf(s) : s.state === 'IN') || []
  const early = acknowledge?.stack.specimens.filter(s => s.state === 'IN' && !isReady(s) && (acknowledge.request.scope === 'stack' || acknowledge.request.selected_specimen_ids?.includes(s.id))) || []

  function clearFeedback() {
    clearTimeout(greenTimer.current)
    setGreen(false); setResult(null); setError(''); setUndoMessage('')
  }
  function chooseRoom(value: string) {
    if (blocked || value === station) return
    clearFeedback(); setStation(value); setDetail(null); setLast(null)
    localStorage.setItem('fabric-tracker.station', value)
    localStorage.removeItem(LAST_KEY)
    const url = new URL(location.href); url.searchParams.set('station', value)
    history.replaceState(null, '', url.pathname + url.search + url.hash)
  }
  function chooseDirection(value: 'IN' | 'OUT') {
    if (blocked) return
    clearFeedback(); setDirection(value)
  }
  const latest = events.filter(event => ['movement', 'undo', 'modification'].includes(event.kind)) as RecentEvent[]
  return (
    <div className="kiosk-shell scanner-v2">
      <header className="kiosk-header">
        <div className="scanner-room-title">
          <span className="eyebrow">ROOM SCANNER</span>
          <h1>{room?.name || roomChoice?.name || stationConfig?.roomName || 'Room station'}</h1>
          <small>24-hour conditioning</small>
        </div>
        <nav className="scanner-rooms" aria-label="Conditioning room">
          {ROOM_CHOICES.map(choice => <button key={choice.stationId} type="button"
            className={station === choice.stationId ? 'selected' : ''}
            aria-pressed={station === choice.stationId} disabled={blocked}
            onClick={() => chooseRoom(choice.stationId)}>{choice.label}</button>)}
        </nav>
        <div className="scanner-connection">
          <span className={'connection ' + (healthy ? 'good' : 'bad')} role="status"><i />{healthy ? 'Database connected' : 'NOT AVAILABLE'}</span>
          <button type="button" className="scanner-sound" aria-pressed={sound}
            aria-label={sound ? 'Mute scanner sound' : 'Enable scanner sound'}
            onClick={() => { const value = !sound; setSound(value); localStorage.setItem('fabric-tracker.sound', value ? 'on' : 'off') }}>
            {sound ? 'Sound on' : 'Sound off'}
          </button>
        </div>
      </header>

      <div className="kiosk-middle">
        <section className={['readiness-area', green && healthy ? 'confirmed' : '', !healthy ? 'unavailable' : '', pending || pendingUndo ? 'confirming' : ''].join(' ')} aria-live="polite">
          <div className="scanner-scope" role="group" aria-label="Movement scope">
            <span>Scan moves</span>
            <button type="button" aria-pressed={scope === 'stack'} disabled={blocked}
              onClick={() => { clearFeedback(); setScope('stack') }}>Whole stack</button>
            <button type="button" aria-label="Individual specimens" aria-pressed={scope === 'specimen'} disabled={blocked}
              onClick={() => { clearFeedback(); setScope('specimen') }}>Choose specimens</button>
          </div>
          <div className="scanner-result">
            <div className="readiness-icon">{!healthy ? '!' : pending || pendingUndo || busy ? '◷' : green ? '✓' : direction === 'IN' ? '↘' : '↗'}</div>
            <h2>{title}</h2>
            <p className="readiness-description">
              {!healthy ? 'Nothing is recorded until the database reconnects.'
                : pendingUndo ? 'Checking the same Undo. New scans are paused until its result is known.'
                : pending ? 'Checking this transaction. New scans are paused until its result is known.'
                : green && result ? result.affected_count + ' specimens · ' + formatTime(result.recorded_at)
                : result?.status === 'already_recorded' ? result.message
                : scope === 'specimen' ? 'Scan a label, then choose the numbered specimens.'
                : 'Scan any specimen label. The eligible stack records automatically.'}
            </p>
            {green && latency !== null && <small className="latency">Database commit response {latency} ms · Ready for the next scan</small>}
          </div>
          {detail && <div className="last-stack">
            <div><span>IDENTIFIED STACK {detail.is_demo && '· DEMO'}</span>
              <h3>{detail.project_number}<em>/</em>{detail.material_ref}</h3>
              <p>{detail.test_code} · {detail.preconditioning_code}</p></div>
            <div className="stack-counts"><span><b>{detail.specimens.filter(s => s.state === 'IN').length}</b> inside</span>
              <span><b>{detail.specimens.filter(s => !!exitOf(s)).length}</b> finished</span><span><b>{detail.specimen_count}</b> total</span></div>
          </div>}
          {last && !green && !pending && !pendingUndo && <div className="scanner-receipt">
            <b>Last recorded: {last.direction} · {last.count} specimens</b><span>{formatTime(last.recorded_at)}</span>
          </div>}
          {undoMessage && <div className="kiosk-message undo-success" role="status">{undoMessage}</div>}
          {error && <div className="kiosk-message" role="alert">{error}</div>}
          {pendingUndo && <button className="button secondary" disabled={!healthy || busy} onClick={() => void reconcileUndo()}>Check Undo result</button>}
          {pending && <button className="button secondary" disabled={!healthy || busy} onClick={() => void recover()}>Check scan result</button>}
        </section>

        <aside className="recent-panel">
          <div className="recent-heading"><h2>This room's activity</h2><span>{latest.length}</span></div>
          <div className="recent-scroll" ref={historyRef}>
            {latest.map(event => <div className="recent-row" key={event.id}>
              <span className={'recent-direction ' + (event.kind !== 'movement' ? 'amended' : event.direction === 'IN' ? 'in' : '')}>
                {event.kind === 'undo' ? 'UNDO' : event.kind === 'modification' ? 'EDIT' : event.direction}
              </span>
              <div><b>{event.project_number || 'Stack ' + event.stack_id.slice(0, 8)}</b>
                <p>{event.material_ref || ''}{event.preconditioning_code ? ' · ' + event.preconditioning_code : ''}</p>
                <small>{event.kind === 'undo' ? 'Transaction reversed' : event.kind === 'modification' ? 'Correction recorded' : event.direction + ' · ' + (event.effective_count ?? event.affected_count) + ' specimens'}
                  {event.undone ? ' · Undone' : event.modified ? ' · Modified' : ''}</small>
                <time>{formatTime(event.recorded_at)}</time>
              </div>
            </div>)}
            {!latest.length && <div className="recent-empty">{healthy ? 'Recorded activity for this room will appear here.' : 'Activity will load when the database reconnects.'}</div>}
          </div>
          <div className="recent-foot">Confirmed database activity · Scroll here</div>
        </aside>
      </div>

      <footer className="kiosk-dock">
        <div className="dock-direction" role="group" aria-label="Persistent movement direction">
          <button type="button" className={direction === 'IN' ? 'active in' : ''} aria-label="IN" aria-pressed={direction === 'IN'}
            disabled={blocked} onClick={() => chooseDirection('IN')}><span>↘</span><div>IN<small>Keep recording IN</small></div></button>
          <button type="button" className={direction === 'OUT' ? 'active out' : ''} aria-label="OUT" aria-pressed={direction === 'OUT'}
            disabled={blocked} onClick={() => chooseDirection('OUT')}><span>↗</span><div>OUT<small>Keep recording OUT</small></div></button>
        </div>
        <button type="button" className="dock-undo" aria-label="Undo last" disabled={!healthy || blocked || !last} onClick={() => void undo()}>
          <span>↶</span><div>Undo last{last && <small>{last.direction} · {last.count} specimens</small>}</div>
        </button>
      </footer>

      {selection && <Modal title="Choose the specimens moving" onClose={() => !busy && setSelection(null)} wide>
        <div className="modal-body"><p className="modal-intro">{selection.stack.project_number} / {selection.stack.material_ref} · Move <b>{selection.direction}</b>. Choose the numbered physical specimens.</p>
          <div className="selection-tools"><button className="button secondary" onClick={() => setSelection(old => old && { ...old, ids: eligible.map(s => s.id) })}>Select all eligible ({eligible.length})</button>
            <button className="button text" onClick={() => setSelection(old => old && { ...old, ids: [] })}>Clear</button></div>
          <div className="selection-grid">{selection.stack.specimens.map(s => {
            const allowed = eligible.some(item => item.id === s.id)
            return <label key={s.id} className={['selection-tile', selection.ids.includes(s.id) ? 'selected' : '', !allowed ? 'disabled' : ''].join(' ')}>
              <input type="checkbox" disabled={!allowed || busy} checked={selection.ids.includes(s.id)}
                onChange={event => setSelection(old => old && { ...old, ids: event.target.checked ? [...old.ids, s.id] : old.ids.filter(id => id !== s.id) })} />
              <b>{String(s.number).padStart(2, '0')}</b><small>{exitOf(s) ? 'Finished' : s.state === 'IN' ? elapsed(entryOf(s)) : 'Awaiting entry'}</small>
            </label>
          })}</div>
        </div>
        <div className="modal-actions"><span>{selection.ids.length} selected</span><button className="button secondary" onClick={() => setSelection(null)}>Cancel</button>
          <button className="button primary" disabled={!healthy || busy || !!pending || !selection.ids.length} onClick={() => {
            const chosen = selection
            void submit({ request_id: requestId(), station_id: station, code: chosen.code, direction: chosen.direction, scope: 'selection', selected_specimen_ids: chosen.ids, expected_version: chosen.stack.version }, chosen.stack)
          }}>Record {selection.ids.length} {selection.direction}</button>
        </div>
      </Modal>}
      {acknowledge && <Modal title="Early checkout" onClose={() => !busy && setAcknowledge(null)}>
        <div className="modal-body"><div className="notice warning">These specimens have not completed 24 hours. Override records their actual removal and incomplete conditioning.</div>
          <div className="early-list">{early.map(s => <div key={s.id}><b>Specimen {String(s.number).padStart(2, '0')}</b><span>{elapsed(entryOf(s))} of 24h</span></div>)}</div>
          {!early.length && <p>{acknowledge.result.message}</p>}
        </div>
        <div className="modal-actions"><button className="button secondary" onClick={() => setAcknowledge(null)}>Keep in room</button>
          <button className="button danger" disabled={!healthy || busy || !!pending} onClick={() => {
            const chosen = acknowledge
            void submit({ ...chosen.request, request_id: requestId(), acknowledge_early: true }, chosen.stack)
          }}>Override & record OUT</button></div>
      </Modal>}
    </div>
  )
}
