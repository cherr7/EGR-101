import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { BatchSummary, Config, Draft, PrepRow, Stack } from './types'
import { api, duration, formatTime, post } from './api'
import { ErrorNotice, Modal, PageHeader } from './App'
import LabelPrinter from './LabelPrinter'
import BatchProgress from './BatchProgress'

// Review state is shared with the former Prep page so an unfinished review survives the upgrade.
const REVIEW_KEY = 'fabric-tracker.prep-review.v3'
const OLD_KEY = 'fabric-tracker.prep-draft.v2'
const fields = ['project_number', 'material_ref', 'test_code', 'preconditioning_code', 'specimen_count'] as const
const labels = {
  project_number: 'Project number', material_ref: 'Material / sample', test_code: 'Test',
  preconditioning_code: 'Treatment', specimen_count: 'Pieces to condition',
}
const emptyRow = (): PrepRow => ({
  project_number: '', material_ref: '', test_code: '', preconditioning_code: 'AR', specimen_count: 1,
})
type Review = { draft: Draft | null; rows: PrepRow[] }
export type WizardStep = 'upload' | 'confirm' | 'print' | 'done'
const STEPS: { key: WizardStep; title: string }[] = [
  { key: 'upload', title: 'Upload prep sheet' }, { key: 'confirm', title: 'Confirm stacks' },
  { key: 'print', title: 'Print labels' }, { key: 'done', title: 'Track' },
]

/** Parse #batch/new, #batch/<draft id>/<step>. */
export function batchRoute(): { id: string | null; step: WizardStep } {
  const [, id, step] = location.hash.slice(1).split('?')[0].split('/')
  if (!id || id === 'new') return { id: null, step: 'upload' }
  return { id, step: (STEPS.some(s => s.key === step) ? step : 'confirm') as WizardStep }
}
const go = (id: string | null, step: WizardStep) => { location.hash = id ? `batch/${id}/${step}` : 'batch/new' }

function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (value && typeof value === 'object') {
    const object = value as Record<string, unknown>
    return '{' + Object.keys(object).sort().map(key => JSON.stringify(key) + ':' + canonical(object[key])).join(',') + '}'
  }
  return JSON.stringify(value) ?? 'null'
}
function restoredReview(): Review {
  try {
    const current = JSON.parse(localStorage.getItem(REVIEW_KEY) || 'null') as Review | null
    if (current && Array.isArray(current.rows)) return current
    const old = JSON.parse(localStorage.getItem(OLD_KEY) || 'null') as Draft | null
    if (old) return { draft: old, rows: old.rows }
  } catch { /* An invalid local cache must not prevent a fresh import. */ }
  return { draft: null, rows: [emptyRow()] }
}
const blankReview = (review: Review) => !review.draft && review.rows.length === 1 && canonical(review.rows[0]) === canonical(emptyRow())
function missingFields(row: PrepRow) {
  return fields.filter(field => field === 'specimen_count'
    ? !Number.isInteger(row.specimen_count) || row.specimen_count < 1 || row.specimen_count > 500
    : !row[field]?.trim())
}
function describe(value: unknown): string {
  if (value == null) return '—'
  if (Array.isArray(value)) return value.map(describe).join(', ')
  if (typeof value === 'object') return Object.entries(value as Record<string, unknown>)
    .map(([key, content]) => key.replaceAll('_', ' ') + ': ' + describe(content)).join(' · ')
  return String(value)
}
function SourceDetails({ row }: { row: PrepRow }) {
  const metadata = row.metadata || {}
  const source = metadata.source as Record<string, unknown> | undefined
  const cells = source?.cells as Record<string, Record<string, unknown>> | undefined
  const warnings = metadata.warnings as string[] | undefined
  return <>
    <div className="source-grid">
      {source && Object.entries(source).filter(([key]) => key !== 'cells').map(([key, value]) =>
        <div key={key}><span>{key.replaceAll('_', ' ')}</span><b>{describe(value)}</b></div>)}
      {Object.entries(metadata).filter(([key]) => key !== 'source' && key !== 'warnings').map(([key, value]) =>
        <div key={key}><span>{key.replaceAll('_', ' ')}</span><b>{describe(value)}</b></div>)}
    </div>
    {cells && <div className="table-wrap source-cells"><table>
      <thead><tr><th>Source field</th><th>Excel cell</th><th>Workbook value</th></tr></thead>
      <tbody>{Object.entries(cells).map(([name, value]) => <tr key={name}>
        <td>{name.replaceAll('_', ' ')}</td><td><b>{describe(value.cell)}</b></td>
        <td>{describe(value.value ?? value.raw ?? value)}</td>
      </tr>)}</tbody>
    </table></div>}
    {warnings?.map((warning, index) => <div className="notice warning" key={index}>{warning}</div>)}
    {!Object.keys(metadata).length && <p className="subtle">Manually entered row. No workbook source cells.</p>}
  </>
}

