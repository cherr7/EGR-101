import type { Stages } from './types'

/** Pieces in each mutually exclusive stage, left (done) to right (not started). */
export function stageSegments(s: Stages) {
  return [
    { key: 'finished', label: 'Finished', count: s.finished },
    { key: 'ready', label: 'Ready to remove', count: s.ready },
    { key: 'conditioning', label: 'Conditioning', count: s.inside - s.ready },
    { key: 'labeled', label: 'Labeled, not in room', count: s.labeled - s.entered },
    { key: 'unlabeled', label: 'Needs labels', count: s.total - s.labeled },
  ].filter(segment => segment.count > 0)
}

export default function BatchProgress({ stages, legend = false }: { stages: Stages; legend?: boolean }) {
  const segments = stageSegments(stages)
  const summary = segments.map(s => `${s.count} ${s.label.toLowerCase()}`).join(', ')
  return <div className="batch-progress">
    <div className="stage-bar" role="img" aria-label={`${stages.total} pieces: ${summary || 'none'}`}>
      {segments.map(s => <span key={s.key} className={'stage ' + s.key} style={{ flexGrow: s.count }} title={`${s.label}: ${s.count}`} />)}
    </div>
    {legend && <ul className="stage-legend">
      {segments.map(s => <li key={s.key}><i className={'stage ' + s.key} aria-hidden="true" />{s.label} <b>{s.count}</b></li>)}
    </ul>}
  </div>
}
