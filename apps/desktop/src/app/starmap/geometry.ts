import type { StarmapNode } from '@/types/hermes'

import {
  AGE_GRADIENT,
  CORTEX_NODE_VISUALS,
  FIT_PADDING,
  RING_INNER,
  RING_OUTER,
  TILT,
  ZOOM_MAX,
  ZOOM_MIN
} from './constants'
import { aggregateStarRadius } from './lod'
import type { Ring, Shape, Viewport } from './types'

export function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v))
}

// FNV-1a — stable per-id seed for layout angle / starfield.
export function hash(input: string): number {
  let h = 2166136261

  for (let i = 0; i < input.length; i += 1) {
    h ^= input.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }

  return h >>> 0
}

export function ambientNodePosition(
  node: Pick<StarmapNode, 'id'> & { x: number; y: number },
  time: null | number
): { x: number; y: number } {
  if (time === null) {
    return node
  }

  const phase = ((hash(`${node.id}:nebula-motion`) % 10_000) / 10_000) * Math.PI * 2

  return {
    x: node.x + Math.cos(time * 0.00016 + phase) * 1.35,
    y: node.y + Math.sin(time * 0.00013 + phase * 1.37) * 1.1
  }
}

export function ambientNodeTwinkle(node: Pick<StarmapNode, 'aggregate' | 'id'>, time: null | number): number {
  if (time === null) {
    return 1
  }

  const phase = ((hash(`${node.id}:nebula-twinkle`) % 10_000) / 10_000) * Math.PI * 2
  const wave = 0.5 + Math.sin(time * 0.0011 + phase) * 0.5

  return node.aggregate ? 0.9 + wave * 0.1 : 0.68 + wave * 0.32
}

export function nodeRadius(n: StarmapNode): number {
  if (n.aggregate) {
    return aggregateStarRadius(n.aggregate.count, n.aggregate.kind)
  }

  if (n.cortexType) {
    const visual = CORTEX_NODE_VISUALS[n.cortexType]
    const activity = Math.min(1, Math.sqrt(Math.max(0, n.useCount)) * 0.12)

    return visual.radius + activity + (n.pinned ? 0.6 : 0)
  }

  const base = n.state === 'archived' || n.state === 'stale' ? 2.4 : 3

  return base + Math.sqrt(Math.max(0, n.useCount)) * 0.55 + (n.pinned ? 0.8 : 0)
}

// Smoothstep recency → ink alpha along the age gradient.
export function recencyInk(rec: number): number {
  const reach = Math.max(0.01, AGE_GRADIENT.reach)
  const mid = clamp(AGE_GRADIENT.mid, 0.01, 0.99)
  const t = clamp(rec / reach, 0, 1)

  if (t <= mid) {
    const p = t / mid

    return AGE_GRADIENT.oldInk + (AGE_GRADIENT.midInk - AGE_GRADIENT.oldInk) * (p * p * (3 - 2 * p))
  }

  const p = (t - mid) / (1 - mid)

  return AGE_GRADIENT.midInk + (AGE_GRADIENT.newInk - AGE_GRADIENT.midInk) * (p * p * (3 - 2 * p))
}

// Trace a centred geometric shape of radius r into the current path.
export function shapePath(ctx: CanvasRenderingContext2D, shape: Shape, x: number, y: number, r: number): void {
  ctx.beginPath()

  if (shape === 'square') {
    ctx.rect(x - r, y - r, r * 2, r * 2)

    return
  }

  if (shape === 'circle') {
    ctx.arc(x, y, r, 0, Math.PI * 2)

    return
  }

  if (shape === 'star') {
    for (let i = 0; i < 10; i += 1) {
      const a = -Math.PI / 2 + (i / 10) * Math.PI * 2
      const pointRadius = i % 2 === 0 ? r : r * 0.46
      const px = x + Math.cos(a) * pointRadius
      const py = y + Math.sin(a) * pointRadius

      if (i === 0) {
        ctx.moveTo(px, py)
      } else {
        ctx.lineTo(px, py)
      }
    }

    ctx.closePath()

    return
  }

  const pts = shape === 'diamond' ? 4 : shape === 'triangle' ? 3 : 6
  // Diamond/triangle point up; hexagon is flat-topped.
  const rot = shape === 'hexagon' ? Math.PI / 6 : -Math.PI / 2

  for (let i = 0; i < pts; i += 1) {
    const a = rot + (i / pts) * Math.PI * 2
    const px = x + Math.cos(a) * r
    const py = y + Math.sin(a) * r

    if (i === 0) {
      ctx.moveTo(px, py)
    } else {
      ctx.lineTo(px, py)
    }
  }

  ctx.closePath()
}

export interface FitViewportOptions {
  /** Keep the entire requested radius in frame instead of preserving the legacy minimum scale. */
  contain?: boolean
  padding?: number
}