function Stepper({ step, committed, canConfirm, id }: { step: WizardStep; committed: boolean; canConfirm: boolean; id: string | null }) {
  const reachable = (key: WizardStep) => key === 'upload' || key === 'confirm' ? !committed && (key === 'upload' || canConfirm) : committed
  const index = STEPS.findIndex(s => s.key === step)
  return <ol className="wizard-steps" aria-label="Batch steps">
    {STEPS.map((s, at) => <li key={s.key} className={(s.key === step ? 'current ' : '') + (at < index ? 'complete' : '')}
      aria-current={s.key === step ? 'step' : undefined}>
      <button disabled={!reachable(s.key) || s.key === step} onClick={() => go(s.key === 'upload' ? null : id || 'local', s.key)}>
        <span className="step-number">{at < index ? '✓' : at + 1}</span>{s.title}
      </button>
    </li>)}
  </ol>
}

export default function BatchWizard({ config, healthy }: { config: Config; healthy: boolean }) {
  const [route, setRoute] = useState(batchRoute)
  const initial = useRef(restoredReview()).current
  const [rows, setRows] = useState(initial.rows)
  const [draft, setDraft] = useState<Draft | null>(initial.draft)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [onlyMissing, setOnlyMissing] = useState(false)
  const [sourceRow, setSourceRow] = useState<number | null>(null)
  const [selectedSheets, setSelectedSheets] = useState<string[]>(() =>
    initial.draft?.sheets?.filter(sheet => (sheet as { selected?: boolean }).selected).map(sheet => sheet.name) || [])
  const [dragging, setDragging] = useState(false)
  const uploadFile = useRef<File | null>(null)
  const alive = useRef(true)
  const operation = useRef(0)
  const workLock = useRef(false)
  const current = useRef<Review>({ rows, draft })
  current.current = { rows, draft }
  const committed = !!draft?.committed
  const unresolved = rows.map((row, index) => ({ row, index, missing: missingFields(row) })).filter(row => row.missing.length)
  const valid = rows.length > 0 && unresolved.length === 0
  const total = rows.reduce((sum, row) => sum + (Number.isFinite(row.specimen_count) ? row.specimen_count : 0), 0)
  // A committed batch opened by id (e.g. from the dashboard) is viewed independently of the local review.
  const viewingOther = !!route.id && route.id !== 'local' && route.id !== draft?.id

  useEffect(() => {
    const change = () => { if (location.hash.startsWith('#batch')) { const next = batchRoute(); setRoute(previous => { if (previous.step !== 'done' && next.step === 'done') setMessage(''); return next }) } }
    window.addEventListener('hashchange', change)
    return () => window.removeEventListener('hashchange', change)
  }, [])
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false; operation.current++ }
  }, [])
  // Save on every edit before a navigation paint. No extra Save step is required.
  useLayoutEffect(() => {
    try { localStorage.setItem(REVIEW_KEY, JSON.stringify({ rows, draft })) }
    catch { setError('This browser cannot preserve your review. Keep this page open until registration finishes.') }
  }, [rows, draft])
  useEffect(() => {
    const saved = current.current
    if (!healthy || !saved.draft || workLock.current) return
    const id = saved.draft.id
    let active = true
    void api<Draft>('/prep-drafts/' + id).then(value => {
      if (!active || !alive.current || current.current.draft?.id !== id || workLock.current) return
      if (value.committed) {
        setDraft(value)
        setRows(value.rows)
      } else {
        const local = current.current
        const hasEdits = canonical(local.rows) !== canonical(local.draft?.rows)
        if (hasEdits && canonical(value.rows) !== canonical(local.draft?.rows)
          && canonical(value.rows) !== canonical(local.rows)) {
          setError('Another browser changed this preparation. Your edits are kept here; review the latest workbook before registering.')
        } else {
          setDraft(value)
          if (!hasEdits) setRows(value.rows)
        }
      }
    }).catch(err => {
      if (active && alive.current) setError('The saved review is still available on this computer. Could not check its registration: ' + (err as Error).message)
    })
    return () => { active = false }
  }, [healthy])
  // Keep the URL consistent with what this browser can actually show.
  useEffect(() => {
    if (viewingOther) return
    if (route.id === null) {
      // "New batch": a registered review is finished, so start blank; an unfinished one resumes.
      if (committed) { operation.current++; setDraft(null); setRows([emptyRow()]); uploadFile.current = null; setSelectedSheets([]); setMessage('') }
      else if (!blankReview({ rows, draft })) { setMessage('Resumed your unfinished review. Use “Start another batch” to discard it.'); go(draft?.id || 'local', 'confirm') }
    }
    else if ((route.step === 'upload' || route.step === 'confirm') && committed) go(draft!.id, 'print')
    else if ((route.step === 'print' || route.step === 'done') && !committed && route.id) go(draft?.id || 'local', 'confirm')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route, committed, viewingOther])

  async function run<T>(work: () => Promise<T>, success: (value: T) => void) {
    if (workLock.current || !healthy) return
    const id = ++operation.current
    workLock.current = true
    setBusy(true)
    setError('')
    try {
      const value = await work()
      if (alive.current && id === operation.current) success(value)
    } catch (err) {
      if (alive.current && id === operation.current) setError((err as Error).message)
    } finally {
      workLock.current = false
      if (alive.current && id === operation.current) setBusy(false)
    }
  }
  function update(index: number, field: typeof fields[number], value: string) {
    setRows(previous => previous.map((row, at) => at === index
      ? { ...row, [field]: field === 'specimen_count' ? Number(value) : value } : row))
  }
  function jumpToMissing() {
    const first = unresolved[0]
    if (!first) return
    setOnlyMissing(true)
    requestAnimationFrame(() => {
      const input = document.getElementById('prep-' + first.index + '-' + first.missing[0])
      input?.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' })
      input?.focus({ preventScroll: true })
    })
  }
  async function upload(file: File, sheets?: string[], newBatch = false) {
    await run(async () => {
      const data = new FormData()
      data.append('file', file)
      if (sheets?.length) data.append('sheet_names', JSON.stringify(sheets))
      if (newBatch) data.append('new_batch', 'true')
      return api<Draft>(file.name.toLowerCase().endsWith('.csv') ? '/prep-drafts/csv' : '/prep-drafts/workbook',
        { method: 'POST', body: data }, 30000)
    }, value => {
      uploadFile.current = file
      setDraft(value)
      setRows(value.rows.length ? value.rows : [emptyRow()])
      setSelectedSheets(value.sheets?.filter(sheet => (sheet as { selected?: boolean }).selected).map(sheet => sheet.name) || [])
      setOnlyMissing(false)
      setMessage(value.committed ? 'This workbook was already registered as a batch.' : `Imported ${value.rows.length} ${value.rows.length === 1 ? 'row' : 'rows'} from ${file.name}.`)
      go(value.id, value.committed ? 'print' : 'confirm')
    })
  }
  async function register() {
    const reviewed = current.current
    await run(async () => {
      const recovered = reviewed.draft ? await api<Draft>('/prep-drafts/' + reviewed.draft.id) : null
      let saved: Draft
      if (recovered?.committed) saved = recovered
      else {
        // Refuse an unseen edit from another browser rather than replacing it
        // with this browser's older source review.
        if (recovered && reviewed.draft && canonical(recovered.rows) !== canonical(reviewed.draft.rows)
          && canonical(recovered.rows) !== canonical(reviewed.rows)) {
          throw new Error('This imported preparation changed in another browser. Import the workbook again to review the latest values before registering.')
        }
        const value = recovered
          ? await api<Draft>('/prep-drafts/' + recovered.id, { method: 'PUT', body: JSON.stringify({ rows: reviewed.rows, expected_rows: recovered.rows }) })
          : await post<Draft>('/prep-drafts', { rows: reviewed.rows })
        // Preserve the authoritative import ID before the commit can lose its response.
        localStorage.setItem(REVIEW_KEY, JSON.stringify({ draft: value, rows: reviewed.rows }))
        if (alive.current) setDraft(value)
        const result = await post<{ stack_ids: number[] }>('/prep-drafts/' + value.id + '/commit', { expected_rows: value.rows })
        saved = { ...value, committed: true, stack_ids: result.stack_ids }
        localStorage.setItem(REVIEW_KEY, JSON.stringify({ draft: saved, rows: saved.rows }))
      }
      return saved
    }, saved => {
      setDraft(saved)
      setRows(saved.rows)
      setMessage(`Registered ${saved.stack_ids.length} ${saved.stack_ids.length === 1 ? 'stack' : 'stacks'}. Their specimen numbers are now permanent.`)
      go(saved.id, 'print')
    })
  }
  function startNew() {
    operation.current++
    setDraft(null)
    setRows([emptyRow()])
    uploadFile.current = null
    setSelectedSheets([])
    setOnlyMissing(false)
    setMessage('')
    setError('')
    try { localStorage.removeItem(REVIEW_KEY) } catch { /* nothing to clear */ }
    go(null, 'upload')
  }
  function manualEntry() {
    setMessage('')
    setRows([emptyRow()])
    go('local', 'confirm')
  }
  const shown = rows.map((row, index) => ({ row, index })).filter(({ row }) => !onlyMissing || missingFields(row).length)
  const step = viewingOther ? (route.step === 'print' ? 'print' : 'done') : route.step
  const batchId = viewingOther ? route.id! : draft?.id || null

  return <>
    <PageHeader title="New batch" description="Prep sheet → confirm stacks → print labels → track in the rooms.">
      {(!blankReview({ rows, draft }) || viewingOther) && <button className="button secondary" disabled={busy} onClick={startNew}>＋ Start another batch</button>}
    </PageHeader>
    <Stepper step={step} committed={viewingOther || committed} canConfirm={!blankReview({ rows, draft }) || route.id === 'local'} id={batchId} />
    <ErrorNotice message={error} />
    {message && <div className="notice success" role="status">{message}</div>}

    {step === 'upload' && <section className="card">
      <div className="card-heading"><div><h2>1 · Upload the prep sheet</h2><p>Excel (.xlsx / .xlsm) or CSV. Nothing is registered until you confirm the stacks.</p></div></div>
      <div className="padded">
        <label className={'drop-zone ' + (dragging ? 'dragging ' : '') + (busy || !healthy ? 'disabled' : '')}
          onDragOver={e => { e.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)}
          onDrop={e => { e.preventDefault(); setDragging(false); const file = e.dataTransfer.files?.[0]; if (file && healthy && !busy) void upload(file) }}>
          <span className="drop-icon" aria-hidden="true">⇪</span>
          <b>{busy ? 'Reading workbook…' : 'Drop the prep sheet here, or click to choose a file'}</b>
          <small>.xlsx, .xlsm, or .csv · macros are never run</small>
          <input aria-label="Import prep workbook" type="file" accept=".xlsx,.xlsm,.csv" disabled={busy || !healthy}
            onChange={e => { if (e.target.files?.[0]) void upload(e.target.files[0]); e.target.value = '' }} />
        </label>
        <p className="input-help">No spreadsheet? <button className="button text inline" disabled={busy} onClick={manualEntry}>Enter stacks manually →</button></p>
      </div>
    </section>}

    {step === 'confirm' && <section className="card prep-card">
      <div className="card-heading">
        <div>
          <h2>2 · Confirm stacks {draft?.source && <span className="badge outline">{draft.source.filename}</span>}</h2>
          <p>Each row is one physical stack: one material, test, and treatment. Include every piece going into conditioning, extras too.</p>
        </div>
        <label className={'button secondary upload ' + (busy || !healthy ? 'disabled' : '')}>↑ Replace file
          <input aria-label="Replace prep workbook" type="file" accept=".xlsx,.xlsm,.csv"
            disabled={busy || !healthy} onChange={e => { if (e.target.files?.[0]) void upload(e.target.files[0]); e.target.value = '' }} />
        </label>
      </div>
      {draft?.warnings?.length ? <details className="import-warnings">
        <summary>Workbook review notes · {draft.warnings.length}</summary>
        {draft.warnings.map((warning, index) => <p key={index}>{warning}</p>)}
      </details> : null}
      {draft?.sheets?.length ? <details className="sheet-inventory">
        <summary>{draft.sheets.length} workbook sheets inspected</summary>
        <div className="sheet-list">{draft.sheets.map(sheet => <label className="check-label" key={sheet.name}>
          <input type="checkbox" checked={selectedSheets.includes(sheet.name)} disabled={!uploadFile.current || busy}
            onChange={e => setSelectedSheets(previous => e.target.checked ? [...previous, sheet.name] : previous.filter(name => name !== sheet.name))} />
          <span><b>{sheet.name}</b> · {sheet.kind.replaceAll('_', ' ')} · {sheet.candidate_count} candidates</span>
        </label>)}</div>
        {uploadFile.current && <button className="button secondary" disabled={busy || !selectedSheets.length}
          onClick={() => uploadFile.current && void upload(uploadFile.current, selectedSheets)}>Review selected sheets</button>}
        <p className="input-help">{uploadFile.current ? 'Only selected sheets are imported.' : 'Choose the workbook again to change the sheet selection.'}</p>
      </details> : null}
      <div className="prep-exceptions">
        <span className={'badge ' + (unresolved.length ? 'warning' : 'good')}>
          {unresolved.length ? `${unresolved.length} ${unresolved.length === 1 ? 'row needs' : 'rows need'} values` : 'All required values present'}
        </span>
        <label className="check-label"><input type="checkbox" checked={onlyMissing}
          onChange={e => setOnlyMissing(e.target.checked)} />Only rows needing attention</label>
        {unresolved.length > 0 && <button className="button text" onClick={jumpToMissing}>Go to first missing value →</button>}
      </div>
      <datalist id="treatments">{config.treatments.map(treatment => <option key={treatment} value={treatment} />)}</datalist>
      <div className="table-wrap"><table className="prep-table">
        <thead><tr><th>#</th>{fields.map(field => <th key={field}>{labels[field]}</th>)}<th>Source</th><th /></tr></thead>
        <tbody>{shown.map(({ row, index }) => <tr key={index}>
          <td className="row-number">{String(index + 1).padStart(2, '0')}</td>
          {fields.map(field => <td key={field}>
            <input id={'prep-' + index + '-' + field} aria-label={field + ' row ' + (index + 1)}
              className={missingFields(row).includes(field) ? 'unresolved' : ''}
              list={field === 'preconditioning_code' ? 'treatments' : undefined}
              type={field === 'specimen_count' ? 'number' : 'text'} min={1} max={field === 'specimen_count' ? 500 : undefined}
              value={row[field] ?? ''} placeholder="Required" disabled={busy}
              onChange={e => update(index, field, e.target.value)} />
          </td>)}
          <td><button className="button text" aria-label={'Source row ' + (index + 1)} onClick={() => setSourceRow(index)}>Review ↗</button></td>
          <td><button className="icon-button" aria-label={'Remove row ' + (index + 1)}
            disabled={busy || rows.length === 1}
            onClick={() => setRows(previous => previous.filter((_, at) => at !== index))}>×</button></td>
        </tr>)}</tbody>
      </table></div>
      {!shown.length && <div className="empty-inline"><h3>No unresolved rows</h3><p>Turn off the attention filter to review every stack.</p></div>}
      <div className="card-actions">
        <button className="button text" disabled={busy} onClick={() => setRows(previous => [...previous, emptyRow()])}>＋ Add stack</button>
        <span className="summary-count">{rows.length} {rows.length === 1 ? 'stack' : 'stacks'} · {total} physical specimens</span>
        <button className="button primary" disabled={!valid || !healthy || busy} onClick={() => void register()}>
          {busy ? 'Registering…' : `Register ${rows.length} ${rows.length === 1 ? 'stack' : 'stacks'} & continue →`}
        </button>
      </div>
      <p className="input-help padded">Registering assigns permanent specimen numbers. After this step the rows can't be edited; add extra pieces as a new batch.</p>
    </section>}

    {step === 'print' && batchId && <PrintStep id={batchId} healthy={healthy}
      onContinue={() => go(batchId, 'done')} />}
    {step === 'done' && batchId && <DoneStep id={batchId} healthy={healthy} onAnother={startNew}
      newPhysicalBatch={!viewingOther && uploadFile.current ? () => void upload(uploadFile.current!, selectedSheets, true) : undefined} />}

    {sourceRow !== null && <Modal title={'Workbook source · Stack ' + (sourceRow + 1)} wide onClose={() => setSourceRow(null)}>
      <div className="modal-body"><SourceDetails row={rows[sourceRow]} /></div>
      <div className="modal-actions"><button className="button secondary" onClick={() => setSourceRow(null)}>Back to review</button></div>
    </Modal>}
  </>
}

