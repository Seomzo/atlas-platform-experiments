import type {
  CortexGraphResponse,
  CortexNodeType,
  StarmapAggregate,
  StarmapEdge,
  StarmapGraph,
  StarmapNode
} from '@/types/hermes'

import { cortexToStarmap } from './cortex'

export const LOD_COMMUNITY_PAGE_SIZE = 24
export const LOD_DETAIL_PAGE_SIZE = 160
export const LOD_DETAIL_PAGES_PER_RESOLVE = 2

export type LodTier = 'aggregate' | 'detail'

export function lodTierForZoom(zoom: number, fitZoom: number): LodTier {
  return zoom / Math.max(fitZoom, Number.EPSILON) >= 1.8 - 1e-6 ? 'detail' : 'aggregate'
}

export function aggregateStarRadius(count: number, kind: StarmapAggregate['kind']): number {
  const base = kind === 'community' ? 7.2 : 6.2

  return base + Math.log10(Math.max(0, count) + 1) * 2.1
}

export function brainRegionRadius(totalNodes: number, main: boolean): number {
  return (main ? 118 : 72) + Math.sqrt(Math.max(0, totalNodes)) * (main ? 0.62 : 0.5)
}

function aggregateNode(
  aggregate: StarmapAggregate,
  options: Pick<StarmapNode, 'category' | 'id' | 'label' | 'timestamp'> & { cortexType: CortexNodeType }
): StarmapNode {
  return {
    ...options,
    aggregate,
    createdBy: null,
    kind: options.cortexType,
    pinned: false,
    state: 'aggregate',
    summary: `${aggregate.count} attested nodes`,
    useCount: aggregate.count
  }
}

/** Build the bounded aggregate sky. Every returned star is traceable to one
 * exact count in the backend response; no sampled detail nodes enter this tier. */
export function cortexAggregatesToStarmap(response: CortexGraphResponse): StarmapGraph {
  const communityNodes = new Map(response.nodes.map(node => [node.id, node]))

  const nodes: StarmapNode[] = response.communities
    .filter(community => community.member_count > 0)
    .map(community => {
      const source = communityNodes.get(community.id)
      const updatedAt = source?.updated_at ? Date.parse(source.updated_at) : Number.NaN

      const aggregate: StarmapAggregate = {
        count: community.member_count,
        key: `community:${community.id}`,
        kind: 'community',
        sourceId: community.id
      }

      return aggregateNode(aggregate, {
        category: community.domain,
        cortexType: 'community',
        id: `aggregate:${aggregate.key}`,
        label: community.label,
        timestamp: Number.isFinite(updatedAt) ? Math.floor(updatedAt / 1000) : null
      })
    })

  for (const item of response.aggregates.types) {
    if (item.type === 'community' || item.uncommunitied_count <= 0) {
      continue
    }

    const aggregate: StarmapAggregate = {
      count: item.uncommunitied_count,
      key: `type:${item.type}`,
      kind: 'type',
      type: item.type
    }

    nodes.push(
      aggregateNode(aggregate, {
        category: 'uncommunitied',
        cortexType: item.type,
        id: `aggregate:${aggregate.key}`,
        label: item.type,
        timestamp: null
      })
    )
  }

  return {
    clusters: response.aggregates.types.map(item => ({ category: item.type, count: item.count })),
    edges: [],
    memory: [],
    nodes,
    source: 'cortex',
    stats: {
      aggregateTier: true,
      contract: response.version,
      generatedAt: response.generated_at,
      relationCount: response.aggregates.relation_count,
      totalNodes: response.aggregates.total_nodes
    }
  }
}

export function hasOnlyAttestedAggregates(graph: StarmapGraph, response: CortexGraphResponse): boolean {
  const communityCounts = new Map(response.communities.map(item => [`community:${item.id}`, item.member_count]))
  const typeCounts = new Map(response.aggregates.types.map(item => [`type:${item.type}`, item.uncommunitied_count]))

  return graph.nodes.every(node => {
    if (!node.aggregate) {
      return false
    }

    const count =
      node.aggregate.kind === 'community' ? communityCounts.get(node.aggregate.key) : typeCounts.get(node.aggregate.key)

    return count === node.aggregate.count
  })
}

export interface CortexRegionPageRequest {
  brainProfile: string
  cursor?: string
  region: StarmapAggregate
}

export type CortexRegionPageFetcher = (request: CortexRegionPageRequest) => Promise<CortexGraphResponse>

export interface ResolvedCortexRegion {
  complete: boolean
  graph: StarmapGraph
  nextCursor: null | string
  pages: number
  region: StarmapAggregate
}

function cacheKey(brainProfile: string, regionKey: string): string {
  return `${brainProfile}\u0000${regionKey}`
}