// Center the tilted disk in the viewport at a fit zoom. `outer` is the radius to
// fit (defaults to the full disk); the scrubber passes the revealed extent so the
// camera tightens at the core and zooms out as the rings grow.
export function fitViewport(
  w: number,
  h: number,
  outer: number = RING_OUTER,
  options: FitViewportOptions = {}
): Viewport {
  if (w <= 0 || h <= 0) {
    return { k: 1, x: w / 2, y: h / 2 }
  }

  const padding = options.padding ?? FIT_PADDING

  // Fit zoom for a disk of radius r into this viewport (capped at 2.2× zoom-in).
  const kFor = (r: number): number => {
    const spanX = (r + 30) * 2

    return Math.min((w - padding * 2) / spanX, (h - padding * 2) / (spanX * TILT), 2.2)
  }

  // Legacy maps keep their historic minimum scale and remain pannable. Cortex
  // uses `contain`, allowing a growing brain to zoom out just enough that every
  // ring remains visible and centered.
  const k = clamp(options.contain ? kFor(outer) : Math.max(kFor(outer), kFor(RING_OUTER)), ZOOM_MIN, ZOOM_MAX)

  return { k, x: w / 2, y: h / 2 }
}

export function centerViewportOn(w: number, h: number, worldX: number, worldY: number, k: number): Viewport {
  return { k, x: w / 2 - worldX * k, y: h / 2 - worldY * k * TILT }
}

// Change zoom while keeping the world point beneath a screen-space anchor in
// exactly the same place. This is the natural "zoom into what I'm pointing at"
// camera used by Cortex when moving deeper into the graph.
export function zoomViewportAt(viewport: Viewport, screenX: number, screenY: number, k: number): Viewport {
  const worldX = (screenX - viewport.x) / viewport.k
  const worldY = (screenY - viewport.y) / (viewport.k * TILT)

  return { k, x: screenX - worldX * k, y: screenY - worldY * k * TILT }
}

export interface StagedZoomOutResult {
  stage: 'cluster' | 'detail' | 'overview'
  viewport: Viewport
}

// Cortex zoom-out has three semantic bands. At detail scale the pointer remains
// the subject. Across cluster scale the camera progressively yields to the graph
// origin. Only at the full-fit floor does it lock precisely to overview. This
// avoids the disorienting first-wheel-tick snap of a binary camera mode.
export function stagedZoomOutViewport(
  viewport: Viewport,
  screenX: number,
  screenY: number,
  w: number,
  h: number,
  k: number,
  fitK: number
): StagedZoomOutResult {
  const safeFit = Math.max(fitK, Number.EPSILON)
  const ratio = k / safeFit
  const local = zoomViewportAt(viewport, screenX, screenY, k)
  const centered = centerViewportOn(w, h, 0, 0, k)

  if (ratio <= 1.04) {
    return { stage: 'overview', viewport: centered }
  }

  const detailRelease = 2.7
  const overviewApproach = 1.04
  const progress = clamp((detailRelease - ratio) / (detailRelease - overviewApproach), 0, 1)

  if (progress === 0) {
    return { stage: 'detail', viewport: local }
  }

  const eased = progress * progress * (3 - 2 * progress)
  // Pull is deliberately weak at cluster entry and authoritative near overview:
  // the camera arcs home over several gestures instead of switching anchors.
  const fullStepPull = eased * (0.1 + 0.55 * eased)
  // Normalize the pull to the actual zoom delta. A mouse-wheel notch gets the
  // full step; a stream of tiny trackpad deltas composes to the same movement
  // over time instead of applying the full recenter on every microscopic event.
  const gesture = clamp(Math.abs(Math.log(k / viewport.k)) / 0.106, 0, 1)
  const pull = 1 - (1 - fullStepPull) ** gesture

  return {
    stage: 'cluster',
    viewport: {
      k,
      x: local.x + (centered.x - local.x) * pull,
      y: local.y + (centered.y - local.y) * pull
    }
  }
}

// Target radius for a node at recency `rec` (oldest at the core), scaled to a
// disk of the given outer radius.
export function radiusForRecency(rec: number, outer: number = RING_OUTER): number {
  return RING_INNER + rec * (outer - RING_INNER)
}

// Screen-space scale at the graph's fully-rested fit. Nodes size against THIS,
// not the live (playback) camera — so a spore-zoom moves WHERE they sit, not how
// big they read (billboarded), while a full-map view keeps its honest density.
export const fitScale = (w: number, h: number, rings: Ring[], options?: FitViewportOptions): number =>
  fitViewport(w, h, rings.at(-1)?.r ?? RING_OUTER, options).k

// Squared distance from point (px,py) to segment a→b — for cheap link hit-tests.
export function distToSegmentSq(px: number, py: number, ax: number, ay: number, bx: number, by: number): number {
  const dx = bx - ax
  const dy = by - ay
  const len = dx * dx + dy * dy
  const t = len ? clamp(((px - ax) * dx + (py - ay) * dy) / len, 0, 1) : 0
  const cx = ax + dx * t
  const cy = ay + dy * t

  return (px - cx) ** 2 + (py - cy) ** 2
}
