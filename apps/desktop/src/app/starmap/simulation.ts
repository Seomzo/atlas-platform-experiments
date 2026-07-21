import {
  forceCollide,
  forceLink,
  type ForceLink,
  forceManyBody,
  forceRadial,
  forceSimulation,
  forceX,
  forceY,
  type Simulation
} from 'd3-force'

import type { StarmapGraph, StarmapNode } from '@/types/hermes'

import { RING_STEPS, TILT } from './constants'
import { clamp, hash, nodeRadius, radiusForRecency } from './geometry'
import { formatDate } from './text'
import { computeRecency, recForRatio } from './time-axis'
import type { Ring, SimLink, SimNode, Viewport } from './types'

export interface BuiltSim {
  byId: Map<string, SimNode>
  links: SimLink[]
  nodes: SimNode[]
  rings: Ring[]
  sim: Simulation<SimNode, SimLink>
}

/** Freeze resolved detail outside the viewport and make force work zero-cost for
 * those nodes. The renderer separately culls their pixels and incident links. */
export function updateSimulationViewport(
  simulation: Simulation<SimNode, SimLink>,
  nodes: SimNode[],
  links: SimLink[],
  viewport: Viewport,
  size: { h: number; w: number },
  padding = 80
): number {
  const activeNodes: SimNode[] = []
  const activeIds = new Set<string>()
  let activeCount = 0
  let changed = false

  for (const node of nodes) {
    const x = node.x * viewport.k + viewport.x
    const y = node.y * viewport.k * TILT + viewport.y
    const active = x >= -padding && x <= size.w + padding && y >= -padding && y <= size.h + padding

    if (node.lodActive !== active) {
      changed = true
      node.lodActive = active
    }

    if (active) {
      activeCount += 1
      activeNodes.push(node)
      activeIds.add(node.id)
      node.fx = null
      node.fy = null
    } else {
      node.fx = node.x
      node.fy = node.y
      node.vx = 0
      node.vy = 0
    }
  }

  if (changed) {
    const linkForce = simulation.force('link') as ForceLink<SimNode, SimLink> | undefined
    linkForce?.links([])
    simulation.nodes(activeNodes)
    linkForce?.links(
      links.filter(link => {
        const source = typeof link.source === 'object' ? link.source.id : String(link.source)
        const target = typeof link.target === 'object' ? link.target.id : String(link.target)

        return activeIds.has(source) && activeIds.has(target)
      })
    )
    simulation.alpha(0.18).restart()
  }

  return activeCount
}

const DAY = 86_400

// Roughly how many nodes share one ignite burst within a ring band — the build
// reads as clustered pops, not a 1-by-1 trickle or an all-at-once flood.
const CLUSTER_SIZE = 5

// Constant ring SCALE: the core radius and the per-ring band are pinned to the
// canonical 5-ring layout, so the empty core and every band are ALWAYS that
// size on the disk — more data grows the disk OUTWARD (more rings) instead of
// stretching a fixed disk thinner. The camera caps its zoom at the 5-ring
// extent (see fitViewport), so this world size is also a constant screen size.
const RING_CORE = radiusForRecency(recForRatio(0))
const RING_BAND = (radiusForRecency(recForRatio(1)) - RING_CORE) / RING_STEPS
const ringRadius = (i: number): number => RING_CORE + i * RING_BAND

// Place a node INSIDE its ring's band (the annulus the ring caps), biased toward
// mid-band so it reads as "within the ring", only occasionally grazing an edge —
// never sitting on the outline like a bead.
const placeRadius = (i: number, id: string): number => {
  const outer = ringRadius(i)
  const inner = i > 0 ? ringRadius(i - 1) : RING_CORE - RING_BAND * 0.5
  const h = (hash(id) % 1000) / 1000

  return outer - (0.15 + 0.7 * h) * (outer - inner)
}

interface Unit {
  kind: 'day' | 'month'
  step: number
}

// "Nice" calendar intervals, fine → coarse, WITH intermediate rungs (2-day,
// bi-weekly, 2/3/6-month) so the bucketer can land NEAR the target count instead
// of jumping straight from weekly (≈9) to monthly (≈3) and missing the ~5 sweet spot.
const UNITS: Unit[] = [
  { kind: 'day', step: 1 },
  { kind: 'day', step: 2 },
  { kind: 'day', step: 7 },
  { kind: 'day', step: 14 },
  { kind: 'month', step: 1 },
  { kind: 'month', step: 2 },
  { kind: 'month', step: 3 },
  { kind: 'month', step: 6 },
  { kind: 'month', step: 12 }
]

