import { describe, expect, it } from 'vitest'

import type { CortexGraphResponse, CortexNodeType } from '@/types/hermes'

import { CORTEX_NODE_VISUALS, nodeShape } from './constants'
import { CORTEX_NODE_TYPES, cortexNodeAriaLabel, cortexToStarmap, filterCortexNodes } from './cortex'
import { nodeRadius } from './geometry'

function response(): CortexGraphResponse {
  return {
    communities: [],
    edges: [
      {
        created_at: '2026-07-14T10:00:00Z',
        direction: 'directed',
        id: 'relation_1',
        metadata: {},
        source: 'entity_1',
        status: 'active',
        target: 'memory_1',
        type: 'supports',
        updated_at: '2026-07-14T10:00:00Z'
      }
    ],
    facets: {
      domains: [{ count: 6, value: 'personal' }],
      statuses: [{ count: 6, value: 'active' }],
      types: CORTEX_NODE_TYPES.map(value => ({ count: 1, value }))
    },
    generated_at: '2026-07-14T10:01:00Z',
    layout_seed: 'stable-layout',
    next_cursor: null,
    nodes: CORTEX_NODE_TYPES.map((type, index) => ({
      badges: type === 'memory' ? ['cited', 'protected'] : ['private'],
      community: type === 'community' ? 'community_1' : null,
      created_at: `2026-07-${String(10 + index).padStart(2, '0')}T10:00:00Z`,
      degree: 1,
      domain: 'personal',
      id: `${type}_1`,
      label: type === 'entity' ? 'Jordan Customer' : `${type} fixture`,
      metadata: {},
      privacy: 'private',
      status: 'active',
      summary: `${type} summary`,
      type,
      updated_at: '2026-07-14T10:00:00Z',
      usage: type === 'memory' ? 2 : 0
    })),
    projection: 'growth',
    redaction_summary: {
      document_bodies_hidden: 0,
      nodes_omitted_by_limit: 0,
      raw_evidence_bodies_hidden: 1
    },
    retrieval_run_id: null,
    timeline_window: { end: '2026-07-14T10:00:00Z', start: '2026-07-10T10:00:00Z' },
    version: 'atlas.cortex.graph.v1'
  }
}

describe('Cortex graph adapter', () => {
  it('preserves typed nodes, directed edges, stable ids, and time', () => {
    const graph = cortexToStarmap(response())

    expect(graph.source).toBe('cortex')
    expect(graph.nodes.map(node => [node.id, node.cortexType, node.kind])).toEqual(
      CORTEX_NODE_TYPES.map(type => [`${type}_1`, type, type])
    )
    expect(graph.nodes.every(node => node.kind !== 'skill')).toBe(true)
    expect(graph.nodes[1]?.pinned).toBe(true)
    expect(graph.nodes[1]?.timestamp).toBe(Date.parse('2026-07-14T10:00:00Z') / 1000)
    expect(graph.edges[0]).toMatchObject({
      direction: 'directed',
      id: 'relation_1',
      source: 'entity_1',
      target: 'memory_1',
      type: 'supports'
    })
  })

  it('cannot leak an unexpected overview body into the renderer projection', () => {
    const payload = response() as CortexGraphResponse & { evidence_body?: string }
    payload.evidence_body = 'RAW-PRIVATE-EVIDENCE'

    expect(JSON.stringify(cortexToStarmap(payload))).not.toContain('RAW-PRIVATE-EVIDENCE')
  })

  it('builds a descriptive accessible node label', () => {
    const node = cortexToStarmap(response()).nodes[0]!

    expect(cortexNodeAriaLabel(node)).toBe('Jordan Customer, entity, personal, active, private')
  })

  it('gives all six Cortex types distinct renderer treatments', () => {
    const graph = cortexToStarmap(response())
    const visuals = CORTEX_NODE_TYPES.map(type => CORTEX_NODE_VISUALS[type])

    expect(new Set(visuals.map(visual => visual.color)).size).toBe(6)
    expect(new Set(visuals.map(visual => visual.shape)).size).toBe(6)
    expect(new Set(visuals.map(visual => visual.radius)).size).toBe(6)
    expect(graph.nodes.map(nodeShape)).toEqual(visuals.map(visual => visual.shape))
    expect(new Set(graph.nodes.map(nodeRadius)).size).toBe(6)
  })

  it('filters node types without changing the graph adapter contract', () => {
    const nodes = cortexToStarmap(response()).nodes
    const hiddenTypes = new Set<CortexNodeType>(['evidence', 'session'])
    const filtered = filterCortexNodes(nodes, { domain: null, hiddenTypes, query: '' })

    expect(filtered.map(node => node.cortexType)).toEqual(['entity', 'memory', 'document', 'community'])
  })
})
