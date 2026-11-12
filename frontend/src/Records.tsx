import { useEffect, useRef, useState } from 'react'
import { api, elapsed, entryOf, exitOf, formatTime, isReady, specimenStatus } from './api'
import type { Config, Specimen, SpecimenRecord, Stack } from './types'
import { ErrorNotice, Modal, PageHeader } from './App'
import LabelDialog from './LabelDialog'
import StackEditor from './StackEditor'

export function Status({ specimen }: { specimen: Specimen }) {
  return <span className={'badge ' + (isReady(specimen) ? 'good' : specimen.state === 'IN' ? 'warning' : 'muted')}>
    {isReady(specimen) ? 'Ready' : specimenStatus(specimen)}
  </span>
}
function recordedPeriod(value: unknown) {
  if (!Array.isArray(value)) return null
  const period = value.find(row => row && typeof row === 'object' && !row.voided)
  return period as { entered_at?: string; exited_at?: string } | undefined
}
function SpecimenHistory({
  record, stack, config, healthy, onClose,
}: { record: SpecimenRecord; stack: Stack; config: Config; healthy: boolean; onClose: () => void }) {
  const [printing, setPrinting] = useState(false)
  const roomId = record.room_id || record.sessions?.[0]?.room_id
  const history = [...(record.raw_json_time || [])].reverse()
  return <>
    <Modal title={'Specimen ' + String(record.number).padStart(2, '0') + ' · history'} wide onClose={onClose}>
      <div className="modal-body">
        <div className="detail-summary">
          <div><span className="eyebrow">{record.project_number} / {record.material_ref}</span>
            <h3>{record.test_code} · {record.preconditioning_code}</h3>
            <p>Specimen {record.number} of {stack.specimen_count} · {config.rooms.find(room => room.id === roomId)?.name || roomId || 'No room entry'}</p>
          </div>
          <Status specimen={record} />
        </div>
        <div className="specimen-identity"><span>Permanent label ID</span><code>{record.barcode_value}</code></div>
        <div className="specimen-time-grid">
          <div><span>ENTRY</span><b>{formatTime(entryOf(record))}</b></div>
          <div><span>EXIT</span><b>{formatTime(exitOf(record))}</b></div>
          <div><span>Residence time</span><b>{elapsed(entryOf(record), exitOf(record))}</b></div>
        </div>
        <h3 className="section-title">This specimen’s observations & changes</h3>
        <div className="specimen-history-list">
          {history.map((item, index) => {
            const kind = String(item.type || item.kind || 'Observation')
            const direction = item.direction ? String(item.direction) : ''
            const before = recordedPeriod(item.before), after = recordedPeriod(item.after)
            return <article className="specimen-history-row" key={String(item.transaction_id || index)}>
              <div className="history-row-heading">
                <b>{kind === 'movement' ? direction : kind.replaceAll('_', ' ') + (direction ? ' · ' + direction : '')}</b>
                <span>{formatTime(String(item.recorded_at || ''))}</span>
              </div>
              {item.occurred_at && item.occurred_at !== item.recorded_at
                ? <p>Movement claimed at {formatTime(String(item.occurred_at))}; recorded at {formatTime(String(item.recorded_at))}.</p> : null}
              {(before || after) && <div className="history-before-after">
                <div><span>Before</span><b>ENTRY {formatTime(before?.entered_at)}</b><small>EXIT {formatTime(before?.exited_at)}</small></div>
                <div><span>After</span><b>ENTRY {formatTime(after?.entered_at)}</b><small>EXIT {formatTime(after?.exited_at)}</small></div>
              </div>}
              {item.note ? <p>{String(item.note)}</p> : null}
              {item.transaction_id && (kind === 'movement' || item.original_transaction_id)
                ? <a className="button text" href={'#transactions?stack=' + stack.id + '&event=' + String(item.original_transaction_id || item.transaction_id)}>
                  Open transaction →
                </a> : null}
            </article>
          })}
          {!history.length && <div className="empty-inline"><h3>No observations yet</h3><p>The first movement or label print will appear here.</p></div>}
        </div>
      </div>
      <div className="modal-actions">
        <button className="button secondary" disabled={!healthy} onClick={() => setPrinting(true)}>Reprint this specimen’s label</button>
        <span className="grow" /><button className="button secondary" onClick={onClose}>Close</button>
      </div>
    </Modal>
    {printing && <LabelDialog stacks={[stack]} initialSelectedIds={[record.id]} healthy={healthy} onClose={() => setPrinting(false)} />}
  </>
}

