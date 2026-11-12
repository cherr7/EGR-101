import { useEffect, useMemo, useState } from 'react'
import { api, elapsed, entryOf, formatTime, isReady, serverNow, specimenNumbers } from './api'
import { Modal } from './App'
import type { AuditEvent, Stack } from './types'
import { SCANNER_SETUP_CODES } from './scannerSetupCodes'

export type ActivityItem = AuditEvent & { project_number?: string; material_ref?: string; test_code?: string; preconditioning_code?: string; specimen_numbers?: number[]; note?: string }

export function KioskActivity({ events, disabled, error, onUndo, onClose }: {
  events: ActivityItem[]; disabled: boolean; error: string; onUndo: (event: ActivityItem) => void; onClose: () => void
}) {
  const [filter, setFilter] = useState('')
  const visible = events.filter(event => !filter || event.direction === filter)
  return <Modal title="Activity" onClose={onClose} wide>
    <div className="modal-body kiosk-panel-body">
      <div className="kiosk-panel-tools" role="group" aria-label="Activity direction">
        {['', 'IN', 'OUT'].map(value => <button type="button" key={value} aria-pressed={filter === value} onClick={() => setFilter(value)}>{value || 'All'}</button>)}
      </div>
      {error && <div className="notice error" role="alert">{error}</div>}
      <div className="expanded-activity-list">
        {visible.map(event => <article key={event.id} className={'expanded-activity-row ' + (event.undone ? 'undone' : '')} data-event-id={event.id}>
          <span className={'recent-direction ' + event.direction?.toLowerCase()}>{event.direction}</span>
          <div className="expanded-activity-details"><b>{event.project_number || 'Stack ' + event.stack_id} / {event.material_ref}</b>
            <p>{[event.test_code, event.preconditioning_code].filter(Boolean).join(' · ')}</p>
            {!!event.specimen_numbers?.length && <p className="activity-numbers">{specimenNumbers(event.specimen_numbers)}</p>}
            {event.note && <p>{event.note}</p>}
            <small>{event.undone ? 'Undone' : event.modified ? 'Modified' : ''}</small>
          </div>
          <div className="expanded-activity-time"><b>{event.effective_count ?? event.affected_count} specimens</b>
            <time>{formatTime(event.effective_occurred_at || event.occurred_at || event.recorded_at)}</time>
            {event.modified && <small>Recorded {formatTime(event.recorded_at)}</small>}
          </div>
          <button type="button" className="activity-minus" aria-label={'Undo transaction ' + event.id} title={event.undone ? 'Undone' : event.can_undo === false ? 'Later activity prevents Undo' : 'Undo'}
            disabled={disabled || event.undone || event.can_undo === false} onClick={() => onUndo(event)}>−</button>
        </article>)}
        {!visible.length && <p className="panel-empty">No activity</p>}
      </div>
    </div>
  </Modal>
}

