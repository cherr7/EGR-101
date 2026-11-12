import { useCallback, useRef } from 'react'
import type { Stack } from './types'
import { Modal } from './App'
import LabelPrinter from './LabelPrinter'
import type { LabelOutcome } from './LabelPrinter'

/** Modal wrapper around LabelPrinter, used for reprints from records, the dashboard, and elsewhere. */
export default function LabelDialog({
  stacks, initialSelectedIds, healthy, onClose, onOutcome,
}: {
  stacks: Stack[]
  initialSelectedIds?: number[]
  healthy: boolean
  onClose: () => void
  onOutcome?: (outcome: LabelOutcome) => void
}) {
  const busy = useRef(false)
  const track = useCallback((value: boolean) => { busy.current = value }, [])
  return <Modal title="Preview & print labels" wide onClose={() => !busy.current && onClose()}>
    <LabelPrinter stacks={stacks} initialSelectedIds={initialSelectedIds} healthy={healthy}
      onClose={onClose} onOutcome={onOutcome} onBusyChange={track} />
  </Modal>
}