// Floor a timestamp to the start of its calendar bucket — the key nodes group by.
function bucketStart(ts: number, { kind, step }: Unit): number {
  if (kind === 'day') {
    const period = step * DAY

    return Math.floor(ts / period) * period
  }

  const d = new Date(ts * 1000)
  d.setUTCHours(0, 0, 0, 0)
  // Floor to a step-month boundary in ABSOLUTE months so steps align across
  // years (3-month → Jan/Apr/Jul/Oct, 12-month → Jan).
  const absMonth = Math.floor((d.getUTCFullYear() * 12 + d.getUTCMonth()) / step) * step
  d.setUTCFullYear(Math.floor(absMonth / 12), absMonth % 12, 1)

  return Math.floor(d.getTime() / 1000)
}

const populatedStarts = (stamps: number[], u: Unit): number[] =>
  [...new Set(stamps.map(t => bucketStart(t, u)))].sort((a, b) => a - b)

// "Nice ticks" for time (à la D3/Heckbert): aim for a target ring count that
// grows ~log2 with the span, then snap to the calendar interval whose POPULATED
// count lands nearest it (ties + overshoot break toward fewer/finer). The floor
// is 5 — fewer than that and the play-through "steps" between rings get big and
// abrupt; ~5+ evenly-paced rings give the smooth Spore-style build-up.
function chooseUnit(stamps: number[], spanDays: number): Unit {
  const target = clamp(Math.round(4 + Math.log2(Math.max(1, spanDays / 60))), 5, 12)
  let best = UNITS[0]!
  let bestScore = Infinity

  for (const u of UNITS) {
    const count = populatedStarts(stamps, u).length

    if (!count) {
      continue
    }

    const score = Math.abs(count - target) + (count > target ? 0.5 : 0)

    if (score < bestScore) {
      bestScore = score
      best = u
    }
  }

  return best
}

function bucketLabel(ts: number, { kind, step }: Unit): string {
  if (kind === 'day') {
    return formatDate(ts)
  }

  try {
    const d = new Date(ts * 1000)

    return step >= 12
      ? String(d.getUTCFullYear())
      : d.toLocaleDateString(undefined, { month: 'short', timeZone: 'UTC', year: 'numeric' })
  } catch {
    return formatDate(ts)
  }
}

interface Layout {
  // bucket/cap ring a node belongs to (the ring it ignites behind)
  index: (n: StarmapNode) => number
  // reveal coordinate (0–1) a node ignites at — staggered within its band
  rec: (n: StarmapNode) => number
  rings: Ring[]
  // world radius a node is drawn at (inside its band)
  tr: (n: StarmapNode) => number
}

// Even, unlabeled-ish fallback when there's no usable time span (undated graph
// or one instant): keep the legacy continuous mapping so nothing regresses.
function evenLayout(recById: Map<string, number>, minTs: null | number, maxTs: null | number, timed: boolean): Layout {
  const rings: Ring[] = Array.from({ length: RING_STEPS + 1 }, (_, i) => ({
    label:
      timed && minTs !== null && maxTs !== null
        ? formatDate(Math.round(minTs + (maxTs - minTs) * (i / RING_STEPS)))
        : null,
    r: ringRadius(i),
    ratio: recForRatio(i / RING_STEPS)
  }))

  const capRing = (rec: number): number => {
    for (let i = 0; i < rings.length; i += 1) {
      if ((rings[i]?.ratio ?? 1) >= rec - 1e-3) {
        return i
      }
    }

    return rings.length - 1
  }

  return {
    index: n => capRing(recById.get(n.id) ?? 0),
    rec: n => recById.get(n.id) ?? 0,
    rings,
    tr: n => radiusForRecency(recById.get(n.id) ?? 0)
  }
}

