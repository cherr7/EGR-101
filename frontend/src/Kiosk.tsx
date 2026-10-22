import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError, codeFor, elapsed, formatTime, post, requestId } from './api'
import type { Config, Movement, MovementResult, Stack } from './types'
import { ErrorNotice, Modal, PageHeader } from './App'

const PENDING_KEY = 'fabric-tracker.pending-movement.v1'
function readPending(): Movement | null {
  try { return JSON.parse(localStorage.getItem(PENDING_KEY) || 'null') as Movement | null } catch { return null }
}

export default function Kiosk({ config, healthy }: { config: Config; healthy: boolean }) {
  const [station, setStation] = useState(() => localStorage.getItem('fabric-tracker.station') || 'shared-door')
  const [direction, setDirection] = useState<'IN' | 'OUT'>('IN')
  const [scope, setScope] = useState<'stack' | 'specimen'>('stack')
  const [input, setInput] = useState('')
  const [detail, setDetail] = useState<Stack | null>(null)
  const [stackList, setStackList] = useState<Stack[]>([])
  const [simulatedStack, setSimulatedStack] = useState('')
  const [simulatedSpecimen, setSimulatedSpecimen] = useState('')
  const [error, setError] = useState('')
  const [result, setResult] = useState<MovementResult | null>(null)
  const [resultDirection, setResultDirection] = useState<'IN' | 'OUT'>('IN')
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<Movement | null>(readPending)
  const [selection, setSelection] = useState<{ stack: Stack; code: string; direction: 'IN' | 'OUT'; ids: string[] } | null>(null)
  const [acknowledge, setAcknowledge] = useState<{ request: Movement; stack: Stack; result: MovementResult } | null>(null)
  const [latency, setLatency] = useState<number | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const inFlight = useRef(false)
  const active = useRef(true)
  useEffect(() => { active.current = true; inputRef.current?.focus(); return () => { active.current = false } }, [])
  useEffect(() => { if (healthy && !readPending() && !selection && !acknowledge) requestAnimationFrame(() => inputRef.current?.focus()) }, [healthy])
  useEffect(() => { if (config.stations.length && !config.stations.some(s => s.id === station)) setStation(config.stations.find(s => s.id === 'shared-door')?.id || config.stations[0].id) }, [config.stations, station])
  useEffect(() => { localStorage.setItem('fabric-tracker.station', station) }, [station])
  const focusScan = () => requestAnimationFrame(() => inputRef.current?.focus())
  const room = config.rooms.find(r => r.id === config.stations.find(s => s.id === station)?.room_id)
  const refreshList = useCallback(async () => {
    try { const value = await api<{ items: Stack[] }>('/stacks'); if (active.current) { setStackList(value.items); setSimulatedStack(old => old || value.items[0]?.id || '') } } catch { /* Health status owns availability. */ }
  }, [])
  useEffect(() => { if (healthy) void refreshList() }, [healthy, refreshList])
  useEffect(() => {
    if (!simulatedStack || !healthy) return
    let alive = true
    void api<Stack>(`/stacks/${simulatedStack}`).then(value => { if (alive) { setStackList(old => old.map(s => s.id === value.id ? value : s)); setSimulatedSpecimen(value.specimens[0]?.id || '') } }).catch(() => {})
    return () => { alive = false }
  }, [simulatedStack, healthy])
  const acceptResult = useCallback(async (value: MovementResult, request: Movement, loaded?: Stack) => {
    localStorage.removeItem(PENDING_KEY); setPending(null); setResult(value); setResultDirection(request.direction); setStation(request.station_id); setError('')
    let current = loaded
    if ((!current || value.status === 'committed' || value.status === 'already_recorded' || value.status === 'rejected') && value.stack_id) {
      try { current = await api<Stack>(`/stacks/${value.stack_id}`) } catch { /* Result is already confirmed. */ }
    }
    if (!active.current) return
    if (current) setDetail(current)
    if (value.status === 'selection_required' && current) {
      setSelection({ stack: current, code: request.code, direction: request.direction, ids: [] }); setAcknowledge(null)
    } else if (value.status === 'acknowledgment_required' && current) {
      setAcknowledge({ request, stack: current, result: value }); setSelection(null)
    } else {
      setSelection(null); setAcknowledge(null)
      if (value.status === 'committed' || value.status === 'already_recorded') { setScope('stack'); setInput(''); void refreshList(); setTimeout(() => inputRef.current?.focus(), 50) }
      if (value.status === 'rejected') setError(value.message)
    }
  }, [refreshList])
  async function submit(request: Movement, loaded?: Stack) {
    if (inFlight.current || !healthy || pending) return
    inFlight.current = true; setBusy(true); setError(''); setResult(null); setLatency(null)
    // This is a single unresolved submitted transaction, never an offline queue.
    // Persist before the request so a reload can reconcile the same identity.
    localStorage.setItem(PENDING_KEY, JSON.stringify(request)); setPending(request)
    const start = performance.now()
    try {
      const value = await post<MovementResult>('/movements', request)
      if (active.current) { setLatency(Math.round(performance.now() - start)); await acceptResult(value, request, loaded) }
    } catch (err) {
      if (err instanceof ApiError && err.status < 500) { localStorage.removeItem(PENDING_KEY); setPending(null); setSelection(null); setAcknowledge(null); setError(err.message); if (loaded) void api<Stack>(`/stacks/${loaded.id}`).then(setDetail).catch(() => {}) }
      else setError('The response was interrupted. Checking this same request before accepting another scan.')
    } finally { inFlight.current = false; if (active.current) setBusy(false) }
  }
  const recover = useCallback(async () => {
    const request = readPending()
    if (!request || !healthy || inFlight.current) return
    inFlight.current = true; setBusy(true)
    try {
      let value: MovementResult
      try { value = await api<MovementResult>(`/movement-requests/${request.request_id}`, {}, 4500) }
      catch (err) {
        if (err instanceof ApiError && err.status === 404) value = await post<MovementResult>('/movements', request)
        else throw err
      }
      if (active.current) await acceptResult(value, request)
    } catch { if (active.current) setError('Confirmation is still pending. Keep this station open; it will retry the same request when connected.') }
    finally { inFlight.current = false; if (active.current) setBusy(false) }
  }, [healthy, acceptResult])
  useEffect(() => {
    if (!pending || !healthy) return
    void recover()
    const timer = setInterval(() => void recover(), 5000)
    return () => clearInterval(timer)
  }, [pending, healthy, recover])
  async function scan(code: string) {
    if (!healthy || busy || pending || selection || acknowledge || !code.trim()) return
    setBusy(true); setError(''); setResult(null)
    try {
      const stack = await post<Stack>('/lookup', { code: code.trim() }); setDetail(stack)
      if (scope === 'specimen' && !stack.scanned_specimen_id) { setSelection({ stack, code: code.trim(), direction, ids: [] }); return }
      setBusy(false)
      await submit({ request_id: requestId(), station_id: station, code: code.trim(), direction, scope, expected_version: stack.version }, stack)
    } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }
  const simulated = stackList.find(s => s.id === simulatedStack)
  const selectedEarly = acknowledge ? acknowledge.stack.specimens.filter(s => acknowledge.request.scope === 'stack' || acknowledge.request.selected_specimen_ids?.includes(s.id) || (acknowledge.request.scope === 'specimen' && codeFor(s.id) === acknowledge.request.code)).filter(s => s.state === 'IN' && s.due_at && Date.parse(s.due_at) > Date.now()) : []
  return <>
    <PageHeader eyebrow="ROOM STATION" title="Every specimen, accounted for." description="Scan any member to move its stack. Select individual specimens when only part of a stack moves."><span className="badge prototype">LIVE SCAN WORKFLOW</span></PageHeader>
    <div className="kiosk-grid">
      <section className="card station-card"><div className="room-heading"><div><div className="eyebrow">CURRENT STATION</div><label className="station-select"><span className="sr-only">Station</span><select value={station} disabled={busy || !!pending || !!selection || !!acknowledge} onChange={e => { setStation(e.target.value); focusScan() }}>{config.stations.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label><p>{room?.name || 'Waiting for room configuration'}</p></div><span className="room-symbol">↔</span></div>
        <div className="room-conditions"><div><b>{room?.temperature_c ?? '—'}<small>{room?.temperature_tolerance_c !== undefined && `±${room.temperature_tolerance_c}`}°C</small></b><span>Temperature</span></div><div><b>{room?.relative_humidity ?? '—'}<small>{room?.humidity_tolerance !== undefined && `±${room.humidity_tolerance}`} %</small></b><span>Relative humidity</span></div><div><b>{room?.required_hours ?? 24}<small>h</small></b><span>Continuous conditioning</span></div></div>
        <div className="control-label"><span>01</span> Movement direction</div><div className="direction-buttons"><button aria-pressed={direction === 'IN'} className={direction === 'IN' ? 'selected in' : ''} disabled={busy || !!pending} aria-label="IN" onClick={() => { setDirection('IN'); focusScan() }}><span>↘</span><strong>IN</strong><small>Begin conditioning</small></button><button aria-pressed={direction === 'OUT'} className={direction === 'OUT' ? 'selected out' : ''} disabled={busy || !!pending} aria-label="OUT" onClick={() => { setDirection('OUT'); focusScan() }}><span>↗</span><strong>OUT</strong><small>Remove specimens</small></button></div>
        <div className="control-label"><span>02</span> What is moving?</div><div className="segmented"><button className={scope === 'stack' ? 'active' : ''} aria-pressed={scope === 'stack'} disabled={busy || !!pending} onClick={() => { setScope('stack'); focusScan() }}>▦ Whole stack</button><button className={scope === 'specimen' ? 'active' : ''} aria-pressed={scope === 'specimen'} disabled={busy || !!pending} onClick={() => { setScope('specimen'); focusScan() }}>◻ Individual specimens</button></div>
        <div className="scan-box"><div className="control-label"><span>03</span> Scan a specimen label</div><form onSubmit={e => { e.preventDefault(); void scan(input) }}><span className="scan-symbol">▥</span><input ref={inputRef} aria-label="Scan code" value={input} onChange={e => setInput(e.target.value)} disabled={!healthy || busy || !!pending} placeholder="Scan QR code or paste its ID…" autoComplete="off" spellCheck={false} /><button className="button primary" disabled={!healthy || busy || !!pending || !input.trim()}>{busy ? 'Checking…' : 'Scan →'}</button></form><p>USB scanner: keep this field focused and scan. The scanner sends Enter.</p></div>
        <ErrorNotice message={error} />
        {pending && <div className="notice warning" role="status"><strong>Awaiting database confirmation.</strong> This request is being reconciled. Another scan will be available once its outcome is known.<button className="button text" disabled={!healthy || busy} onClick={() => void recover()}>Check confirmation</button></div>}
        {result && !pending && <div className={`scan-result ${result.status === 'committed' ? 'success' : result.status === 'already_recorded' ? 'neutral' : 'warning'}`} role="status"><span>{result.status === 'committed' ? '✓' : result.status === 'already_recorded' ? '↺' : '!'}</span><div><strong>{result.status === 'committed' ? `${resultDirection} confirmed · ${result.affected_count} specimens` : result.status === 'already_recorded' ? 'Already recorded' : result.status === 'selection_required' ? 'Choose the exact specimens' : result.status === 'acknowledgment_required' ? 'Conditioning time is incomplete' : 'Movement rejected'}</strong><p>{result.message}</p>{result.recorded_at && <small>{formatTime(result.recorded_at)} ET{latency !== null ? ` · Confirmed in ${latency} ms` : ''}</small>}</div></div>}
      </section>
      <div className="kiosk-aside"><section className="card simulator"><div className="card-heading"><div><span className="badge muted">DEMO TOOL</span><h2>Scanner simulator</h2><p>Try the workflow with a registered label.</p></div><span className="mini-icon">⌁</span></div><label>Stack<select aria-label="Simulator stack" value={simulatedStack} disabled={busy || !!pending} onChange={e => setSimulatedStack(e.target.value)}><option value="">Select a registered stack</option>{stackList.map(s => <option key={s.id} value={s.id}>{s.project_number} · {s.material_ref} · {s.test_code}{s.is_demo ? ' [DEMO]' : ''}</option>)}</select></label><label>Specimen<select aria-label="Simulator specimen" value={simulatedSpecimen} disabled={busy || !!pending} onChange={e => setSimulatedSpecimen(e.target.value)}>{simulated?.specimens?.map(s => <option key={s.id} value={s.id}>Specimen {String(s.number).padStart(2, '0')} · {s.state}</option>)}</select></label><button className="button secondary full" disabled={!healthy || busy || !!pending || !simulatedSpecimen} onClick={() => { const code = codeFor(simulatedSpecimen); setInput(code); void scan(code) }}>Simulate specimen scan →</button><button className="button text full" disabled={!healthy || busy || !!pending || !simulatedStack} onClick={() => { const code = codeFor(simulatedStack, true); setInput(code); void scan(code) }}>Scan stack header instead</button><p className="input-help">Simulator scans use the same database transactions as a physical scanner.</p></section>
        <section className="quiet-card"><div className="eyebrow">AT A GLANCE</div><h3>One scan can move a whole stack.</h3><p>The scanned label identifies the group. A green confirmation means the database has recorded every affected specimen.</p><div className="legend"><span className="status-dot in" /> In room <span className="status-dot out" /> Out of room</div></section>
      </div>
    </div>
    {detail && <section className="card stack-card"><div className="card-heading"><div><div className="eyebrow">LAST SCANNED STACK {detail.is_demo && '· DEMO'}</div><h2>{detail.project_number} <span className="subtle">/</span> {detail.material_ref}</h2><p>{detail.test_code} · {detail.preconditioning_code} · {detail.specimens.length} specimens · Fixed membership</p></div><button className="button secondary" disabled={!healthy || busy || !!pending || !!selection || !!acknowledge} onClick={() => setSelection({ stack: detail, code: codeFor(detail.id, true), direction, ids: [] })}>Choose specimens</button></div><div className="specimen-grid">{detail.specimens.map(s => <div key={s.id} className={`specimen-tile ${s.state === 'IN' ? 'in' : ''}`}><b>{String(s.number).padStart(2, '0')}</b><span>{s.state === 'IN' ? 'IN ROOM' : 'OUT'}</span><small>{s.state === 'IN' ? elapsed(s.entered_at) : '—'}</small></div>)}</div></section>}
    {selection && <Modal title="Select the specimens moving" onClose={() => { setSelection(null); focusScan() }}><p className="modal-intro">{selection.stack.project_number} / {selection.stack.material_ref}. Choose exactly which specimens to move <strong>{selection.direction}</strong>. Mixed stacks always require an explicit selection.</p><div className="selection-grid">{selection.stack.specimens.map(s => <label key={s.id} className={`selection-tile ${selection.ids.includes(s.id) ? 'selected' : ''}`}><input type="checkbox" checked={selection.ids.includes(s.id)} onChange={e => setSelection(old => old ? { ...old, ids: e.target.checked ? [...old.ids, s.id] : old.ids.filter(id => id !== s.id) } : null)} /><b>{String(s.number).padStart(2, '0')}</b><small>{s.state} {s.state === 'IN' ? elapsed(s.entered_at) : ''}</small></label>)}</div><div className="modal-actions"><span>{selection.ids.length} selected</span><button className="button secondary" onClick={() => setSelection(null)}>Cancel</button><button className="button primary" disabled={!healthy || busy || !!pending || selection.ids.length === 0} onClick={() => { const chosen = selection; void submit({ request_id: requestId(), station_id: station, code: chosen.code, direction: chosen.direction, scope: 'selection', selected_specimen_ids: chosen.ids, expected_version: chosen.stack.version }, chosen.stack) }}>Confirm {selection.direction}</button></div></Modal>}
    {acknowledge && <Modal title="Conditioning is not complete" onClose={() => { setAcknowledge(null); focusScan() }}><div className="notice warning">These specimens have not completed the required continuous conditioning period. Removing them records an incomplete session.</div><div className="early-list">{selectedEarly.length ? selectedEarly.map(s => <div key={s.id}><strong>Specimen {String(s.number).padStart(2, '0')}</strong><span>{elapsed(s.entered_at)} of {room?.required_hours || 24}h</span></div>) : <p>{acknowledge.result.message}</p>}</div><p className="modal-intro">Your acknowledgment will be saved in the audit history.</p><div className="modal-actions"><button className="button secondary" onClick={() => setAcknowledge(null)}>Keep in room</button><button className="button danger" disabled={!healthy || busy || !!pending} onClick={() => { const value = acknowledge; void submit({ ...value.request, request_id: requestId(), acknowledge_early: true }, value.stack) }}>Acknowledge & record OUT</button></div></Modal>}
  </>
}
