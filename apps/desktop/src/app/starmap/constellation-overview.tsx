import { useEffect, useMemo, useRef, useState } from 'react'

import { WorkerAvatar } from '@/app/profiles/worker-avatar'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import { TILT } from './constants'
import type { ConstellationBrainStatus, ConstellationPoint, ConstellationScene } from './constellation'
import { CORTEX_NODE_TYPES } from './cortex'
import { TypeGlyph } from './cortex-workspace'
import { fitViewport } from './geometry'
import { StarMap } from './star-map'

interface ConstellationOverviewProps {
  onOpenBrain: (profile: string) => void
  scene: ConstellationScene
}

function statusLabel(
  status: ConstellationBrainStatus,
  nodeCount: number,
  labels: {
    cortexDisabled: string
    noMemoryYet: string
    nodes: (count: number) => string
    unavailable: string
  }
): string {
  if (status === 'disabled') {
    return labels.cortexDisabled
  }

  if (status === 'empty') {
    return labels.noMemoryYet
  }

  if (status === 'unavailable') {
    return labels.unavailable
  }

  return labels.nodes(nodeCount)
}

function project(point: ConstellationPoint, size: { h: number; w: number }, outerRadius: number): ConstellationPoint {
  const viewport = fitViewport(size.w, size.h, outerRadius, { contain: true, padding: 64 })

  return { x: viewport.x + point.x * viewport.k, y: viewport.y + point.y * viewport.k * TILT }
}

