import type { CortexGraphNode, CortexGraphResponse, StarmapGraph, StarmapNode } from '@/types/hermes'

function timestamp(value: null | string): null | number {
  if (!value) {
    return null
  }

  const millis = Date.parse(value)

  return Number.isFinite(millis) ? Math.floor(millis / 1000) : null
}

export function cortexNodeAriaLabel(
  node: Pick<StarmapNode, 'category' | 'cortexType' | 'label' | 'privacy' | 'state'>
): string {
  const parts = [node.label, node.cortexType ?? 'memory item', node.category, node.state]

  if (node.privacy) {
    parts.push(node.privacy)
  }

  return parts.filter(Boolean).join(', ')
}

function adaptNode(node: CortexGraphNode): StarmapNode {
  return {
    badges: [...node.badges],
    category: node.domain,
    cortexType: node.type,
    createdBy: null,
    id: node.id,
    kind: node.type === 'memory' ? 'memory' : 'skill',
    label: node.label,
    memorySource: node.type === 'memory' ? 'memory' : undefined,
    pinned: node.badges.includes('protected') || node.badges.includes('pinned'),
    privacy: node.privacy,
    state: node.status,
    summary: node.summary,
    timestamp: timestamp(node.updated_at ?? node.created_at),
    useCount: Math.max(0, node.usage || node.degree)
  }
}

/** Adapt the native DTO to the existing radial renderer without flattening its
 * typed semantics. No raw body can enter this projection because the Cortex
 * overview contract does not define one. */
export function cortexToStarmap(graph: CortexGraphResponse): StarmapGraph {
  return {
    clusters: graph.facets.domains.map(item => ({ category: item.value, count: item.count })),
    edges: graph.edges.map(edge => ({
      direction: edge.direction,
      id: edge.id,
      source: edge.source,
      status: edge.status,
      target: edge.target,
      type: edge.type
    })),
    memory: [],
    nodes: graph.nodes.map(adaptNode),
    source: 'cortex',
    stats: {
      communities: graph.communities.length,
      contract: graph.version,
      generatedAt: graph.generated_at,
      projection: graph.projection,
      redaction: { ...graph.redaction_summary }
    }
  }
}
