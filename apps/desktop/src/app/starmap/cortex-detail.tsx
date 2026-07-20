import { useEffect, useRef, useState } from 'react'

import { PageLoader } from '@/components/page-loader'
import { Button } from '@/components/ui/button'
import { getCortexNode } from '@/hermes'
import type { CortexNodeDetailResponse } from '@/types/hermes'

import { useOnProfileSwitch } from '../hooks/use-on-profile-switch'

export function CortexDetail({
  brainProfile,
  immersive = false,
  nodeId,
  onClose
}: {
  brainProfile: string
  immersive?: boolean
  nodeId: string
  onClose: () => void
}) {
  const [detail, setDetail] = useState<CortexNodeDetailResponse | null>(null)
  const [error, setError] = useState<null | string>(null)
  const request = useRef(0)

  useOnProfileSwitch(() => {
    request.current += 1
    setDetail(null)
    setError(null)
    onClose()
  })

  useEffect(() => {
    const id = request.current + 1
    request.current = id
    setDetail(null)
    setError(null)

    void getCortexNode(nodeId, brainProfile).then(
      value => request.current === id && setDetail(value),
      reason => request.current === id && setError(reason instanceof Error ? reason.message : String(reason))
    )

    return () => {
      request.current += 1
    }
  }, [brainProfile, nodeId])

  return (
    <aside
      aria-label="Cortex node details"
      className={
        immersive
          ? 'relative z-20 flex min-h-0 flex-1 flex-col bg-[#08111d] text-[#dcecff]'
          : 'relative z-20 flex w-[22rem] shrink-0 flex-col border-l border-(--ui-stroke-secondary) bg-(--ui-bg-elevated)'
      }
    >
      <header
        className={`flex items-start justify-between gap-3 border-b px-4 py-3 ${immersive ? 'border-white/8' : 'border-(--ui-stroke-secondary)'}`}
      >
        <div className="min-w-0">
          <p
            className={`text-[0.6rem] font-semibold uppercase tracking-[0.18em] ${immersive ? 'text-[#56c8ff]' : 'text-muted-foreground'}`}
          >
            Cortex detail
          </p>
          <h2 className={`mt-1 truncate text-sm font-semibold ${immersive ? 'text-white' : ''}`}>
            {detail?.node.label ?? 'Loading…'}
          </h2>
        </div>
        <Button
          aria-label="Close node details"
          className={`h-7 px-2 ${immersive ? 'text-[#8198ae] hover:bg-white/5 hover:text-white' : ''}`}
          onClick={onClose}
          size="sm"
          variant="ghost"
        >
          Back
        </Button>
      </header>
      {error ? (
        <p className="p-4 text-xs text-destructive">{error}</p>
      ) : !detail ? (
        <PageLoader aria-label="Loading Cortex node details" className="min-h-0 flex-1" />
      ) : (
        <div
          className={`min-h-0 flex-1 space-y-5 overflow-y-auto p-4 text-xs ${immersive ? '[scrollbar-color:#20364a_transparent]' : ''}`}
        >
          <div aria-label="Node properties" className="flex flex-wrap gap-1.5">
            {[detail.node.type, detail.node.domain, detail.node.status, detail.node.privacy, ...detail.node.badges].map(
              (value, index) => (
                <span
                  className={`rounded-full border px-2 py-0.5 text-[0.66rem] ${immersive ? 'border-white/10 bg-white/[0.035] text-[#92a9be]' : 'border-(--ui-stroke-secondary)'}`}
                  key={`${value}-${index}`}
                >
                  {value}
                </span>
              )
            )}
          </div>
          {detail.node.summary ? (
            <section>
              <h3
                className={`mb-1 text-[0.68rem] font-semibold uppercase tracking-wide ${immersive ? 'text-[#6f879d]' : 'text-muted-foreground'}`}
              >
                Summary
              </h3>
              <p className={`whitespace-pre-wrap leading-relaxed ${immersive ? 'text-[#bacbdd]' : ''}`}>
                {detail.node.summary}
              </p>
            </section>
          ) : null}
          {detail.content ? (
            <section>
              <h3
                className={`mb-1 text-[0.68rem] font-semibold uppercase tracking-wide ${immersive ? 'text-[#6f879d]' : 'text-muted-foreground'}`}
              >
                Content
              </h3>
              <p
                className={`max-h-72 overflow-y-auto whitespace-pre-wrap rounded-lg p-3 leading-relaxed ${immersive ? 'border border-white/7 bg-[#0b1826] text-[#b8cadd]' : 'bg-(--ui-bg-secondary)'}`}
              >
                {detail.content}
              </p>
            </section>
          ) : null}
          <section>
            <h3
              className={`mb-2 text-[0.68rem] font-semibold uppercase tracking-wide ${immersive ? 'text-[#6f879d]' : 'text-muted-foreground'}`}
            >
              Connections ({detail.edges.length})
            </h3>
            {detail.edges.length ? (
              <ul className="space-y-1.5">
                {detail.edges.map(edge => {
                  const labelFor = (id: string) =>
                    id === detail.node.id
                      ? detail.node.label
                      : (detail.neighbors.find(node => node.id === id)?.label ?? id)

                  return (
                    <li
                      className={`rounded-lg px-2.5 py-2 ${immersive ? 'border border-white/7 bg-[#0b1826]' : 'bg-(--ui-bg-secondary)'}`}
                      key={edge.id}
                    >
                      <span className="font-medium">{labelFor(edge.source)}</span>
                      <span className={`px-1.5 ${immersive ? 'text-[#647b92]' : 'text-muted-foreground'}`}>
                        → {edge.type.replaceAll('_', ' ')} →
                      </span>
                      <span className="font-medium">{labelFor(edge.target)}</span>
                    </li>
                  )
                })}
              </ul>
            ) : (
              <p className={immersive ? 'text-[#647b92]' : 'text-muted-foreground'}>No typed connections yet.</p>
            )}
          </section>
          <section>
            <h3
              className={`mb-2 text-[0.68rem] font-semibold uppercase tracking-wide ${immersive ? 'text-[#6f879d]' : 'text-muted-foreground'}`}
            >
              Source evidence ({detail.evidence.length})
            </h3>
            {detail.evidence.length ? (
              <ul className="space-y-2">
                {detail.evidence.map(evidence => (
                  <li
                    className={`rounded-lg border p-2 ${immersive ? 'border-white/8 bg-white/[0.02]' : 'border-(--ui-stroke-secondary)'}`}
                    key={evidence.id}
                  >
                    <details>
                      <summary className="cursor-pointer font-medium">
                        {evidence.source_type.replaceAll('_', ' ')} · {new Date(evidence.occurred_at).toLocaleString()}
                      </summary>
                      <p
                        className={`mt-2 whitespace-pre-wrap ${immersive ? 'text-[#72889e]' : 'text-muted-foreground'}`}
                      >
                        {evidence.content}
                      </p>
                    </details>
                  </li>
                ))}
              </ul>
            ) : (
              <p className={immersive ? 'text-[#647b92]' : 'text-muted-foreground'}>
                No source body is attached to this node.
              </p>
            )}
          </section>
          <p className={`text-[0.66rem] ${immersive ? 'text-[#526a80]' : 'text-muted-foreground'}`}>
            {detail.edges.length} connections · {detail.redaction_summary.evidence_omitted} sources omitted by limit
          </p>
        </div>
      )}
    </aside>
  )
}
