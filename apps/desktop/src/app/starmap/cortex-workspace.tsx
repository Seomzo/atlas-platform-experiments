import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useState } from 'react'

import { Codicon } from '@/components/ui/codicon'
import { $cronJobs } from '@/store/cron'
import {
  $cortexDreamJob,
  $cortexHealth,
  $cortexStatusError,
  loadStarmapGraph,
  refreshCortexDreamStatus,
  refreshCortexHealth,
  runCortexDreamNow
} from '@/store/starmap'
import type { StarmapGraph, StarmapNode } from '@/types/hermes'

import { cortexNodeAriaLabel } from './cortex'
import { CortexDetail } from './cortex-detail'
import { domainColor } from './domain-color'
import { StarMap } from './star-map'
import { isCortexSystemJob } from './system-job'

interface CortexWorkspaceProps {
  graph: StarmapGraph
  onSelectNode: (id: null | string) => void
  selectedNodeId: null | string
}

function relativeTime(value: null | string | undefined, now: number, future = false): string {
  if (!value) {
    return 'Not recorded'
  }

  const timestamp = new Date(value).getTime()

  if (!Number.isFinite(timestamp)) {
    return value
  }

  const seconds = Math.round(Math.abs(timestamp - now) / 1000)

  const [amount, unit] =
    seconds < 60
      ? [seconds, 'sec']
      : seconds < 3_600
        ? [Math.round(seconds / 60), 'min']
        : seconds < 86_400
          ? [Math.round(seconds / 3_600), 'hr']
          : [Math.round(seconds / 86_400), 'day']

  return future ? `in ${amount} ${unit}${amount === 1 ? '' : 's'}` : `${amount} ${unit}${amount === 1 ? '' : 's'} ago`
}

function nodeSearchText(node: StarmapNode): string {
  return [node.label, node.summary, node.category, node.cortexType, node.state, ...(node.badges ?? [])]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()
}

function NodeGlyph({ node }: { node: StarmapNode }) {
  const color = node.cortexType === 'memory' ? '#f5b85b' : domainColor(node.category)

  const shape =
    node.cortexType === 'memory'
      ? 'rotate-45 rounded-[3px]'
      : node.cortexType === 'evidence'
        ? '[clip-path:polygon(50%_0,100%_100%,0_100%)]'
        : node.cortexType === 'document'
          ? 'rounded-[2px]'
          : node.cortexType === 'community' || node.cortexType === 'session'
            ? '[clip-path:polygon(25%_7%,75%_7%,100%_50%,75%_93%,25%_93%,0_50%)]'
            : 'rounded-full'

  return <span aria-hidden="true" className={`block size-2.5 shrink-0 ${shape}`} style={{ backgroundColor: color }} />
}

