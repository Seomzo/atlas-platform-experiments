import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  $cortexDreamJob,
  $cortexHealth,
  $cortexStatusError,
  refreshCortexDreamStatus,
  refreshCortexHealth,
  runCortexDreamNow
} from '@/store/starmap'

export function CortexStatus() {
  const health = useStore($cortexHealth)
  const dream = useStore($cortexDreamJob)
  const error = useStore($cortexStatusError)
  const [running, setRunning] = useState(false)
  const activeJob = dream?.job

  useEffect(() => {
    if (!activeJob || !['queued', 'running'].includes(activeJob.status)) {
      return
    }

    const timeout = window.setTimeout(() => void refreshCortexDreamStatus(activeJob.id), 2_000)

    return () => window.clearTimeout(timeout)
  }, [activeJob])

  const run = async () => {
    setRunning(true)

    try {
      await runCortexDreamNow()
    } finally {
      setRunning(false)
    }
  }

  const status = health?.status ?? 'checking'

  return (
    <details className="pointer-events-auto absolute left-3 top-16 z-20 w-64 rounded-xl border border-(--ui-stroke-secondary) bg-[color-mix(in_srgb,var(--ui-bg-elevated)_94%,transparent)] text-xs shadow-md backdrop-blur-md [-webkit-app-region:no-drag]">
      <summary
        aria-label={`Atlas Cortex health: ${status}`}
        className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2 font-medium"
      >
        <span>Atlas Cortex</span>
        <span className="flex items-center gap-1.5 text-[0.68rem] text-muted-foreground">
          <span
            aria-hidden="true"
            className={`size-1.5 rounded-full ${status === 'healthy' ? 'bg-emerald-500' : status === 'checking' ? 'bg-amber-400' : 'bg-destructive'}`}
          />
          {status}
        </span>
      </summary>
      <div className="space-y-3 border-t border-(--ui-stroke-secondary) px-3 py-3">
        <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-[0.7rem]">
          <dt className="text-muted-foreground">Memories</dt>
          <dd>{health?.counts.memory_records ?? '—'}</dd>
          <dt className="text-muted-foreground">Entities</dt>
          <dd>{health?.counts.entities ?? '—'}</dd>
          <dt className="text-muted-foreground">Pending maintenance</dt>
          <dd>{health?.jobs.pending ?? '—'}</dd>
          <dt className="text-muted-foreground">Needs review</dt>
          <dd>{health?.quality.observations_awaiting_maintenance ?? '—'}</dd>
          <dt className="text-muted-foreground">Tekion index</dt>
          <dd className="max-w-28 truncate text-right" title={health?.graphrag?.version}>
            {health?.graphrag
              ? `${health.graphrag.version} · ${health.graphrag.status} · ${health.graphrag.document_count}`
              : 'not published'}
          </dd>
        </dl>
        <p aria-live="polite" className="text-[0.68rem] text-muted-foreground">
          {activeJob
            ? `Recovery maintenance ${activeJob.status}`
            : 'Session-end consolidation is automatic'}
        </p>
        {error ? <p className="text-[0.68rem] text-destructive">{error}</p> : null}
        <div className="flex gap-2">
          <Button className="h-7 flex-1 text-[0.68rem]" disabled={running} onClick={() => void run()} size="sm">
            {running ? 'Queueing…' : 'Run recovery'}
          </Button>
          <Button
            aria-label="Refresh Cortex health"
            className="h-7 px-2 text-[0.68rem]"
            onClick={() => void refreshCortexHealth()}
            size="sm"
            variant="outline"
          >
            Refresh
          </Button>
        </div>
      </div>
    </details>
  )
}