// One equal-width ring per POPULATED calendar bucket; a bucket's nodes fill the
// band INSIDE their ring (fanned by angle) and ignite staggered across it.
function buildLayout(
  graph: StarmapGraph,
  recById: Map<string, number>,
  minTs: null | number,
  maxTs: null | number,
  timed: boolean
): Layout {
  const stamps = graph.nodes.map(n => Number(n.timestamp)).filter(Number.isFinite)

  if (!(timed && minTs !== null && maxTs !== null && maxTs > minTs && stamps.length)) {
    return evenLayout(recById, minTs, maxTs, timed)
  }

  const span = maxTs - minTs
  const unit = chooseUnit(stamps, span / DAY)
  const starts = populatedStarts(stamps, unit)

  if (starts.length < 2) {
    return evenLayout(recById, minTs, maxTs, timed)
  }

  const indexOfStart = new Map(starts.map((s, i) => [s, i]))
  // Reveal pacing is per-BUCKET (uniform), matching the equal-width bands: each
  // ring is one even step. (Radius is already index-based.) Using raw time here
  // decouples a ring's ignite moment from its position — a bursty gap makes a
  // ring appear bands ahead of the nodes that belong to it. Labels stay real dates.
  const last = Math.max(1, starts.length - 1)

  const rings: Ring[] = starts.map((s, i) => ({
    label: bucketLabel(s, unit),
    r: ringRadius(i),
    ratio: recForRatio(i / last)
  }))

  // A node's bucket is its ring; undated nodes (rare, in an otherwise-timed
  // graph) fall to the newest ring so they still appear.
  const indexFor = (n: StarmapNode): number => {
    const ts = Number(n.timestamp)

    return Number.isFinite(ts) ? (indexOfStart.get(bucketStart(ts, unit)) ?? starts.length - 1) : starts.length - 1
  }

  // Node POSITION fills the band inside its bucket ring (placeRadius); its IGNITE
  // time is staggered ACROSS that band, ordered by real timestamp, so a busy
  // bucket trickles in over its whole slice instead of every node popping at
  // once (the "everything floods in at the end" bug).
  const buckets: StarmapNode[][] = starts.map(() => [])

  for (const n of graph.nodes) {
    buckets[indexFor(n)]!.push(n)
  }

  const tsOf = (n: StarmapNode): number => (Number.isFinite(Number(n.timestamp)) ? Number(n.timestamp) : Infinity)
  const recByNode = new Map<string, number>()

  buckets.forEach((bucket, i) => {
    bucket.sort((a, b) => (tsOf(a) === tsOf(b) ? a.id.localeCompare(b.id) : tsOf(a) - tsOf(b)))

    const hi = rings[i]!.ratio
    const lo = i > 0 ? rings[i - 1]!.ratio : 0
    const m = bucket.length

    // Ignite in CLUSTERS, not a 1-by-1 trickle: split the band's (time-ordered)
    // nodes into a few sub-bursts (~CLUSTER_SIZE each) that share an ignite
    // moment, spaced across the band, with a hair of per-node jitter so a burst
    // reads as organic rather than perfectly synchronous.
    const clusters = Math.max(1, Math.round(m / CLUSTER_SIZE))

    bucket.forEach((n, k) => {
      const c = Math.min(clusters - 1, Math.floor((k / m) * clusters))
      const jitter = ((hash(n.id) % 100) / 100 - 0.5) * (0.5 / clusters)
      const f = clamp((c + 1) / clusters + jitter, 0.02, 1)
      recByNode.set(n.id, lo + f * (hi - lo))
    })
  })

  return {
    index: indexFor,
    rec: n => recByNode.get(n.id) ?? 0,
    rings,
    tr: n => placeRadius(indexFor(n), n.id)
  }
}

const NEBULA_WELLS = [
  [-0.68, -0.48],
  [0.64, -0.54],
  [-0.58, 0.54],
  [0.58, 0.5],
  [0, -0.02],
  [-0.84, 0.02],
  [0.84, 0.05],
  [0.02, -0.74],
  [-0.02, 0.72],
  [-0.32, -0.2],
  [0.32, 0.23],
  [-0.3, 0.25],
  [0.34, -0.24]
] as const

/** Stable category-biased well assignment. There is no owner-to-well mapping,
 * so workers interleave spatially instead of claiming regions. */
export function nebulaWellIndex(node: Pick<StarmapNode, 'category' | 'id'>, wellCount: number): number {
  const count = Math.max(1, wellCount)
  const neighborhood = hash(node.category || 'general') % count
  const variation = hash(`${node.id}:nebula-variant`) % count

  return (neighborhood + variation) % count
}

function buildNebulaSimulation(graph: StarmapGraph, onTick: () => void, size: { h: number; w: number }): BuiltSim {
  const { rec } = computeRecency(graph.nodes)
  const targets = new Map<string, { x: number; y: number }>()

  const wellCount = Math.min(NEBULA_WELLS.length, Math.max(5, Math.ceil(graph.nodes.length / 70)))
  const halfWidth = Math.max(190, (size.w - 150) / 2)
  const halfHeight = Math.max(140, (size.h - 150) / (2 * TILT))
  const wellRadius = clamp(Math.min(size.w, size.h) * 0.085, 24, 68)

  for (const node of graph.nodes) {
    const well = NEBULA_WELLS[nebulaWellIndex(node, wellCount)] ?? NEBULA_WELLS[0]
    const angle = ((hash(`${node.id}:nebula-angle`) % 10_000) / 10_000) * Math.PI * 2
    const distance = Math.sqrt((hash(`${node.id}:nebula-radius`) % 10_000) / 10_000) * wellRadius

    targets.set(node.id, {
      x: well[0] * halfWidth + Math.cos(angle) * distance,
      y: well[1] * halfHeight + Math.sin(angle) * distance
    })
  }

  const nodes: SimNode[] = graph.nodes.map(node => {
    const target = targets.get(node.id) ?? { x: 0, y: 0 }

    return {
      ...node,
      outerRingIndex: 0,
      rec: rec.get(node.id) ?? 1,
      tr: Math.hypot(target.x, target.y),
      vx: 0,
      vy: 0,
      x: target.x,
      y: target.y
    }
  })

  const byId = new Map(nodes.map(node => [node.id, node]))

  const links: SimLink[] = graph.edges
    .filter(edge => byId.has(edge.source) && byId.has(edge.target))
    .map(edge => ({ ...edge, source: edge.source, target: edge.target }))

  const sim = forceSimulation(nodes)
    .alphaDecay(0.045)
    .velocityDecay(0.66)
    .force(
      'charge',
      forceManyBody<SimNode>().strength(node => (node.lodActive === false ? 0 : -7))
    )
    .force(
      'link',
      forceLink<SimNode, SimLink>(links)
        .id(node => node.id)
        .distance(22)
        .strength(0.035)
    )
    .force(
      'collide',
      forceCollide<SimNode>()
        .radius(node => (node.lodActive === false ? 0 : nodeRadius(node) + 2.5))
        .iterations(2)
    )
    .force('nebula-x', forceX<SimNode>(node => targets.get(node.id)?.x ?? 0).strength(0.2))
    .force('nebula-y', forceY<SimNode>(node => targets.get(node.id)?.y ?? 0).strength(0.2))
    .on('tick', onTick)

  return {
    byId,
    links,
    nodes,
    rings: [{ label: null, r: Math.max(halfWidth, halfHeight), ratio: 1 }],
    sim
  }
}

