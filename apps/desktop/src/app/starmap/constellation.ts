import type { CortexGraphResponse, ProfileInfo, StarmapEdge, StarmapGraph, StarmapNode } from '@/types/hermes'

import { cortexToStarmap } from './cortex'

export const CONSTELLATION_TOTAL_NODE_BUDGET = 420

export type ConstellationBrainStatus = 'disabled' | 'empty' | 'ready' | 'unavailable'

export interface ConstellationPoint {
  x: number
  y: number
}

export interface ConstellationBrainInput {
  graph: CortexGraphResponse | null
  profile: ProfileInfo
  status: ConstellationBrainStatus
}

export interface ConstellationPartition {
  anchor: ConstellationPoint
  anchorId: string
  center: ConstellationPoint
  nodeIds: string[]
  profile: ProfileInfo
  radius: number
  regionId: string
  status: ConstellationBrainStatus
}

export interface ConstellationOwnershipEdge {
  kind: 'ownership'
  profile: string
  source: string
  target: string
}

export interface ConstellationScene {
  graph: StarmapGraph
  outerRadius: number
  ownershipEdges: ConstellationOwnershipEdge[]
  partitions: ConstellationPartition[]
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

export function constellationNodeBudget(brainCount: number): number {
  return Math.max(1, Math.floor(CONSTELLATION_TOTAL_NODE_BUDGET / Math.max(1, brainCount)))
}

export function normalizeConstellationProfiles(profiles: ProfileInfo[]): ProfileInfo[] {
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

export function constellationStatusFromError(
  error: unknown
): Extract<ConstellationBrainStatus, 'disabled' | 'unavailable'> {
  const value = error instanceof Error ? error.message : String(error)

  return /Cortex is disabled for this profile/i.test(value) ? 'disabled' : 'unavailable'
}

function ids(profile: string): { anchorId: string; regionId: string } {
  const encoded = encodeURIComponent(profile)

  return { anchorId: `brain-anchor:${encoded}`, regionId: `brain-region:${encoded}` }
}

function namespaceNode(profile: string, node: StarmapNode): StarmapNode {
  return { ...node, brainProfile: profile, id: `brain:${encodeURIComponent(profile)}:${node.id}` }
}

function namespaceEdge(profile: string, edge: StarmapEdge): StarmapEdge {
  const prefix = `brain:${encodeURIComponent(profile)}:`

  return {
    ...edge,
    brainProfile: profile,
    id: edge.id ? `${prefix}${edge.id}` : undefined,
    source: `${prefix}${edge.source}`,
    target: `${prefix}${edge.target}`
  }
}

interface PartitionGeometry {
  anchor: ConstellationPoint
  center: ConstellationPoint
  radius: number
}

function workerGeometry(index: number, workerCount: number): PartitionGeometry {
  let ring = 1
  let offset = index
  let consumed = 0

  while (offset >= ring * 8) {
    offset -= ring * 8
    consumed += ring * 8
    ring += 1
  }

  const count = Math.min(ring * 8, workerCount - consumed)
  const angle = -Math.PI / 2 + (offset / count) * Math.PI * 2 + (ring % 2 === 0 ? Math.PI / count : 0)
  const anchorDistance = 205 + (ring - 1) * 165
  const regionDistance = anchorDistance + 62

  return {
    anchor: { x: Math.cos(angle) * anchorDistance, y: Math.sin(angle) * anchorDistance },
    center: { x: Math.cos(angle) * regionDistance, y: Math.sin(angle) * regionDistance },
    radius: 72
  }
}

export function buildConstellationScene(inputs: ConstellationBrainInput[]): ConstellationScene {
  const nodes: StarmapNode[] = []
  const edges: StarmapEdge[] = []
  const partitions: ConstellationPartition[] = []
  const ownershipEdges: ConstellationOwnershipEdge[] = []
  const clusterCounts = new Map<string, number>()

  inputs.forEach((input, index) => {
    const profileName = input.profile.name
    const graph = input.graph ? cortexToStarmap(input.graph) : null
    const brainNodes = graph?.nodes.map(node => namespaceNode(profileName, node)) ?? []
    const brainNodeIds = new Set(brainNodes.map(node => node.id))

    const brainEdges =
      graph?.edges
        .map(edge => namespaceEdge(profileName, edge))
        .filter(edge => brainNodeIds.has(edge.source) && brainNodeIds.has(edge.target)) ?? []

    const geometry =
      index === 0
        ? { anchor: { x: 0, y: 0 }, center: { x: 0, y: 0 }, radius: 118 }
        : workerGeometry(index - 1, inputs.length - 1)

    const { anchorId, regionId } = ids(profileName)
    const status = input.status === 'ready' && brainNodes.length === 0 ? 'empty' : input.status

    nodes.push(...brainNodes)
    edges.push(...brainEdges)

    for (const node of brainNodes) {
      clusterCounts.set(node.category, (clusterCounts.get(node.category) ?? 0) + 1)
    }

    partitions.push({
      ...geometry,
      anchorId,
      nodeIds: brainNodes.map(node => node.id),
      profile: input.profile,
      regionId,
      status
    })
    ownershipEdges.push({ kind: 'ownership', profile: profileName, source: anchorId, target: regionId })
  })

  const outerRadius = Math.max(
    170,
    ...partitions.map(partition => Math.hypot(partition.center.x, partition.center.y) + partition.radius + 38)
  )

  return {
    graph: {
      clusters: [...clusterCounts.entries()].map(([category, count]) => ({ category, count })),
      edges,
      memory: [],
      nodes,
      source: 'cortex',
      stats: {
        brains: partitions.length,
        constellation: true,
        ownershipEdges: ownershipEdges.length
      }
    },
    outerRadius,
    ownershipEdges,
    partitions
  }
}

export function hasOnlyIntraBrainKnowledgeEdges(scene: ConstellationScene): boolean {
  const ownerByNode = new Map(scene.graph.nodes.map(node => [node.id, node.brainProfile]))

  return scene.graph.edges.every(edge => {
    const sourceOwner = ownerByNode.get(edge.source)
    const targetOwner = ownerByNode.get(edge.target)

    return Boolean(sourceOwner && sourceOwner === targetOwner && edge.brainProfile === sourceOwner)
  })
}