export class ProgressiveCortexResolver {
  private readonly cache = new Map<string, ResolvedCortexRegion>()
  private readonly inflight = new Map<string, Promise<ResolvedCortexRegion>>()

  constructor(private readonly fetchPage: CortexRegionPageFetcher) {}

  get(brainProfile: string, regionKey: string): ResolvedCortexRegion | undefined {
    return this.cache.get(cacheKey(brainProfile, regionKey))
  }

  resolve(
    brainProfile: string,
    region: StarmapAggregate,
    maxPages = LOD_DETAIL_PAGES_PER_RESOLVE
  ): Promise<ResolvedCortexRegion> {
    const key = cacheKey(brainProfile, region.key)
    const existingInflight = this.inflight.get(key)

    if (existingInflight) {
      return existingInflight
    }

    const cached = this.cache.get(key)

    if (cached?.complete || maxPages <= 0) {
      return Promise.resolve(cached ?? this.empty(region))
    }

    const task = this.walk(brainProfile, cached ?? this.empty(region), maxPages).finally(() => {
      this.inflight.delete(key)
    })

    this.inflight.set(key, task)

    return task
  }

  clear(): void {
    this.cache.clear()
    this.inflight.clear()
  }

  private empty(region: StarmapAggregate): ResolvedCortexRegion {
    return {
      complete: false,
      graph: { clusters: [], edges: [], memory: [], nodes: [], source: 'cortex', stats: {} },
      nextCursor: null,
      pages: 0,
      region
    }
  }

  private async walk(
    brainProfile: string,
    initial: ResolvedCortexRegion,
    maxPages: number
  ): Promise<ResolvedCortexRegion> {
    const nodeById = new Map(initial.graph.nodes.map(node => [node.id, node]))
    const edgeById = new Map<string, StarmapEdge>()

    for (const edge of initial.graph.edges) {
      edgeById.set(edge.id ?? `${edge.source}->${edge.target}:${edge.type ?? ''}`, edge)
    }

    let cursor = initial.pages > 0 ? initial.nextCursor : undefined
    let pages = initial.pages
    let walked = 0

    while (walked < maxPages && (pages === 0 || cursor)) {
      const response = await this.fetchPage({ brainProfile, cursor: cursor ?? undefined, region: initial.region })
      const page = cortexToStarmap(response)

      for (const node of page.nodes) {
        nodeById.set(node.id, node)
      }

      for (const edge of page.edges) {
        edgeById.set(edge.id ?? `${edge.source}->${edge.target}:${edge.type ?? ''}`, edge)
      }

      cursor = response.next_cursor ?? undefined
      pages += 1
      walked += 1
    }

    const nodeIds = new Set(nodeById.keys())

    const result: ResolvedCortexRegion = {
      complete: !cursor,
      graph: {
        clusters: [],
        edges: [...edgeById.values()].filter(edge => nodeIds.has(edge.source) && nodeIds.has(edge.target)),
        memory: [],
        nodes: [...nodeById.values()],
        source: 'cortex',
        stats: { pages }
      },
      nextCursor: cursor ?? null,
      pages,
      region: initial.region
    }

    this.cache.set(cacheKey(brainProfile, initial.region.key), result)

    return result
  }
}

export function mergeResolvedRegion(base: StarmapGraph, resolved: ResolvedCortexRegion): StarmapGraph {
  const loaded = resolved.graph.nodes.length
  const remaining = Math.max(0, resolved.region.count - loaded)

  const nodes = base.nodes
    .filter(node => node.aggregate?.key !== resolved.region.key)
    .concat(
      remaining > 0
        ? base.nodes
            .filter(node => node.aggregate?.key === resolved.region.key)
            .map(node => ({
              ...node,
              aggregate: node.aggregate ? { ...node.aggregate, count: remaining } : undefined,
              summary: `${remaining} attested nodes remain`,
              useCount: remaining
            }))
        : [],
      resolved.graph.nodes
    )

  const nodeIds = new Set(nodes.map(node => node.id))

  const edges = [...base.edges, ...resolved.graph.edges].filter(
    (edge, index, all) =>
      nodeIds.has(edge.source) &&
      nodeIds.has(edge.target) &&
      all.findIndex(item =>
        item.id ? item.id === edge.id : item.source === edge.source && item.target === edge.target
      ) === index
  )

  return {
    ...base,
    edges,
    nodes,
    stats: { ...base.stats, resolvedNodes: resolved.graph.nodes.length }
  }
}

export function regionQuery(region: StarmapAggregate): {
  community?: string
  types: CortexNodeType[]
  uncommunitied?: boolean
} {
  if (region.kind === 'community' && region.sourceId) {
    return {
      community: region.sourceId,
      types: ['entity', 'memory', 'evidence', 'session', 'document']
    }
  }

  return { types: [region.type ?? 'entity'], uncommunitied: true }
}
