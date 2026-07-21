import type { CortexGraphResponse, ProfileInfo, StarmapEdge, StarmapGraph, StarmapNode } from '@/types/hermes'

import { cortexNebulaToStarmap } from './lod'

export type NebulaBrainStatus = 'disabled' | 'empty' | 'ready' | 'unavailable'

export interface NebulaBrainInput {
  graph: CortexGraphResponse | null
  profile: ProfileInfo
  status: NebulaBrainStatus
}

export interface NebulaBrain {
  nodeIds: string[]
  profile: ProfileInfo
  status: NebulaBrainStatus
  totalNodeCount: number
}

export interface NebulaScene {
  brains: NebulaBrain[]
  graph: StarmapGraph
}

const DEFAULT_PROFILE: ProfileInfo = {
  display_name: 'Atlas',
  gateway_running: false,
  has_avatar: false,
  has_env: false,
  is_default: true,
  model: null,
  name: 'default',
  path: '',
  provider: null,
  role: '',
  skill_count: 0
}

export function normalizeNebulaProfiles(profiles: ProfileInfo[]): ProfileInfo[] {
  const byName = new Map(profiles.map(profile => [profile.name, profile]))
  const resolvedMain = profiles.find(profile => profile.is_default) ?? byName.get('default') ?? DEFAULT_PROFILE

  const main = {
    ...resolvedMain,
    display_name:
      resolvedMain.display_name.trim() && resolvedMain.display_name.trim() !== resolvedMain.name
        ? resolvedMain.display_name
        : 'Atlas'
  }

  const workers = profiles
    .filter(profile => profile.name !== main.name && !profile.is_default)
    .sort(
      (left, right) =>
        (left.display_name || left.name).localeCompare(right.display_name || right.name) ||
        left.name.localeCompare(right.name)
    )

  return [main, ...workers]
}

export function nebulaStatusFromError(error: unknown): Extract<NebulaBrainStatus, 'disabled' | 'unavailable'> {
  const value = error instanceof Error ? error.message : String(error)

  return /Cortex is disabled for this profile/i.test(value) ? 'disabled' : 'unavailable'
}

function prefix(profile: string): string {
  return `brain:${encodeURIComponent(profile)}:`
}

function namespaceNode(profile: string, node: StarmapNode): StarmapNode {
  return { ...node, brainProfile: profile, id: `${prefix(profile)}${node.id}` }
}

function namespaceEdge(profile: string, edge: StarmapEdge): StarmapEdge {
  const namespace = prefix(profile)

  return {
    ...edge,
    brainProfile: profile,
    id: edge.id ? `${namespace}${edge.id}` : undefined,
    source: `${namespace}${edge.source}`,
    target: `${namespace}${edge.target}`
  }
}

export function nativeNebulaNodeId(node: Pick<StarmapNode, 'brainProfile' | 'id'>): string {
  if (!node.brainProfile) {
    return node.id
  }

  const namespace = prefix(node.brainProfile)

  return node.id.startsWith(namespace) ? node.id.slice(namespace.length) : node.id
}

export function buildNebulaScene(inputs: NebulaBrainInput[]): NebulaScene {
  const nodes: StarmapNode[] = []
  const edges: StarmapEdge[] = []
  const brains: NebulaBrain[] = []
  const clusterCounts = new Map<string, number>()
  let relationCount = 0

  for (const input of inputs) {
    const profileName = input.profile.name
    const graph = input.graph ? cortexNebulaToStarmap(input.graph) : null
    const brainNodes = graph?.nodes.map(node => namespaceNode(profileName, node)) ?? []
    const brainNodeIds = new Set(brainNodes.map(node => node.id))

    const brainEdges =
      graph?.edges
        .map(edge => namespaceEdge(profileName, edge))
        .filter(edge => brainNodeIds.has(edge.source) && brainNodeIds.has(edge.target)) ?? []

    const totalNodeCount = input.graph?.aggregates.total_nodes ?? 0
    const status = input.status === 'ready' && totalNodeCount === 0 ? 'empty' : input.status

    nodes.push(...brainNodes)
    edges.push(...brainEdges)
    relationCount += input.graph?.aggregates.relation_count ?? 0

    for (const item of graph?.clusters ?? []) {
      clusterCounts.set(item.category, (clusterCounts.get(item.category) ?? 0) + item.count)
    }

    brains.push({
      nodeIds: brainNodes.map(node => node.id),
      profile: input.profile,
      status,
      totalNodeCount
    })
  }

  return {
    brains,
    graph: {
      clusters: [...clusterCounts.entries()].map(([category, count]) => ({ category, count })),
      edges,
      memory: [],
      nodes,
      source: 'cortex',
      stats: {
        brains: brains.length,
        nebula: true,
        relationCount,
        totalNodes: brains.reduce((sum, brain) => sum + brain.totalNodeCount, 0)
      }
    }
  }
}

export function hasOnlyIntraBrainKnowledgeEdges(scene: NebulaScene): boolean {
  const ownerByNode = new Map(scene.graph.nodes.map(node => [node.id, node.brainProfile]))

  return scene.graph.edges.every(edge => {
    const sourceOwner = ownerByNode.get(edge.source)
    const targetOwner = ownerByNode.get(edge.target)

    return Boolean(sourceOwner && sourceOwner === targetOwner && edge.brainProfile === sourceOwner)
  })
}
