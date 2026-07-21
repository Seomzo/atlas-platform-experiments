import { describe, expect, it } from 'vitest'

import type { CortexGraphResponse, CortexNodeType, ProfileInfo } from '@/types/hermes'

import {
  buildConstellationScene,
  CONSTELLATION_TOTAL_NODE_BUDGET,
  constellationNodeBudget,
  hasOnlyIntraBrainKnowledgeEdges,
  normalizeConstellationProfiles
} from './constellation'
import { buildSimulation } from './simulation'

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

function graph(nodeCount: number): CortexGraphResponse {
  const nodes = Array.from({ length: nodeCount }, (_, index) => ({
    badges: [],
    community: null,
    created_at: `2026-07-${String((index % 20) + 1).padStart(2, '0')}T00:00:00Z`,
    degree: index === 0 ? 1 : 2,
    domain: `domain_${index % 4}`,
    id: `node_${index}`,
    label: `Node ${index}`,
    metadata: {},
    privacy: 'private',
    status: 'active',
    summary: `Summary ${index}`,
    type: NODE_TYPES[index % NODE_TYPES.length]!,
    updated_at: '2026-07-20T00:00:00Z',
    usage: index % 7
  }))

  return {
    communities: [],
    edges: nodes.slice(1).map((node, index) => ({
      created_at: '2026-07-20T00:00:00Z',
      direction: 'directed' as const,
      id: `edge_${index}`,
      metadata: {},
      source: nodes[index]!.id,
      status: 'active',
      target: node.id,
      type: 'fixture_relation',
      updated_at: '2026-07-20T00:00:00Z'
    })),
    facets: { domains: [], statuses: [], types: [] },
    generated_at: '2026-07-20T00:00:00Z',
    layout_seed: 'fixture',
    next_cursor: null,
    nodes,
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

describe('brain constellation composition', () => {
  it('normalizes the main brain first and divides one bounded overview budget', () => {
    const profiles = normalizeConstellationProfiles([profile('zeta'), profile('default', true), profile('alpha')])

    expect(profiles.map(item => item.name)).toEqual(['default', 'alpha', 'zeta'])
    expect(constellationNodeBudget(4)).toBe(105)
    expect(constellationNodeBudget(4) * 4).toBeLessThanOrEqual(CONSTELLATION_TOTAL_NODE_BUDGET)
  })

  it('partitions every brain into a distinct region anchored to its identity', () => {
    const inputs = [profile('default', true), profile('parts'), profile('service'), profile('sales')].map(item => ({
      graph: graph(3),
      profile: item,
      status: 'ready' as const
    }))

    const scene = buildConstellationScene(inputs)

    expect(scene.partitions).toHaveLength(4)
    expect(scene.partitions[0]?.center).toEqual({ x: 0, y: 0 })
    expect(
      new Set(scene.partitions.slice(1).map(partition => `${partition.center.x},${partition.center.y}`)).size
    ).toBe(3)

    for (const partition of scene.partitions) {
      expect(scene.ownershipEdges).toContainEqual({
        kind: 'ownership',
        profile: partition.profile.name,
        source: partition.anchorId,
        target: partition.regionId
      })
      expect(partition.nodeIds.every(id => scene.graph.nodes.some(node => node.id === id))).toBe(true)
    }
  })

  it('keeps all knowledge edges inside their source brain', () => {
    const scene = buildConstellationScene([
      { graph: graph(4), profile: profile('default', true), status: 'ready' },
      { graph: graph(4), profile: profile('service'), status: 'ready' }
    ])

    expect(scene.graph.nodes).toHaveLength(8)
    expect(scene.graph.edges).toHaveLength(6)
    expect(hasOnlyIntraBrainKnowledgeEdges(scene)).toBe(true)
    expect(scene.graph.edges.every(edge => edge.type === 'fixture_relation')).toBe(true)
    expect(scene.graph.edges.some(edge => edge.type === 'ownership')).toBe(false)
  })

  it('represents empty and disabled brains without placeholder nodes', () => {
    const scene = buildConstellationScene([
      { graph: graph(0), profile: profile('default', true), status: 'empty' },
      { graph: null, profile: profile('service'), status: 'disabled' }
    ])

    expect(scene.graph.nodes).toHaveLength(0)
    expect(scene.partitions.map(partition => partition.status)).toEqual(['empty', 'disabled'])
  })

  it('settles a measured 1 main + 3 worker scene within the 420-node envelope', () => {
    const perBrain = constellationNodeBudget(4)

    const scene = buildConstellationScene(
      [profile('default', true), profile('parts'), profile('service'), profile('sales')].map(item => ({
        graph: graph(perBrain),
        profile: item,
        status: 'ready' as const
      }))
    )

    const simulation = buildSimulation(scene.graph, () => {}, scene)
    simulation.sim.stop()
    simulation.sim.tick(180)

    expect(scene.graph.nodes).toHaveLength(420)
    expect(scene.graph.edges).toHaveLength(416)
    expect(scene.ownershipEdges).toHaveLength(4)
    expect(simulation.nodes.every(node => Number.isFinite(node.x) && Number.isFinite(node.y))).toBe(true)
  })
})