function PrintStep({ id, healthy, onContinue }: { id: string; healthy: boolean; onContinue: () => void }) {
  const [stacks, setStacks] = useState<Stack[] | null>(null)
  const [error, setError] = useState('')
  const [outcome, setOutcome] = useState<'printed' | 'manual' | null>(null)
  useEffect(() => {
    if (!healthy) return
    let alive = true
    void api<Draft>('/prep-drafts/' + id)
      .then(draft => Promise.all(draft.stack_ids.map(stackId => api<Stack>('/stacks/' + stackId))))
      .then(value => { if (alive) setStacks(value) })
      .catch(err => { if (alive) setError((err as Error).message) })
    return () => { alive = false }
  }, [id, healthy])
  const onOutcome = useCallback((value: 'printed' | 'manual') => setOutcome(value), [])
  return <section className="card">
    <div className="card-heading"><div><h2>3 · Print labels</h2>
      <p>Preview the actual labels, then print to the label printer. If the printer isn't available, open the PDF, print it, and record it.</p></div></div>
    <ErrorNotice message={error} />
    {!stacks && !error && <div className="empty-inline compact"><p>Loading stacks…</p></div>}
    {stacks && <LabelPrinter stacks={stacks} healthy={healthy} variant="inline"
      onOutcome={onOutcome}
      extraActions={outcome
        ? <button className="button primary" onClick={onContinue}>Continue →</button>
        : <button className="button text" onClick={onContinue} title="The stacks stay on the dashboard as 'Needs labels'">Print later</button>} />}
  </section>
}

