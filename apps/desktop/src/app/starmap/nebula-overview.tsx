import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useState } from 'react'

import { WorkerAvatar } from '@/app/profiles/worker-avatar'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { $starmapBrainFilter, selectStarmapBrainFilter } from '@/store/starmap'
import type { CortexNodeType, StarmapGraph } from '@/types/hermes'

import { CORTEX_NODE_TYPES } from './cortex'
import { CortexDetail } from './cortex-detail'
import { TypeGlyph } from './cortex-workspace'
import type { NebulaBrain, NebulaBrainStatus, NebulaScene } from './nebula'
import { nativeNebulaNodeId } from './nebula'
import { StarMap } from './star-map'

interface BrainFilterChipsProps {
  brains: NebulaBrain[]
  onFilter: (profile: null | string) => void
  onIsolate: (profile: string) => void
  selectedProfile: null | string
}

function chipStatus(status: NebulaBrainStatus, nodeCount: number, labels: ReturnType<typeof useI18n>['t']): string {
  const copy = labels.starmap.cortex.nebula

  if (status === 'disabled') {
    return copy.cortexDisabled
  }

  if (status === 'empty') {
    return copy.noMemoryYet
  }

  if (status === 'unavailable') {
    return copy.unavailable
  }

  return copy.nodes(nodeCount)
}

export function BrainFilterChips({ brains, onFilter, onIsolate, selectedProfile }: BrainFilterChipsProps) {
  const { t } = useI18n()
  const copy = t.starmap.cortex.nebula

  return (
    <nav
      aria-label={copy.filterByBrain}
      className="pointer-events-auto flex max-w-[min(72vw,56rem)] items-center justify-end gap-2 overflow-x-auto rounded-2xl bg-[#050b14]/35 p-1 [scrollbar-width:none]"
    >
      {brains.map((brain, index) => {
        const wholeSky = index === 0
        const selected = wholeSky ? selectedProfile === null : selectedProfile === brain.profile.name
        const name = brain.profile.display_name.trim() || brain.profile.name
        const empty = brain.status === 'empty'

        return (
          <div
            className={cn(
              'flex shrink-0 items-center rounded-full border bg-[#0b1626]/90 shadow-[0_8px_28px_rgba(0,0,0,.22)] backdrop-blur-md transition',
              selected
                ? 'border-[#5b8def]/75 shadow-[0_0_22px_rgba(91,141,239,.22)]'
                : 'border-[#263957]/80 hover:border-[#5b8def]/65',
              brain.status === 'disabled' && 'opacity-55 grayscale',
              brain.status === 'unavailable' && 'opacity-60',
              empty && 'border-dashed opacity-75'
            )}
            data-brain-chip={brain.profile.name}
            data-no-memory={empty || undefined}
            key={brain.profile.name}
          >
            <button
              aria-label={wholeSky ? copy.showWholeSky : copy.filterToBrain(name)}
              aria-pressed={selected}
              className="flex items-center gap-2 rounded-l-full py-1.5 pl-1.5 pr-2.5 text-left outline-none focus-visible:ring-1 focus-visible:ring-[#7ea9ff]"
              onClick={() => onFilter(wholeSky ? null : brain.profile.name)}
              onDoubleClick={() => onIsolate(brain.profile.name)}
              type="button"
            >
              <WorkerAvatar className="size-7 rounded-full text-[0.65rem]" profile={brain.profile} />
              <span className="min-w-0">
                <span className="block max-w-28 truncate text-[0.68rem] font-medium text-[#dce9fb]">{name}</span>
                <span
                  className={cn(
                    'block max-w-28 truncate text-[0.5rem] leading-tight text-[#667c9e]',
                    empty && 'text-[#718198]'
                  )}
                >
                  {wholeSky ? copy.wholeSky : chipStatus(brain.status, brain.totalNodeCount, t)}
                </span>
              </span>
            </button>
            <button
              aria-label={copy.isolateBrain(name)}
              className="mr-1.5 grid size-6 place-items-center rounded-full border border-white/7 text-[#5f7596] transition hover:border-[#5b8def]/40 hover:bg-[#5b8def]/10 hover:text-[#a9c4ff] focus-visible:outline focus-visible:ring-1 focus-visible:ring-[#7ea9ff]"
              onClick={() => onIsolate(brain.profile.name)}
              title={copy.isolateBrain(name)}
              type="button"
            >
              <Codicon name="go-to-file" size="0.65rem" />
            </button>
          </div>
        )
      })}
    </nav>
  )
}

interface NebulaOverviewProps {
  onIsolateBrain: (profile: string) => void
  scene: NebulaScene
}

