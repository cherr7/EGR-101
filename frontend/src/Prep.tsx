import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { Config, Draft, PrepHeader, PrepProblem, PrepRow, Stack } from './types'
import { api, post } from './api'
import { ErrorNotice, Modal, PageHeader } from './App'
import LabelDialog from './LabelDialog'

// The screen is the prep sheet: the header values once above each worksheet's table, then
// one row per sheet row with the sheet's own columns in the sheet's order. Registration
// turns each row into one stack per sample number, on the server.
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

function SourceDetails({ row }: { row: PrepRow }) {
  const metadata = row.metadata || {}
  const cells = metadata.cells as Record<string, { cell?: string; value?: string; formula?: string }> | undefined
  const warnings = metadata.warnings as string[] | undefined
  return <>
    {cells && Object.keys(cells).length ? <div className="table-wrap source-cells"><table>
      <thead><tr><th>Sheet column</th><th>Excel cell</th><th>Workbook value</th></tr></thead>
      <tbody>{Object.entries(cells).map(([name, value]) => <tr key={name}>
        <td>{name.replaceAll('_', ' ')}</td><td><b>{value.cell || '—'}</b></td>
        <td>{value.formula ? `=${value.formula} → ${value.value || 'not cached'}` : value.value || '—'}</td>
      </tr>)}</tbody>
    </table></div> : <p className="subtle">Typed on this screen. No workbook cells.</p>}
    {warnings?.map((warning, index) => <div className="notice warning" key={index}>{warning}</div>)}
  </>
}

