import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { BatchSummary, Config, Draft, PrepHeader, PrepProblem, PrepRow, Stack } from './types'
import { api, formatTime, post } from './api'
import { ErrorNotice, Modal, PageHeader } from './App'
import LabelPrinter from './LabelPrinter'
import BatchProgress from './BatchProgress'

// Step 2 is the prep sheet itself: the header values once above each worksheet's table, then
// one row per sheet row with the sheet's own columns in the sheet's order. Registration turns
// each row into one stack per sample number, on the server (backend/app/prepsheet.py).
const REVIEW_KEY = 'fabric-tracker.prep-sheet.v4'
const COLUMNS = [
  ['test', 'Test'],
  ['tested_count', 'Total Number of Specimens to be Tested'],
  ['conditions', 'Conditions'],
  ['conditioning', 'Specimens per sample for Conditioning'],
  ['sample', 'Sample Number'],
  ['sample_type', 'Sample Type'],
] as const
type Column = typeof COLUMNS[number][0]
const HEADER_FIELDS = [
  ['labware_number', 'LabWare #'], ['project_number', 'Oracle project'], ['standard', 'Standard'], ['regime', 'Regime'],
] as const
const blankHeader = (): PrepHeader => ({ project_number: '', labware_number: '', standard: '', regime: '' })
const blankRow = (sheet = 'Preparation'): PrepRow => ({ sheet, test: '', tested_count: '', conditions: '', conditioning: '', sample: '', sample_type: '' })
type Review = { draft: Draft | null; headers: Record<string, PrepHeader>; rows: PrepRow[] }
const fresh = (): Review => ({ draft: null, headers: { Preparation: blankHeader() }, rows: [blankRow()] })
export type WizardStep = 'upload' | 'confirm' | 'print' | 'done'
const STEPS: { key: WizardStep; title: string }[] = [
  { key: 'upload', title: 'Upload prep sheet' }, { key: 'confirm', title: 'Check the sheet' },
  { key: 'print', title: 'Print labels' }, { key: 'done', title: 'Track' },
]

/** Parse #batch/new, #batch/<draft id>/<step>. 'local' is a sheet typed here and not saved yet. */
export function batchRoute(): { id: string | null; step: WizardStep } {
  const [, id, step] = location.hash.slice(1).split('?')[0].split('/')
  if (!id || id === 'new') return { id: null, step: 'upload' }
  return { id, step: (STEPS.some(s => s.key === step) ? step : 'confirm') as WizardStep }
}
const go = (id: number | string | null, step: WizardStep) => { location.hash = id ? `batch/${id}/${step}` : 'batch/new' }

function restoredReview(): Review {
  try {
    const value = JSON.parse(localStorage.getItem(REVIEW_KEY) || 'null') as Review | null
    if (value && Array.isArray(value.rows) && value.headers) return value
  } catch { /* An unreadable local copy must not block a fresh import. */ }
  return fresh()
}

