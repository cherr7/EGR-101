import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError, elapsed, entryOf, exitOf, formatTime, isReady, post, requestId, specimenNumber } from './api'
import type { AuditEvent, Config, Movement, MovementResult, Stack } from './types'
import { Modal } from './App'
import { attachScanner } from './scannerCapture'
import { attachInputBridge, INITIAL_INPUT_STATUS } from './inputBridge'
import type { InputBridgeStatus } from './inputBridge'
import './kiosk.css'
import { KioskActivity, KioskInventory, KioskScannerSetup } from './KioskPanels'
import type { ActivityItem } from './KioskPanels'

const PENDING_KEY = 'fabric-tracker.pending-movement.v2', UNDO_KEY = 'fabric-tracker.pending-undo.v1', LEGACY_LAST_KEY = 'fabric-tracker.last-movement.v2'
function stored<T>(key: string): T | null { try { return JSON.parse(localStorage.getItem(key) || 'null') as T | null } catch { return null } }
type Last = { event_id: string; count: number; version?: number }
type PendingUndo = Last & { body: { expected_version?: number }; submitted_at: string }
type Selection = { stack: Stack; code: string; direction: 'IN' | 'OUT'; ids: number[] }
type Acknowledgment = { stack: Stack; request: Movement; result: MovementResult }
type ErrorKind = 'scan' | 'undo' | 'setup'
const ERROR_TITLES: Record<ErrorKind, string> = { scan: 'Scan not recorded', undo: 'Undo not applied', setup: 'Storage unavailable' }

const RESULT_HOLD_MS = 12000
const NOTICE_HOLD_MS = 4000
const ROOM_CHOICES = [
  { stationId: 'ppe-door', roomId: 'ppe', label: 'PPE', name: 'PPE conditioning room' },
  { stationId: 'shared-door', roomId: 'shared', label: 'Shared', name: 'Shared conditioning room' },
  { stationId: 'footwear-door', roomId: 'footwear', label: 'Footwear', name: 'Footwear conditioning room' },
]
const toUndoTarget = (event: ActivityItem): Last => ({ event_id: event.id, count: event.effective_count ?? event.affected_count, version: event.version })