// Build the radial time simulation: a node's distance from the core encodes its
// timestamp bucket (radial force dominates; charge/collide only spread nodes
// around their date ring). Rings are dated, equal-width gridlines.
export function buildSimulation(
  graph: StarmapGraph,
  onTick: () => void,
  nebula = false,
  size: { h: number; w: number } = { h: 700, w: 1_000 }
): BuiltSim {
  if (nebula) {
    return buildNebulaSimulation(graph, onTick, size)
  }

  const { maxTs, minTs, rec: recById, timed } = computeRecency(graph.nodes)
  const { index, rec: recOf, rings, tr: trOf } = buildLayout(graph, recById, minTs, maxTs, timed)

  // Cortex domains occupy stable angular neighborhoods. Time still controls a
  // node's distance from the core, while domain controls the direction it grows
  // toward. That keeps the temporal story without collapsing a 400-node graph
  // into a random hairball.
  const domains = [...new Set(graph.nodes.map(node => node.category || 'general'))].sort()

  const domainAngle = new Map(
    domains.map((domain, index) => [domain, (index / Math.max(1, domains.length)) * Math.PI * 2 - Math.PI / 2])
  )

  const targetAngle = new Map<string, number>()

  const nodes: SimNode[] = graph.nodes.map(n => {
    const rec = recOf(n)
    const tr = trOf(n)
    const center = domainAngle.get(n.category || 'general') ?? 0
    const sector = domains.length > 1 ? Math.min(1.25, (Math.PI * 2 * 0.68) / domains.length) : Math.PI * 2
    const jitter = ((hash(`${n.id}:domain`) % 10_000) / 10_000 - 0.5) * sector
    const angle = center + jitter
    targetAngle.set(n.id, angle)

    return { ...n, outerRingIndex: index(n), rec, tr, vx: 0, vy: 0, x: Math.cos(angle) * tr, y: Math.sin(angle) * tr }
  })

  const byId = new Map(nodes.map(n => [n.id, n]))

  const links: SimLink[] = graph.edges
    .filter(e => byId.has(e.source) && byId.has(e.target))
    .map(e => ({ ...e, source: e.source, target: e.target }))

  const sim = forceSimulation(nodes)
    .alphaDecay(0.05)
    .velocityDecay(0.62)
    .force(
      'charge',
      forceManyBody<SimNode>().strength(node => (node.lodActive === false ? 0 : -18))
    )
    .force(
      'link',
      forceLink<SimNode, SimLink>(links)
        .id(n => n.id)
        .distance(26)
        .strength(0.025)
    )
    .force(
      'collide',
      forceCollide<SimNode>()
        .radius(n => (n.lodActive === false ? 0 : nodeRadius(n) + 4))
        .iterations(2)
    )
    .force('radial', forceRadial<SimNode>(n => (n as SimNode).tr, 0, 0).strength(0.92))
    .force(
      'domain-x',
      forceX<SimNode>(n => Math.cos(targetAngle.get(n.id) ?? 0) * n.tr).strength(graph.source === 'cortex' ? 0.09 : 0)
    )
    .force(
      'domain-y',
      forceY<SimNode>(n => Math.sin(targetAngle.get(n.id) ?? 0) * n.tr).strength(graph.source === 'cortex' ? 0.09 : 0)
    )
    .on('tick', onTick)

  return { byId, links, nodes, rings, sim }
}