export function ConstellationOverview({ onOpenBrain, scene }: ConstellationOverviewProps) {
  const { t } = useI18n()
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const [size, setSize] = useState({ h: 0, w: 0 })
  const copy = t.starmap.cortex.constellation

  useEffect(() => {
    const element = wrapRef.current

    if (!element) {
      return
    }

    const sync = () => setSize({ h: element.clientHeight, w: element.clientWidth })
    const observer = new ResizeObserver(sync)
    observer.observe(element)
    sync()

    return () => observer.disconnect()
  }, [])

  const projected = useMemo(
    () =>
      scene.partitions.map(partition => ({
        anchor: project(partition.anchor, size, scene.outerRadius),
        center: project(partition.center, size, scene.outerRadius),
        partition,
        radius: fitViewport(size.w, size.h, scene.outerRadius, { contain: true, padding: 64 }).k * partition.radius
      })),
    [scene, size]
  )

  const simulationLayout = useMemo(
    () => ({ outerRadius: scene.outerRadius, partitions: scene.partitions }),
    [scene.outerRadius, scene.partitions]
  )

  const workerCount = Math.max(0, scene.partitions.length - 1)

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-[#050b14] text-[#e9f5ff] [--background:#050b14] [--foreground:#e9f5ff] [--theme-primary:#56c8ff] [--theme-secondary:#f5b85b]">
      <header className="relative z-30 flex shrink-0 items-start justify-between gap-6 border-b border-white/8 bg-[#07101c]/94 px-6 py-4 shadow-[0_16px_45px_rgba(0,0,0,0.28)] backdrop-blur-xl">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-[0.58rem] font-semibold uppercase tracking-[0.24em] text-[#66d3ff]">
            <span className="size-1.5 rounded-full bg-[#66d3ff] shadow-[0_0_14px_#56c8ff]" />
            {copy.eyebrow}
          </p>
          <h1
            className="mt-1 text-[1.55rem] font-semibold leading-none tracking-[-0.025em] text-white"
            style={{ fontFamily: 'Iowan Old Style, Palatino Linotype, Georgia, serif' }}
          >
            {copy.title}
          </h1>
          <p className="mt-1.5 max-w-2xl text-[0.7rem] leading-relaxed text-[#8298ad]">{copy.description}</p>
        </div>

        <div className="mr-9 flex shrink-0 items-center gap-5 pt-1">
          <dl className="flex items-center gap-4">
            <div className="border-l border-white/10 pl-4">
              <dt className="text-[0.52rem] uppercase tracking-[0.16em] text-[#647b91]">{copy.workers}</dt>
              <dd className="mt-0.5 font-mono text-sm tabular-nums text-[#dff4ff]">{workerCount}</dd>
            </div>
            <div className="border-l border-white/10 pl-4">
              <dt className="text-[0.52rem] uppercase tracking-[0.16em] text-[#647b91]">{copy.totalNodes}</dt>
              <dd className="mt-0.5 font-mono text-sm tabular-nums text-[#dff4ff]">
                {Number(scene.graph.stats.totalNodes ?? 0).toLocaleString()}
              </dd>
            </div>
          </dl>
          <div className="hidden max-w-72 rounded-lg border border-[#56c8ff]/12 bg-[#56c8ff]/[0.045] px-3 py-2 text-[0.58rem] leading-relaxed text-[#7690a7] xl:block">
            {copy.lodOverview}
          </div>
        </div>
      </header>

      <div className="relative min-h-0 flex-1 overflow-hidden" ref={wrapRef}>
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0"
          style={{
            backgroundImage:
              'radial-gradient(circle at 50% 48%, rgba(43,131,185,.18), transparent 24%), radial-gradient(circle at 14% 82%, rgba(38,177,178,.08), transparent 34%), radial-gradient(circle at 86% 15%, rgba(91,99,194,.08), transparent 31%), linear-gradient(rgba(92,158,200,.022) 1px, transparent 1px), linear-gradient(90deg, rgba(92,158,200,.022) 1px, transparent 1px)',
            backgroundSize: 'auto, auto, auto, 30px 30px, 30px 30px'
          }}
        />

        <StarMap ariaLabel={copy.ariaLabel} constellation={simulationLayout} graph={scene.graph} />

        {size.w > 0 && size.h > 0 ? (
          <div className="pointer-events-none absolute inset-0 z-20">
            <svg aria-hidden="true" className="absolute inset-0 size-full overflow-visible">
              <defs>
                <linearGradient id="constellation-ownership" x1="0" x2="1">
                  <stop offset="0" stopColor="#69d2ff" stopOpacity="0.68" />
                  <stop offset="1" stopColor="#69d2ff" stopOpacity="0.16" />
                </linearGradient>
              </defs>
              {projected.map(({ anchor, center, partition }) =>
                anchor.x === center.x && anchor.y === center.y ? null : (
                  <line
                    data-edge-kind="ownership"
                    data-profile={partition.profile.name}
                    key={partition.anchorId}
                    stroke="url(#constellation-ownership)"
                    strokeDasharray="2 5"
                    strokeLinecap="round"
                    strokeWidth="1"
                    x1={anchor.x}
                    x2={center.x}
                    y1={anchor.y}
                    y2={center.y}
                  />
                )
              )}
            </svg>

            {projected.map(({ center, partition, radius }) => (
              <div
                aria-hidden="true"
                className={cn(
                  'absolute rounded-[50%] border bg-[#0a1624]/10 shadow-[inset_0_0_45px_rgba(80,190,239,0.025)]',
                  partition.status === 'ready' && 'border-[#65cdf5]/10',
                  partition.status === 'empty' && 'border-dashed border-[#6d879d]/16',
                  partition.status === 'disabled' && 'border-dashed border-[#c18080]/18 opacity-55',
                  partition.status === 'unavailable' && 'border-dotted border-[#a08672]/18 opacity-50'
                )}
                data-brain-region={partition.regionId}
                key={partition.regionId}
                style={{
                  height: radius * 2 * TILT,
                  left: center.x,
                  top: center.y,
                  transform: 'translate(-50%, -50%)',
                  width: radius * 2
                }}
              />
            ))}

            {projected.map(({ anchor, partition }, index) => {
              const name = partition.profile.display_name.trim() || partition.profile.name
              const muted = partition.status !== 'ready'

              return (
                <button
                  aria-label={`${copy.openBrain(name)} — ${statusLabel(partition.status, partition.totalNodeCount, copy)}`}
                  className={cn(
                    'pointer-events-auto absolute flex -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-1.5 rounded-xl px-2 py-1.5 text-center outline-none transition duration-200 hover:scale-[1.04] focus-visible:ring-1 focus-visible:ring-[#78d8ff]',
                    muted && 'opacity-70 hover:opacity-100'
                  )}
                  data-brain-anchor={partition.anchorId}
                  key={partition.anchorId}
                  onClick={() => onOpenBrain(partition.profile.name)}
                  style={{ left: anchor.x, top: anchor.y }}
                  type="button"
                >
                  <span
                    className={cn(
                      'relative rounded-full border bg-[#091522] p-1 shadow-[0_0_0_5px_rgba(5,11,20,.8),0_0_28px_rgba(78,190,239,.18)]',
                      index === 0 ? 'border-[#7edbff]/55' : 'border-white/16',
                      partition.status === 'disabled' && 'border-[#d48787]/35 grayscale'
                    )}
                  >
                    <WorkerAvatar
                      className={cn('rounded-full', index === 0 ? 'size-12 text-base' : 'size-9 text-xs')}
                      profile={partition.profile}
                    />
                    {partition.status === 'disabled' ? (
                      <span className="absolute -right-1 -top-1 grid size-4 place-items-center rounded-full border border-[#f1a1a1]/25 bg-[#2a131a] text-[#f0a0a0]">
                        <Codicon name="circle-slash" size="0.55rem" />
                      </span>
                    ) : null}
                  </span>
                  <span className="max-w-32 truncate text-[0.68rem] font-medium text-[#dceeff]">{name}</span>
                  <span
                    className={cn(
                      'rounded-full border px-2 py-0.5 text-[0.5rem] uppercase tracking-[0.12em]',
                      partition.status === 'ready' && 'border-[#5ccbf4]/12 bg-[#5ccbf4]/6 text-[#7ecbe9]',
                      partition.status === 'empty' && 'border-white/8 bg-white/[0.025] text-[#71879b]',
                      partition.status === 'disabled' && 'border-[#d48787]/14 bg-[#d48787]/5 text-[#b27b7f]',
                      partition.status === 'unavailable' && 'border-[#c7a579]/12 bg-[#c7a579]/5 text-[#9f876d]'
                    )}
                  >
                    {statusLabel(partition.status, partition.totalNodeCount, copy)}
                  </span>
                </button>
              )
            })}
          </div>
        ) : null}

        <div className="pointer-events-none absolute bottom-3 left-4 z-30 flex flex-wrap items-center gap-2 rounded-lg border border-white/8 bg-[#07111d]/75 px-3 py-2 text-[0.55rem] text-[#72889d] shadow-lg backdrop-blur-md">
          {CORTEX_NODE_TYPES.map(type => (
            <span className="flex items-center gap-1.5" key={type}>
              <TypeGlyph type={type} /> {t.starmap.cortex.nodeTypes[type]}
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}
