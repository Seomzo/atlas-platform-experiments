import { describe, expect, it } from 'vitest'

import { centerViewportOn, fitViewport, stagedZoomOutViewport, zoomViewportAt } from './geometry'

describe('Cortex camera geometry', () => {
  it('contains a growing graph instead of preserving the legacy minimum scale', () => {
    const legacy = fitViewport(760, 540, 520)
    const contained = fitViewport(760, 540, 520, { contain: true, padding: 64 })

    expect(contained.k).toBeLessThan(legacy.k)
    expect(contained.x).toBe(380)
    expect(contained.y).toBe(270)
  })

  it('centers a selected world point at any zoom', () => {
    const viewport = centerViewportOn(800, 600, 125, -80, 1.4)

    expect(125 * viewport.k + viewport.x).toBeCloseTo(400)
    expect(-80 * viewport.k + viewport.y).toBeCloseTo(300)
  })

  it('keeps the world point under the cursor fixed while zooming in', () => {
    const initial = { k: 0.8, x: 300, y: 220 }
    const anchor = { x: 175, y: 410 }
    const worldX = (anchor.x - initial.x) / initial.k
    const worldY = (anchor.y - initial.y) / initial.k
    const zoomed = zoomViewportAt(initial, anchor.x, anchor.y, 1.6)

    expect(worldX * zoomed.k + zoomed.x).toBeCloseTo(anchor.x)
    expect(worldY * zoomed.k + zoomed.y).toBeCloseTo(anchor.y)
  })

  it('preserves local focus before a zoom-out reaches cluster scale', () => {
    const initial = { k: 2.8, x: -420, y: 180 }
    const result = stagedZoomOutViewport(initial, 640, 420, 900, 600, 2.5, 0.8)
    const local = zoomViewportAt(initial, 640, 420, 2.5)

    expect(result.stage).toBe('detail')
    expect(result.viewport).toEqual(local)
  })

  it('eases toward center through cluster scale without snapping there', () => {
    const initial = { k: 1.4, x: -500, y: 260 }
    const result = stagedZoomOutViewport(initial, 700, 420, 900, 600, 1.1, 0.5)
    const local = zoomViewportAt(initial, 700, 420, 1.1)
    const centered = centerViewportOn(900, 600, 0, 0, 1.1)

    expect(result.stage).toBe('cluster')
    expect(Math.abs(result.viewport.x - centered.x)).toBeLessThan(Math.abs(local.x - centered.x))
    expect(Math.abs(result.viewport.y - centered.y)).toBeLessThan(Math.abs(local.y - centered.y))
    expect(result.viewport).not.toEqual(centered)
  })

  it('locks to the centered overview only at the full-fit floor', () => {
    const result = stagedZoomOutViewport({ k: 0.6, x: -240, y: 510 }, 120, 540, 900, 600, 0.51, 0.5)

    expect(result.stage).toBe('overview')
    expect(result.viewport).toEqual(centerViewportOn(900, 600, 0, 0, 0.51))
  })

  it('scales cinematic recentering to the size of a trackpad gesture', () => {
    const initial = { k: 1, x: -420, y: 250 }
    const tiny = stagedZoomOutViewport(initial, 700, 420, 900, 600, 0.997, 0.5)
    const notch = stagedZoomOutViewport(initial, 700, 420, 900, 600, 0.9, 0.5)
    const tinyLocal = zoomViewportAt(initial, 700, 420, 0.997)
    const notchLocal = zoomViewportAt(initial, 700, 420, 0.9)

    expect(tiny.stage).toBe('cluster')
    expect(notch.stage).toBe('cluster')
    expect(Math.abs(tiny.viewport.x - tinyLocal.x)).toBeLessThan(Math.abs(notch.viewport.x - notchLocal.x))
  })
})
