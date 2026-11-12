import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, duration, formatTime, hashParams } from './api'
import type { AgeBucket, Config, DashboardData, PipelineStack, ReadyRoom, Stack } from './types'
import { ErrorNotice, Modal, PageHeader } from './App'
import { StackDetail } from './Records'
import LabelDialog from './LabelDialog'
import BatchProgress from './BatchProgress'

const FILTER_KEY = 'fabric-tracker.dashboard-filters.v1'
const FILTERS = { room: 'room_id', project: 'project_number', test: 'test_code', treatment: 'preconditioning_code', batch: 'batch_id', q: 'q' } as const
type FilterName = keyof typeof FILTERS
type Filters = Partial<Record<FilterName, string>>
const BUCKET_LABEL: Record<AgeBucket, string> = { under_1d: '< 1 day', '1_to_3d': '1–3 days', over_3d: '> 3 days' }
const STALE_PRINT_MS = 2 * 3600 * 1000
const GAP_ROWS = 8, BATCH_CARDS = 6

function ShowMore({ total, shown, open, noun, onToggle }: { total: number; shown: number; open: boolean; noun: string; onToggle: () => void }) {
  if (total <= shown) return null
  return <div className="show-more"><button className="button text" aria-expanded={open} onClick={onToggle}>{open ? 'Show fewer' : `Show all ${total} ${noun}`}</button></div>
}

function readFilters(): Filters {
  const params = hashParams()
  const fromHash = Object.fromEntries(Object.keys(FILTERS).flatMap(name => params.get(name) ? [[name, params.get(name)!]] : [])) as Filters
  if (Object.keys(fromHash).length || params.has('all')) return fromHash
  try { return JSON.parse(localStorage.getItem(FILTER_KEY) || '{}') as Filters } catch { return {} }
}
function writeFilters(filters: Filters) {
  const params = new URLSearchParams(Object.entries(filters).filter(([, v]) => v) as [string, string][])
  const hash = '#dashboard' + (params.toString() ? '?' + params : '')
  if (location.hash !== hash) history.replaceState(null, '', hash)
  try { localStorage.setItem(FILTER_KEY, JSON.stringify(filters)) } catch { /* filters still live in the URL */ }
}
const stackName = (s: PipelineStack) => <><b>{s.project_number} / {s.material_ref}</b><small>{s.test_code} · {s.preconditioning_code}{s.is_demo ? ' · demo' : ''}</small></>
const batchLink = (s: PipelineStack) => s.batch ? <a className="batch-chip" href={'#batch/' + s.batch.id + '/done'} title="Open batch">{s.batch.label}</a> : <span className="subtle">—</span>