export function CortexWorkspace({ graph, onSelectNode, selectedNodeId }: CortexWorkspaceProps) {
  const health = useStore($cortexHealth)
  const dream = useStore($cortexDreamJob)
  const statusError = useStore($cortexStatusError)
  const cronJobs = useStore($cronJobs)
  const [query, setQuery] = useState('')
  const [domain, setDomain] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const interval = window.setInterval(() => setNow(Date.now()), 15_000)

    return () => window.clearInterval(interval)
  }, [])

  const activeJob = dream?.job

  useEffect(() => {
    if (!activeJob || !['queued', 'running'].includes(activeJob.status)) {
      return
    }

    const timeout = window.setTimeout(() => void refreshCortexDreamStatus(activeJob.id), 2_000)

    return () => window.clearTimeout(timeout)
  }, [activeJob])

  const domains = useMemo(() => {
    const counts = new Map<string, number>()

    for (const node of graph.nodes) {
      counts.set(node.category, (counts.get(node.category) ?? 0) + 1)
    }

    return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
  }, [graph.nodes])

  const matchingNodes = useMemo(() => {
    const needle = query.trim().toLowerCase()

    return graph.nodes.filter(
      node => (!domain || node.category === domain) && (!needle || nodeSearchText(node).includes(needle))
    )
  }, [domain, graph.nodes, query])

  const visibleGraph = useMemo<StarmapGraph>(() => {
    if (!domain && !query.trim()) {
      return graph
    }

    const visibleIds = new Set(matchingNodes.map(node => node.id))

    return {
      ...graph,
      nodes: matchingNodes,
      edges: graph.edges.filter(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target)),
      clusters: domains
        .filter(([category]) => !domain || category === domain)
        .map(([category, count]) => ({ category, count }))
    }
  }, [domain, domains, graph, matchingNodes, query])

  useEffect(() => {
    if (selectedNodeId && !visibleGraph.nodes.some(node => node.id === selectedNodeId)) {
      onSelectNode(null)
    }
  }, [onSelectNode, selectedNodeId, visibleGraph.nodes])

  const systemJob = cronJobs.find(isCortexSystemJob)
  const memoryCount = health?.counts.memory_records ?? graph.nodes.filter(node => node.cortexType === 'memory').length

  const evidenceCount =
    health?.counts.evidence_items ?? graph.nodes.filter(node => node.cortexType === 'evidence').length

  const entityCount = health?.counts.entities ?? graph.nodes.filter(node => node.cortexType === 'entity').length
  const status = health?.status ?? 'checking'

  const runRecovery = async () => {
    setRunning(true)

    try {
      await runCortexDreamNow()
    } finally {
      setRunning(false)
    }
  }

  const refresh = () => void Promise.all([refreshCortexHealth(), loadStarmapGraph(true)])

  return (
    <div
      className="flex min-h-0 flex-1 flex-col overflow-hidden bg-[#050b14] text-[#e9f5ff] [--background:#07111f] [--chrome-action-hover:rgba(255,255,255,0.08)] [--foreground:#e9f5ff] [--theme-primary:#56c8ff] [--theme-secondary:#f5b85b] [--ui-text-tertiary:#91a5bb]"
      style={{ fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif' }}
    >
      <header className="relative shrink-0 border-b border-white/8 bg-[#07101c]/95 px-5 pb-3 pt-5 shadow-[0_16px_45px_rgba(0,0,0,0.28)] backdrop-blur-xl sm:px-7">
        <div className="flex items-center gap-5 pr-10">
          <div className="min-w-0 flex-1">
            <div className="mb-1 flex items-center gap-2.5 text-[0.62rem] font-semibold uppercase tracking-[0.24em] text-[#56c8ff]">
              <span className="relative flex size-2">
                <span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-400 opacity-50" />
                <span className="relative inline-flex size-2 rounded-full bg-emerald-400" />
              </span>
              Atlas Cortex · live memory
            </div>
            <h1
              className="truncate text-[1.65rem] font-semibold leading-none tracking-[-0.025em] text-white"
              style={{ fontFamily: 'Iowan Old Style, Palatino Linotype, Georgia, serif' }}
            >
              Memory Graph
            </h1>
            <p className="mt-1.5 truncate text-[0.72rem] text-[#8499af]">
              A living map of what Atlas knows, where it came from, and how ideas connect.
            </p>
          </div>

          <dl className="hidden shrink-0 items-center gap-5 xl:flex">
            {[
              ['Nodes', graph.nodes.length],
              ['Memories', memoryCount],
              ['Evidence', evidenceCount]
            ].map(([label, value]) => (
              <div className="min-w-16 border-l border-white/10 pl-4" key={label}>
                <dt className="text-[0.56rem] uppercase tracking-[0.16em] text-[#70879f]">{label}</dt>
                <dd className="mt-0.5 font-mono text-base tabular-nums text-[#dff4ff]">{value}</dd>
              </div>
            ))}
          </dl>

          <label className="group flex h-9 w-56 shrink-0 items-center gap-2 rounded-lg border border-white/10 bg-white/[0.045] px-3 transition focus-within:border-[#56c8ff]/55 focus-within:bg-white/[0.065] xl:w-64">
            <Codicon className="text-[#6f879f] group-focus-within:text-[#56c8ff]" name="search" size="0.85rem" />
            <input
              aria-label="Search memory graph"
              className="min-w-0 flex-1 bg-transparent text-xs text-white outline-none placeholder:text-[#60758b]"
              onChange={event => setQuery(event.target.value)}
              placeholder="Search memories, people, work…"
              value={query}
            />
            {query ? (
              <button
                aria-label="Clear graph search"
                className="text-[#71869c] hover:text-white"
                onClick={() => setQuery('')}
                type="button"
              >
                <Codicon name="close" size="0.75rem" />
              </button>
            ) : null}
          </label>

          <div className="hidden shrink-0 items-center gap-2 rounded-full border border-emerald-300/15 bg-emerald-400/[0.07] px-3 py-1.5 text-[0.65rem] text-emerald-200 lg:flex">
            <Codicon name="lock" size="0.7rem" /> Private
            <span className="text-emerald-300/45">·</span>
            <span className="capitalize">{status}</span>
          </div>
        </div>

        <nav
          aria-label="Filter memory graph by knowledge domain"
          className="mt-4 flex items-center gap-1.5 overflow-x-auto pr-8 [scrollbar-width:none]"
        >
          <button
            aria-pressed={domain === null}
            className="shrink-0 rounded-full border border-white/10 px-3 py-1 text-[0.64rem] text-[#8ea4bb] transition hover:border-white/20 hover:text-white aria-pressed:border-[#56c8ff]/40 aria-pressed:bg-[#56c8ff]/10 aria-pressed:text-[#bdeaff]"
            onClick={() => setDomain(null)}
            type="button"
          >
            All domains <span className="ml-1 font-mono text-[0.58rem] opacity-60">{graph.nodes.length}</span>
          </button>
          {domains.map(([name, count]) => (
            <button
              aria-pressed={domain === name}
              className="flex shrink-0 items-center gap-1.5 rounded-full border border-white/8 bg-white/[0.025] px-3 py-1 text-[0.64rem] text-[#849ab1] transition hover:border-white/20 hover:text-white aria-pressed:border-white/20 aria-pressed:bg-white/[0.08] aria-pressed:text-white"
              key={name}
              onClick={() => setDomain(current => (current === name ? null : name))}
              type="button"
            >
              <span className="size-1.5 rounded-full" style={{ backgroundColor: domainColor(name) }} />
              {name.replaceAll('_', ' ')}
              <span className="font-mono text-[0.56rem] opacity-55">{count}</span>
            </button>
          ))}
        </nav>
      </header>

      <div className="flex min-h-0 flex-1">
        <main className="relative flex min-w-0 flex-1 overflow-hidden bg-[#050b14]">
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-0 opacity-80"
            style={{
              backgroundImage:
                'radial-gradient(circle at 47% 45%, rgba(28,100,147,.20), transparent 35%), radial-gradient(circle at 15% 100%, rgba(28,183,176,.08), transparent 42%), linear-gradient(rgba(98,164,206,.025) 1px, transparent 1px), linear-gradient(90deg, rgba(98,164,206,.025) 1px, transparent 1px)',
              backgroundSize: 'auto, auto, 28px 28px, 28px 28px'
            }}
          />
          {visibleGraph.nodes.length ? (
            <StarMap graph={visibleGraph} onNodeSelect={onSelectNode} selectedNodeId={selectedNodeId} />
          ) : (
            <div className="relative z-10 m-auto max-w-sm text-center">
              <div className="mx-auto mb-4 grid size-14 place-items-center rounded-full border border-[#56c8ff]/20 bg-[#56c8ff]/8 text-[#56c8ff]">
                <Codicon name="search" size="1.25rem" />
              </div>
              <h2 className="text-sm font-medium text-white">No matching memories</h2>
              <p className="mt-1 text-xs leading-relaxed text-[#71879e]">
                Try another phrase or return to all knowledge domains.
              </p>
              <button
                className="mt-4 rounded-md border border-white/12 px-3 py-1.5 text-xs text-[#aac0d5] hover:bg-white/5 hover:text-white"
                onClick={() => {
                  setQuery('')
                  setDomain(null)
                }}
                type="button"
              >
                Clear filters
              </button>
            </div>
          )}
          {(query || domain) && visibleGraph.nodes.length ? (
            <div className="pointer-events-none absolute left-4 top-4 z-20 rounded-full border border-[#56c8ff]/15 bg-[#07121f]/85 px-3 py-1.5 text-[0.63rem] text-[#8ea5bc] shadow-lg backdrop-blur-md">
              Showing <span className="font-mono text-[#d9f3ff]">{visibleGraph.nodes.length}</span> of{' '}
              {graph.nodes.length} nodes
            </div>
          ) : null}
        </main>

        <aside className="relative z-20 flex w-[18.5rem] shrink-0 flex-col border-l border-white/8 bg-[#08111d]/96 shadow-[-18px_0_50px_rgba(0,0,0,0.22)] min-[1180px]:w-[20rem]">
          {selectedNodeId ? (
            <CortexDetail immersive nodeId={selectedNodeId} onClose={() => onSelectNode(null)} />
          ) : (
            <>
              <div className="border-b border-white/8 px-4 pb-4 pt-4">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="text-[0.55rem] font-semibold uppercase tracking-[0.2em] text-[#56c8ff]">
                      System telemetry
                    </p>
                    <h2 className="mt-1 text-sm font-semibold text-white">Cortex Activity</h2>
                  </div>
                  <button
                    aria-label="Refresh Cortex activity"
                    className="grid size-7 place-items-center rounded-md border border-white/8 text-[#71889f] transition hover:bg-white/5 hover:text-white"
                    onClick={refresh}
                    type="button"
                  >
                    <Codicon name="refresh" size="0.78rem" />
                  </button>
                </div>

                <div className="mt-4 overflow-hidden rounded-xl border border-white/8 bg-[#0b1725]">
                  <div className="flex items-start gap-3 border-b border-white/7 px-3 py-3">
                    <div className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg bg-[#56c8ff]/10 text-[#65ccff]">
                      <Codicon name="pulse" size="0.8rem" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-[0.7rem] font-medium text-[#dceeff]">Maintenance heartbeat</p>
                        <span className="flex items-center gap-1 text-[0.57rem] text-emerald-300">
                          <span className="size-1.5 rounded-full bg-emerald-400" /> active
                        </span>
                      </div>
                      <p className="mt-1 text-[0.62rem] leading-relaxed text-[#73899f]">
                        Keeps queues healthy and recovers missed work. It does not invent memories.
                      </p>
                      <dl className="mt-2.5 grid grid-cols-2 gap-2">
                        <div>
                          <dt className="text-[0.52rem] uppercase tracking-wide text-[#5f758b]">Last check</dt>
                          <dd className="mt-0.5 font-mono text-[0.62rem] text-[#9eb4c9]">
                            {relativeTime(systemJob?.last_run_at, now)}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-[0.52rem] uppercase tracking-wide text-[#5f758b]">Next check</dt>
                          <dd className="mt-0.5 font-mono text-[0.62rem] text-[#9eb4c9]">
                            {relativeTime(systemJob?.next_run_at, now, true)}
                          </dd>
                        </div>
                      </dl>
                    </div>
                  </div>
                  <div className="flex items-start gap-3 px-3 py-3">
                    <div className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg bg-[#f5b85b]/10 text-[#f5b85b]">
                      <Codicon name="sparkle" size="0.8rem" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-[0.7rem] font-medium text-[#dceeff]">Session consolidation</p>
                      <p className="mt-1 text-[0.62rem] leading-relaxed text-[#73899f]">
                        At session boundaries, Cortex distills durable memory and evidence from the conversation.
                      </p>
                      <p className="mt-2 text-[0.58rem] text-[#60768d]">
                        {activeJob ? `Latest recovery: ${activeJob.status}` : 'Automatic · private · source-linked'}
                      </p>
                    </div>
                  </div>
                </div>

                <div className="mt-3 grid grid-cols-3 gap-1.5">
                  {[
                    ['Entities', entityCount],
                    ['Queued', health?.jobs.pending ?? 0],
                    ['Review', health?.quality.observations_awaiting_maintenance ?? 0]
                  ].map(([label, value]) => (
                    <div className="rounded-lg border border-white/7 bg-white/[0.025] px-2 py-2" key={label}>
                      <p className="font-mono text-sm text-[#d9efff]">{value}</p>
                      <p className="mt-0.5 text-[0.52rem] uppercase tracking-wide text-[#62788e]">{label}</p>
                    </div>
                  ))}
                </div>
                {statusError ? (
                  <p className="mt-2 text-[0.6rem] leading-relaxed text-[#ff938a]">{statusError}</p>
                ) : null}
                <button
                  className="mt-3 w-full rounded-lg border border-[#56c8ff]/16 bg-[#56c8ff]/[0.055] px-3 py-2 text-[0.65rem] font-medium text-[#9edfff] transition hover:border-[#56c8ff]/30 hover:bg-[#56c8ff]/10 disabled:opacity-50"
                  disabled={running}
                  onClick={() => void runRecovery()}
                  type="button"
                >
                  {running ? 'Queueing recovery…' : 'Run recovery maintenance'}
                </button>
              </div>

              <div className="flex min-h-0 flex-1 flex-col">
                <div className="flex items-center justify-between px-4 pb-2 pt-4">
                  <h3 className="text-[0.58rem] font-semibold uppercase tracking-[0.18em] text-[#71879d]">
                    {query ? 'Search results' : domain ? `${domain.replaceAll('_', ' ')} nodes` : 'Explore the graph'}
                  </h3>
                  <span className="font-mono text-[0.56rem] text-[#526980]">{matchingNodes.length}</span>
                </div>
                <ul
                  aria-label="Atlas Cortex graph nodes"
                  className="min-h-0 flex-1 overflow-y-auto px-2 pb-3 [scrollbar-color:#20364a_transparent]"
                >
                  {matchingNodes
                    .slice()
                    .sort((a, b) => b.useCount - a.useCount || a.label.localeCompare(b.label))
                    .slice(0, 60)
                    .map(node => (
                      <li key={node.id}>
                        <button
                          aria-label={cortexNodeAriaLabel(node)}
                          className="group flex w-full items-start gap-2.5 rounded-lg px-2 py-2 text-left transition hover:bg-white/[0.045] focus-visible:outline focus-visible:outline-1 focus-visible:outline-[#56c8ff]/60"
                          onClick={() => onSelectNode(node.id)}
                          type="button"
                        >
                          <span className="mt-1.5 grid size-5 shrink-0 place-items-center rounded-md border border-white/7 bg-white/[0.025]">
                            <NodeGlyph node={node} />
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-[0.69rem] font-medium text-[#c9dbea] transition group-hover:text-white">
                              {node.label}
                            </span>
                            <span className="mt-0.5 block truncate text-[0.57rem] text-[#62788e]">
                              {node.cortexType} · {node.category.replaceAll('_', ' ')}
                            </span>
                            {node.summary ? (
                              <span className="mt-1 line-clamp-2 block text-[0.58rem] leading-relaxed text-[#6f8499]">
                                {node.summary}
                              </span>
                            ) : null}
                          </span>
                          <Codicon
                            className="mt-1 text-[#41586e] opacity-0 transition group-hover:opacity-100"
                            name="chevron-right"
                            size="0.65rem"
                          />
                        </button>
                      </li>
                    ))}
                </ul>
              </div>
            </>
          )}
        </aside>
      </div>
    </div>
  )
}
