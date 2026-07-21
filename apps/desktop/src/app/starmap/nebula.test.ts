import { describe, expect, it } from 'vitest'

import type { CortexGraphNode, CortexGraphResponse, CortexNodeType, ProfileInfo } from '@/types/hermes'

import { buildNebulaScene, hasOnlyIntraBrainKnowledgeEdges, normalizeNebulaProfiles } from './nebula'
import { brainFilterAlpha } from './render'
import { buildSimulation, nebulaWellIndex } from './simulation'

const NODE_TYPES: CortexNodeType[] = ['entity', 'memory', 'evidence', 'session', 'document', 'community']

function profile(name: string, isDefault = false): ProfileInfo {
  return {
    display_name: isDefault ? 'Atlas' : name.replaceAll('_', ' '),
    gateway_running: false,
    has_avatar: false,
    has_env: true,
    is_default: isDefault,
    model: null,
    name,
    path: `/tmp/${name}`,
    provider: null,
    role: isDefault ? 'main' : 'worker',
    skill_count: 0
  }
}

function node(id: string, type: CortexNodeType): CortexGraphNode {
  return {
    badges: [],
    community: null,
    created_at: '2026-07-20T00:00:00Z',
    degree: 1,
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

function graph(total: number, nodes: CortexGraphNode[] = []): CortexGraphResponse {
  const visibleCounts = new Map<CortexNodeType, number>()

  for (const item of nodes) {
    visibleCounts.set(item.type, (visibleCounts.get(item.type) ?? 0) + 1)
  }

  const remaining = Math.max(0, total - nodes.length)

  return {
    aggregates: {
      relation_count: nodes.length > 1 ? 1 : 0,
      total_nodes: total,
      types: NODE_TYPES.map((type, index) => ({
        count: (visibleCounts.get(type) ?? 0) + (index === 0 ? remaining : 0),
        type,
        uncommunitied_count: (visibleCounts.get(type) ?? 0) + (index === 0 ? remaining : 0)
      }))
    },
    communities: [],
    edges:
      nodes.length > 1
        ? [
            {
              created_at: '2026-07-20T00:00:00Z',
              direction: 'directed',
              id: 'edge-1',
              metadata: {},
              source: nodes[0]!.id,
              status: 'active',
              target: nodes[1]!.id,
              type: 'related',
              updated_at: '2026-07-20T00:00:00Z'
            }
          ]
        : [],
    facets: { domains: [], statuses: [], types: [] },
    generated_at: '2026-07-20T00:00:00Z',
    layout_seed: 'fixture',
    next_cursor: total > nodes.length ? 'next' : null,
    nodes,
    projection: 'growth',
    redaction_summary: {
      document_bodies_hidden: 0,
      nodes_omitted_by_limit: Math.max(0, total - nodes.length),
      raw_evidence_bodies_hidden: 0
    },
    retrieval_run_id: null,
    timeline_window: { end: null, start: null },
    version: 'atlas.cortex.graph.v1'
  }
}

describe('dense nebula composition', () => {
  it('dims non-selected brains to the approved seven-percent filter opacity', () => {
    expect(brainFilterAlpha('service', null)).toBe(1)
    expect(brainFilterAlpha('service', 'service')).toBe(1)
    expect(brainFilterAlpha('default', 'service')).toBe(0.07)
  })

  it('normalizes Atlas first and keeps workers stable', () => {
    const profiles = normalizeNebulaProfiles([profile('zeta'), profile('default', true), profile('alpha')])

    expect(profiles.map(item => item.name)).toEqual(['default', 'alpha', 'zeta'])
  })

  it('preserves each real node and edge owner through the shared adapter', () => {
    const scene = buildNebulaScene([
      {
        graph: graph(2, [node('atlas-entity', 'entity'), node('atlas-memory', 'memory')]),
        profile: profile('default', true),
        status: 'ready'
      },
      {
        graph: graph(2, [node('worker-entity', 'entity'), node('worker-evidence', 'evidence')]),
        profile: profile('service'),
        status: 'ready'
      }
    ])

    expect(scene.graph.nodes.filter(item => !item.aggregate).map(item => item.brainProfile)).toEqual([
      'default',
      'default',
      'service',
      'service'
    ])
    expect(scene.graph.edges.map(edge => edge.brainProfile)).toEqual(['default', 'service'])
    expect(hasOnlyIntraBrainKnowledgeEdges(scene)).toBe(true)
    expect('partitions' in scene).toBe(false)
    expect('ownershipEdges' in scene).toBe(false)
  })

  it('keeps empty and disabled brains as honest chip state with no nodes', () => {
    const scene = buildNebulaScene([
      { graph: graph(0), profile: profile('default', true), status: 'empty' },
      { graph: null, profile: profile('service'), status: 'disabled' }
    ])

    expect(scene.graph.nodes).toHaveLength(0)
    expect(scene.brains.map(brain => brain.status)).toEqual(['empty', 'disabled'])
    expect(scene.brains.every(brain => brain.nodeIds.length === 0)).toBe(true)
  })

  it('mixes layout wells by knowledge neighborhood rather than brain region', () => {
    const atlasWells = new Set(
      Array.from({ length: 120 }, (_, index) =>
        nebulaWellIndex({ category: 'personal', id: `brain:default:entity-${index}` }, 11)
      )
    )

    const workerWells = new Set(
      Array.from({ length: 120 }, (_, index) =>
        nebulaWellIndex({ category: 'personal', id: `brain:service:entity-${index}` }, 11)
      )
    )

    expect(atlasWells.size).toBeGreaterThanOrEqual(8)
    expect(workerWells.size).toBeGreaterThanOrEqual(8)
    expect([...atlasWells].some(index => workerWells.has(index))).toBe(true)

    const scene = buildNebulaScene([
      { graph: graph(50_000), profile: profile('default', true), status: 'ready' },
      { graph: graph(50_000), profile: profile('service'), status: 'ready' }
    ])

    const simulation = buildSimulation(scene.graph, () => {}, true, { h: 720, w: 1_280 })
    simulation.sim.stop()
    simulation.sim.tick(180)

    expect(scene.graph.nodes.length).toBeLessThanOrEqual(NODE_TYPES.length * 2)
    expect(scene.graph.stats.totalNodes).toBe(100_000)
    expect(simulation.nodes.every(item => Number.isFinite(item.x) && Number.isFinite(item.y))).toBe(true)
  })
})
