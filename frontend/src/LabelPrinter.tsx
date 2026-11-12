import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { api, apiBlob, ApiError, nextRequestId, post } from './api'
import type { PrinterInfo, PrintJob, Stack } from './types'
import { ErrorNotice } from './App'

const PRINT_KEY = 'fabric-tracker.pending-print.v3'   // v3: 'print-<n>' request IDs
// The host sizes labels for Brother DK-1209 die-cut labels (62 x 29 mm) on the QL-810W.
type LabelSelection = {
  stack_ids: number[]
  selected_specimen_ids: number[]
}
type PrintIntent = LabelSelection & {
  request_id: string
  printer_queue: string
  copies: number
}
export type LabelOutcome = 'printed' | 'manual'
function readPending(): PrintIntent | null {
  try {
    const value = localStorage.getItem(PRINT_KEY)
    return value ? JSON.parse(value) as PrintIntent : null
  } catch { return null }
}
function forgetPending(identifier?: string) {
  if (identifier && readPending()?.request_id !== identifier) return
  localStorage.removeItem(PRINT_KEY)
}

/**
 * Label selection, image preview (drawn like the PDF), printing, and manual "printed from PDF" confirmation.
 * Rendered inside LabelDialog's modal, or inline as the batch wizard's print step.
 */