export default function Prep({ config, healthy }: { config: Config; healthy: boolean }) {
  const initial = useRef(restoredReview()).current
  const [headers, setHeaders] = useState(initial.headers)
  const [rows, setRows] = useState(initial.rows)
  const [draft, setDraft] = useState<Draft | null>(initial.draft)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [sourceRow, setSourceRow] = useState<number | null>(null)
  const [registeredStacks, setRegisteredStacks] = useState<Stack[]>([])
  const [labelStacks, setLabelStacks] = useState<Stack[] | null>(null)
  const [selectedSheets, setSelectedSheets] = useState<string[]>(() => initial.draft?.sheets?.filter(sheet => sheet.selected).map(sheet => sheet.name) || [])
  const uploadFile = useRef<File | null>(null)
  const alive = useRef(true), operation = useRef(0), workLock = useRef(false)
  const current = useRef<Review>({ draft, headers, rows }); current.current = { draft, headers, rows }
  const registered = draft?.committed ? draft.stack_ids : []
  const locked = busy || registered.length > 0
  const { problems, stacks, specimens } = check(headers, rows)
  const problemAt = (index: number, column: string) => problems.find(p => p.row === index && p.column === column)
  const sheets = [...new Set([...Object.keys(headers), ...rows.map(row => row.sheet)])].filter(name => rows.some(row => row.sheet === name))

  useEffect(() => { alive.current = true; return () => { alive.current = false; operation.current++ } }, [])
  // Every edit is kept in this browser before the next paint; nothing to save by hand.
  useLayoutEffect(() => {
    try { localStorage.setItem(REVIEW_KEY, JSON.stringify({ draft, headers, rows })) }
    catch { setError('This browser cannot keep your edits. Keep this page open until registration finishes.') }
  }, [draft, headers, rows])
  useEffect(() => {
    if (!healthy || !draft?.committed || registeredStacks.length) return
    let active = true
    void Promise.all(draft.stack_ids.map(id => api<Stack>('/stacks/' + id))).then(value => { if (active && alive.current) setRegisteredStacks(value) }).catch(() => {})
    return () => { active = false }
  }, [healthy, draft, registeredStacks.length])

  async function run<T>(work: () => Promise<T>, success: (value: T) => void) {
    if (workLock.current || !healthy) return
    const id = ++operation.current
    workLock.current = true; setBusy(true); setError('')
    try { const value = await work(); if (alive.current && id === operation.current) success(value) }
    catch (err) { if (alive.current && id === operation.current) setError((err as Error).message) }
    finally { workLock.current = false; if (alive.current && id === operation.current) setBusy(false) }
  }
  function load(value: Draft) {
    setDraft(value)
    setHeaders(Object.keys(value.headers || {}).length ? value.headers : { Preparation: blankHeader() })
    setRows(value.rows.length ? value.rows : [blankRow()])
    setRegisteredStacks([])
    setLabelStacks(null)
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
      setMessage(value.committed ? 'This sheet is already registered.' : `Imported ${value.rows.length} sheet rows. Check them against the paper sheet.`)
    })
  }
  async function register() {
    const reviewed = current.current
    await run(async () => {
      let saved = reviewed.draft
      // Check the server copy first: an earlier save whose response was lost may already hold these edits.
      const latest = saved ? await api<Draft>('/prep-drafts/' + saved.id) : null
      if (latest?.committed) saved = latest
      if (!saved?.committed) {
        const body = { headers: reviewed.headers, rows: reviewed.rows }
        if (latest && canonical({ headers: latest.headers, rows: latest.rows }) === canonical(body)) saved = latest
        else if (latest && saved && latest.revision !== saved.revision)
          throw new Error('This prep sheet changed in another browser. Load the latest saved sheet, then check it again.')
        else saved = saved
          ? await api<Draft>('/prep-drafts/' + saved.id, { method: 'PUT', body: JSON.stringify({ ...body, expected_revision: saved.revision }) })
          : await post<Draft>('/prep-drafts', body)
        // Keep the server's draft id before the commit, so a lost commit response can be checked.
        localStorage.setItem(REVIEW_KEY, JSON.stringify({ ...reviewed, draft: saved }))
        if (alive.current) setDraft(saved)
        try {
          const result = await post<{ stack_ids: number[] }>('/prep-drafts/' + saved.id + '/commit', { expected_revision: saved.revision })
          saved = { ...saved, committed: true, stack_ids: result.stack_ids }
        } catch (err) {
          const check = await api<Draft>('/prep-drafts/' + saved.id).catch(() => null)
          if (!check?.committed) throw err
          saved = check
        }
      }
      const value = await Promise.all(saved.stack_ids.map(id => api<Stack>('/stacks/' + id)))
      return { saved, value }
    }, ({ saved, value }) => {
      setDraft(saved); setRegisteredStacks(value); setLabelStacks(value)
      setMessage(`Registered ${saved.stack_ids.length} stacks.`)
    })
  }
  async function openLabels() {
    // After a reload the registered stacks may still be loading; fetch them rather than open an empty dialog.
    if (registeredStacks.length === registered.length) { setLabelStacks(registeredStacks); return }
    await run(() => Promise.all(registered.map(id => api<Stack>('/stacks/' + id))), value => { setRegisteredStacks(value); setLabelStacks(value) })
  }
  function startNew() {
    operation.current++
    const value = fresh()
    setDraft(null); setHeaders(value.headers); setRows(value.rows)
    uploadFile.current = null
    setSelectedSheets([]); setRegisteredStacks([]); setLabelStacks(null); setMessage(''); setError('')
  }
  async function reload() {
    if (!draft) return
    await run(() => api<Draft>('/prep-drafts/' + draft.id), value => { load(value); setMessage('Loaded the latest saved sheet.') })
  }

  return <>
    <PageHeader title="Preparation">
      <button className="button secondary" disabled={busy} onClick={startNew}>＋ New prep sheet</button>
    </PageHeader>
    <ErrorNotice message={error} />
    {error.includes('another browser') && <button className="button secondary" disabled={busy} onClick={() => void reload()}>Load the latest saved sheet</button>}
    {message && <div className="notice success" role="status">{message}</div>}
    <section className="card prep-card">
      <div className="card-heading">
        <div><h2>Prep sheet {draft?.source && <span className="badge outline">{draft.source.filename}</span>}</h2></div>
        <label className={'button primary upload ' + (busy || !healthy ? 'disabled' : '')}>↑ Import Excel / CSV
          <input aria-label="Import prep workbook" type="file" accept=".xlsx,.xlsm,.csv" disabled={busy || !healthy}
            onChange={e => { if (e.target.files?.[0]) void upload(e.target.files[0]); e.target.value = '' }} />
        </label>
      </div>
      {draft?.warnings?.length ? <details className="import-warnings">
        <summary>Import notes · {draft.warnings.length}</summary>
        {draft.warnings.map((warning, index) => <p key={index}>{warning}</p>)}
      </details> : null}
      {draft?.sheets && draft.sheets.length > 1 ? <details className="sheet-inventory">
        <summary>{draft.sheets.length} worksheets in this file</summary>
        <div className="sheet-list">{draft.sheets.map(sheet => <label className="check-label" key={sheet.name}>
          <input type="checkbox" checked={selectedSheets.includes(sheet.name)} disabled={!uploadFile.current || locked}
            onChange={e => setSelectedSheets(previous => e.target.checked ? [...previous, sheet.name] : previous.filter(name => name !== sheet.name))} />
          <span><b>{sheet.name}</b> · {sheet.kind.replaceAll('_', ' ')} · {sheet.candidate_count} rows</span>
        </label>)}</div>
        {uploadFile.current && !registered.length && <button className="button secondary" disabled={busy || !selectedSheets.length}
          onClick={() => uploadFile.current && void upload(uploadFile.current, selectedSheets)}>Use the selected worksheets</button>}
      </details> : null}
      {!registered.length && <div className="prep-exceptions">
        <span className={'badge ' + (problems.length ? 'warning' : 'good')}>{problems.length ? problems.length + ' cells to fill' : 'Every row is complete'}</span>
        {problems.length > 0 && <button className="button text" onClick={jumpToProblem}>Go to the first one →</button>}
      </div>}
      <datalist id="treatments">{config.treatments.map(treatment => <option key={treatment} value={treatment} />)}</datalist>
      {sheets.map(sheet => {
        const header = headers[sheet] || blankHeader()
        const indexed = rows.map((row, index) => ({ row, index })).filter(item => item.row.sheet === sheet)
        return <div className="prep-sheet" key={sheet} data-sheet={sheet}>
          {sheets.length > 1 && <h3 className="prep-sheet-name">{sheet}</h3>}
          <div className="prep-sheet-header" role="group" aria-label={`${sheet} header`}>
            {HEADER_FIELDS.map(([field, label]) => <label key={field}>{label}
              <input id={`prep-header-${sheet}-${field}`} value={header[field]} disabled={locked}
                className={field === 'project_number' && !projectOf(header) ? 'unresolved' : ''}
                placeholder={field === 'project_number' ? 'Oracle project or LabWare #' : ''}
                onChange={e => setHeader(sheet, field, e.target.value)} />
            </label>)}
          </div>
          <div className="table-wrap"><table className="prep-table prep-sheet-table">
            <thead><tr>{COLUMNS.map(([column, label]) => <th key={column} className={'col-' + column}>{label}</th>)}<th><span className="visually-hidden">Row tools</span></th></tr></thead>
            <tbody>{indexed.map(({ row, index }, position) => {
              const pieces = piecesPerSample(row.conditioning), samples = samplesOf(row.sample)
              return <tr key={index} data-row={index}>
                {COLUMNS.map(([column, label]) => {
                  const problem = problemAt(index, column)
                  return <td key={column} className={'col-' + column}>
                    <input id={`prep-${index}-${column}`} aria-label={`${label}, row ${position + 1}`} title={problem?.message}
                      className={problem ? 'unresolved' : ''} value={row[column]} disabled={locked}
                      list={column === 'conditions' ? 'treatments' : undefined}
                      placeholder={column === 'test' && position > 0 ? '〃 same as above' : ''}
                      onChange={e => setCell(index, column, e.target.value)} />
                    {column === 'conditioning' && row.conditioning && pieces !== null && <small className="cell-hint">{pieces} pieces</small>}
                    {column === 'sample' && samples.length > 1 && <small className="cell-hint">{samples.length} stacks</small>}
                    {column === 'sample' && typeof row.metadata?.sample_count === 'number' && <small className="cell-hint">sheet: {String(row.metadata.sample_count)} sample(s)</small>}
                  </td>
                })}
                <td className="row-tools">
                  {row.metadata?.cells ? <button className="button text" aria-label={`Workbook cells, row ${position + 1}`} onClick={() => setSourceRow(index)}>Cells ↗</button> : null}
                  <button className="icon-button" aria-label={`Remove row ${position + 1}`} disabled={locked || rows.length === 1}
                    onClick={() => setRows(previous => previous.filter((_, at) => at !== index))}>×</button>
                </td>
              </tr>
            })}</tbody>
          </table></div>
          {!registered.length && <button className="button text add-row" disabled={locked} onClick={() => addRow(sheet)}>＋ Add row</button>}
        </div>
      })}
      <div className="card-actions">
        <span className="summary-count">{rows.length} sheet rows · {stacks} stacks · {specimens} specimens</span>
        <button className="button primary" disabled={(!registered.length && problems.length > 0) || !healthy || busy}
          onClick={() => registered.length ? void openLabels() : void register()}>
          {busy ? 'Working…' : registered.length ? 'Preview / reprint labels →' : 'Register & preview labels →'}
        </button>
      </div>
    </section>
    {registered.length > 0 && <section className="card">
      <div className="card-heading">
        <div><h2>Registered stacks</h2></div>
        {uploadFile.current && <button className="button secondary" disabled={busy || !healthy}
          onClick={() => uploadFile.current && void upload(uploadFile.current, selectedSheets, true)}>Register this file again as a new batch</button>}
      </div>
      {registeredStacks.map(stack => <div className="export-row" key={stack.id}>
        <div><b>Stack {stack.id} · {stack.project_number} / {stack.material_ref}</b>
          <small>{stack.test_code} · {stack.preconditioning_code} · {stack.specimen_count} specimens</small></div>
        <button className="button secondary" disabled={!healthy || busy} onClick={() => setLabelStacks([stack])}>Preview / reprint</button>
        <a className="button text" href={'#records?stack=' + stack.id}>Open or edit stack →</a>
      </div>)}
    </section>}
    {sourceRow !== null && rows[sourceRow] && <Modal title="Workbook cells for this row" wide onClose={() => setSourceRow(null)}>
      <div className="modal-body"><SourceDetails row={rows[sourceRow]} /></div>
      <div className="modal-actions"><button className="button secondary" onClick={() => setSourceRow(null)}>Back to the sheet</button></div>
    </Modal>}
    {labelStacks && <LabelDialog stacks={labelStacks} healthy={healthy} onClose={() => setLabelStacks(null)} />}
  </>
}