export function NebulaOverview({ onIsolateBrain, scene }: NebulaOverviewProps) {
  const { t } = useI18n()
  const brainFilter = useStore($starmapBrainFilter)
  const [hiddenTypes, setHiddenTypes] = useState<Set<CortexNodeType>>(() => new Set())
  const [selectedNodeId, setSelectedNodeId] = useState<null | string>(null)
  const copy = t.starmap.cortex.nebula

  const visibleGraph = useMemo<StarmapGraph>(() => {
    if (!hiddenTypes.size) {
      return scene.graph
    }

    const nodes = scene.graph.nodes.filter(node => !node.cortexType || !hiddenTypes.has(node.cortexType))
    const ids = new Set(nodes.map(node => node.id))

    return {
      ...scene.graph,
      edges: scene.graph.edges.filter(edge => ids.has(edge.source) && ids.has(edge.target)),
      nodes
    }
  }, [hiddenTypes, scene.graph])

  const selectedNode = useMemo(
    () => scene.graph.nodes.find(node => node.id === selectedNodeId && !node.aggregate) ?? null,
    [scene.graph.nodes, selectedNodeId]
  )

  const brainLabels = useMemo(
    () =>
      new Map(
        scene.brains.map(brain => {
          const name = brain.profile.display_name.trim() || brain.profile.name

          return [brain.profile.name, copy.ownerBrain(name)]
        })
      ),
    [copy, scene.brains]
  )

  useEffect(() => {
    if (
      selectedNode &&
      ((brainFilter && selectedNode.brainProfile !== brainFilter) ||
        (selectedNode.cortexType && hiddenTypes.has(selectedNode.cortexType)))
    ) {
      setSelectedNodeId(null)
    }
  }, [brainFilter, hiddenTypes, selectedNode])

  const toggleType = (type: CortexNodeType) => {
    setHiddenTypes(current => {
      const next = new Set(current)

      if (next.has(type)) {
        next.delete(type)
      } else {
        next.add(type)
      }

      return next
    })
  }

  const workerCount = Math.max(0, scene.brains.length - 1)

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-[#050b14] text-[#e9f5ff] [--background:#050b14] [--foreground:#e9f5ff] [--theme-primary:#56c8ff] [--theme-secondary:#f5b85b]">
      <header className="relative z-30 flex shrink-0 items-start justify-between gap-6 border-b border-white/8 bg-[#07101c]/92 px-6 py-4 shadow-[0_16px_45px_rgba(0,0,0,0.24)] backdrop-blur-xl">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-[0.58rem] font-semibold uppercase tracking-[0.24em] text-[#5b8def]">
            <span className="size-1.5 rounded-full bg-[#5b8def] shadow-[0_0_14px_#5b8def]" />
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

        <dl className="mr-9 flex shrink-0 items-center gap-4 pt-1">
          <div className="border-l border-white/10 pl-4">
            <dt className="text-[0.52rem] uppercase tracking-[0.16em] text-[#647b91]">{copy.totalNodes}</dt>
            <dd className="mt-0.5 font-mono text-sm tabular-nums text-[#dff4ff]">
              {Number(scene.graph.stats.totalNodes ?? 0).toLocaleString()}
            </dd>
          </div>
          <div className="border-l border-white/10 pl-4">
            <dt className="text-[0.52rem] uppercase tracking-[0.16em] text-[#647b91]">{copy.workers}</dt>
            <dd className="mt-0.5 font-mono text-sm tabular-nums text-[#dff4ff]">{workerCount}</dd>
          </div>
        </dl>
      </header>

      <div className="flex min-h-0 flex-1">
        <main className="relative min-w-0 flex-1 overflow-hidden bg-[#050b14]">
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-0"
            style={{
              backgroundImage:
                'radial-gradient(circle at 48% 44%, rgba(43,94,166,.17), transparent 28%), radial-gradient(circle at 13% 84%, rgba(38,177,178,.07), transparent 32%), radial-gradient(circle at 87% 18%, rgba(116,78,184,.07), transparent 31%)'
            }}
          />

          {visibleGraph.nodes.length ? (
            <StarMap
              ariaLabel={copy.ariaLabel}
              brainFilter={brainFilter}
              brainLabels={brainLabels}
              graph={visibleGraph}
              inspectHint={copy.clickToInspect}
              nebula
              onNodeSelect={id => {
                const node = id ? scene.graph.nodes.find(item => item.id === id) : null
                setSelectedNodeId(node?.aggregate ? null : (node?.id ?? null))
              }}
              selectedNodeId={selectedNodeId}
            />
          ) : (
            <div className="absolute inset-0 grid place-items-center text-center">
              <div>
                <p className="text-sm font-medium text-[#dbe8f8]">{copy.emptySkyTitle}</p>
                <p className="mt-1 text-xs text-[#667c96]">{copy.emptySkyDescription}</p>
              </div>
            </div>
          )}

          <nav
            aria-label={t.starmap.cortex.typeFilterLabel}
            className="pointer-events-auto absolute bottom-4 left-4 z-30 flex max-w-[calc(100%-2rem)] flex-wrap items-center gap-1.5 rounded-xl border border-white/8 bg-[#07111d]/78 px-2.5 py-2 shadow-lg backdrop-blur-md"
          >
            {CORTEX_NODE_TYPES.map(type => {
              const visible = !hiddenTypes.has(type)

              return (
                <button
                  aria-label={t.starmap.cortex.nodeTypes[type]}
                  aria-pressed={visible}
                  className="flex items-center gap-1.5 rounded-full px-2 py-1 text-[0.58rem] text-[#647a94] opacity-45 transition hover:bg-white/5 hover:text-white aria-pressed:text-[#a9bfd8] aria-pressed:opacity-100"
                  key={type}
                  onClick={() => toggleType(type)}
                  type="button"
                >
                  <TypeGlyph type={type} />
                  {t.starmap.cortex.nodeTypes[type]}
                </button>
              )
            })}
          </nav>

          <div className="absolute bottom-4 right-4 z-30">
            <BrainFilterChips
              brains={scene.brains}
              onFilter={selectStarmapBrainFilter}
              onIsolate={onIsolateBrain}
              selectedProfile={brainFilter}
            />
          </div>
        </main>

        {selectedNode?.brainProfile ? (
          <div className="relative z-20 flex w-[20rem] shrink-0 flex-col border-l border-white/8 bg-[#08111d]/96 shadow-[-18px_0_50px_rgba(0,0,0,0.22)]">
            <CortexDetail
              brainProfile={selectedNode.brainProfile}
              immersive
              nodeId={nativeNebulaNodeId(selectedNode)}
              onClose={() => setSelectedNodeId(null)}
            />
          </div>
        ) : null}
      </div>
    </div>
  )
}