export default function Kiosk({ config, healthy }: { config: Config; healthy: boolean }) {
  const stationConfig = (window as unknown as { fabricStationConfig?: { stationId?: string; fixed?: boolean; stationName?: string; roomId?: string; roomName?: string; inputBridge?: boolean } }).fabricStationConfig
  const bridgeConfigured = stationConfig?.inputBridge === true
  const [inputStatus, setInputStatus] = useState<InputBridgeStatus>(INITIAL_INPUT_STATUS)
  const inputStatusRef = useRef(inputStatus); inputStatusRef.current = inputStatus
  const [station, setStation] = useState(() => stationConfig ? localStorage.getItem('fabric-tracker.station') || stationConfig.stationId || 'shared-door' : new URLSearchParams(location.search).get('station') || localStorage.getItem('fabric-tracker.station') || 'shared-door')
  const [direction, setDirection] = useState<'IN' | 'OUT'>(() => localStorage.getItem('fabric-tracker.direction') === 'OUT' ? 'OUT' : 'IN')
  const [scope, setScope] = useState<'stack' | 'specimen'>(() => localStorage.getItem('fabric-tracker.scope') === 'specimen' ? 'specimen' : 'stack'), [busy, setBusy] = useState(false), [pending, setPending] = useState<Movement | null>(() => stored<Movement>(PENDING_KEY))
  const [pendingUndo, setPendingUndo] = useState<PendingUndo | null>(() => stored<PendingUndo>(UNDO_KEY)), [undoMessage, setUndoMessage] = useState(''), [noticeVersion, setNoticeVersion] = useState(0), [errorKind, setErrorKind] = useState<ErrorKind>('scan')
  const [selection, setSelection] = useState<Selection | null>(null), [acknowledge, setAcknowledge] = useState<Acknowledgment | null>(null)
  const [detail, setDetail] = useState<Stack | null>(null), [events, setEvents] = useState<ActivityItem[]>([])
  const [panel, setPanel] = useState<'activity' | 'inventory' | 'setup' | null>(null)
  const [result, setResult] = useState<MovementResult | null>(null), [resultDirection, setResultDirection] = useState<'IN' | 'OUT'>('IN'), [green, setGreen] = useState(false), [error, setError] = useState(''), [focused, setFocused] = useState(document.hasFocus())
  const inFlight = useRef(false), active = useRef(true), greenTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined), historyRef = useRef<HTMLDivElement>(null)
  const activityRevision = useRef(0), activityFetch = useRef(0), feedbackRevision = useRef(0)
  const currentRoomId = config.stations.find(s => s.id === station)?.room_id || ROOM_CHOICES.find(r => r.stationId === station)?.roomId || stationConfig?.roomId
  const eventRoom = useRef(currentRoomId); eventRoom.current = currentRoomId
  const transactionBlocked = busy || !!pendingUndo || !!pending || !!selection || !!acknowledge
  const blocked = transactionBlocked || panel !== null
  // Undo from the dock reverses this station's newest movement, never a staff entry or another door's scan.
  const top = events.find(event => event.actor === station)
  const undoTarget = top && !top.undone && top.can_undo !== false ? toUndoTarget(top) : null
  useEffect(() => { active.current = true; return () => { active.current = false; clearTimeout(greenTimer.current) } }, [])
  useEffect(() => {
    // The Pi touchscreen reaches Chromium as a mouse, so lists scroll by dragging and a drag never counts as a tap.
    let drag: { element: HTMLElement; y: number; top: number; moved: boolean } | null = null
    const scroller = (target: EventTarget | null) => {
      for (let element = target as HTMLElement | null; element && element !== document.body; element = element.parentElement) {
        const overflow = getComputedStyle(element).overflowY
        if ((overflow === 'auto' || overflow === 'scroll') && element.scrollHeight > element.clientHeight) return element
      }
      return null
    }
    const down = (event: PointerEvent) => {
      const element = event.pointerType === 'mouse' && event.button === 0 ? scroller(event.target) : null
      drag = element && { element, y: event.clientY, top: element.scrollTop, moved: false }
    }
    const move = (event: PointerEvent) => {
      if (!drag || (!drag.moved && Math.abs(event.clientY - drag.y) < 8)) return
      drag.moved = true; drag.element.scrollTop = drag.top - (event.clientY - drag.y); event.preventDefault()
    }
    const swallow = (event: Event) => { event.preventDefault(); event.stopPropagation() }
    const up = () => {
      if (drag?.moved) { window.addEventListener('click', swallow, { capture: true, once: true }); setTimeout(() => window.removeEventListener('click', swallow, true)) }
      drag = null
    }
    window.addEventListener('pointerdown', down, true); window.addEventListener('pointermove', move, true)
    window.addEventListener('pointerup', up, true); window.addEventListener('pointercancel', up, true)
    return () => {
      window.removeEventListener('pointerdown', down, true); window.removeEventListener('pointermove', move, true)
      window.removeEventListener('pointerup', up, true); window.removeEventListener('pointercancel', up, true)
    }
  }, [])
  useEffect(() => {
    // Setup errors block every scan, so they stay visible until the operator acts again.
    if ((!error && !undoMessage) || pending || pendingUndo || (error && errorKind === 'setup')) return
    const timer = setTimeout(() => { setError(''); setUndoMessage('') }, NOTICE_HOLD_MS)
    return () => clearTimeout(timer)
  }, [error, undoMessage, pending, pendingUndo, noticeVersion, errorKind])
  useEffect(() => { if (config.stations.length && !config.stations.some(s => s.id === station)) setStation(config.stations.find(s => s.id === 'shared-door')?.id || config.stations[0].id) }, [config.stations, station])
  useEffect(() => { localStorage.setItem('fabric-tracker.station', station) }, [station]); useEffect(() => { localStorage.setItem('fabric-tracker.direction', direction) }, [direction])
  useEffect(() => { localStorage.setItem('fabric-tracker.scope', scope) }, [scope])
  const refreshEvents = useCallback(async () => {
    if (!currentRoomId) return
    const revision = activityRevision.current, fetchId = ++activityFetch.current
    try {
      const value = await api<{ items: ActivityItem[] }>('/events?limit=40&kind=movement&room_id=' + encodeURIComponent(currentRoomId))
      if (active.current && eventRoom.current === currentRoomId && revision === activityRevision.current && fetchId === activityFetch.current) setEvents(value.items)
    } catch { /* Availability is owned by the health check. */ }
  }, [currentRoomId])
  useEffect(() => {
    setEvents([])
    if (!healthy) return
    void refreshEvents()
    const timer = setInterval(() => void refreshEvents(), 5000)
    return () => clearInterval(timer)
  }, [healthy, refreshEvents])
  const reportError = useCallback((message: string, kind: ErrorKind = 'scan') => {
    clearTimeout(greenTimer.current); feedbackRevision.current++
    setGreen(false); setResult(null); setUndoMessage(''); setError(message); setErrorKind(kind); setNoticeVersion(value => value + 1)
  }, [])
  const accept = useCallback(async (value: MovementResult, request: Movement, loaded?: Stack) => {
    if (!active.current) return
    localStorage.removeItem(PENDING_KEY); setPending(null); setResult(value); setResultDirection(request.direction); setError('')
    if (value.status === 'committed') {
      // The commit response confirms the observation. Activity and the receipt do
      // not wait for a second stack request or for the next five-second poll.
      const members = loaded?.specimens.filter(s => request.scope === 'selection'
        ? request.selected_specimen_ids?.includes(s.id) : request.direction === 'IN' ? !entryOf(s) : s.state === 'IN') || []
      if (value.event_id && value.recorded_at) {
        const event: ActivityItem = {
          id: value.event_id, kind: 'movement', direction: request.direction, actor: request.station_id,
          recorded_at: value.recorded_at, stack_id: value.stack_id, affected_count: value.affected_count,
          specimen_ids: members.map(s => s.id), room_id: currentRoomId, version: value.version, can_undo: true,
          specimen_numbers: members.map(s => s.number),
          project_number: loaded?.project_number, material_ref: loaded?.material_ref,
          test_code: loaded?.test_code, preconditioning_code: loaded?.preconditioning_code,
        }
        activityRevision.current++
        setEvents(old => [event, ...old.filter(item => item.id !== event.id)])
        historyRef.current?.scrollTo({ top: 0 })
      }
      if (loaded && value.recorded_at) {
        const moved = new Set(members.map(s => s.id))
        setDetail({ ...loaded, version: value.version ?? loaded.version, specimens: loaded.specimens.map(s => !moved.has(s.id) ? s : {
          ...s, state: request.direction, ENTRY: request.direction === 'IN' ? value.recorded_at : entryOf(s),
          EXIT: request.direction === 'OUT' ? value.recorded_at : null,
          room_id: request.direction === 'IN' ? currentRoomId || null : null,
        }) })
      }
      setSelection(null); setAcknowledge(null); setGreen(true)
      clearTimeout(greenTimer.current)
      greenTimer.current = setTimeout(() => { if (active.current) { setGreen(false); setResult(null); setDetail(null) } }, RESULT_HOLD_MS)
      void refreshEvents()
      const revision = feedbackRevision.current
      if (value.stack_id) void api<Stack>(`/stacks/${value.stack_id}`).then(stack => {
        if (active.current && revision === feedbackRevision.current) setDetail(stack)
      }).catch(() => { /* A failed detail refresh does not invalidate a confirmed movement. */ })
      return
    }
    let current = loaded
    if (value.stack_id) try { current = await api<Stack>(`/stacks/${value.stack_id}`) } catch { /* The movement response itself is authoritative. */ }
    if (!active.current) return
    if (current) setDetail(current)
    if (value.status === 'selection_required' && current) { setSelection({ stack: current, code: request.code, direction: request.direction, ids: [] }); setAcknowledge(null) }
    else if (value.status === 'acknowledgment_required' && current) { setAcknowledge({ request, stack: current, result: value }); setSelection(null) }
    else {
      setSelection(null); setAcknowledge(null)
      if (value.status === 'already_recorded') {
        clearTimeout(greenTimer.current)
        greenTimer.current = setTimeout(() => { if (active.current) { setResult(null); setDetail(null) } }, NOTICE_HOLD_MS)
      } else if (value.status === 'rejected') reportError(value.message)
    }
  }, [currentRoomId, refreshEvents, reportError])
  async function submit(request: Movement, loaded?: Stack) {
    if (inFlight.current || !healthy || stored<Movement>(PENDING_KEY) || stored<PendingUndo>(UNDO_KEY)) return
    clearTimeout(greenTimer.current); feedbackRevision.current++
    inFlight.current = true; setBusy(true); setGreen(false); setUndoMessage(''); setError(''); setResult(null)
    try { localStorage.setItem(PENDING_KEY, JSON.stringify(request)); setPending(request) } catch { inFlight.current = false; setBusy(false); reportError('This browser cannot preserve a pending request. Enable local storage before scanning.', 'setup'); return }
    try { const value = await post<MovementResult>('/movements', request); if (active.current) await accept(value, request, loaded) }
    catch (err) { if (!active.current) return; if (err instanceof ApiError && err.status < 500) { localStorage.removeItem(PENDING_KEY); setPending(null); setSelection(null); setAcknowledge(null); reportError(err.message) } else reportError('Response interrupted. This same request must be confirmed before another scan.') }
    finally { inFlight.current = false; if (active.current) setBusy(false) }
  }
  const recover = useCallback(async () => { const request = stored<Movement>(PENDING_KEY); if (!request || !healthy || inFlight.current) return; inFlight.current = true; setBusy(true); try { let value: MovementResult; try { value = await api<MovementResult>(`/movement-requests/${request.request_id}`, {}, 4500) } catch (err) { if (err instanceof ApiError && err.status === 404) value = await post<MovementResult>('/movements', request); else throw err } if (active.current) await accept(value, request) } catch { if (active.current) reportError('Confirmation pending. The same request will be checked again when connected.') } finally { inFlight.current = false; if (active.current) setBusy(false) } }, [healthy, accept, reportError])
  useEffect(() => { if (!pending || !healthy) return; void recover(); const timer = setInterval(() => void recover(), 5000); return () => clearInterval(timer) }, [pending, healthy, recover])
  async function scan(code: string) {
    if (!healthy || blocked || inFlight.current) return
    clearTimeout(greenTimer.current); feedbackRevision.current++
    inFlight.current = true; setBusy(true); setError(''); setUndoMessage(''); setResult(null); setGreen(false)
    try { const stack = await post<Stack>('/lookup', { code }); if (!active.current) return; setDetail(stack); if (scope === 'specimen') { const scanned = stack.specimens.find(s => s.id === stack.scanned_specimen_id); const eligible = scanned && (direction === 'IN' ? !entryOf(scanned) : scanned.state === 'IN'); setSelection({ stack, code, direction, ids: eligible ? [scanned!.id] : [] }); return } inFlight.current = false; await submit({ request_id: requestId(), station_id: station, code, direction, scope: 'stack', expected_version: stack.version }, stack) }
    catch (err) { if (active.current) reportError((err as Error).message) } finally { inFlight.current = false; if (active.current) setBusy(false) }
  }
  const panelOnly = panel !== null && !transactionBlocked
  const scanRef = useRef(scan), stateRef = useRef({ healthy, blocked, panelOnly, station, direction, scope }); scanRef.current = scan; stateRef.current = { healthy, blocked, panelOnly, station, direction, scope }
  useEffect(() => {
    const globals = window as unknown as { fabricScannerCaptureReady: boolean; fabricInputBridgeConnected: boolean; fabricInputBridgeReady: boolean }
    const canScan = () => stateRef.current.healthy && !stateRef.current.blocked && !inFlight.current && !document.hidden
    const reject = (message: string) => { if (!stateRef.current.blocked) reportError(message) }
    const refuse = () => { if (stateRef.current.healthy && stateRef.current.panelOnly && !inFlight.current) reportError('Scan not recorded. Close this panel, then rescan the label.') }
    const disposeKeyboard = attachScanner({
      enabled: () => canScan() && document.hasFocus() && (!bridgeConfigured || inputStatusRef.current.fallback),
      context: () => [stateRef.current.station, stateRef.current.direction, stateRef.current.scope, stateRef.current.blocked].join('|'),
      onScan: code => void scanRef.current(code), onReject: message => { if (!bridgeConfigured || inputStatusRef.current.fallback) reject(message) },
      // Never queue input during a popup, pending transaction, or active direct reader; only an open panel is reported.
      onBlocked: () => { if (!bridgeConfigured || inputStatusRef.current.fallback) refuse() },
      onFocusChange: () => setFocused(document.hasFocus() && !document.hidden),
    })
    const disposeBridge = bridgeConfigured ? attachInputBridge({
      url: '/station/input/events', statusUrl: '/station/input/status', enabled: canScan,
      onScan: code => void scanRef.current(code), onReject: reject, onBlocked: refuse,
      onStatus: value => { inputStatusRef.current = value; setInputStatus(value); globals.fabricInputBridgeConnected = value.connected; globals.fabricInputBridgeReady = value.ready },
    }) : () => {}
    globals.fabricScannerCaptureReady = true
    return () => { disposeKeyboard(); disposeBridge(); globals.fabricScannerCaptureReady = false; globals.fabricInputBridgeConnected = false; globals.fabricInputBridgeReady = false }
  }, [bridgeConfigured, reportError])
  const finishUndo = useCallback(async (intent: PendingUndo) => {
    localStorage.removeItem(UNDO_KEY); localStorage.removeItem(LEGACY_LAST_KEY)
    clearTimeout(greenTimer.current); feedbackRevision.current++; activityRevision.current++
    setPendingUndo(null); setGreen(false); setResult(null); setError(''); setDetail(null); setUndoMessage(`${intent.count} specimens restored`)
    setEvents(old => old.map(event => event.id === intent.event_id ? { ...event, undone: true, can_undo: false } : event))
    historyRef.current?.scrollTo({ top: 0 })
    void refreshEvents()
  }, [refreshEvents])
  const undoStatus = (intent: PendingUndo) => api<AuditEvent>('/transactions/' + encodeURIComponent(intent.event_id), {}, 4500)
  const reconcileUndo = useCallback(async () => {
    const intent = stored<PendingUndo>(UNDO_KEY); if (!intent || !healthy || inFlight.current) return
    inFlight.current = true; setBusy(true)
    const reject = (message: string) => { localStorage.removeItem(UNDO_KEY); setPendingUndo(null); reportError(message, 'undo') }
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
        catch (checkError) { if (checkError instanceof ApiError && checkError.status === 404) reject(checkError.message); else reportError('Undo confirmation is pending. This transaction will be checked again before scanning resumes.', 'undo') }
      } else reportError('Undo confirmation is pending. This transaction will be checked again before scanning resumes.', 'undo')
    } finally { inFlight.current = false; if (active.current) setBusy(false) }
  }, [healthy, finishUndo, reportError])
  useEffect(() => { if (!pendingUndo || !healthy) return; void reconcileUndo(); const timer = setInterval(() => void reconcileUndo(), 5000); return () => clearInterval(timer) }, [pendingUndo, healthy, reconcileUndo])
  async function undo(target: Last | null = undoTarget) {
    if (!target || !healthy || transactionBlocked || inFlight.current || stored<PendingUndo>(UNDO_KEY)) return
    const intent: PendingUndo = { ...target, body: { expected_version: target.version }, submitted_at: new Date().toISOString() }
    try { localStorage.setItem(UNDO_KEY, JSON.stringify(intent)) } catch { reportError('This browser cannot preserve a pending undo. Enable local storage before continuing.', 'setup'); return }
    setPendingUndo(intent); setGreen(false); setResult(null); setUndoMessage(''); setError('')
    await reconcileUndo()
  }
  const inputReady = bridgeConfigured ? inputStatus.ready || inputStatus.fallback && focused : focused
  const title = !healthy ? 'Offline' : pendingUndo ? 'Confirming undo…' : pending ? 'Confirming scan…' : !inputReady ? bridgeConfigured && !inputStatus.fallback ? inputStatus.busy ? 'Scanner in use' : 'Connect scanner' : 'Tap to resume' : busy ? 'Reading…' : error ? ERROR_TITLES[errorKind] : green && result ? `${resultDirection} confirmed` : result?.status === 'already_recorded' ? 'Already recorded' : undoMessage ? 'Transaction undone' : `Ready for ${direction}`
  const eligible = selection?.stack.specimens.filter(s => selection.direction === 'IN' ? !entryOf(s) : s.state === 'IN') || []
  const early = acknowledge?.stack.specimens.filter(s => s.state === 'IN' && !isReady(s) && (acknowledge.request.scope === 'stack' || acknowledge.request.selected_specimen_ids?.includes(s.id))) || []

  function clearFeedback() {
    clearTimeout(greenTimer.current); feedbackRevision.current++
    setGreen(false); setResult(null); setError(''); setUndoMessage(''); setDetail(null)
  }
  function chooseRoom(value: string) {
    if (blocked || value === station) return
    clearFeedback(); activityRevision.current++; setEvents([]); setStation(value)
    localStorage.setItem('fabric-tracker.station', value)
    const url = new URL(location.href); url.searchParams.set('station', value)
    history.replaceState(null, '', url.pathname + url.search + url.hash)
  }
  function chooseDirection(value: 'IN' | 'OUT') {
    if (blocked) return
    clearFeedback(); setDirection(value)
  }
  return (
    <div className="kiosk-shell scanner-v2">
      <header className="kiosk-header">
        <nav className="scanner-rooms" aria-label="Conditioning room">
          {ROOM_CHOICES.map(choice => <button key={choice.stationId} type="button"
            className={station === choice.stationId ? 'selected' : ''}
            aria-pressed={station === choice.stationId} disabled={blocked}
            onClick={() => chooseRoom(choice.stationId)}>{choice.label}</button>)}
        </nav>
        <div className="scanner-scope" role="group" aria-label="Movement scope">
          <button type="button" aria-pressed={scope === 'stack'} disabled={blocked}
            onClick={() => { clearFeedback(); setScope('stack') }}>Stack</button>
          <button type="button" aria-pressed={scope === 'specimen'} disabled={blocked}
            onClick={() => { clearFeedback(); setScope('specimen') }}>Individual</button>
        </div>
        <div className="scanner-network">
          <div className="scanner-status"><span className={'scanner-connection ' + (healthy ? 'online' : 'offline')} role="status"><i aria-hidden="true" />{healthy ? 'Online' : 'Offline'}</span>
            {bridgeConfigured && <small className={'scanner-ip ' + (inputStatus.localIps.length ? '' : 'missing')} aria-label="Station IP address">
              {inputStatus.localIps.length ? inputStatus.localIps.join(' · ') : inputStatus.connected ? 'No network' : 'IP unknown'}</small>}</div>
          <button type="button" className="scanner-setup-button" aria-label="Scanner setup" title="Scanner setup" disabled={blocked} onClick={() => setPanel('setup')}>
            <svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></svg>
          </button></div>
      </header>

      <div className="kiosk-middle">
        <section className={['readiness-area', green && healthy && inputReady && !error ? 'confirmed' : '', !healthy || !inputReady ? 'unavailable' : '', error && !pending && !pendingUndo ? 'scan-error' : '', pending || pendingUndo ? 'confirming' : ''].join(' ')}
          data-input-ready={inputReady} aria-live="polite">
          <div className="scanner-result">
            <div className="readiness-icon" aria-hidden="true">{!healthy || !inputReady || error ? '!' : pending || pendingUndo || busy ? '◷' : green ? '✓' : direction === 'IN' ? '↘' : '↗'}</div>
            <h2>{title}</h2>
            {green && result && healthy && inputReady && <p className="readiness-description">{result.affected_count} specimens · {formatTime(result.recorded_at)}</p>}
            {(pending || pendingUndo) && healthy && <p className="readiness-description">Waiting for confirmation</p>}
            {!inputReady && healthy && bridgeConfigured && !inputStatus.fallback && <p className="readiness-description">{inputStatus.busy ? 'Close the other scanner page' : 'Check the USB connection'}</p>}
            {undoMessage && <p className="readiness-description undo-success" role="status">{undoMessage}</p>}
            {error && !pending && !pendingUndo && healthy && <div className="kiosk-message" role="alert">{error}</div>}
          </div>
          {detail && green && healthy && inputReady && !error && <div className="last-stack">
            <div><h3>{detail.project_number}<em>/</em>{detail.material_ref}</h3>
              <p>{detail.test_code} · {detail.preconditioning_code}</p></div>
            <div className="stack-counts"><span><b>{detail.specimens.filter(s => s.state === 'IN').length}</b> inside</span>
              <span><b>{detail.specimens.filter(s => !!exitOf(s)).length}</b> finished</span><span><b>{detail.specimen_count}</b> total</span></div>
          </div>}
          {pendingUndo && <button className="button secondary" disabled={!healthy || busy} onClick={() => void reconcileUndo()}>Check Undo result</button>}
          {pending && <button className="button secondary" disabled={!healthy || busy} onClick={() => void recover()}>Check scan result</button>}
        </section>

        <aside className="recent-panel">
          <div className="recent-heading"><h2>Activity</h2><div className="panel-tools">
            <button type="button" className="panel-tool" disabled={blocked} onClick={() => setPanel('inventory')}>Inventory</button>
            <button type="button" className="panel-tool expand-activity" aria-label="Expand Activity" disabled={blocked} onClick={() => { setPanel('activity'); void refreshEvents() }}>
              <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M9 4H4v5m11-5h5v5M4 15v5h5m11-5v5h-5" /></svg>
            </button></div></div>
          <div className="recent-scroll" ref={historyRef}>
            {events.map(event => <div className={'recent-row ' + (event.undone ? 'undone' : '')} key={event.id}
              id={'activity-' + event.id} data-event-id={event.id} data-undo-target={undoTarget?.event_id === event.id}>
              <span className={'recent-direction ' + (event.direction === 'IN' ? 'in' : 'out')}>{event.direction}</span>
              <div><b>{event.project_number || 'Stack ' + event.stack_id}</b>
                <p>{event.material_ref || ''}{event.preconditioning_code ? ' · ' + event.preconditioning_code : ''}</p>
                <small>{event.effective_count ?? event.affected_count} specimens
                  {event.undone ? ' · Undone' : event.modified ? ' · Modified' : ''}</small>
                <time>{formatTime(event.recorded_at)}</time>
              </div>
            </div>)}
          </div>
        </aside>
      </div>

      <footer className="kiosk-dock">
        <button type="button" className={direction === 'IN' ? 'active in' : ''} aria-pressed={direction === 'IN'}
          disabled={blocked} onClick={() => chooseDirection('IN')}><svg className="dock-icon" aria-hidden="true" viewBox="0 0 24 24"><path d="M5 5l14 14M7 19h12V7" /></svg><span>IN</span></button>
        <button type="button" className={direction === 'OUT' ? 'active out' : ''} aria-pressed={direction === 'OUT'}
          disabled={blocked} onClick={() => chooseDirection('OUT')}><svg className="dock-icon" aria-hidden="true" viewBox="0 0 24 24"><path d="M5 19L19 5M7 5h12v12" /></svg><span>OUT</span></button>
        <button type="button" className="dock-undo" aria-describedby={undoTarget ? 'activity-' + undoTarget.event_id : undefined}
          disabled={!healthy || blocked || !undoTarget} onClick={() => void undo()}><svg className="dock-icon" aria-hidden="true" viewBox="0 0 24 24"><path d="M9 5L4 10l5 5M4 10h9a7 7 0 0 1 7 7v2" /></svg><span>Undo</span></button>
      </footer>

      {panel === 'activity' && <KioskActivity events={events} disabled={!healthy || transactionBlocked} error={error} onClose={() => setPanel(null)}
        onUndo={event => void undo(toUndoTarget(event))} />}
      {panel === 'setup' && <KioskScannerSetup onClose={() => setPanel(null)} />}
      {panel === 'inventory' && <KioskInventory roomId={currentRoomId} healthy={healthy} notice={error} onClose={() => setPanel(null)} />}

      {selection && <Modal title="Choose the specimens moving" onClose={() => !busy && setSelection(null)} wide>
        <div className="modal-body"><p className="modal-intro">{selection.stack.project_number} / {selection.stack.material_ref} · Move <b>{selection.direction}</b>. Choose the numbered physical specimens.</p>
          <div className="selection-tools"><button className="button secondary" onClick={() => setSelection(old => old && { ...old, ids: eligible.map(s => s.id) })}>Select all eligible ({eligible.length})</button>
            <button className="button text" onClick={() => setSelection(old => old && { ...old, ids: [] })}>Clear</button></div>
          <div className="selection-grid">{selection.stack.specimens.map(s => {
            const allowed = eligible.some(item => item.id === s.id)
            return <label key={s.id} className={['selection-tile', selection.ids.includes(s.id) ? 'selected' : '', !allowed ? 'disabled' : ''].join(' ')}>
              <input type="checkbox" disabled={!allowed || busy} checked={selection.ids.includes(s.id)}
                onChange={event => setSelection(old => old && { ...old, ids: event.target.checked ? [...old.ids, s.id] : old.ids.filter(id => id !== s.id) })} />
              <b>{specimenNumber(s.number)}</b><small>{exitOf(s) ? 'Finished' : s.state === 'IN' ? elapsed(entryOf(s)) : 'Awaiting entry'}</small>
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
          <div className="early-list">{early.map(s => <div key={s.id}><b>Specimen {specimenNumber(s.number)}</b><span>{elapsed(entryOf(s))} of 24h</span></div>)}</div>
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