export default function LabelPrinter({
  stacks, initialSelectedIds, healthy, variant = 'modal', onClose, closeLabel = 'Close', onOutcome, onBusyChange, extraActions,
}: {
  stacks: Stack[]
  initialSelectedIds?: number[]
  healthy: boolean
  variant?: 'modal' | 'inline'
  onClose?: () => void
  closeLabel?: string
  onOutcome?: (outcome: LabelOutcome) => void
  onBusyChange?: (busy: boolean) => void
  extraActions?: ReactNode
}) {
  const [selected, setSelected] = useState<number[]>(() => initialSelectedIds || stacks.flatMap(stack => stack.specimens.map(s => s.id)))
  const [expanded, setExpanded] = useState<number | null>(() => initialSelectedIds ? stacks[0]?.id ?? null : null)
  const [copies, setCopies] = useState(1)
  const [queue, setQueue] = useState('')
  const [printers, setPrinters] = useState<PrinterInfo | null>(null)
  const [previewURL, setPreviewURL] = useState('')
  const [previewFingerprint, setPreviewFingerprint] = useState('')
  const shownURL = useRef(''); shownURL.current = previewURL
  useEffect(() => () => { if (shownURL.current) URL.revokeObjectURL(shownURL.current) }, [])
  const [previewing, setPreviewing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [job, setJob] = useState<PrintJob | null>(null)
  const [pending, setPending] = useState<PrintIntent | null>(readPending)
  const [manual, setManual] = useState<'idle' | 'confirming' | 'done'>('idle')
  const alive = useRef(true)
  const serial = useRef(0)
  const printLock = useRef(false)
  const outcome = useRef(onOutcome)
  outcome.current = onOutcome
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false; serial.current++ }
  }, [])
  useEffect(() => { onBusyChange?.(busy) }, [busy, onBusyChange])

  const selection = useMemo<LabelSelection>(() => ({
    stack_ids: stacks.filter(stack => stack.specimens.some(s => selected.includes(s.id))).map(stack => stack.id).sort((a, b) => a - b),
    selected_specimen_ids: [...selected].sort((a, b) => a - b),
  }), [stacks, selected])
  const fingerprint = JSON.stringify(selection)
  const valid = selected.length > 0
  useEffect(() => {
    if (!healthy) return
    let current = true
    void api<PrinterInfo>('/printers').then(value => {
      if (current) {
        setPrinters(value)
        // Never fall back to the Mac's default printer: that is usually an office printer, not the label roll.
        setQueue(previous => previous || value.configured_queue || value.printers.find(p => /\bQL|Brother/i.test(p.name))?.name || '')
      }
    }).catch(() => { if (current) setPrinters(null) })
    return () => { current = false }
  }, [healthy])

  useEffect(() => {
    const requestSerial = ++serial.current
    const controller = new AbortController()
    setPreviewFingerprint('')
    setPreviewing(false)
    if (!healthy || !valid) { setPreviewURL(''); return () => controller.abort() }
    setPreviewing(true)
    const timer = setTimeout(() => {
      // An image, not an embedded PDF: kiosk/embedded Chromium and some Safari settings have no PDF viewer.
      // The old image stays until the new one is ready, so ticking a box never flashes the preview blank.
      void apiBlob('/labels/preview.png', selection, controller.signal).then(blob => {
        if (controller.signal.aborted || requestSerial !== serial.current || !alive.current) return
        const objectURL = URL.createObjectURL(blob)
        setPreviewURL(previous => { if (previous) setTimeout(() => URL.revokeObjectURL(previous), 1000); return objectURL })
        setPreviewFingerprint(fingerprint)
        setError('')
      }).catch(err => {
        if (!controller.signal.aborted && requestSerial === serial.current && alive.current) setError((err as Error).message)
      }).finally(() => {
        if (!controller.signal.aborted && requestSerial === serial.current && alive.current) setPreviewing(false)
      })
    }, 180)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [healthy, valid, fingerprint, selection])

  function acceptJob(value: PrintJob) {
    if (alive.current) setJob(value)
    if (value.status === 'submitted' || value.status === 'failed') {
      forgetPending(value.request_id)
      if (alive.current && !readPending()) setPending(null)
    }
    if (value.status === 'submitted') outcome.current?.('printed')
  }
  async function checkPrevious() {
    const intent = readPending()
    if (!intent) { setPending(null); return }
    if (printLock.current || !healthy) return
    printLock.current = true
    setBusy(true)
    setError('')
    try {
      const value = await api<PrintJob>('/print-jobs/' + intent.request_id)
      if (alive.current) acceptJob(value)
    } catch (err) {
      if (alive.current) {
        if (err instanceof ApiError && err.status === 404) {
          if (Array.isArray(intent.stack_ids) && Array.isArray(intent.selected_specimen_ids) && intent.printer_queue) {
            // The original request may still be rendering/claiming its job.
            // Reuse its ID and body; never replace an ambiguous request with a new ID.
            try { acceptJob(await post<PrintJob>('/print-jobs', intent)) }
            catch (retryError) { setError('Still no answer about the last print. ' + (retryError as Error).message) }
          } else {
            setJob({ request_id: intent.request_id, status: 'unknown',
              message: 'Can’t check this old print request. Look at the printer, then allow a reprint if nothing came out.' })
          }
        } else setError('Still no answer about the last print. Try Check again.')
      }
    } finally {
      printLock.current = false
      if (alive.current) setBusy(false)
    }
  }
  // CUPS accepts jobs for an offline printer and holds them; keep checking until the labels leave the Mac's queue.
  useEffect(() => {
    if (!job || job.status !== 'submitted' || !job.waiting_cups_jobs || !healthy) return
    const timer = setTimeout(() => {
      void api<PrintJob>('/print-jobs/' + job.request_id).then(value => { if (alive.current) setJob(value) }).catch(() => {})
    }, 3000)
    return () => clearTimeout(timer)
  }, [job, healthy])
  const checkRef = useRef(checkPrevious)
  checkRef.current = checkPrevious
  useEffect(() => {
    if (!pending || !healthy) return
    void checkRef.current()
    const timer = setInterval(() => void checkRef.current(), 5000)
    return () => clearInterval(timer)
  }, [pending, healthy])

  async function print() {
    if (!healthy || printLock.current || readPending() || !valid || !queue || previewFingerprint !== fingerprint) return
    printLock.current = true
    let intent: PrintIntent
    try { intent = { ...selection, request_id: await nextRequestId('print'), printer_queue: queue, copies } }
    catch (err) { printLock.current = false; setError('Couldn’t get a print number from the Mac. ' + (err as Error).message); return }
    try { localStorage.setItem(PRINT_KEY, JSON.stringify(intent)) }
    catch { printLock.current = false; setError('This browser blocks local storage, which printing needs. Turn it on and try again.'); return }
    setPending(intent)
    setBusy(true)
    setError('')
    setJob(null)
    try {
      const result = await post<PrintJob>('/print-jobs', intent)
      if (alive.current) acceptJob(result)
    } catch (err) {
      if (alive.current) {
        if (err instanceof ApiError && err.status < 500) {
          try { acceptJob(await api<PrintJob>('/print-jobs/' + intent.request_id)) }
          catch (statusError) {
            if (statusError instanceof ApiError && statusError.status === 404) {
              forgetPending(intent.request_id); if (!readPending()) setPending(null)
              setJob({ request_id: intent.request_id, status: 'failed', error: err.message })
            }
          }
        }
        setError('No answer from the print request. ' + (err as Error).message)
      }
    } finally {
      printLock.current = false
      if (alive.current) setBusy(false)
    }
  }
  async function confirmManual() {
    if (!healthy || busy || !valid) return
    setBusy(true); setError(''); setManual('confirming')
    try {
      await post('/labels/confirm', { stack_ids: selection.stack_ids, specimen_ids: selection.selected_specimen_ids, request_id: await nextRequestId('print') })
      if (!alive.current) return
      setManual('done')
      outcome.current?.('manual')
    } catch (err) {
      if (alive.current) { setManual('idle'); setError((err as Error).message) }
    } finally { if (alive.current) setBusy(false) }
  }
  const allIds = stacks.flatMap(stack => stack.specimens.map(s => s.id))
  const chosen = printers?.printers.find(printer => printer.name === queue)
  function openPdf() {
    // Open the window inside the click so pop-up blockers allow it, then fill it with the exact print PDF.
    const tab = window.open('', '_blank')
    void apiBlob('/labels/preview', selection).then(blob => { if (tab) tab.location.href = URL.createObjectURL(blob) })
      .catch(err => { tab?.close(); setError((err as Error).message) })
  }
  function toggle(ids: number[], checked: boolean) {
    if (busy) return
    setSelected(previous => checked ? [...new Set([...previous, ...ids])] : previous.filter(id => !ids.includes(id)))
  }
  const canPrint = healthy && valid && !busy && !pending && !previewing && previewFingerprint === fingerprint &&
    !!queue && printers?.available && Number.isInteger(copies) && copies >= 1 && copies <= 10

  return <>
    <div className={(variant === 'modal' ? 'modal-body ' : 'padded ') + 'label-dialog'}>
      <section className="label-selection-panel">
        <div className="selection-heading">
          <h3>Labels to print</h3>
          <span data-testid="label-scope">{selected.length} selected</span>
        </div>
        <div className="selection-tools">
          <button className="button text" disabled={busy} onClick={() => setSelected(allIds)}>Select all</button>
          <button className="button text" disabled={busy} onClick={() => setSelected([])}>Clear</button>
        </div>
        {stacks.map(stack => {
          const ids = stack.specimens.map(s => s.id)
          const count = ids.filter(id => selected.includes(id)).length
          return <div className="label-stack-choice" key={stack.id}>
            <div className="label-stack-heading">
              <label className="check-label">
                <input type="checkbox" aria-label={'Print stack ' + stack.material_ref + ' ' + stack.preconditioning_code}
                  checked={count === ids.length} disabled={busy}
                  onChange={e => toggle(ids, e.target.checked)} />
                <span><b>{stack.project_number} / {stack.material_ref}</b>
                  <small>{stack.test_code} · {stack.preconditioning_code} · {count} of {ids.length} labels</small></span>
              </label>
              <button className="button text" disabled={busy} aria-expanded={expanded === stack.id}
                onClick={() => setExpanded(expanded === stack.id ? null : stack.id)}>Numbers {expanded === stack.id ? '−' : '+'}</button>
            </div>
            {expanded === stack.id && <div className="selection-grid label-number-grid">
              {stack.specimens.map(specimen => <label key={specimen.id}
                className={'selection-tile ' + (selected.includes(specimen.id) ? 'selected' : '')}>
                <input type="checkbox" aria-label={'Label specimen ' + String(specimen.number).padStart(2, '0') + ' ' + stack.material_ref}
                  disabled={busy} checked={selected.includes(specimen.id)}
                  onChange={e => toggle([specimen.id], e.target.checked)} />
                <b>{String(specimen.number).padStart(2, '0')}</b>
              </label>)}
            </div>}
          </div>
        })}
      </section>
      <section className="label-preview-panel">
        <div className="preview-toolbar">
          <label>Printer
            <select aria-label="Printer queue" value={queue} disabled={busy} onChange={e => setQueue(e.target.value)}>
              <option value="">Choose a printer</option>
              {printers?.printers.map(printer => <option key={printer.name} value={printer.name}>
                {printer.name}{printer.label_printer ? '' : ' (not a label printer)'}{printer.alerts?.includes('offline') ? ' · offline' : ''}</option>)}
            </select>
          </label>
          <label>Copies<input aria-label="Print copies" type="number" min="1" max="10"
            disabled={busy} value={copies} onChange={e => setCopies(Number(e.target.value))} /></label>
        </div>
        {previewURL ? <div className="label-preview-image"><img src={previewURL} alt={`Preview of ${Math.min(selected.length, 12)} of ${selected.length} labels`} />
          {selected.length > 12 && <p className="input-help">Showing the first 12 of {selected.length} labels. Open the PDF to see every page.</p>}</div> :
          <div className="pdf-preview preview-empty" role="status">{previewing ? 'Loading preview…' : !selected.length ? 'Select labels to preview.' : !healthy ? 'Preview unavailable offline.' : 'Preview unavailable.'}</div>}
        {printers && !printers.available && <p className="input-help">No printer available. Open the PDF, print it, then choose “I printed these from the PDF”.</p>}
        {chosen && (!chosen.label_printer || !!chosen.warnings?.length) && <div className="notice warning printer-warnings" role="status"><ul>
          {!chosen.label_printer && <li>This isn’t the Brother label printer. Labels are sized for 62 × 29 mm DK-1209 rolls.</li>}
          {chosen.warnings?.map(warning => <li key={warning}>{warning}</li>)}
        </ul></div>}
        <p className="input-help">Load DK-1209 labels (62 × 29 mm die-cut). Another roll prints the wrong size or stops the printer.</p>
        {job && <div className={'notice ' + (job.status === 'submitted' && !job.waiting_cups_jobs ? 'success' : job.status === 'failed' ? 'error' : 'warning')} role="status">
          {job.status === 'submitted' ? job.message || 'Sent to printer.' : job.message || job.error || job.status}
        </div>}
        {manual === 'done' && <div className="notice success" role="status">Recorded: {selected.length} {selected.length === 1 ? 'label' : 'labels'} printed from the PDF.</div>}
        {pending && <div className="notice warning">
          <b>No answer from the printer yet. Check whether labels came out before printing again.</b>
          <button className="button secondary" disabled={!healthy || busy} onClick={() => void checkPrevious()}>Check again</button>
          {job?.status === 'unknown' && <button className="button text" disabled={busy} onClick={() => {
            forgetPending(); setPending(null); setJob(null)
          }}>Nothing printed: allow reprint</button>}
        </div>}
        <ErrorNotice message={error} />
      </section>
    </div>
    <div className={variant === 'modal' ? 'modal-actions' : 'card-actions'}>
      <button className="button secondary" disabled={!valid || !healthy} onClick={openPdf}>Open PDF ↗</button>
      {valid && manual !== 'done' && <button className="button text" disabled={!healthy || busy || !valid}
        onClick={() => void confirmManual()}>{manual === 'confirming' ? 'Recording…' : 'I printed these from the PDF'}</button>}
      <span className="grow" />
      {extraActions}
      {onClose && <button className="button secondary" disabled={busy} onClick={onClose}>{closeLabel}</button>}
      <button className="button primary" disabled={!canPrint} onClick={() => void print()}>
        {busy && manual !== 'confirming' ? 'Checking print request…' : 'Print ' + selected.length * copies + (selected.length * copies === 1 ? ' label' : ' labels')}
      </button>
    </div>
  </>
}
