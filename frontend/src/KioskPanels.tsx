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