export default function Dashboard({ config, healthy }: { config: Config; healthy: boolean }) {
  const [filters, setFilters] = useState<Filters>(readFilters)
  const [search, setSearch] = useState(filters.q || '')
  const [data, setData] = useState<DashboardData | null>(null)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [detail, setDetail] = useState<Stack | null>(null)
  const [printing, setPrinting] = useState<Stack[] | null>(null)
  const [opening, setOpening] = useState(false)
  const [expanded, setExpanded] = useState<Record<'labels' | 'scan' | 'batches', boolean>>({ labels: false, scan: false, batches: false })
  const serial = useRef(0)

  useEffect(() => { writeFilters(filters) }, [filters])
  useEffect(() => {
    // Only links that carry filters (e.g. a batch's "Filter dashboard") replace the current ones. A bare
    // #dashboard is plain navigation; re-reading it would wipe a search the user has started typing.
    const change = () => { if (location.hash.startsWith('#dashboard?')) { const next = readFilters(); setFilters(next); setSearch(next.q || '') } }
    window.addEventListener('hashchange', change)
    return () => window.removeEventListener('hashchange', change)
  }, [])
  useEffect(() => {  // debounce free-text search
    const timer = setTimeout(() => setFilters(f => (f.q || '') === search.trim() ? f : { ...f, q: search.trim() || undefined }), 300)
    return () => clearTimeout(timer)
  }, [search])
  useEffect(() => {
    if (!healthy) return
    let alive = true
    const load = async () => {
      const request = ++serial.current
      const params = new URLSearchParams(Object.entries(filters).flatMap(([k, v]) => v ? [[FILTERS[k as FilterName], v]] : []))
      try {
        const value = await api<DashboardData>('/dashboard' + (params.toString() ? '?' + params : ''), {}, 8000)
        if (alive && request === serial.current) { setData(value); setError('') }
      } catch (err) { if (alive) setError((err as Error).message) }
    }
    void load()
    const timer = setInterval(load, 15000)
    return () => { alive = false; clearInterval(timer) }
  }, [healthy, filters, refresh])

  const set = (name: FilterName, value: string) => setFilters(f => ({ ...f, [name]: value || undefined }))
  const active = Object.values(filters).some(Boolean)
  const openStack = useCallback(async (id: number) => {
    try { setDetail(await api<Stack>('/stacks/' + id)) } catch (err) { setError((err as Error).message) }
  }, [])
  async function printFor(rows: PipelineStack[]) {
    setOpening(true)
    try { setPrinting(await Promise.all(rows.map(row => api<Stack>('/stacks/' + row.id)))) }
    catch (err) { setError((err as Error).message) }
    finally { setOpening(false) }
  }
  const gaps = data?.gaps
  const gapCount = (gaps?.needs_labels.length || 0) + (gaps?.not_scanned_in.length || 0)
  const openBatches = data?.batches.filter(b => b.open) || []
  const closedBatches = data?.batches.filter(b => !b.open) || []
  const readyTotal = useMemo(() => data?.ready.reduce((sum, room) => sum + room.pieces, 0) || 0, [data])

  return <>
    <PageHeader title="Dashboard" description={data ? `Updated ${formatTime(data.server_time)}` : undefined}>
      <div className="header-actions">
        <button className="button secondary" disabled={!healthy} onClick={() => setRefresh(v => v + 1)}>↻ Refresh</button>
        <a className="button primary" href="#batch/new">＋ New batch</a>
      </div>
    </PageHeader>
    <ErrorNotice message={error} />

    <section className="card dashboard-filters" aria-label="Dashboard filters">
      <div className="room-chips" role="group" aria-label="Room">
        <button className={'room-chip ' + (!filters.room ? 'active' : '')} aria-pressed={!filters.room} onClick={() => set('room', '')}>All rooms</button>
        {(data?.rooms || config.rooms.map(r => ({ id: r.id, name: r.name, inside: 0, ready: 0 }))).map(room =>
          <button key={room.id} className={'room-chip ' + (filters.room === room.id ? 'active' : '')} aria-pressed={filters.room === room.id}
            onClick={() => set('room', filters.room === room.id ? '' : room.id)}>
            <span>{room.name}</span><small>{room.inside} in{room.ready ? <> · <b className="ready-count">{room.ready} ready</b></> : null}</small>
          </button>)}
      </div>
      <div className="filter-row">
        <select aria-label="Project" value={filters.project || ''} onChange={e => set('project', e.target.value)}>
          <option value="">All projects</option>{data?.facets.projects.map(v => <option key={v}>{v}</option>)}
        </select>
        <select aria-label="Test" value={filters.test || ''} onChange={e => set('test', e.target.value)}>
          <option value="">All tests</option>{data?.facets.tests.map(v => <option key={v}>{v}</option>)}
        </select>
        <select aria-label="Treatment" value={filters.treatment || ''} onChange={e => set('treatment', e.target.value)}>
          <option value="">All treatments</option>{data?.facets.treatments.map(v => <option key={v}>{v}</option>)}
        </select>
        <select aria-label="Batch" value={filters.batch || ''} onChange={e => set('batch', e.target.value)}>
          <option value="">All batches</option>
          {data?.facets.batches.map(b => <option key={b.id} value={b.id}>{b.label} · {b.created_at ? formatTime(b.created_at).split(',')[0] : ''}</option>)}
          {filters.batch && !data?.facets.batches.some(b => String(b.id) === filters.batch) && <option value={filters.batch}>Selected batch</option>}
        </select>
        <label className="search-label"><span aria-hidden="true">⌕</span>
          <input aria-label="Search dashboard" placeholder="Search project, material, test…" value={search} onChange={e => setSearch(e.target.value)} /></label>
        {active && <button className="button text" onClick={() => { setSearch(''); setFilters({}) }}>Clear filters</button>}
      </div>
    </section>

    {!data && !error && <div className="empty-inline"><p>Loading dashboard…</p></div>}
    {data && <>
      <section className="card" aria-labelledby="gaps-heading">
        <div className="card-heading"><div>
          <h2 id="gaps-heading">Needs attention <span className={'count-pill ' + (gapCount ? 'warn' : '')}>{gapCount}</span></h2>
          <p>Registered pieces not in a room yet.</p>
        </div></div>
        {!gapCount && <div className="empty-inline compact"><p>Nothing waiting.</p></div>}
        {gaps!.needs_labels.length > 0 && <div className="gap-group">
          <div className="gap-heading"><h3>Needs labels <span className="count-pill">{gaps!.needs_labels.length}</span></h3>
            <button className="button secondary" disabled={!healthy || opening} onClick={() => void printFor(gaps!.needs_labels)}>
              {opening ? 'Opening…' : `Print all ${gaps!.needs_labels.reduce((n, s) => n + s.unlabeled_waiting, 0)} labels`}</button></div>
          <div className="table-wrap"><table>
            <thead><tr><th>Stack</th><th>Unlabeled</th><th>Registered</th><th>Batch</th><th /></tr></thead>
            <tbody>{(expanded.labels ? gaps!.needs_labels : gaps!.needs_labels.slice(0, GAP_ROWS)).map(s => <tr key={s.id} data-testid={'needs-labels-' + s.id}>
              <td>{stackName(s)}</td>
              <td><b>{s.unlabeled_waiting}</b> of {s.total}{s.print_attempt && <small><span className="badge warning">Print {s.print_attempt === 'failed' ? 'failed' : 'unconfirmed'}</span></small>}</td>
              <td>{duration(s.registered_at)} ago<small>{formatTime(s.registered_at)}</small></td>
              <td>{batchLink(s)}</td>
              <td className="row-actions"><button className="button secondary" disabled={!healthy || opening} onClick={() => void printFor([s])}>Print labels</button></td>
            </tr>)}</tbody>
          </table></div>
          <ShowMore total={gaps!.needs_labels.length} shown={GAP_ROWS} open={expanded.labels} noun="stacks" onToggle={() => setExpanded(e => ({ ...e, labels: !e.labels }))} />
        </div>}
        {gaps!.not_scanned_in.length > 0 && <div className="gap-group">
          <div className="gap-heading"><h3>Labeled, not scanned in <span className="count-pill">{gaps!.not_scanned_in.length}</span></h3>
            <span className="subtle">Scan these IN at their room.</span></div>
          <div className="table-wrap"><table>
            <thead><tr><th>Stack</th><th>Not in a room</th><th>Labels printed</th><th>Batch</th><th /></tr></thead>
            <tbody>{(expanded.scan ? gaps!.not_scanned_in : gaps!.not_scanned_in.slice(0, GAP_ROWS)).map(s => {
              const since = s.labeled_at || s.registered_at
              const stale = Date.parse(data.server_time) - Date.parse(since) > STALE_PRINT_MS
              return <tr key={s.id} data-testid={'not-scanned-' + s.id}>
                <td>{stackName(s)}</td>
                <td><b>{s.labeled_waiting}</b> of {s.total}{s.entered ? <small>{s.entered} already in</small> : null}</td>
                <td><span className={stale ? 'badge warning' : ''}>{duration(since)} ago</span><small>{formatTime(since)}</small></td>
                <td>{batchLink(s)}</td>
                <td className="row-actions"><button className="button text" onClick={() => void openStack(s.id)}>Details</button></td>
              </tr>
            })}</tbody>
          </table></div>
          <ShowMore total={gaps!.not_scanned_in.length} shown={GAP_ROWS} open={expanded.scan} noun="stacks" onToggle={() => setExpanded(e => ({ ...e, scan: !e.scan }))} />
        </div>}
      </section>

      <section className="card" aria-labelledby="batches-heading">
        <div className="card-heading"><div>
          <h2 id="batches-heading">Batches in progress <span className="count-pill">{openBatches.length}</span></h2>
        </div></div>
        {!openBatches.length && <div className="empty-inline compact"><p>No open batches.</p></div>}
        <div className="batch-grid padded">
          {(expanded.batches ? openBatches : openBatches.slice(0, BATCH_CARDS)).map(b => <article className="batch-card" key={b.id} data-testid={'batch-' + b.id}>
            <header><a href={'#batch/' + b.id + '/done'}><b>{b.label}</b></a><small>{formatTime(b.created_at)} · {b.stacks} {b.stacks === 1 ? 'stack' : 'stacks'} · {b.stages.total} pieces</small></header>
            <BatchProgress stages={b.stages} legend />
            <footer><a className="button text" href={'#dashboard?batch=' + b.id}>Show only this batch</a><a className="button text" href={'#transactions?batch=' + b.id}>History →</a></footer>
          </article>)}
        </div>
        <ShowMore total={openBatches.length} shown={BATCH_CARDS} open={expanded.batches} noun="batches" onToggle={() => setExpanded(e => ({ ...e, batches: !e.batches }))} />
        {closedBatches.length > 0 && <details className="closed-batches padded">
          <summary>Recently completed · {closedBatches.length}</summary>
          {closedBatches.map(b => <div className="export-row" key={b.id}><div><b>{b.label}</b><small>{formatTime(b.created_at)} · {b.stages.total} pieces finished</small></div>
            <a className="button text" href={'#transactions?batch=' + b.id}>History →</a></div>)}
        </details>}
      </section>

      <section className="card" aria-labelledby="ready-heading">
        <div className="card-heading"><div>
          <h2 id="ready-heading">Ready to remove <span className="count-pill">{readyTotal}</span></h2>
          <p>Finished 24 hours. Oldest first.</p>
        </div></div>
        <div className="ready-rooms">{data.ready.map(room => <ReadyRoomList key={room.room_id} room={room} serverTime={data.server_time} onOpen={id => void openStack(id)} />)}</div>
      </section>
    </>}
    {detail && <Modal title="Stack & specimen details" wide onClose={() => setDetail(null)}>
      <div className="modal-body"><StackDetail stack={detail} config={config} healthy={healthy}
        onChanged={value => { setDetail(value); setRefresh(v => v + 1) }} /></div>
    </Modal>}
    {printing && <LabelDialog stacks={printing} healthy={healthy}
      onClose={() => { setPrinting(null); setRefresh(v => v + 1) }}
      onOutcome={() => setRefresh(v => v + 1)} />}
  </>
}