export function StackDetail({ stack: given, config, healthy = true, onChanged }: { stack: Stack; config: Config; healthy?: boolean; onChanged?: (stack: Stack) => void }) {
  const [edited, setEdited] = useState<Stack | null>(null)
  const stack = edited && edited.id === given.id ? edited : given
  const [editing, setEditing] = useState(false)
  const [printing, setPrinting] = useState(false)
  const [focused, setFocused] = useState<SpecimenRecord | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const alive = useRef(true), operation = useRef(0)
  useEffect(() => { alive.current = true; return () => { alive.current = false; operation.current++ } }, [])
  useEffect(() => { operation.current++; setFocused(null); setError(''); setLoading(false) }, [stack.id])
  async function openSpecimen(id: number) {
    const token = ++operation.current
    setLoading(true)
    setError('')
    try {
      const value = await api<SpecimenRecord>('/specimens/' + id)
      if (alive.current && token === operation.current) setFocused(value)
    } catch (err) { if (alive.current && token === operation.current) setError((err as Error).message) }
    finally { if (alive.current && token === operation.current) setLoading(false) }
  }
  const roomName = (id?: string | null) => config.rooms.find(room => room.id === id)?.name || id || '—'
  return <div className="stack-detail">
    <div className="detail-summary">
      <div><span className="eyebrow">{stack.project_number}</span><h3>{stack.material_ref}</h3>
        <p>{stack.test_code} · {stack.preconditioning_code} · {stack.specimen_count} specimens {stack.is_demo && <span className="badge muted">DEMO</span>}</p>
      </div>
      <div className="detail-actions">
        <button className="button secondary" disabled={!healthy} onClick={() => setEditing(true)}>Edit stack</button>
        <button className="button secondary" disabled={!healthy} onClick={() => setPrinting(true)}>Preview / reprint labels</button>
      </div>
    </div>
    <ErrorNotice message={error} />
    <div className="table-wrap"><table>
      <thead><tr><th>Specimen</th><th>Status</th><th>Room</th><th>ENTRY</th><th>EXIT</th><th>Residence time</th><th /></tr></thead>
      <tbody>{stack.specimens.map(specimen => <tr key={specimen.id}>
        <td><b>#{String(specimen.number).padStart(2, '0')}</b>
          <small className="id-hint" title={specimen.barcode_value}>{specimen.barcode_value}</small></td>
        <td><Status specimen={specimen} /></td>
        <td>{roomName(specimen.room_id || specimen.sessions?.[0]?.room_id)}</td>
        <td>{formatTime(entryOf(specimen))}</td><td>{formatTime(exitOf(specimen))}</td>
        <td>{elapsed(entryOf(specimen), exitOf(specimen))}</td>
        <td><button className="button text" disabled={!healthy || loading} onClick={() => void openSpecimen(specimen.id)}>History →</button></td>
      </tr>)}</tbody>
    </table></div>
    <h3 className="section-title">Stack activity</h3>
    <div className="audit-list">
      {(stack.events || []).map(event => <div className="audit-row" key={event.id}>
        <span className={'audit-mark ' + (event.direction === 'IN' ? 'in' : '')}>{event.kind === 'movement' ? event.direction : '↺'}</span>
        <div><b>{event.kind.replaceAll('_', ' ')} {event.direction || ''}
          {event.undone && <span className="badge muted">Undone</span>}{event.modified && <span className="badge warning">Modified</span>}</b>
          <small>{formatTime(event.recorded_at)} · {event.effective_count ?? event.affected_count} specimens</small>
          {(event.note || event.reason) && <p>{event.note || event.reason}</p>}
        </div>
        <a className="button text" href={'#transactions?stack=' + stack.id + '&event=' + String(event.payload?.event_id || event.id)}>
          {event.kind === 'movement' && !event.undone ? 'Edit →' : 'View →'}
        </a>
      </div>)}
      {!stack.events?.length && <p className="subtle">No movement or printing history yet.</p>}
    </div>
    {printing && <LabelDialog stacks={[stack]} healthy={healthy} onClose={() => setPrinting(false)} />}
    {editing && <StackEditor stack={stack} config={config} healthy={healthy} onClose={() => setEditing(false)}
      onSaved={value => { setEdited(value); onChanged?.(value) }} />}
    {focused && <SpecimenHistory record={focused} stack={stack} config={config} healthy={healthy} onClose={() => setFocused(null)} />}
  </div>
}