export function KioskInventory({ roomId, healthy, notice, onClose }: { roomId?: string; healthy: boolean; notice: string; onClose: () => void }) {
  const [items, setItems] = useState<Stack[]>([]), [filter, setFilter] = useState('ready'), [project, setProject] = useState('')
  const [expanded, setExpanded] = useState<number | null>(null), [error, setError] = useState(''), [loading, setLoading] = useState(true), [now, setNow] = useState(serverNow())
  useEffect(() => {
    if (!healthy || !roomId) { setItems([]); setLoading(false); return }
    let active = true, revision = 0
    const load = async () => {
      const current = ++revision
      try {
        const value = await api<{ items: Stack[] }>('/stacks?room_id=' + encodeURIComponent(roomId))
        if (active && current === revision) { setItems(value.items); setError(''); setNow(serverNow()) }
      } catch (err) { if (active && current === revision) { setItems([]); setError((err as Error).message) } }
      finally { if (active && current === revision) setLoading(false) }
    }
    setLoading(true); void load()
    const refresh = setInterval(() => void load(), 10000)
    return () => { active = false; clearInterval(refresh) }
  }, [healthy, roomId])
  const rows = useMemo(() => items.map(stack => {
    const inside = stack.specimens.filter(s => s.state === 'IN' && s.room_id === roomId)
    const ready = inside.filter(s => isReady(s, now)), conditioning = inside.filter(s => !isReady(s, now))
    const due = conditioning.map(s => Date.parse(s.due_at || '')).filter(Number.isFinite).sort((a, b) => a - b)[0]
    const oldest = Math.min(...inside.map(s => Date.parse(entryOf(s) || '')).filter(Number.isFinite))
    return { stack, inside, ready, conditioning, due, oldest }
  }).filter(row => row.inside.length), [items, roomId, now])
  const scoped = rows.filter(row => !project || row.stack.project_number === project)
  const readyCount = scoped.reduce((n, row) => n + row.ready.length, 0), insideCount = scoped.reduce((n, row) => n + row.inside.length, 0)
  const visible = scoped.filter(row => filter === 'inside' || (filter === 'ready' ? row.ready.length > 0 : row.due > now && row.due <= now + 2 * 3600000))
    .sort((a, b) => filter === 'soon' ? a.due - b.due : Number(b.ready.length > 0) - Number(a.ready.length > 0) || a.oldest - b.oldest)
  return <Modal title="Inventory" onClose={onClose} wide>
    <div className="modal-body kiosk-panel-body">
      <div className="kiosk-panel-tools">
        <div role="group" aria-label="Inventory status">
          <button type="button" aria-pressed={filter === 'ready'} onClick={() => setFilter('ready')}>Ready <b>{readyCount}</b></button>
          <button type="button" aria-pressed={filter === 'inside'} onClick={() => setFilter('inside')}>Inside <b>{insideCount}</b></button>
          <button type="button" aria-pressed={filter === 'soon'} onClick={() => setFilter('soon')}>Next 2h</button>
        </div>
        <select aria-label="Inventory project" value={project} onChange={event => { setProject(event.target.value); setExpanded(null) }}>
          <option value="">All projects</option>{[...new Set(rows.map(row => row.stack.project_number))].sort().map(value => <option key={value}>{value}</option>)}
        </select>
      </div>
      {notice && healthy && <div className="notice error" role="alert">{notice}</div>}
      {!healthy ? <p className="notice error" role="alert">Offline</p> : error ? <p className="notice error" role="alert">{error}</p> :
        <div className="inventory-list">
          {visible.map(({ stack, inside, ready, conditioning, due, oldest }) => <article key={stack.id} className={'inventory-item ' + (ready.length ? 'ready' : '')} data-stack-id={stack.id}>
            <button type="button" className="inventory-row" aria-expanded={expanded === stack.id} onClick={() => setExpanded(expanded === stack.id ? null : stack.id)}>
              <span><b>{stack.project_number} / {stack.material_ref}</b><small>{stack.test_code} · {stack.preconditioning_code}</small></span>
              <span className="inventory-counts"><b>{ready.length} ready</b><small>{inside.length} inside</small></span>
              <span className="inventory-times"><b>{Number.isFinite(oldest) ? elapsed(new Date(oldest).toISOString()) : '—'}</b>
                <small>{due ? 'Next ' + formatTime(new Date(due).toISOString()) : ''}</small></span>
              <span aria-hidden="true">{expanded === stack.id ? '−' : '+'}</span>
            </button>
            {expanded === stack.id && <div className="inventory-pieces">
              {!!ready.length && <p><b>Ready</b><span>{specimenNumbers(ready.map(s => s.number))}</span></p>}
              {!!conditioning.length && <p><b>Conditioning</b><span>{specimenNumbers(conditioning.map(s => s.number))}</span></p>}
            </div>}
          </article>)}
          {!visible.length && <p className="panel-empty">{loading ? 'Loading…' : filter === 'ready' ? 'No ready specimens' : filter === 'soon' ? 'None due in 2h' : 'Room empty'}</p>}
        </div>}
    </div>
  </Modal>
}