function DoneStep({ id, healthy, onAnother, newPhysicalBatch }: { id: string; healthy: boolean; onAnother: () => void; newPhysicalBatch?: () => void }) {
  const [batch, setBatch] = useState<BatchSummary | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!healthy) return
    let alive = true
    const load = () => api<BatchSummary>('/batches/' + id).then(value => { if (alive) { setBatch(value); setError('') } })
      .catch(err => { if (alive) setError((err as Error).message) })
    void load()
    const timer = setInterval(load, 10000)
    return () => { alive = false; clearInterval(timer) }
  }, [id, healthy])
  if (!batch) return <><ErrorNotice message={error} />{!error && <div className="empty-inline compact"><p>Loading batch…</p></div>}</>
  const s = batch.stages
  const next = s.labeled < s.total ? { text: `${s.total - s.labeled} pieces still need labels.`, action: <a className="button secondary" href={`#batch/${id}/print`}>Print labels</a> }
    : s.entered < s.total ? { text: 'Take the labeled stacks to their conditioning room and scan them IN at the door station.', action: null }
    : s.finished < s.total ? { text: s.ready ? `${s.ready} pieces are ready to remove.` : 'Everything is conditioning. The dashboard shows when pieces reach 24 h.', action: null }
    : { text: 'Every piece has been scanned out. This batch is complete.', action: null }
  return <section className="card">
    <div className="card-heading"><div>
      <h2>4 · Track this batch <span className={'badge ' + (batch.open ? 'blue' : 'good')}>{batch.open ? 'Open' : 'Complete'}</span></h2>
      <p><b>{batch.label}</b> · registered {formatTime(batch.created_at)} ({duration(batch.created_at)} ago) · {batch.stacks.length} stacks · {s.total} pieces</p>
    </div></div>
    <div className="padded">
      <BatchProgress stages={s} legend />
      <div className="notice info next-step"><b>Next:</b> {next.text} {next.action}</div>
    </div>
    <div className="table-wrap"><table>
      <thead><tr><th>Stack</th><th>Labeled</th><th>In room</th><th>Ready</th><th>Finished</th><th /></tr></thead>
      <tbody>{batch.stacks.map(stack => <tr key={stack.id}>
        <td><b>{stack.project_number} / {stack.material_ref}</b><small>{stack.test_code} · {stack.preconditioning_code}</small></td>
        <td>{stack.labeled}/{stack.total}</td><td>{stack.inside}</td><td>{stack.ready}</td><td>{stack.finished}/{stack.total}</td>
        <td className="row-actions"><a className="button text" href={'#records?stack=' + stack.id}>Specimens →</a></td>
      </tr>)}</tbody>
    </table></div>
    <div className="card-actions">
      <a className="button secondary" href={'#dashboard?batch=' + id}>View on dashboard</a>
      <a className="button secondary" href={'#transactions?batch=' + id}>Transaction history</a>
      {newPhysicalBatch && <button className="button text" onClick={newPhysicalBatch}>New physical batch from this workbook</button>}
      <span className="grow" />
      <button className="button primary" onClick={onAnother}>＋ Start another batch</button>
    </div>
  </section>
}
