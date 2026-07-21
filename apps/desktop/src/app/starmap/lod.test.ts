import { describe, expect, it, vi } from 'vitest'

import type { CortexGraphNode, CortexGraphResponse, CortexNodeType, StarmapAggregate } from '@/types/hermes'

import {
  aggregateStarRadius,
  brainRegionRadius,
  cortexAggregatesToStarmap,
  hasOnlyAttestedAggregates,
  LOD_COMMUNITY_PAGE_SIZE,
  lodTierForZoom,
  ProgressiveCortexResolver
} from './lod'
import { buildSimulation, updateSimulationViewport } from './simulation'

const NODE_TYPES: CortexNodeType[] = ['entity', 'memory', 'evidence', 'session', 'document', 'community']

function node(id: string, type: CortexNodeType = 'evidence'): CortexGraphNode {
  return {
    badges: [],
    community: null,
    created_at: '2026-07-20T00:00:00Z',
    degree: 0,
    domain: 'personal',
    id,
    label: id,
    metadata: {},
    privacy: 'private',
    status: 'active',
    summary: id,
    type,
    updated_at: '2026-07-20T00:00:00Z',
    usage: 0
  }
}

function response(
  options: {
    communities?: CortexGraphResponse['communities']
    nextCursor?: null | string
    nodes?: CortexGraphNode[]
    total?: number
    relationCount?: number
    typeCounts?: Partial<Record<CortexNodeType, number>>
  } = {}
): CortexGraphResponse {
  const typeCounts = options.typeCounts ?? {
    entity: 10_000,
    memory: 5_000,
    evidence: 25_000,
    session: 5_000,
    document: 4_997,
    community: 3
  }

  return {
    aggregates: {
      relation_count: options.relationCount ?? 100_000,
      total_nodes: options.total ?? 50_000,
      types: NODE_TYPES.map(type => ({
        count: typeCounts[type] ?? 0,
        type,
        uncommunitied_count: type === 'community' ? 0 : (typeCounts[type] ?? 0)
      }))
    },
    communities: options.communities ?? [],
    edges: [],
    facets: { domains: [], statuses: [], types: [] },
    generated_at: '2026-07-20T00:00:00Z',
    layout_seed: 'fixture',
    next_cursor: options.nextCursor ?? null,
    nodes: options.nodes ?? [],
    projection: 'growth',
    redaction_summary: {
      document_bodies_hidden: 0,
      nodes_omitted_by_limit: 0,
      raw_evidence_bodies_hidden: 0
    },
    retrieval_run_id: null,
    timeline_window: { end: null, start: null },
    version: 'atlas.cortex.graph.v1'
  }
}

