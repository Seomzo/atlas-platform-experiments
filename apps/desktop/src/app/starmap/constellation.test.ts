import { describe, expect, it } from 'vitest'

import type { CortexGraphResponse, CortexNodeType, ProfileInfo } from '@/types/hermes'

import {
  buildConstellationScene,
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
  const quotient = Math.floor(nodeCount / NODE_TYPES.length)
  const remainder = nodeCount % NODE_TYPES.length

  const counts = NODE_TYPES.map((type, index) => ({
    count: quotient + (index < remainder ? 1 : 0),
    type,
    uncommunitied_count: type === 'community' ? 0 : quotient + (index < remainder ? 1 : 0)
  }))

  return {
    aggregates: { relation_count: nodeCount * 2, total_nodes: nodeCount, types: counts },
    communities: [],
    edges: [],
    facets: { domains: [], statuses: [], types: [] },
    generated_at: '2026-07-20T00:00:00Z',
    layout_seed: 'fixture',
    next_cursor: null,
    nodes: [],
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
  it('normalizes the main brain first without assigning a shared node ceiling', () => {
    const profiles = normalizeConstellationProfiles([profile('zeta'), profile('default', true), profile('alpha')])

    expect(profiles.map(item => item.name)).toEqual(['default', 'alpha', 'zeta'])
    expect(profiles).toHaveLength(3)
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

    expect(scene.graph.nodes.length).toBeGreaterThan(0)
    expect(scene.graph.edges).toHaveLength(0)
    expect(hasOnlyIntraBrainKnowledgeEdges(scene)).toBe(true)
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

  it('settles a 50k-node brain fixture as a bounded aggregate scene', () => {
    const scene = buildConstellationScene(
      [profile('default', true), profile('parts'), profile('service'), profile('sales')].map(item => ({
        graph: graph(50_000),
        profile: item,
        status: 'ready' as const
      }))
    )

    const simulation = buildSimulation(scene.graph, () => {}, scene)
    simulation.sim.stop()
    simulation.sim.tick(180)

    expect(scene.graph.nodes.length).toBeLessThanOrEqual(NODE_TYPES.length * 4)
    expect(scene.graph.stats.totalNodes).toBe(200_000)
    expect(scene.graph.edges).toHaveLength(0)
    expect(scene.ownershipEdges).toHaveLength(4)
    expect(simulation.nodes.every(node => Number.isFinite(node.x) && Number.isFinite(node.y))).toBe(true)
  })
})
