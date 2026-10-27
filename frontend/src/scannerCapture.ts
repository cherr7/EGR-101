/** Decimal ECC200 payloads, UUIDs, and the legacy labeled UUID format. */
export function validScannerCode(code: string) {
  return /^(?:\]d[0-9])?\d{1,39}$/.test(code) || /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(code) || /^(?:UL1:[PS]:)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(code)
}

/** Keyboard-wedge capture: page-wide, without a visible input or browser USB permission. */
export function attachScanner(options: { enabled: () => boolean; onScan: (value: string) => void; onReject: (message: string) => void; onBlocked: () => void; onFocusChange: () => void; context?: () => string }) {
  let buffer = '', invalid = false, started = false, allowed = false, context: string | undefined, timeout: ReturnType<typeof setTimeout> | undefined
  const reset = () => { buffer = ''; invalid = false; started = false; allowed = false; context = undefined; clearTimeout(timeout) }
  const consume = (event: KeyboardEvent) => { event.preventDefault(); event.stopImmediatePropagation() }
  const keydown = (event: KeyboardEvent) => {
    // Enter ends a scan; with the Eyoyo suffix set to "None" a scan is ESC, payload, ESC instead.
    if (event.key === 'Enter' || event.key === 'Escape') { consume(event); if (event.isComposing || event.ctrlKey || event.altKey || event.metaKey) { reset(); return } if (!started) return; const code = buffer, bad = invalid, permitted = allowed && context === options.context?.(); reset(); if (!permitted || !options.enabled()) { options.onBlocked(); return } if (bad || !validScannerCode(code)) { options.onReject('Unrecognized scanner input. Rescan the complete label.'); return } options.onScan(code); return }
    if (event.isComposing || event.ctrlKey || event.altKey || event.metaKey) { reset(); return }
    if (event.key.length === 1) { consume(event); if (event.key === ' ' && !started) return; if (!started) { allowed = options.enabled(); context = options.context?.() } started = true; if (buffer.length >= 132 || !/^[\x21-\x7e]$/.test(event.key)) invalid = true; else buffer += event.key; clearTimeout(timeout); timeout = setTimeout(() => { buffer = ''; invalid = true; options.onReject('Incomplete scan cleared. Rescan the complete label.') }, 350) }
    else if (started && event.key !== 'Shift') { consume(event); reset() }
  }
  const keyup = (event: KeyboardEvent) => { if (event.key === ' ' || event.key === 'Enter' || event.key === 'Escape') consume(event) }
  const interrupted = () => { reset(); options.onFocusChange() }
  document.addEventListener('keydown', keydown, true); document.addEventListener('keyup', keyup, true); document.addEventListener('visibilitychange', interrupted); window.addEventListener('blur', interrupted); window.addEventListener('focus', interrupted)
  return () => { reset(); document.removeEventListener('keydown', keydown, true); document.removeEventListener('keyup', keyup, true); document.removeEventListener('visibilitychange', interrupted); window.removeEventListener('blur', interrupted); window.removeEventListener('focus', interrupted) }
}
