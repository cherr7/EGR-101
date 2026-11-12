import { useEffect, useState } from 'react'
import { api } from './api'

type StationPresence = { id: string; name: string; status: 'ready' | 'no_scanner' | 'offline' | 'never_seen' }
type HostInfo = { ip: string | null; hostname: string; port: number; url: string | null; hostname_url: string }
type Network = { host: HostInfo; stations: StationPresence[] }

const STATUS: Record<StationPresence['status'], { tone: string; label: string }> = {
  ready: { tone: 'good', label: 'Ready' },
  no_scanner: { tone: 'warn', label: 'No scanner' },
  offline: { tone: 'bad', label: 'Offline' },
  never_seen: { tone: 'idle', label: 'Not connected' },
}

export default function NetworkStatus({ healthy }: { healthy: boolean | null }) {
  const [network, setNetwork] = useState<Network | null>(null)
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
  return <div className="network-status" aria-label="Network status">
    <div className="network-host">
      <a href={host.url || host.hostname_url} target="_blank" rel="noreferrer" data-testid="host-address">{address}</a>
    </div>
    <ul className="network-stations" aria-label="Scanner stations">
      {stations.map(s => <li key={s.id} className={'connection station-presence ' + STATUS[s.status].tone} title={STATUS[s.status].label} data-testid={`station-${s.id}`}>
        <i aria-hidden="true" />
        <span className="station-presence-name">{s.name.replace(/( conditioning room)? (door|station)$/i, '')}</span>
        <span className="sr-only">{STATUS[s.status].label}</span>
      </li>)}
    </ul>
  </div>
}