describe('Starmap level of detail', () => {
  it('maps larger attested counts to strictly larger stars and brain regions', () => {
    const counts = [0, 1, 10, 1_000, 50_000]

    for (let index = 1; index < counts.length; index += 1) {
      expect(aggregateStarRadius(counts[index]!, 'type')).toBeGreaterThan(
        aggregateStarRadius(counts[index - 1]!, 'type')
      )
      expect(brainRegionRadius(counts[index]!, false)).toBeGreaterThan(brainRegionRadius(counts[index - 1]!, false))
    }
  })

  it('selects aggregate and detail tiers from zoom relative to the fitted camera', () => {
    expect(lodTierForZoom(0.8, 0.8)).toBe('aggregate')
    expect(lodTierForZoom(1.4, 0.8)).toBe('aggregate')
    expect(lodTierForZoom(1.44, 0.8)).toBe('detail')
    expect(lodTierForZoom(2.4, 0.8)).toBe('detail')
  })

  it('walks cursors progressively and reuses the per-brain region cache', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(response({ nextCursor: 'cursor-2', nodes: [node('evidence-1'), node('evidence-2')] }))
      .mockResolvedValueOnce(response({ nodes: [node('evidence-3'), node('evidence-4')] }))

    const resolver = new ProgressiveCortexResolver(fetcher)
    const region: StarmapAggregate = { count: 4, key: 'type:evidence', kind: 'type', type: 'evidence' }

    const first = await resolver.resolve('default', region, 1)
    expect(first.nextCursor).toBe('cursor-2')
    expect(first.graph.nodes).toHaveLength(2)

    const complete = await resolver.resolve('default', region, 2)
    expect(fetcher.mock.calls.map(([request]) => request.cursor)).toEqual([undefined, 'cursor-2'])
    expect(complete.complete).toBe(true)
    expect(complete.graph.nodes.map(item => item.id)).toEqual(['evidence-1', 'evidence-2', 'evidence-3', 'evidence-4'])

    await resolver.resolve('default', region, 2)
    expect(fetcher).toHaveBeenCalledTimes(2)
    expect(resolver.get('default', region.key)).toBe(complete)
  })

  it('renders only aggregates attested by backend community or type counts', () => {
    const communities = [
      {
        domain: 'personal',
        generated_at: '2026-07-20T00:00:00Z',
        id: 'community-1',
        label: 'Customer knowledge',
        level: 0,
        member_count: 12_000,
        parent_id: null,
        status: 'active',
        visible_member_count: 0
      }
    ]

    const payload = response({ communities, nodes: [node('community-1', 'community')] })
    const graph = cortexAggregatesToStarmap(payload)

    expect(graph.nodes.length).toBeLessThanOrEqual(NODE_TYPES.length)
    expect(hasOnlyAttestedAggregates(graph, payload)).toBe(true)
    expect(graph.nodes.every(item => item.aggregate && item.aggregate.count > 0)).toBe(true)
  })

  it('keeps a 50k-node / 100k-edge fixture in a small steady-state aggregate scene', () => {
    const communities = Array.from({ length: LOD_COMMUNITY_PAGE_SIZE }, (_, index) => ({
      domain: 'personal',
      generated_at: '2026-07-20T00:00:00Z',
      id: `community-${index}`,
      label: `Knowledge region ${index}`,
      level: 0,
      member_count: 1_000 + index,
      parent_id: null,
      status: 'active' as const,
      visible_member_count: 0
    }))

    const payload = response({
      communities,
      nodes: communities.map(item => node(item.id, 'community')),
      typeCounts: {
        community: LOD_COMMUNITY_PAGE_SIZE,
        document: 4_976,
        entity: 10_000,
        evidence: 25_000,
        memory: 5_000,
        session: 5_000
      }
    })

    const graph = cortexAggregatesToStarmap(payload)
    const simulation = buildSimulation(graph, () => {})
    simulation.sim.stop()

    const started = performance.now()
    simulation.sim.tick(600)
    const averageFrameMs = (performance.now() - started) / 600

    expect(payload.aggregates.total_nodes).toBe(50_000)
    expect(payload.aggregates.relation_count).toBe(100_000)
    expect(graph.nodes).toHaveLength(LOD_COMMUNITY_PAGE_SIZE + NODE_TYPES.length - 1)
    expect(graph.edges).toHaveLength(0)
    expect(averageFrameMs).toBeLessThan(1000 / 60)
    console.info(`WS-17 aggregate fixture: ${averageFrameMs.toFixed(3)} ms/simulation frame`)
  })

  it('removes off-viewport resolved nodes from active force simulation', () => {
    const graph = cortexAggregatesToStarmap(response())
    const simulation = buildSimulation(graph, () => {})
    simulation.sim.stop()
    simulation.nodes[0]!.x = 10
    simulation.nodes[0]!.y = 10

    for (const node of simulation.nodes.slice(1)) {
      node.x = 10_000
      node.y = 10_000
    }

    const active = updateSimulationViewport(
      simulation.sim,
      simulation.nodes,
      simulation.links,
      { k: 1, x: 0, y: 0 },
      { h: 100, w: 100 },
      0
    )

    simulation.sim.stop()

    expect(active).toBe(1)
    expect(simulation.sim.nodes()).toEqual([simulation.nodes[0]])
    expect(simulation.nodes[1]!.lodActive).toBe(false)
  })
})
