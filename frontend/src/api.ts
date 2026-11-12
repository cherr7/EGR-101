let serverOffsetMs = 0
export const serverNow = () => Date.now() + serverOffsetMs
export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) { super(message); this.status = status }
}

/** The server's error text: FastAPI's detail (a string, validation list or {message}), else the fallback. */
function errorMessage(body: { detail?: unknown; message?: string }, fallback: string) {
  const detail = body.detail as string | { msg: string; loc?: string[] }[] | { message?: string } | undefined
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map(item => `${item.loc?.join('.') || 'Input'}: ${item.msg}`).join('; ')
  return detail?.message || body.message || fallback
}

export async function api<T>(path: string, options: RequestInit = {}, timeout = 10000): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)
  try {
    const response = await fetch(`/api${path}`, { credentials: 'include', ...options, headers: { ...(options.body && !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}), ...options.headers }, signal: controller.signal })
    const body = await response.json().catch(() => ({}))
    const serverTime = body.server_time || body.items?.[0]?.server_time
    if (serverTime && Number.isFinite(Date.parse(serverTime))) serverOffsetMs = Date.parse(serverTime) - Date.now()
    if (!response.ok) throw new ApiError(response.status, errorMessage(body, `Request failed (${response.status})`))
    return body as T
  } finally { clearTimeout(timer) }
}
export const post = <T,>(path: string, body: unknown) => api<T>(path, { method: 'POST', body: JSON.stringify(body) })
export async function apiBlob(path: string, body: unknown, signal?: AbortSignal): Promise<Blob> {
  const response = await fetch('/api' + path, {
    method: 'POST', credentials: 'include',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
  })
  if (!response.ok) throw new ApiError(response.status, errorMessage(await response.json().catch(() => ({})), 'Could not generate the label preview.'))
  return response.blob()
}
export const formatTime = (value?: string | null) => value ? new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit' }).format(new Date(value)) : '—'
export const elapsed = (value?: string | null, end?: string | null) => {
  if (!value) return '—'
  const minutes = Math.max(0, Math.floor(((end ? Date.parse(end) : serverNow()) - Date.parse(value)) / 60000))
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}
export const entryOf = (s: import('./types').Specimen) => s.ENTRY ?? s.entry_at ?? s.sessions?.[0]?.entered_at ?? s.entered_at
export const exitOf = (s: import('./types').Specimen) => s.EXIT ?? s.exit_at ?? s.sessions?.[0]?.exited_at ?? null
export const specimenNumber = (value: number) => String(value).padStart(2, '0')
export const specimenNumbers = (values: number[]) => values.map(specimenNumber).join(', ')
export const specimenStatus = (s: import('./types').Specimen) => exitOf(s) ? 'Finished' : entryOf(s) ? 'Conditioning' : 'Awaiting entry'
export const isReady = (s: import('./types').Specimen, now = serverNow()) => typeof s.ready === 'boolean' ? s.ready : s.state === 'IN' && !!s.due_at && Date.parse(s.due_at) <= now
export const localTimeInput = (value: string) => new Intl.DateTimeFormat('sv-SE', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date(value)).replace(' ', 'T')
export function easternToUTC(value: string) { const target = Date.parse(`${value}Z`); let guess = target; for (let i = 0; i < 4; i++) { const represented = Date.parse(`${localTimeInput(new Date(guess).toISOString())}Z`); guess += target - represented } if (localTimeInput(new Date(guess).toISOString()).slice(0, value.length) !== value) throw new Error('This Eastern Time does not exist. Check the daylight-saving change.'); return new Date(guess).toISOString() }
const REQUEST_IDS_KEY = 'fabric-tracker.request-ids.v2'
type RequestBlock = { next: number; last: number }
type Pools = Record<string, RequestBlock[]>
const readPools = (): Pools => { try { return JSON.parse(localStorage.getItem(REQUEST_IDS_KEY) || '{}') as Pools } catch { return {} } }
const remaining = (blocks: RequestBlock[] = []) => blocks.reduce((sum, b) => sum + Math.max(0, b.last - b.next + 1), 0)
async function reserve(client: string) {
  const reserved = await post<{ first: number; last: number }>('/request-ids', { client, count: client === 'print' ? 5 : 50 })
  const pools = readPools()
  pools[client] = [...(pools[client] || []).filter(b => b.next <= b.last), { next: reserved.first, last: reserved.last }]
  localStorage.setItem(REQUEST_IDS_KEY, JSON.stringify(pools))
}
/** Retry IDs are '<client>-<n>': the kiosk station id or 'print', then a number the server
 *  reserved for this browser. A request keeps its ID through every retry; the server refuses
 *  a reused ID with different details, so a duplicate can never record twice. A lost
 *  reservation response only skips numbers. */
export async function nextRequestId(client: string): Promise<string> {
  if (!remaining(readPools()[client])) await reserve(client)
  const pools = readPools()
  const block = (pools[client] || []).find(b => b.next <= b.last)
  if (!block) throw new Error('Can’t reach the server. Try again.')
  const value = block.next
  block.next += 1
  localStorage.setItem(REQUEST_IDS_KEY, JSON.stringify(pools))   // claimed before it is used
  return `${client}-${value}`
}
/** Top up a station's numbers in the background so a scan never waits for a reservation. */
export async function reserveAhead(client: string) {
  try { if (remaining(readPools()[client]) < 10) await reserve(client) } catch { /* The next scan reserves its own block. */ }
}
/** Compact duration since/until an ISO time, e.g. "45 min", "3 h 10 min", "2 d 4 h". */
export function duration(value?: string | null, until = serverNow()) {
  if (!value) return '—'
  const minutes = Math.max(0, Math.round((until - Date.parse(value)) / 60000))
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`
  return hours % 24 ? `${Math.floor(hours / 24)} d ${hours % 24} h` : `${Math.floor(hours / 24)} d`
}
export const bytes = (value?: number | null) => value == null ? '—' : value >= 1e12 ? `${(value / 1e12).toFixed(2)} TB` : value >= 1e9 ? `${(value / 1e9).toFixed(1)} GB` : value >= 1e6 ? `${(value / 1e6).toFixed(1)} MB` : `${Math.ceil(value / 1e3)} KB`
/** Hash-route query parameters, e.g. #dashboard?room=shared -> {room: 'shared'}. */
export const hashParams = () => new URLSearchParams(location.hash.split('?')[1] || '')