export function KioskScannerSetup({ onClose }: { onClose: () => void }) {
  return <section className="scanner-setup-strip" role="dialog" aria-label="Scanner setup">
    <ol>
      {SCANNER_SETUP_CODES.map(code => <li key={code.payload}>
        <svg viewBox={`0 0 ${code.size} ${code.size}`} role="img" aria-label={code.label + ' setup code'} shapeRendering="crispEdges"><rect width={code.size} height={code.size} fill="#fff" /><path d={code.path} fill="#000" /></svg>
        <b>{code.label}</b>
      </li>)}
    </ol>
    <div className="scanner-setup-side"><p>Scan in order. Scans are not recorded while this is open.</p>
      <button type="button" className="button secondary" onClick={onClose}>Done</button></div>
  </section>
}

export type RoomChoice = { stationId: string; roomId: string; label: string; name: string }
export type StationInfo = {
  stationId: string; roomName: string; stationIps: string[]; server: string; reportedServer: string | null; online: boolean
  scanner: string; onPi: boolean
}
export const IDLE_CHOICES = [0, 2, 5, 10]

/** Station settings: room, scanner setup, idle reset, station info, and (on a Pi) exit kiosk mode. */
export function KioskSettings({ rooms, station, roomLocked, onChooseRoom, onScannerSetup, idleMinutes, onIdleMinutes, info, onClose }: {
  rooms: RoomChoice[]; station: string; roomLocked: boolean; onChooseRoom: (stationId: string) => void; onScannerSetup: () => void
  idleMinutes: number; onIdleMinutes: (minutes: number) => void; info: StationInfo; onClose: () => void
}) {
  const [packageBuilt, setPackageBuilt] = useState<string | null>(null)
  const [exitStep, setExitStep] = useState(0), [countdown, setCountdown] = useState(0), [exitState, setExitState] = useState<'' | 'sending' | 'sent' | 'failed'>('')
  useEffect(() => {
    let alive = true
    fetch('/station-package.json', { cache: 'no-store' }).then(response => response.ok ? response.json() : null)
      .then(value => { if (alive && typeof value?.built_at === 'string') setPackageBuilt(value.built_at) }).catch(() => undefined)
    return () => { alive = false }
  }, [])
  useEffect(() => {
    if (exitStep !== 3) return
    const timer = setInterval(() => setCountdown(value => Math.max(0, value - 1)), 1000)
    return () => clearInterval(timer)
  }, [exitStep])
  async function exitKiosk() {
    setExitState('sending')
    try {
      const response = await fetch('/station/exit', { method: 'POST' })
      setExitState(response.ok ? 'sent' : 'failed')
    } catch { setExitState('failed') }
  }
  if (exitStep > 0) {
    const steps = [
      null,
      { title: 'Exit kiosk mode?', text: 'The scanner page closes and the Raspberry Pi desktop appears. Use this only for maintenance.', action: 'Continue' },
      { title: 'Are you sure?', text: `${info.roomName} cannot record any scans until this Pi is rebooted.`, action: 'Yes, I’m sure' },
      { title: 'Last step', text: 'Restart the Pi (or run systemctl --user start fabric-scanner.target) to return to the scanner.', action: 'Exit kiosk mode' },
    ]
    const step = steps[exitStep]!
    return <Modal title="Exit kiosk mode" onClose={onClose}>
      <div className="modal-body kiosk-exit" data-testid="exit-step" data-step={exitStep}>
        <p className="kiosk-exit-step">Step {exitStep} of 3</p>
        <h3>{exitState === 'sent' ? 'Exiting kiosk mode…' : step.title}</h3>
        <p>{exitState === 'sent' ? 'The scanner page is closing. Reboot the Pi to return to kiosk mode.' : step.text}</p>
        {exitState === 'failed' && <div className="notice error" role="alert">The station did not respond. Kiosk mode is still on; reboot the Pi or try again.</div>}
      </div>
      {exitState !== 'sent' && <div className="modal-actions">
        <button type="button" className="button secondary" disabled={exitState === 'sending'} onClick={() => { setExitStep(0); setExitState('') }}>Cancel</button>
        {exitStep < 3
          ? <button type="button" className="button danger" onClick={() => { if (exitStep === 2) setCountdown(3); setExitStep(exitStep + 1) }}>{step.action}</button>
          : <button type="button" className="button danger" disabled={countdown > 0 || exitState === 'sending'} onClick={() => void exitKiosk()}>
              {countdown > 0 ? `${step.action} (${countdown})` : exitState === 'sending' ? 'Exiting…' : step.action}</button>}
      </div>}
    </Modal>
  }
  const rows: [string, React.ReactNode][] = [
    ['Station', <>{info.stationId}<small> · {info.roomName}</small></>],
    ['Station IP', info.stationIps.length ? info.stationIps.join(' · ') : 'Unknown'],
    ['Server', <>{info.server}{info.reportedServer && info.reportedServer !== info.server && <small> · {info.reportedServer}</small>}</>],
    ['Connection', info.online ? 'Online' : <span className="kiosk-settings-bad">Offline</span>],
    ['Scanner', info.scanner],
    ['Station package', packageBuilt ? formatTime(packageBuilt) : '—'],
    ['Screen', `${window.innerWidth} × ${window.innerHeight}`],
  ]
  return <Modal title="Station settings" onClose={onClose} wide>
    <div className="modal-body kiosk-settings">
      <section aria-label="Conditioning room">
        <h3>Room</h3>
        <div className="kiosk-settings-rooms" role="group" aria-label="Conditioning room">
          {rooms.map(choice => <button key={choice.stationId} type="button" className={station === choice.stationId ? 'selected' : ''}
            aria-pressed={station === choice.stationId} disabled={roomLocked} onClick={() => onChooseRoom(choice.stationId)}>{choice.label}</button>)}
        </div>
        <p className="kiosk-settings-help">{roomLocked ? 'Finish the current scan or undo before changing the room.' : 'Scans from this station are recorded in the selected room.'}</p>
      </section>
      <section aria-label="After idle">
        <h3>After idle, return to IN + Stack</h3>
        <div className="kiosk-settings-choices" role="group" aria-label="Idle reset">
          {IDLE_CHOICES.map(minutes => <button key={minutes} type="button" aria-pressed={idleMinutes === minutes} onClick={() => onIdleMinutes(minutes)}>{minutes ? `${minutes} min` : 'Off'}</button>)}
        </div>
        <p className="kiosk-settings-help">The next person starts from IN and Stack. The room never changes.</p>
      </section>
      <section aria-label="Scanner setup">
        <h3>Scanner setup</h3>
        <button type="button" className="button secondary" onClick={onScannerSetup}>Show scanner setup codes</button>
      </section>
      <section aria-label="Station info" className="kiosk-settings-info">
        <h3>Station info</h3>
        <dl>{rows.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl>
        <button type="button" className="button secondary" disabled={roomLocked} onClick={() => location.reload()}>Reload page</button>
      </section>
      {info.onPi && <section aria-label="Exit kiosk mode" className="kiosk-settings-exit">
        <h3>Exit kiosk mode</h3>
        <p className="kiosk-settings-help">Shows the Pi desktop for maintenance. A reboot returns to kiosk mode.</p>
        <button type="button" className="button danger-quiet" disabled={roomLocked} onClick={() => setExitStep(1)}>Exit kiosk mode…</button>
      </section>}
    </div>
    <div className="modal-actions"><button type="button" className="button primary" onClick={onClose}>Done</button></div>
  </Modal>
}