/** Mirror of backend prepsheet.conditioning_count: '6', '5 Warp, 5 Fill', '3 15x15'. */
export function piecesPerSample(value: string): number | null {
  const text = value.trim()
  if (/^\d+(\.0+)?$/.test(text)) { const n = Math.floor(Number(text)); return n >= 1 && n <= 500 ? n : null }
  const panel = /^(\d+)\s+\d+(?:\.\d+)?\s*[x×]\s*\d+(?:\.\d+)?(?:\s*(?:in|inch|inches|cm|mm|["″]))?$/i.exec(text)
  if (panel) { const n = Number(panel[1]); return n >= 1 && n <= 500 ? n : null }
  let total = 0
  for (const piece of text.split(/\s*(?:,|\+|;|\band\b)\s*/i)) {
    const match = /^(\d+)\s*(Warp|Fill|Contact|Spaced|Length|Width)\s*$/i.exec(piece)
    if (!match) return null
    total += Number(match[1])
  }
  return total >= 1 && total <= 500 ? total : null
}
const samplesOf = (value: string) => value.split(/[,;\n]/).map(item => item.trim()).filter(Boolean)
function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + canonical((value as Record<string, unknown>)[key])).join(',') + '}'
  return JSON.stringify(value ?? null)
}
const projectOf = (header?: PrepHeader) => (header?.project_number || '').trim() || (header?.labware_number || '').trim()
const sheetOf = (review: Pick<Review, 'headers' | 'rows'>) => canonical({ headers: review.headers, rows: review.rows })
const blankReview = (review: Review) => !review.draft && sheetOf(review) === sheetOf(fresh())

/** Mirror of backend prepsheet.resolve, for highlighting cells while typing. The server decides. */
function check(headers: Record<string, PrepHeader>, rows: PrepRow[]) {
  const problems: PrepProblem[] = []
  const previous: Record<string, string> = {}
  let stacks = 0, specimens = 0
  rows.forEach((row, index) => {
    const before = problems.length
    const add = (column: string, message: string) => problems.push({ row: index, sheet: row.sheet, column, message })
    if (row.test.trim()) previous[row.sheet] = row.test.trim()
    const count = piecesPerSample(row.conditioning), samples = samplesOf(row.sample)
    const expected = row.metadata?.sample_count
    if (!projectOf(headers[row.sheet])) add('project_number', 'Enter the Oracle project or LabWare # for this sheet.')
    if (!previous[row.sheet]) add('test', 'Enter the test.')
    if (!row.conditions.trim()) add('conditions', 'Enter the conditions (treatment).')
    if (count === null) add('conditioning', 'Enter the pieces per sample, e.g. 6 or 5 Warp, 5 Fill.')
    if (!samples.length) add('sample', 'Enter the sample number.')
    else if (typeof expected === 'number' && samples.length !== expected) add('sample', `The sheet lists ${expected} sample(s); enter ${expected} sample number(s).`)
    else if (new Set(samples).size !== samples.length) add('sample', 'A sample number is repeated in this row.')
    if (problems.length === before) { stacks += samples.length; specimens += samples.length * (count || 0) }
  })
  return { problems, stacks, specimens }
}

// Only what a reviewer compares against the workbook; internal import flags stay hidden.
const METADATA_FIELDS = [
  ['comments', 'Comment'], ['sample_count', '# of samples'], ['sample_released', 'Sample released'],
  ['implied_spare_pieces', 'Spare pieces (implied)'], ['shares_samples', 'Shares samples with another test'],
] as const
function SourceDetails({ row }: { row: PrepRow }) {
  const metadata = row.metadata || {}
  const cells = metadata.cells as Record<string, { cell?: string; value?: string; formula?: string }> | undefined
  const warnings = metadata.warnings as string[] | undefined
  const facts = METADATA_FIELDS.map(([key, label]) => [label, metadata[key]] as const)
    .filter(([, value]) => value !== undefined && value !== null && value !== '' && value !== false)
  return <>
    {facts.length > 0 && <div className="source-grid">
      {facts.map(([label, value]) => <div key={label}><span>{label}</span><b>{value === true ? 'Yes' : String(value)}</b></div>)}
    </div>}
    {cells && Object.keys(cells).length ? <div className="table-wrap source-cells"><table>
      <thead><tr><th>Sheet column</th><th>Excel cell</th><th>Workbook value</th></tr></thead>
      <tbody>{Object.entries(cells).map(([name, value]) => <tr key={name}>
        <td>{name.replaceAll('_', ' ')}</td><td><b>{value.cell || '—'}</b></td>
        <td>{value.formula ? `=${value.formula} → ${value.value || 'not cached'}` : value.value || '—'}</td>
      </tr>)}</tbody>
    </table></div> : <p className="subtle">Entered by hand.</p>}
    {warnings?.map((warning, index) => <div className="notice warning" key={index}>{warning}</div>)}
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
  const [headers, setHeaders] = useState(initial.headers)
  const [rows, setRows] = useState(initial.rows)
  const [draft, setDraft] = useState<Draft | null>(initial.draft)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [sourceRow, setSourceRow] = useState<number | null>(null)
  const [selectedSheets, setSelectedSheets] = useState<string[]>(() => initial.draft?.sheets?.filter(sheet => sheet.selected).map(sheet => sheet.name) || [])
  const [dragging, setDragging] = useState(false)
  const uploadFile = useRef<File | null>(null)
  const alive = useRef(true), operation = useRef(0), workLock = useRef(false)
  const current = useRef<Review>({ draft, headers, rows }); current.current = { draft, headers, rows }
  const committed = !!draft?.committed
  const { problems, stacks, specimens } = check(headers, rows)
  const problemAt = (index: number, column: string) => problems.find(p => p.row === index && p.column === column)
  const sheets = [...new Set([...Object.keys(headers), ...rows.map(row => row.sheet)])].filter(name => rows.some(row => row.sheet === name))
  // A registered batch opened by id (e.g. from the dashboard) is viewed apart from the local review.
  const viewingOther = !!route.id && route.id !== 'local' && route.id !== String(draft?.id)

  useEffect(() => {
    const change = () => { if (location.hash.startsWith('#batch')) { const next = batchRoute(); setRoute(previous => { if (previous.step !== 'done' && next.step === 'done') setMessage(''); return next }) } }
    window.addEventListener('hashchange', change)
    return () => window.removeEventListener('hashchange', change)
  }, [])
  useEffect(() => { alive.current = true; return () => { alive.current = false; operation.current++ } }, [])
  // Every edit is kept in this browser before the next paint; nothing to save by hand.
  useLayoutEffect(() => {
    try { localStorage.setItem(REVIEW_KEY, JSON.stringify({ draft, headers, rows })) }
    catch { setError('This browser can’t save your progress.') }
  }, [draft, headers, rows])
  function load(value: Draft) {
    setDraft(value)
    setHeaders(Object.keys(value.headers || {}).length ? value.headers : { Preparation: blankHeader() })
    setRows(value.rows.length ? value.rows : [blankRow()])
  }
  // Compare with the server copy once it is reachable: another browser may have registered or changed it.
  useEffect(() => {
    const saved = current.current
    if (!healthy || !saved.draft || workLock.current) return
    const id = saved.draft.id
    let active = true
    void api<Draft>('/prep-drafts/' + id).then(value => {
      if (!active || !alive.current || current.current.draft?.id !== id || workLock.current) return
      const local = current.current
      const hasEdits = sheetOf(local) !== sheetOf(local.draft!)
      if (value.committed || !hasEdits) load(value)
      else if (value.revision !== local.draft!.revision)
        setError('This sheet was changed in another browser.')
    }).catch(err => { if (active && alive.current) setError('Couldn’t check this sheet with the server: ' + (err as Error).message) })
    return () => { active = false }
  }, [healthy])
  // Keep the URL consistent with what this browser can actually show.
  useEffect(() => {
    if (viewingOther) return
    if (route.id === null) {
      // "New batch": a registered review is finished, so start blank; an unfinished one resumes.
      if (committed) reset()
      else if (!blankReview({ draft, headers, rows })) { setMessage('Continuing your unfinished batch.'); go(draft?.id || 'local', 'confirm') }
    }
    else if ((route.step === 'upload' || route.step === 'confirm') && committed) go(draft!.id, 'print')
    else if ((route.step === 'print' || route.step === 'done') && !committed && route.id) go(draft?.id || 'local', 'confirm')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route, committed, viewingOther])

  async function run<T>(work: () => Promise<T>, success: (value: T) => void) {
    if (workLock.current || !healthy) return
    const id = ++operation.current
    workLock.current = true; setBusy(true); setError('')
    try { const value = await work(); if (alive.current && id === operation.current) success(value) }
    catch (err) { if (alive.current && id === operation.current) setError((err as Error).message) }
    finally { workLock.current = false; if (alive.current && id === operation.current) setBusy(false) }
  }
  const setCell = (index: number, column: Column, value: string) =>
    setRows(previous => previous.map((row, at) => at === index ? { ...row, [column]: value } : row))
  const setHeader = (sheet: string, field: keyof PrepHeader, value: string) =>
    setHeaders(previous => ({ ...previous, [sheet]: { ...(previous[sheet] || blankHeader()), [field]: value } }))
  function addRow(sheet: string) {
    setRows(previous => {
      const last = previous.map(row => row.sheet).lastIndexOf(sheet)
      return [...previous.slice(0, last + 1), blankRow(sheet), ...previous.slice(last + 1)]
    })
  }
  function jumpToProblem() {
    const first = problems[0]
    if (!first) return
    const id = first.column === 'project_number' ? `prep-header-${first.sheet}-project_number` : `prep-${first.row}-${first.column}`
    const input = document.getElementById(id)
    input?.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' })
    input?.focus({ preventScroll: true })
  }
  async function upload(file: File, chosen?: string[], newBatch = false) {
    await run(async () => {
      const data = new FormData()
      data.append('file', file)
      if (chosen?.length) data.append('sheet_names', JSON.stringify(chosen))
      if (newBatch) data.append('new_batch', 'true')
      return api<Draft>(file.name.toLowerCase().endsWith('.csv') ? '/prep-drafts/csv' : '/prep-drafts/workbook', { method: 'POST', body: data }, 30000)
    }, value => {
      uploadFile.current = file
      load(value)
      setSelectedSheets(value.sheets?.filter(sheet => sheet.selected).map(sheet => sheet.name) || [])
      setMessage(value.committed ? 'This sheet is already registered.' : `Imported ${value.rows.length} sheet ${value.rows.length === 1 ? 'row' : 'rows'} from ${file.name}.`)
      go(value.id, value.committed ? 'print' : 'confirm')
    })
  }
  async function register() {
    const reviewed = current.current
    await run(async () => {
      let saved = reviewed.draft
      // Check the server copy first: an earlier save whose response was lost may already hold these edits.
      const latest = saved ? await api<Draft>('/prep-drafts/' + saved.id) : null
      if (latest?.committed) return latest
      const body = { headers: reviewed.headers, rows: reviewed.rows }
      if (latest && sheetOf(latest) === sheetOf(body)) saved = latest
      else if (latest && saved && latest.revision !== saved.revision)
        throw new Error('This sheet was changed in another browser.')
      else saved = saved
        ? await api<Draft>('/prep-drafts/' + saved.id, { method: 'PUT', body: JSON.stringify({ ...body, expected_revision: saved.revision }) })
        : await post<Draft>('/prep-drafts', body)
      // Keep the server's draft id before the commit, so a lost commit response can be checked.
      localStorage.setItem(REVIEW_KEY, JSON.stringify({ ...reviewed, draft: saved }))
      if (alive.current) setDraft(saved)
      try {
        const result = await post<{ stack_ids: number[] }>('/prep-drafts/' + saved.id + '/commit', { expected_revision: saved.revision })
        return { ...saved, committed: true, stack_ids: result.stack_ids }
      } catch (err) {
        const recheck = await api<Draft>('/prep-drafts/' + saved.id).catch(() => null)
        if (!recheck?.committed) throw err
        return recheck
      }
    }, saved => {
      setDraft(saved)
      setMessage(`Registered ${saved.stack_ids.length} ${saved.stack_ids.length === 1 ? 'stack' : 'stacks'}.`)
      go(saved.id, 'print')
    })
  }
  function reset() {
    operation.current++
    const value = fresh()
    setDraft(null); setHeaders(value.headers); setRows(value.rows)
    uploadFile.current = null
    setSelectedSheets([]); setMessage('')
  }
  function startNew() {
    reset()
    setError('')
    try { localStorage.removeItem(REVIEW_KEY) } catch { /* nothing to clear */ }
    go(null, 'upload')
  }
  function manualEntry() {
    setMessage('')
    go('local', 'confirm')
  }
  async function reloadSaved() {
    if (!draft) return
    await run(() => api<Draft>('/prep-drafts/' + draft.id), value => { load(value); setMessage('Loaded the latest saved sheet.') })
  }
  const step = viewingOther ? (route.step === 'print' ? 'print' : 'done') : route.step
  const batchId = viewingOther ? route.id! : draft?.id ? String(draft.id) : null

  return <>
    <PageHeader title="New batch">
      {(!blankReview({ draft, headers, rows }) || viewingOther) && <button className="button secondary" disabled={busy} onClick={startNew}>New batch</button>}
    </PageHeader>
    <Stepper step={step} committed={viewingOther || committed} canConfirm={!blankReview({ draft, headers, rows }) || route.id === 'local'} id={batchId} />
    <ErrorNotice message={error} />
    {error.includes('another browser') && <button className="button secondary" disabled={busy} onClick={() => void reloadSaved()}>Load the latest saved sheet</button>}
    {message && <div className="notice success" role="status">{message}</div>}

    {step === 'upload' && <section className="card">
      <div className="card-heading"><div><h2>Upload the prep sheet</h2></div></div>
      <div className="padded">
        <label className={'drop-zone ' + (dragging ? 'dragging ' : '') + (busy || !healthy ? 'disabled' : '')}
          onDragOver={e => { e.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)}
          onDrop={e => { e.preventDefault(); setDragging(false); const file = e.dataTransfer.files?.[0]; if (file && healthy && !busy) void upload(file) }}>
          <span className="drop-icon" aria-hidden="true">⇪</span>
          <b>{busy ? 'Reading workbook…' : 'Drop the prep sheet here or click to choose'}</b>
          <small>.xlsx, .xlsm or .csv</small>
          <input aria-label="Import prep workbook" type="file" accept=".xlsx,.xlsm,.csv" disabled={busy || !healthy}
            onChange={e => { if (e.target.files?.[0]) void upload(e.target.files[0]); e.target.value = '' }} />
        </label>
        <p className="input-help"><button className="button text inline" disabled={busy} onClick={manualEntry}>Enter by hand</button></p>
      </div>
    </section>}

    {step === 'confirm' && <section className="card prep-card">
      <div className="card-heading">
        <div>
          <h2>Check the sheet {draft?.source && <span className="badge outline">{draft.source.filename}</span>}</h2>
        </div>
        <label className={'button secondary upload ' + (busy || !healthy ? 'disabled' : '')}>Replace file
          <input aria-label="Replace prep workbook" type="file" accept=".xlsx,.xlsm,.csv"
            disabled={busy || !healthy} onChange={e => { if (e.target.files?.[0]) void upload(e.target.files[0]); e.target.value = '' }} />
        </label>
      </div>
      {draft?.warnings?.length ? <details className="import-warnings">
        <summary>Import notes · {draft.warnings.length}</summary>
        {draft.warnings.map((warning, index) => <p key={index}>{warning}</p>)}
      </details> : null}
      {draft?.sheets && draft.sheets.length > 1 ? <details className="sheet-inventory">
        <summary>Sheets ({draft.sheets.length})</summary>
        <div className="sheet-list">{draft.sheets.map(sheet => <label className="check-label" key={sheet.name}>
          <input type="checkbox" checked={selectedSheets.includes(sheet.name)} disabled={!uploadFile.current || busy}
            onChange={e => setSelectedSheets(previous => e.target.checked ? [...previous, sheet.name] : previous.filter(name => name !== sheet.name))} />
          <span><b>{sheet.name}</b> · {sheet.kind === 'reference' ? 'not a prep sheet' : `${sheet.candidate_count} ${sheet.candidate_count === 1 ? 'row' : 'rows'}`}</span>
        </label>)}</div>
        {uploadFile.current && <button className="button secondary" disabled={busy || !selectedSheets.length}
          onClick={() => uploadFile.current && void upload(uploadFile.current, selectedSheets)}>Import selected sheets</button>}
        <p className="input-help">{uploadFile.current ? '' : 'To change sheets, upload the file again.'}</p>
      </details> : null}
      <div className="prep-exceptions">
        <span className={'badge ' + (problems.length ? 'warning' : 'good')}>{problems.length ? `${problems.length} ${problems.length === 1 ? 'cell' : 'cells'} to fill` : 'Ready to register'}</span>
        {problems.length > 0 && <button className="button text" onClick={jumpToProblem}>Show</button>}
      </div>
      <datalist id="treatments">{config.treatments.map(treatment => <option key={treatment} value={treatment} />)}</datalist>
      {sheets.map(sheet => {
        const header = headers[sheet] || blankHeader()
        const indexed = rows.map((row, index) => ({ row, index })).filter(item => item.row.sheet === sheet)
        return <div className="prep-sheet" key={sheet} data-sheet={sheet}>
          {sheets.length > 1 && <h3 className="prep-sheet-name">{sheet}</h3>}
          <div className="prep-sheet-header" role="group" aria-label={`${sheet} header`}>
            {HEADER_FIELDS.map(([field, label]) => <label key={field}>{label}
              <input id={`prep-header-${sheet}-${field}`} value={header[field]} disabled={busy}
                className={field === 'project_number' && !projectOf(header) ? 'unresolved' : ''}
                placeholder={field === 'project_number' ? 'Oracle project or LabWare #' : ''}
                onChange={e => setHeader(sheet, field, e.target.value)} />
            </label>)}
          </div>
          <div className="table-wrap"><table className="prep-table prep-sheet-table">
            <thead><tr>{COLUMNS.map(([column, label]) => <th key={column} className={'col-' + column}>{label}</th>)}<th><span className="visually-hidden">Row tools</span></th></tr></thead>
            <tbody>{indexed.map(({ row, index }, position) => {
              const pieces = piecesPerSample(row.conditioning), samples = samplesOf(row.sample)
              const notes = (row.metadata?.warnings as string[] | undefined)?.length || 0
              return <tr key={index} data-row={index}>
                {COLUMNS.map(([column, label]) => {
                  const problem = problemAt(index, column)
                  return <td key={column} className={'col-' + column}>
                    <input id={`prep-${index}-${column}`} aria-label={`${label}, row ${position + 1}`} title={problem?.message}
                      className={problem ? 'unresolved' : ''} value={row[column]} disabled={busy}
                      list={column === 'conditions' ? 'treatments' : undefined}
                      placeholder={column === 'test' && position > 0 ? '〃 same as above' : ''}
                      onChange={e => setCell(index, column, e.target.value)} />
                    {column === 'conditioning' && row.conditioning && pieces !== null && <small className="cell-hint">{pieces} pieces</small>}
                    {column === 'sample' && samples.length > 1 && <small className="cell-hint">{samples.length} stacks</small>}
                    {column === 'sample' && typeof row.metadata?.sample_count === 'number' && <small className="cell-hint">sheet: {String(row.metadata.sample_count)} sample(s)</small>}
                  </td>
                })}
                <td className="row-tools">
                  {row.metadata?.cells || notes ? <button className="button text" aria-label={`Source, row ${position + 1}`} onClick={() => setSourceRow(index)}>
                    Source{notes ? ' ⚠' : ''}</button> : null}
                  <button className="icon-button" aria-label={`Remove row ${position + 1}`} disabled={busy || rows.length === 1}
                    onClick={() => setRows(previous => previous.filter((_, at) => at !== index))}>×</button>
                </td>
              </tr>
            })}</tbody>
          </table></div>
          <button className="button text add-row" disabled={busy} onClick={() => addRow(sheet)}>Add row</button>
        </div>
      })}
      <div className="card-actions">
        <span className="summary-count">{rows.length} sheet {rows.length === 1 ? 'row' : 'rows'} · {stacks} {stacks === 1 ? 'stack' : 'stacks'} · {specimens} pieces</span>
        <button className="button primary" disabled={problems.length > 0 || !healthy || busy} onClick={() => void register()}>
          {busy ? 'Registering…' : `Register ${stacks} ${stacks === 1 ? 'stack' : 'stacks'}`}
        </button>
      </div>
    </section>}

    {step === 'print' && batchId && <PrintStep id={batchId} healthy={healthy}
      onContinue={() => go(batchId, 'done')} />}
    {step === 'done' && batchId && <DoneStep id={batchId} healthy={healthy} onAnother={startNew}
      newPhysicalBatch={!viewingOther && uploadFile.current ? () => void upload(uploadFile.current!, selectedSheets, true) : undefined} />}

    {sourceRow !== null && rows[sourceRow] && <Modal title="Source" wide onClose={() => setSourceRow(null)}>
      <div className="modal-body"><SourceDetails row={rows[sourceRow]} /></div>
      <div className="modal-actions"><button className="button secondary" onClick={() => setSourceRow(null)}>Close</button></div>
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
    <div className="card-heading"><div><h2>Print labels</h2></div></div>
    <ErrorNotice message={error} />
    {!stacks && !error && <div className="empty-inline compact"><p>Loading stacks…</p></div>}
    {stacks && <LabelPrinter stacks={stacks} healthy={healthy} variant="inline"
      onOutcome={onOutcome}
      extraActions={outcome
        ? <button className="button primary" onClick={onContinue}>Continue</button>
        : <button className="button text" onClick={onContinue}>Print later</button>} />}
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
    : s.entered < s.total ? { text: 'Scan the stacks IN at their room.', action: null }
    : s.finished < s.total ? { text: s.ready ? `${s.ready} pieces are ready to remove.` : 'All pieces are conditioning.', action: null }
    : { text: 'Every piece is out. Batch complete.', action: null }
  return <section className="card">
    <div className="card-heading"><div>
      <h2>Track this batch <span className={'badge ' + (batch.open ? 'blue' : 'good')}>{batch.open ? 'Open' : 'Complete'}</span></h2>
      <p><b>{batch.label}</b> · {formatTime(batch.created_at)} · {batch.stacks.length} stacks · {s.total} pieces</p>
    </div></div>
    <div className="padded">
      <BatchProgress stages={s} legend />
      <div className="notice info next-step"><b>Next:</b> {next.text} {next.action}</div>
    </div>
    <div className="table-wrap"><table>
      <thead><tr><th>Stack</th><th>Labeled</th><th>In room</th><th>Ready</th><th>Finished</th><th /></tr></thead>
      <tbody>{batch.stacks.map(stack => <tr key={stack.id}>
        <td><b>Stack {stack.id} · {stack.project_number} / {stack.material_ref}</b><small>{stack.test_code} · {stack.preconditioning_code}</small></td>
        <td>{stack.labeled}/{stack.total}</td><td>{stack.inside}</td><td>{stack.ready}</td><td>{stack.finished}/{stack.total}</td>
        <td className="row-actions"><a className="button text" href={'#records?stack=' + stack.id}>Open</a></td>
      </tr>)}</tbody>
    </table></div>
    <div className="card-actions">
      <a className="button secondary" href={'#dashboard?batch=' + id}>View on dashboard</a>
      <a className="button secondary" href={'#transactions?batch=' + id}>Transaction history</a>
      {newPhysicalBatch && <button className="button text" onClick={newPhysicalBatch}>Register again as a new batch</button>}
      <span className="grow" />
      <button className="button primary" onClick={onAnother}>New batch</button>
    </div>
  </section>
}
