/** Label payloads the API can resolve; anything else is rejected before a lookup. */
export const validScannerCode = (code: string) => /^\d{1,39}$/.test(code) || /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(code) || /^(?:UL1:[PS]:)[0-9a-f-]{36}$/i.test(code) || /^0x[0-9a-f]{1,32}$/i.test(code) || /^0b[01]{1,128}$/i.test(code)

/** Keyboard-wedge capture: no input focus or browser USB permission required. */
export function attachScanner(options: { enabled: () => boolean; onScan: (value: string) => void; onReject: (message: string) => void; onBlocked: () => void; onFocusChange: () => void }) {
  let buffer = '', invalid = false, started = false, timeout: ReturnType<typeof setTimeout> | undefined
  const reset = () => { buffer = ''; invalid = false; started = false; clearTimeout(timeout) }
  const consume = (event: KeyboardEvent) => { event.preventDefault(); event.stopImmediatePropagation() }
  const keydown = (event: KeyboardEvent) => {
    if (event.key === 'Enter') { consume(event); if (event.isComposing || event.ctrlKey || event.altKey || event.metaKey) { reset(); return } if (!started) return; const code = buffer, bad = invalid; reset(); if (bad || !validScannerCode(code)) { options.onReject('Unrecognized scanner input. Rescan the complete label.'); return } if (!options.enabled()) { options.onBlocked(); return } options.onScan(code); return }
    if (event.isComposing || event.ctrlKey || event.altKey || event.metaKey) { reset(); return }
    if (event.key.length === 1) { consume(event); if (event.key === ' ' && !started) return; started = true; if (buffer.length >= 132 || !/^[\x21-\x7e]$/.test(event.key)) invalid = true; else buffer += event.key; clearTimeout(timeout); timeout = setTimeout(() => { buffer = ''; invalid = true; options.onReject('Incomplete scan cleared. Rescan the complete label.') }, 350) }
    else if (started && event.key !== 'Shift') { consume(event); reset() }
  }
  const keyup = (event: KeyboardEvent) => { if (event.key === ' ' || event.key === 'Enter') consume(event) }
  const interrupted = () => { reset(); options.onFocusChange() }
  document.addEventListener('keydown', keydown, true); document.addEventListener('keyup', keyup, true); document.addEventListener('visibilitychange', interrupted); window.addEventListener('blur', interrupted); window.addEventListener('focus', interrupted)
  return () => { reset(); document.removeEventListener('keydown', keydown, true); document.removeEventListener('keyup', keyup, true); document.removeEventListener('visibilitychange', interrupted); window.removeEventListener('blur', interrupted); window.removeEventListener('focus', interrupted) }
}
