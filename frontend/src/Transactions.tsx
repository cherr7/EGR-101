import { useEffect, useRef, useState } from 'react'
import { api, ApiError, easternToUTC, entryOf, exitOf, formatTime, localTimeInput, post, serverNow, specimenNumber, specimenNumbers } from './api'
import type { AuditEvent, Config, Specimen, Stack } from './types'
import { ErrorNotice, Modal, PageHeader } from './App'

type Editor = { event: AuditEvent; stack: Stack; ids: string[]; time: string; room: string; note: string }
type Missed = { stackId: string; ids: string[]; direction: 'IN' | 'OUT'; time: string; room: string; note: string }
type Context = { stackId: string; eventId: string }
function readContext(): Context {
  const parameters = new URLSearchParams(location.hash.split('?')[1] || '')
  return { stackId: parameters.get('stack') || '', eventId: parameters.get('event') || '' }
}
function idsFor(event: AuditEvent) { return event.effective_specimen_ids || event.specimen_ids }
function timeFor(event: AuditEvent) { return event.effective_occurred_at || event.occurred_at || event.recorded_at }
function labelFor(event: AuditEvent) {
  return event.kind === 'movement' ? event.direction : event.kind.replaceAll('_', ' ')
}
function numbers(stack: Stack, ids: string[]) {
  return specimenNumbers(stack.specimens.filter(specimen => ids.includes(specimen.id)).map(specimen => specimen.number))
}
function eligible(specimen: Specimen, direction: string | undefined) {
  return direction === 'IN' ? !entryOf(specimen) : specimen.state === 'IN'
}
function projectedCounts(editor: Editor) {
  const previous = new Set(idsFor(editor.event)), chosen = new Set(editor.ids)
  return editor.stack.specimens.reduce((counts, specimen) => {
    let state = exitOf(specimen) ? 'finished' : entryOf(specimen) ? 'inside' : 'awaiting'
    if (previous.has(specimen.id) && !chosen.has(specimen.id)) state = editor.event.direction === 'OUT' ? 'inside' : 'awaiting'
    if (!previous.has(specimen.id) && chosen.has(specimen.id)) state = editor.event.direction === 'OUT' ? 'finished' : 'inside'
    counts[state as keyof typeof counts]++
    return counts
  }, { inside: 0, finished: 0, awaiting: 0 })
}