export default function Records({ config, healthy }: { config: Config; healthy: boolean }) {
  const [items, setItems] = useState<Stack[]>([])
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [roomId, setRoomId] = useState('')
  const [project, setProject] = useState('')
  const [stackFilter, setStackFilter] = useState(() => new URLSearchParams(location.hash.split('?')[1] || '').get('stack') || '')
  const [selectedStack, setSelectedStack] = useState<Stack | null>(null)
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [focused, setFocused] = useState<{ record: SpecimenRecord; stack: Stack } | null>(null)
  const [printing, setPrinting] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [opening, setOpening] = useState(false)
  const [revision, setRevision] = useState(0)
  const alive = useRef(true), openSerial = useRef(0)
  useEffect(() => { alive.current = true; return () => { alive.current = false; openSerial.current++ } }, [])
  useEffect(() => {
    const change = () => setStackFilter(new URLSearchParams(location.hash.split('?')[1] || '').get('stack') || '')
    window.addEventListener('hashchange', change)
    return () => window.removeEventListener('hashchange', change)
  }, [])
  useEffect(() => {
    if (!healthy) return
    let current = true
    setLoading(true)
    void api<{ items: Stack[] }>('/stacks').then(value => {
      if (current) {
        setItems(value.items)
        setError('')
        const known = new Set(value.items.flatMap(stack => stack.specimens.map(s => s.id)))
        setSelectedIds(previous => previous.filter(id => known.has(id)))
      }
    }).catch(err => { if (current) setError((err as Error).message) })
      .finally(() => { if (current) setLoading(false) })
    return () => { current = false }
  }, [healthy, revision])
  const records = items.flatMap(stack => stack.specimens.map(specimen => ({ stack, specimen }))).filter(({ stack, specimen }) =>
    (!stackFilter || String(stack.id) === stackFilter) &&
    (!search || (stack.project_number + ' ' + stack.material_ref + ' ' + stack.test_code + ' ' + stack.preconditioning_code +
      ' ' + specimen.id + ' ' + specimen.barcode_value + ' #' + specimen.number).toLowerCase().includes(search.toLowerCase())) &&
    (!status || (status === 'READY' ? isReady(specimen) : specimenStatus(specimen) === status)) &&
    (!roomId || specimen.room_id === roomId || specimen.sessions?.[0]?.room_id === roomId) &&
    (!project || stack.project_number.includes(project)))
  async function selectSpecimen(stack: Stack, id: number) {
    const token = ++openSerial.current
    setOpening(true)
    setError('')
    try {
      const record = await api<SpecimenRecord>('/specimens/' + id)
      if (alive.current && token === openSerial.current) setFocused({ record, stack })
    } catch (err) { if (alive.current && token === openSerial.current) setError((err as Error).message) }
    finally { if (alive.current && token === openSerial.current) setOpening(false) }
  }
  async function openStack(id: number) {
    const token = ++openSerial.current
    setOpening(true)
    try {
      const stack = await api<Stack>('/stacks/' + id)
      if (alive.current && token === openSerial.current) setSelectedStack(stack)
    } catch (err) { if (alive.current && token === openSerial.current) setError((err as Error).message) }
    finally { if (alive.current && token === openSerial.current) setOpening(false) }
  }
  const openRef = useRef(openStack); openRef.current = openStack
  useEffect(() => { if (healthy && /^\d+$/.test(stackFilter)) void openRef.current(Number(stackFilter)) }, [healthy, stackFilter])
  const query = project ? '?project_number=' + encodeURIComponent(project) : ''
  const visibleIds = records.map(({ specimen }) => specimen.id)
  const selectedStacks = items.filter(stack => stack.specimens.some(s => selectedIds.includes(s.id)))
  return <>
    <PageHeader title="Specimens">
      <button className="button secondary" disabled={!healthy || loading} onClick={() => setRevision(value => value + 1)}>↻ Refresh</button>
    </PageHeader>
    <ErrorNotice message={error} />
    {stackFilter && <div className="context-strip"><span>Showing the selected physical stack.</span><a className="button text" href="#records">Show all registered specimens</a></div>}
    <section className="card">
      <div className="toolbar">
        <label className="search-label"><span>⌕</span><input aria-label="Search records" placeholder="Project, material, test, treatment or label ID" value={search} onChange={e => setSearch(e.target.value)} /></label>
        <select aria-label="Specimen status" value={status} onChange={e => setStatus(e.target.value)}>
          <option value="">All statuses</option><option value="READY">Ready</option><option>Conditioning</option><option>Awaiting entry</option><option>Finished</option>
        </select>
        <select aria-label="Record room" value={roomId} onChange={e => setRoomId(e.target.value)}>
          <option value="">All rooms</option>{config.rooms.map(room => <option key={room.id} value={room.id}>{room.name}</option>)}
        </select>
      </div>
      <div className="card-heading compact">
        <div><h2>Specimens <span className="count-pill">{records.length}</span></h2><p>{loading ? 'Loading records…' : 'Times are displayed in America/New_York.'}</p></div>
        <div className="export-actions">
          <input aria-label="Export project number" className="small-input" placeholder="Project for exports (optional)" value={project} onChange={e => setProject(e.target.value)} />
          <a className="button secondary" href={'/api/exports/sessions.csv' + query}>Session CSV</a>
          <a className="button secondary" href={'/api/exports/history.csv' + query}>Audit CSV</a>
          <a className="button secondary" target="_blank" rel="noreferrer" href={'/api/exports/report' + query}>Print report ↗</a>
        </div>
      </div>
      {selectedIds.length > 0 && <div className="record-selection-actions">
        <b>{selectedIds.length} specimen labels selected</b>
        <button className="button primary" disabled={!healthy} onClick={() => setPrinting(true)}>Preview / reprint selected</button>
        <button className="button text" onClick={() => setSelectedIds([])}>Clear selection</button>
        <small>Selection is kept when filtering; only the checked specimen IDs will print.</small>
      </div>}
      <div className="table-wrap"><table>
        <thead><tr>
          <th><input type="checkbox" aria-label="Select all visible specimen labels"
            checked={!!visibleIds.length && visibleIds.every(id => selectedIds.includes(id))}
            onChange={e => setSelectedIds(previous => e.target.checked ? [...new Set([...previous, ...visibleIds])] : previous.filter(id => !visibleIds.includes(id)))} /></th>
          <th>Specimen / Label ID</th><th>Project / Material</th><th>Test / Treatment</th><th>Status</th><th>ENTRY</th><th>EXIT</th><th />
        </tr></thead>
        <tbody>{records.map(({ stack, specimen }) => <tr key={specimen.id} className={selectedIds.includes(specimen.id) ? 'selected-record' : ''}>
          <td><input type="checkbox" aria-label={'Select label ' + specimen.number + ' ' + stack.material_ref}
            checked={selectedIds.includes(specimen.id)} onChange={e => setSelectedIds(previous => e.target.checked ? [...previous, specimen.id] : previous.filter(id => id !== specimen.id))} /></td>
          <td><b>#{String(specimen.number).padStart(2, '0')}</b><small className="id-hint" title={specimen.barcode_value}>{specimen.barcode_value}</small></td>
          <td><b>{stack.project_number}</b><small>{stack.material_ref}</small><button className="button text small-action" disabled={!healthy || opening} onClick={() => void openStack(stack.id)}>Stack details</button></td>
          <td>{stack.test_code}<small>{stack.preconditioning_code}</small></td>
          <td><Status specimen={specimen} /></td><td>{formatTime(entryOf(specimen))}</td><td>{formatTime(exitOf(specimen))}</td>
          <td><button className="button text" disabled={!healthy || opening}
            aria-label={'History specimen ' + specimen.number + ' ' + stack.material_ref}
            onClick={() => void selectSpecimen(stack, specimen.id)}>History →</button></td>
        </tr>)}</tbody>
      </table></div>
      {!records.length && !loading && <div className="empty-inline"><h3>No matching specimens</h3><p>Import a prep workbook or adjust the filters.</p></div>}
    </section>
    {selectedStack && <Modal title="Stack & specimen details" wide onClose={() => { openSerial.current++; setSelectedStack(null) }}>
      <div className="modal-body"><StackDetail stack={selectedStack} config={config} healthy={healthy}
        onChanged={value => { setSelectedStack(value); setRevision(old => old + 1) }} /></div>
      <div className="modal-actions"><button className="button secondary" onClick={() => setSelectedStack(null)}>Close</button></div>
    </Modal>}
    {focused && <SpecimenHistory record={focused.record} stack={focused.stack} config={config} healthy={healthy} onClose={() => setFocused(null)} />}
    {printing && <LabelDialog stacks={selectedStacks} initialSelectedIds={selectedIds} healthy={healthy} onClose={() => setPrinting(false)} />}
  </>
}
