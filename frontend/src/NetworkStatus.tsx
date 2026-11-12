import { useEffect, useState } from 'react'
import { api, formatTime } from './api'

type StationPresence = {
  id: string; name: string; room_id: string
  status: 'ready' | 'no_scanner' | 'offline' | 'never_seen'
  ip: string | null; last_seen: string | null; scanner_ready: boolean; input: string | null; live_clients: string[]
}
type HostInfo = { ip: string | null; hostname: string; port: number; url: string | null; hostname_url: string }
type Network = { host: HostInfo; stations: StationPresence[] }

const STATUS: Record<StationPresence['status'], { tone: string; label: string }> = {
  ready: { tone: 'good', label: 'Ready' },
  no_scanner: { tone: 'warn', label: 'No scanner' },
  offline: { tone: 'bad', label: 'Offline' },
  never_seen: { tone: 'idle', label: 'Not connected' },
}

function stationTitle(s: StationPresence) {
  const state = STATUS[s.status].label
  if (s.status === 'never_seen') return `${s.name}: not seen since the Mac app started`
  const extra = s.live_clients.length > 1 ? ` · ${s.live_clients.length} devices using this station: ${s.live_clients.join(', ')}` : ''
  return `${s.name}: ${state} · ${s.input === 'usb-bridge' ? 'Pi USB reader' : 'keyboard capture'} · last seen ${formatTime(s.last_seen)}${extra}`
}

export default function NetworkStatus({ healthy }: { healthy: boolean | null }) {
  const [network, setNetwork] = useState<Network | null>(null)
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    if (!healthy) return
    let alive = true
    const load = () => api<Network>('/network/status', {}, 3500).then(value => { if (alive) setNetwork(value) }).catch(() => undefined)
    void load()
    const timer = setInterval(load, 5000)
    return () => { alive = false; clearInterval(timer) }
  }, [healthy])
  if (!network) return null
  const { host, stations } = network
  const address = host.ip ? `${host.ip}:${host.port}` : `${host.hostname}:${host.port}`
  const shareUrl = host.url || host.hostname_url
  async function copy() {
    try { await navigator.clipboard.writeText(shareUrl); setCopied(true); setTimeout(() => setCopied(false), 1500) } catch { /* clipboard unavailable over plain http */ }
  }
  return <div className="network-status" aria-label="Network status">
    <div className="network-host">
      <span className="network-label">Open on other computers</span>
      <span className="network-host-row">
        <a href={shareUrl} target="_blank" rel="noreferrer" title={`Also at ${host.hostname_url}`} data-testid="host-address">{address}</a>
        {window.isSecureContext && <button type="button" className="network-copy" onClick={() => void copy()} aria-label="Copy host address">{copied ? '✓' : '⧉'}</button>}
      </span>
    </div>
    <ul className="network-stations" aria-label="Scanner stations">
      {stations.map(s => <li key={s.id} className={'connection station-presence ' + STATUS[s.status].tone} title={stationTitle(s)} data-testid={`station-${s.id}`}>
        <i aria-hidden="true" />
        <span className="station-presence-name">{s.name.replace(/( conditioning room)? (door|station)$/i, '')}</span>
        <span className="station-presence-ip">{s.status === 'never_seen' ? '—' : s.ip}</span>
        <span className="sr-only">{STATUS[s.status].label}</span>
      </li>)}
    </ul>
  </div>
}