export default function Transactions({ config, healthy }: { config: Config; healthy: boolean }) {
  const [items, setItems] = useState<AuditEvent[]>([])
  const [stacks, setStacks] = useState<Stack[]>([])
  const [context, setContext] = useState(readContext)
  const [search, setSearch] = useState('')
  const [kind, setKind] = useState('')
  const [roomFilter, setRoomFilter] = useState('')
  const [limit, setLimit] = useState(100)
  const [hasMore, setHasMore] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [editor, setEditor] = useState<Editor | null>(null)
  const [viewing, setViewing] = useState<{ event: AuditEvent; stack: Stack } | null>(null)
  const [undo, setUndo] = useState<Editor | null>(null)
  const [missed, setMissed] = useState<Missed | null>(null)
  const alive = useRef(true), serial = useRef(0), mutation = useRef(false), openedContext = useRef('')
  useEffect(() => { alive.current = true; return () => { alive.current = false; serial.current++ } }, [])
  useEffect(() => {
    const change = () => { setContext(readContext()); setEditor(null); setViewing(null); setUndo(null) }
    window.addEventListener('hashchange', change)
    return () => window.removeEventListener('hashchange', change)
  }, [])
  useEffect(() => {
    if (!healthy) return
    let current = true
    const timer = setTimeout(() => {
      setLoading(true)
      const query = new URLSearchParams({ limit: String(limit) })
      if (search) query.set('q', search)
      if (kind) query.set('kind', kind)
      if (roomFilter) query.set('room_id', roomFilter)
      if (context.stackId) query.set('stack_id', context.stackId)
      void Promise.all([
        api<{ items: AuditEvent[]; has_more?: boolean }>('/transactions?' + query),
        api<{ items: Stack[] }>('/stacks'),
      ]).then(([events, rows]) => {
        if (current) { setItems(events.items); setHasMore(!!events.has_more); setStacks(rows.items) }
      }).catch(err => { if (current) setError((err as Error).message) })
        .finally(() => { if (current) setLoading(false) })
    }, 160)
    return () => { current = false; clearTimeout(timer) }
  }, [healthy, search, kind, roomFilter, context.stackId, limit, refresh])

  async function openEvent(eventId: string, stackId: string) {
    if (mutation.current || !healthy) return
    const token = ++serial.current
    setBusy(true)
    setError('')
    try {
      // A single fresh stack response supplies BOTH event values and version.
      // Never pair an old list row with a newer version from a separate fetch.
      const stack = await api<Stack>('/stacks/' + stackId)
      const event = stack.events?.find(row => row.id === eventId)
      if (!event) throw new Error('This transaction is no longer in the stack history. Refresh the log.')
      if (!alive.current || token !== serial.current) return
      if (event.kind !== 'movement' || event.undone) { setViewing({ event, stack }); setEditor(null) }
      else {
        setViewing(null)
        setEditor({
          event, stack, ids: idsFor(event), time: localTimeInput(timeFor(event)),
          room: event.effective_room_id || event.room_id || config.rooms[0]?.id || '',
          note: event.note || event.reason || '',
        })
      }
    } catch (err) { if (alive.current && token === serial.current) setError((err as Error).message) }
    finally { if (alive.current && token === serial.current) setBusy(false) }
  }
  const openRef = useRef(openEvent)
  openRef.current = openEvent
  useEffect(() => {
    const key = context.stackId + ':' + context.eventId
    if (!healthy || !context.stackId || !context.eventId || openedContext.current === key) return
    openedContext.current = key
    void openRef.current(context.eventId, context.stackId)
  }, [healthy, context.stackId, context.eventId])

  async function save() {
    const chosen = editor
    if (!chosen || mutation.current || !healthy) return
    mutation.current = true
    setBusy(true)
    setError('')
    try {
      const body = {
        specimen_ids: chosen.ids,
        ...(chosen.time !== localTimeInput(timeFor(chosen.event)) ? { occurred_at: easternToUTC(chosen.time) } : {}),
        room_id: chosen.room, note: chosen.note, expected_version: chosen.stack.version,
      }
      await api('/transactions/' + chosen.event.id, { method: 'PATCH', body: JSON.stringify(body) })
      if (alive.current) {
        setEditor(null)
        setRefresh(value => value + 1)
        setMessage('Change saved. The original observation remains in the transaction history.')
      }
    } catch (err) {
      if (alive.current) setError(err instanceof ApiError ? err.message :
        'The saved result is not confirmed. Reload the latest transaction before trying another change.')
    } finally { mutation.current = false; if (alive.current) setBusy(false) }
  }
  async function cancel() {
    const chosen = undo
    if (!chosen || mutation.current || !healthy) return
    mutation.current = true
    setBusy(true)
    setError('')
    try {
      try { await post('/transactions/' + chosen.event.id + '/undo', { expected_version: chosen.stack.version, note: chosen.note }) }
      catch (err) {
        const latest = await api<Stack>('/stacks/' + chosen.stack.id).catch(() => null)
        if (!latest?.events?.find(event => event.id === chosen.event.id)?.undone) throw err
      }
      if (alive.current) {
        setUndo(null); setEditor(null); setRefresh(value => value + 1)
        setMessage('The entire transaction was undone. The original and undo both remain in history.')
      }
    } catch (err) {
      if (alive.current) setError(err instanceof ApiError ? err.message :
        'Undo is not yet confirmed. Reload the latest transaction before trying again.')
    } finally { mutation.current = false; if (alive.current) setBusy(false) }
  }
  async function saveMissed() {
    const chosen = missed
    if (!chosen || mutation.current || !healthy) return
    mutation.current = true
    setBusy(true)
    setError('')
    try {
      const stack = stacks.find(row => row.id === chosen.stackId)
      await post('/transactions/missed', {
        stack_id: chosen.stackId, specimen_ids: chosen.ids, direction: chosen.direction,
        occurred_at: easternToUTC(chosen.time), room_id: chosen.room,
        note: chosen.note, expected_version: stack?.version,
      })
      if (alive.current) {
        setMissed(null); setRefresh(value => value + 1)
        setMessage('Missed movement recorded. Claimed movement time and actual recording time are stored separately.')
      }
    } catch (err) { if (alive.current) setError((err as Error).message) }
    finally { mutation.current = false; if (alive.current) setBusy(false) }
  }
  const visible = items.filter(event => !kind || event.kind === kind)
  const missedStack = stacks.find(stack => stack.id === missed?.stackId)
  const scopedStack = stacks.find(stack => stack.id === context.stackId)
  const roomName = (id?: string) => config.rooms.find(room => room.id === id)?.name || id || '—'
  const projection = editor ? projectedCounts(editor) : null
  const originalIds = editor ? idsFor(editor.event) : []
  const removedIds = editor ? originalIds.filter(id => !editor.ids.includes(id)) : []
  const addedIds = editor ? editor.ids.filter(id => !originalIds.includes(id)) : []

  return <>
    <PageHeader title="Transactions">
      <button className="button primary" disabled={!healthy || busy || !stacks.length} onClick={() => {
        setError('')
        setMissed({ stackId: context.stackId, ids: [], direction: 'IN',
          time: localTimeInput(new Date(serverNow()).toISOString()),
          room: config.rooms.find(room => room.id === 'shared')?.id || config.rooms[0]?.id || '', note: '' })
      }}>＋ Record missed scan</button>
    </PageHeader>
    <ErrorNotice message={error} />
    {message && <div className="notice success" role="status">{message}</div>}
    {context.stackId && <div className="context-strip">
      <span>Stack activity: <b>{scopedStack ? scopedStack.project_number + ' / ' + scopedStack.material_ref + ' / ' + scopedStack.preconditioning_code : context.stackId}</b></span>
      <a className="button text" href="#transactions">Show all activity</a>
    </div>}
    <section className="card">
      <div className="toolbar">
        <label className="search-label"><span>⌕</span><input aria-label="Search transactions"
          placeholder="Project, material, test, treatment or transaction ID" value={search}
          onChange={e => { setSearch(e.target.value); setLimit(100) }} /></label>
        <select aria-label="Transaction room" value={roomFilter} onChange={e => { setRoomFilter(e.target.value); setLimit(100) }}>
          <option value="">All rooms</option>{config.rooms.map(room => <option key={room.id} value={room.id}>{room.name}</option>)}
        </select>
        <select aria-label="Transaction type" value={kind} onChange={e => setKind(e.target.value)}>
          <option value="">All activity</option><option value="movement">Movements</option><option value="modification">Modifications</option><option value="undo">Undo</option><option value="label_print">Printing</option>
        </select>
        <button className="button secondary" disabled={!healthy || busy || loading} onClick={() => setRefresh(value => value + 1)}>↻ Refresh</button>
      </div>
      <div className="table-wrap"><table>
        <thead><tr><th>Recorded time</th><th>Activity</th><th>Project / Material</th><th>Specimens</th><th>Actual movement time</th><th>Room</th><th /></tr></thead>
        <tbody>{visible.map(event => {
          const stack = stacks.find(row => row.id === event.stack_id)
          return <tr key={event.id} className={event.undone ? 'undone-row' : ''}>
            <td>{formatTime(event.recorded_at)}<small className="id-hint">{event.id.slice(0, 8)}</small></td>
            <td><span className={'badge ' + (event.kind === 'movement' ? event.direction === 'IN' ? 'good' : 'blue' : 'muted')}>{labelFor(event)}</span>
              {event.modified && <small>Modified</small>}{event.undone && <small>Undone</small>}</td>
            <td><b>{stack?.project_number || '—'}</b><small>{stack?.material_ref || event.stack_id?.slice(0, 8)}</small></td>
            <td><b>{event.effective_count ?? event.affected_count}</b>{event.modified && <small>Originally {event.affected_count}</small>}</td>
            <td>{formatTime(timeFor(event))}</td><td>{roomName(event.effective_room_id || event.room_id)}</td>
            <td><button className="button text" disabled={!healthy || busy}
              aria-label={(event.kind === 'movement' && !event.undone ? 'Edit' : 'View') + ' transaction ' + event.id}
              onClick={() => void openEvent(event.id, event.stack_id)}>{event.kind === 'movement' && !event.undone ? 'Edit →' : 'View →'}</button></td>
          </tr>
        })}</tbody>
      </table></div>
      {!visible.length && <div className="empty-inline"><h3>{loading ? 'Loading activity…' : 'No matching transactions'}</h3><p>Try a project or material reference, or return to all activity.</p></div>}
      <div className="table-foot activity-foot">
        <span>Showing the latest {items.length} matching events. Original observations are retained.</span>
        {hasMore && <button className="button text" disabled={limit >= 500 || loading}
          onClick={() => setLimit(value => Math.min(500, value + 100))}>{limit >= 500 ? 'Narrow the filters to find older activity' : 'Load more activity'}</button>}
      </div>
    </section>

    {editor && <Modal title={'Edit ' + editor.event.direction + ' transaction'} wide onClose={() => !busy && setEditor(null)}>
      <div className="modal-body">
        <div className="editor-context">
          <div><b>{editor.stack.project_number} / {editor.stack.material_ref}</b><p>{editor.stack.test_code} · {editor.stack.preconditioning_code}</p></div>
          <span className="badge outline">{editor.ids.length} specimens</span>
        </div>
        {editor.event.can_undo === false && <div className="notice warning">A specimen has later activity. Review or correct that later movement before changing this transaction.</div>}
        <div className="form-grid">
          <label>Movement time (America/New_York)<input aria-label="Movement time" type="datetime-local" step="1"
            disabled={busy} value={editor.time} onChange={e => setEditor(previous => previous && { ...previous, time: e.target.value })} /></label>
          <label>Room<select aria-label="Movement room" value={editor.room} disabled={busy}
            onChange={e => setEditor(previous => previous && { ...previous, room: e.target.value })}>
            {config.rooms.map(room => <option key={room.id} value={room.id}>{room.name}</option>)}
          </select></label>
        </div>
        <div className="selection-heading"><h3>Affected numbered specimens</h3><span>{editor.ids.length} of {editor.stack.specimen_count}</span></div>
        <div className="selection-tools">
          <button className="button text" disabled={busy} onClick={() => setEditor(previous => previous && {
            ...previous, ids: previous.stack.specimens.filter(specimen => idsFor(previous.event).includes(specimen.id) || eligible(specimen, previous.event.direction)).map(specimen => specimen.id),
          })}>Select all eligible</button>
          <button className="button text" disabled={busy} onClick={() => setEditor(previous => previous && { ...previous, ids: [] })}>Clear</button>
        </div>
        <div className="selection-grid edit-selection">
          {editor.stack.specimens.map(specimen => {
            const allowed = originalIds.includes(specimen.id) || eligible(specimen, editor.event.direction)
            return <label key={specimen.id} className={'selection-tile ' + (editor.ids.includes(specimen.id) ? 'selected' : '') + (!allowed ? ' disabled' : '')}>
              <input type="checkbox" disabled={busy || !allowed} checked={editor.ids.includes(specimen.id)}
                onChange={e => setEditor(previous => previous && {
                  ...previous, ids: e.target.checked ? [...previous.ids, specimen.id] : previous.ids.filter(id => id !== specimen.id),
                })} />
              <b>{specimenNumber(specimen.number)}</b><small>{specimen.state === 'IN' ? 'Inside' : entryOf(specimen) ? 'Finished' : 'Awaiting'}</small>
            </label>
          })}
        </div>
        <section className="correction-preview" aria-label="Correction result preview">
          <div><span>Current {editor.event.direction}</span><b>{originalIds.length} specimens</b></div>
          <span className="correction-arrow">→</span>
          <div><span>After saving {editor.event.direction}</span><b>{editor.ids.length} specimens</b></div>
          <p>Selected numbers: <b>{numbers(editor.stack, editor.ids) || 'None'}</b></p>
          {removedIds.length > 0 && <p>Excluded from this movement: <b>{numbers(editor.stack, removedIds)}</b></p>}
          {addedIds.length > 0 && <p>Added to this movement: <b>{numbers(editor.stack, addedIds)}</b></p>}
          {projection && <p className="correction-projection">Result: <b>{projection.inside} inside</b> · <b>{projection.finished} finished</b> · <b>{projection.awaiting} awaiting entry</b></p>}
          {editor.time !== localTimeInput(timeFor(editor.event)) && <p>Movement time: {formatTime(timeFor(editor.event))} → {editor.time.replace('T', ' ')} Eastern</p>}
          {editor.room !== (editor.event.effective_room_id || editor.event.room_id) && <p>Room: {roomName(editor.event.effective_room_id || editor.event.room_id)} → {roomName(editor.room)}</p>}
        </section>
        <label>Notes (optional)<textarea aria-label="Transaction notes" rows={2} disabled={busy} value={editor.note}
          onChange={e => setEditor(previous => previous && { ...previous, note: e.target.value })} /></label>
        <p className="input-help">Original scan recorded {formatTime(editor.event.recorded_at)} · Original count {editor.event.affected_count}. Every saved change gets its own recording time.</p>
        <ErrorNotice message={error} />
        {error && <button className="button secondary" disabled={busy} onClick={() => void openEvent(editor.event.id, editor.stack.id)}>Reload latest values</button>}
      </div>
      <div className="modal-actions">
        <button className="button danger-quiet" disabled={!healthy || busy || editor.event.can_undo === false} onClick={() => setUndo(editor)}>Undo entire transaction</button>
        <span className="grow" /><button className="button secondary" disabled={busy} onClick={() => setEditor(null)}>Cancel</button>
        <button className="button primary" disabled={!healthy || busy || !editor.ids.length || !editor.time || editor.event.can_undo === false}
          onClick={() => void save()}>{busy ? 'Saving…' : 'Save change'}</button>
      </div>
    </Modal>}
    {undo && <Modal title="Undo the entire transaction?" onClose={() => !busy && setUndo(null)}>
      <div className="modal-body"><p className="modal-intro">Reverse {undo.event.direction} for all {idsFor(undo.event).length} specimens affected by this transaction. The original record remains visible.</p><ErrorNotice message={error} /></div>
      <div className="modal-actions"><button className="button secondary" disabled={busy} onClick={() => setUndo(null)}>Keep transaction</button>
        <button className="button danger" disabled={!healthy || busy} onClick={() => void cancel()}>Undo transaction</button></div>
    </Modal>}
    {viewing && <Modal title="Transaction details" wide onClose={() => setViewing(null)}>
      <div className="modal-body">
        <div className="source-grid">
          <div><span>Activity</span><b>{labelFor(viewing.event)} {viewing.event.direction}</b></div>
          <div><span>Recorded</span><b>{formatTime(viewing.event.recorded_at)}</b></div>
          <div><span>Affected specimens</span><b>{viewing.event.effective_count ?? viewing.event.affected_count}</b></div>
          <div><span>Numbered specimens</span><b>{numbers(viewing.stack, idsFor(viewing.event))}</b></div>
          <div><span>Notes</span><b>{viewing.event.note || viewing.event.reason || '—'}</b></div>
          <div><span>Transaction</span><b>{viewing.event.id}</b></div>
        </div>
        {viewing.event.payload?.event_id ? <a className="button secondary"
          href={'#transactions?stack=' + viewing.stack.id + '&event=' + String(viewing.event.payload.event_id)}>Open original transaction →</a> : null}
        {viewing.event.undone && <div className="notice info">This movement was undone. Its original observation is retained.</div>}
      </div>
      <div className="modal-actions"><button className="button secondary" onClick={() => setViewing(null)}>Close</button></div>
    </Modal>}
    {missed && <Modal title="Record a missed scan" wide onClose={() => !busy && setMissed(null)}>
      <div className="modal-body">
        <p className="modal-intro">Record when the movement actually happened. The time this correction is entered is retained separately.</p>
        <label>Stack<select aria-label="Missed stack" value={missed.stackId} disabled={busy}
          onChange={e => setMissed(previous => previous && { ...previous, stackId: e.target.value, ids: [] })}>
          <option value="">Choose a stack</option>{stacks.map(stack => <option key={stack.id} value={stack.id}>{stack.project_number} / {stack.material_ref} / {stack.test_code} / {stack.preconditioning_code}</option>)}
        </select></label>
        <div className="form-grid three">
          <label>Direction<select aria-label="Missed direction" value={missed.direction} disabled={busy}
            onChange={e => setMissed(previous => previous && { ...previous, direction: e.target.value as 'IN' | 'OUT', ids: [] })}><option>IN</option><option>OUT</option></select></label>
          <label>Actual movement time (Eastern)<input aria-label="Missed movement time" type="datetime-local" step="1" disabled={busy}
            value={missed.time} onChange={e => setMissed(previous => previous && { ...previous, time: e.target.value })} /></label>
          <label>Room<select aria-label="Missed room" value={missed.room} disabled={busy}
            onChange={e => setMissed(previous => previous && { ...previous, room: e.target.value })}>
            {config.rooms.map(room => <option key={room.id} value={room.id}>{room.name}</option>)}
          </select></label>
        </div>
        {missedStack && <>
          <div className="selection-heading"><h3>Numbered specimens</h3>
            <button className="button text" disabled={busy} onClick={() => setMissed(previous => previous && {
              ...previous, ids: missedStack.specimens.filter(specimen => eligible(specimen, previous.direction)).map(specimen => specimen.id),
            })}>Select all eligible</button></div>
          <div className="selection-grid">{missedStack.specimens.map(specimen => {
            const allowed = eligible(specimen, missed.direction)
            return <label className={'selection-tile ' + (missed.ids.includes(specimen.id) ? 'selected' : '') + (!allowed ? ' disabled' : '')} key={specimen.id}>
              <input type="checkbox" disabled={busy || !allowed} checked={missed.ids.includes(specimen.id)}
                onChange={e => setMissed(previous => previous && {
                  ...previous, ids: e.target.checked ? [...previous.ids, specimen.id] : previous.ids.filter(id => id !== specimen.id),
                })} /><b>{specimenNumber(specimen.number)}</b>
              <small>{specimen.state === 'IN' ? 'Inside' : entryOf(specimen) ? 'Finished' : 'Awaiting'}</small>
            </label>
          })}</div>
        </>}
        <label>Notes (optional)<textarea aria-label="Missed notes" rows={2} disabled={busy} value={missed.note}
          onChange={e => setMissed(previous => previous && { ...previous, note: e.target.value })} /></label>
        <ErrorNotice message={error} />
      </div>
      <div className="modal-actions"><span>{missed.ids.length} selected</span>
        <button className="button secondary" disabled={busy} onClick={() => setMissed(null)}>Cancel</button>
        <button className="button primary" disabled={!healthy || busy || !missed.stackId || !missed.ids.length || !missed.time}
          onClick={() => void saveMissed()}>Record missed {missed.direction}</button></div>
    </Modal>}
  </>
}