function ReadyRoomList({ room, serverTime, onOpen }: { room: ReadyRoom; serverTime: string; onOpen: (id: number) => void }) {
  const now = Date.parse(serverTime)
  if (!room.pieces) return <div className="ready-room empty"><span><b>{room.room_name}</b> · nothing ready</span></div>
  return <details className="ready-room" data-testid={'ready-' + room.room_id}>
    <summary>
      <span className="ready-room-title"><b>{room.room_name}</b> · {room.stacks} {room.stacks === 1 ? 'stack' : 'stacks'} · {room.pieces} {room.pieces === 1 ? 'piece' : 'pieces'} ready</span>
      <span className="ready-room-meta">oldest waiting {duration(room.oldest_ready_since, now)}
        {room.buckets.over_3d > 0 && <span className="badge warning">{room.buckets.over_3d} waiting &gt; 3 days</span>}</span>
    </summary>
    <div className="bucket-strip" aria-label="Ready pieces by waiting time">
      {(Object.keys(BUCKET_LABEL) as AgeBucket[]).map(b => <span key={b} className={'bucket age-' + b}>{BUCKET_LABEL[b]} <b>{room.buckets[b]}</b></span>)}
    </div>
    <div className="table-wrap"><table>
      <thead><tr><th>Stack</th><th>Ready</th><th>Waiting</th><th>Batch</th><th /></tr></thead>
      <tbody>{room.items.map(item => <tr key={item.id}>
        <td>{stackName(item)}</td>
        <td><b>{item.room_ready}</b> of {item.room_inside} inside</td>
        <td><span className={'badge age-' + item.age_bucket}>{duration(item.ready_since, now)}</span><small>since {formatTime(item.ready_since)}</small></td>
        <td>{batchLink(item)}</td>
        <td className="row-actions"><button className="button text" onClick={() => onOpen(item.id)}>Details</button></td>
      </tr>)}</tbody>
    </table></div>
  </details>
}
