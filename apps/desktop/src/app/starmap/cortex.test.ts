import { describe, expect, it } from 'vitest'

import type { CortexGraphResponse } from '@/types/hermes'

import { cortexNodeAriaLabel, cortexToStarmap } from './cortex'

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
      domains: [{ count: 2, value: 'personal' }],
      statuses: [{ count: 2, value: 'active' }],
      types: [
        { count: 1, value: 'entity' },
        { count: 1, value: 'memory' }
      ]
    },
    generated_at: '2026-07-14T10:01:00Z',
    layout_seed: 'stable-layout',
    next_cursor: null,
    nodes: [
      {
        badges: ['private'],
        community: null,
        created_at: '2026-07-10T10:00:00Z',
        degree: 1,
        domain: 'personal',
        id: 'entity_1',
        label: 'Jordan Customer',
        metadata: { entity_type: 'person' },
        privacy: 'private',
        status: 'active',
        summary: 'Customer entity',
        type: 'entity',
        updated_at: '2026-07-14T10:00:00Z',
        usage: 0
      },
      {
        badges: ['cited', 'protected'],
        community: null,
        created_at: '2026-07-11T10:00:00Z',
        degree: 1,
        domain: 'personal',
        id: 'memory_1',
        label: 'Prefers text updates',
        metadata: { evidence_count: 1 },
        privacy: 'private',
        status: 'active',
        summary: 'Prefers text updates after 3 PM.',
        type: 'memory',
        updated_at: '2026-07-14T10:00:00Z',
        usage: 2
      }
    ],
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
    expect(graph.nodes.map(node => [node.id, node.cortexType])).toEqual([
      ['entity_1', 'entity'],
      ['memory_1', 'memory']
    ])
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
})
