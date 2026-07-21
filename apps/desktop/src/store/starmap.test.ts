import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { CortexGraphResponse, CortexHealthResponse, ProfileInfo, StarmapGraph } from '@/types/hermes'

const getCortexGraph = vi.fn<(limit?: number, profile?: null | string) => Promise<CortexGraphResponse>>()
const getCortexHealth = vi.fn<(profile?: null | string) => Promise<CortexHealthResponse>>()
const getCortexDream = vi.fn()
const getStarmapGraph = vi.fn<() => Promise<StarmapGraph>>()
const runCortexDream = vi.fn()

vi.mock('@/hermes', () => ({ getCortexDream, getCortexGraph, getCortexHealth, getStarmapGraph, runCortexDream }))

const store = await import('./starmap')

function cortex(label: string): CortexGraphResponse {
  return {
    communities: [],
    edges: [],
    facets: { domains: [], statuses: [], types: [] },
    generated_at: '2026-07-14T00:00:00Z',
    layout_seed: 'seed',
    next_cursor: null,
    nodes: [
      {
        badges: [],
        community: null,
        created_at: '2026-07-14T00:00:00Z',
        degree: 0,
        domain: 'personal',
        id: label,
        label,
        metadata: {},
        privacy: 'private',
        status: 'active',
        summary: label,
        type: 'memory',
        updated_at: '2026-07-14T00:00:00Z',
        usage: 0
      }
    ],
    projection: 'growth',
    redaction_summary: { document_bodies_hidden: 0, nodes_omitted_by_limit: 0, raw_evidence_bodies_hidden: 0 },
    retrieval_run_id: null,
    timeline_window: { end: null, start: null },
    version: 'atlas.cortex.graph.v1'
  }
}

const legacy = (label: string): StarmapGraph => ({
  clusters: [],
  edges: [],
  memory: [],
  nodes: [
    {
      category: 'legacy',
      createdBy: null,
      id: label,
      kind: 'skill',
      label,
      pinned: false,
      state: 'active',
      useCount: 0
    }
  ],
  stats: {}
})

function profile(name: string, isDefault = false): ProfileInfo {
  return {
    display_name: isDefault ? 'Atlas' : name,
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

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void

  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })

  return { promise, reject, resolve }
}

beforeEach(() => {
  store.resetStarmapGraph()
  vi.clearAllMocks()
  getCortexHealth.mockRejectedValue(new Error('health unavailable'))
})

describe('starmap Cortex loading', () => {
  it('prefers the native Cortex projection', async () => {
    getCortexGraph.mockResolvedValue(cortex('native'))

    await store.loadStarmapGraph()

    expect(store.$starmapGraph.get()?.source).toBe('cortex')
    expect(store.$starmapGraph.get()?.nodes[0]?.label).toBe('native')
    expect(getCortexGraph).toHaveBeenCalledWith(500, 'default')
    expect(getCortexHealth).toHaveBeenCalledWith('default')
    expect(getStarmapGraph).not.toHaveBeenCalled()
  })

  it('falls back to the untouched learning graph for older backends', async () => {
    getCortexGraph.mockRejectedValue(Object.assign(new Error('404: not found'), { statusCode: 404 }))
    getStarmapGraph.mockResolvedValue(legacy('legacy'))

    await store.loadStarmapGraph()

    expect(store.$starmapGraph.get()?.source).toBe('legacy')
    expect(store.$starmapGraph.get()?.nodes[0]?.label).toBe('legacy')
  })

  it('does not mask Cortex authorization or server failures with legacy data', async () => {
    getCortexGraph.mockRejectedValue(Object.assign(new Error('500: failed'), { statusCode: 500 }))

    await store.loadStarmapGraph()

    expect(store.$starmapGraph.get()).toBeNull()
    expect(store.$starmapError.get()).toContain('500')
    expect(getStarmapGraph).not.toHaveBeenCalled()
  })

  it('reloads the graph when a maintenance job finishes', async () => {
    getCortexGraph.mockResolvedValueOnce(cortex('before')).mockResolvedValueOnce(cortex('after'))
    getCortexDream.mockResolvedValue({
      job: { status: 'succeeded' },
      version: 'atlas.cortex.job.v1'
    })
    getCortexHealth.mockResolvedValue({} as CortexHealthResponse)

    await store.loadStarmapGraph()
    await store.refreshCortexDreamStatus('job-1')

    expect(store.$starmapGraph.get()?.nodes[0]?.label).toBe('after')
    expect(getCortexGraph).toHaveBeenCalledTimes(2)
  })

  it('prevents a slow previous profile from overwriting the new profile', async () => {
    const oldProfile = deferred<CortexGraphResponse>()
    getCortexGraph.mockReturnValueOnce(oldProfile.promise)
    const oldLoad = store.loadStarmapGraph()

    store.resetStarmapGraph()
    getCortexGraph.mockResolvedValueOnce(cortex('new-profile'))
    await store.loadStarmapGraph()

    oldProfile.resolve(cortex('old-profile'))
    await oldLoad

    expect(store.$starmapGraph.get()?.nodes[0]?.label).toBe('new-profile')
  })

  it('selects an independent worker brain and never masks it with legacy data', async () => {
    getCortexGraph.mockResolvedValueOnce(cortex('worker-memory'))

    await store.selectStarmapBrain('worker_graph')

    expect(store.$starmapBrainProfile.get()).toBe('worker_graph')
    expect(getCortexGraph).toHaveBeenCalledWith(500, 'worker_graph')
    expect(getCortexHealth).toHaveBeenCalledWith('worker_graph')
    expect(store.$starmapGraph.get()?.nodes[0]?.label).toBe('worker-memory')
    expect(getStarmapGraph).not.toHaveBeenCalled()

    getCortexGraph.mockRejectedValueOnce(Object.assign(new Error('404: not found'), { statusCode: 404 }))
    await store.selectStarmapBrain('empty_worker')

    expect(store.$starmapError.get()).toContain('404')
    expect(getStarmapGraph).not.toHaveBeenCalled()
  })

  it('keeps Cortex-disabled brains explicit instead of falling back to legacy data', async () => {
    getCortexGraph.mockRejectedValueOnce(
      Object.assign(new Error('503: Atlas Cortex is disabled for this profile'), { statusCode: 503 })
    )

    await store.selectStarmapBrain('default')

    expect(store.$starmapBrainStatus.get()).toBe('disabled')
    expect(store.$starmapGraph.get()).toBeNull()
    expect(getStarmapGraph).not.toHaveBeenCalled()
  })

  it('prevents a slow previous brain from overwriting the selected brain', async () => {
    const oldBrain = deferred<CortexGraphResponse>()
    getCortexGraph.mockReturnValueOnce(oldBrain.promise)
    const oldLoad = store.selectStarmapBrain('slow-worker')

    getCortexGraph.mockResolvedValueOnce(cortex('current-worker'))
    await store.selectStarmapBrain('current-worker')

    oldBrain.resolve(cortex('slow-worker'))
    await oldLoad

    expect(store.$starmapBrainProfile.get()).toBe('current-worker')
    expect(store.$starmapGraph.get()?.nodes[0]?.label).toBe('current-worker')
  })

  it('loads every brain independently with a proportional overview budget', async () => {
    const profiles = [profile('default', true), profile('parts'), profile('service'), profile('sales')]
    getCortexGraph.mockImplementation(async (_limit, brain) => {
      if (brain === 'service') {
        throw Object.assign(new Error('503: Atlas Cortex is disabled for this profile'), { statusCode: 503 })
      }

      return brain === 'sales' ? { ...cortex('sales'), nodes: [] } : cortex(String(brain))
    })

    await store.showStarmapConstellation(profiles)

    expect(getCortexGraph).toHaveBeenCalledTimes(4)
    expect(getCortexGraph.mock.calls.every(([limit]) => limit === 105)).toBe(true)
    expect(store.$starmapConstellation.get()?.partitions.map(partition => partition.status)).toEqual([
      'ready',
      'ready',
      'empty',
      'disabled'
    ])
    expect(store.$starmapConstellation.get()?.graph.nodes).toHaveLength(2)
    expect(store.$starmapMode.get()).toBe('constellation')
  })

  it('transitions from constellation to an isolated brain and back without shrinking the isolated limit', async () => {
    const profiles = [profile('default', true), profile('service')]
    getCortexGraph.mockImplementation(async (_limit, brain) => cortex(String(brain)))

    await store.showStarmapConstellation(profiles)
    await store.selectStarmapBrain('service')

    expect(store.$starmapMode.get()).toBe('brain')
    expect(store.$starmapBrainProfile.get()).toBe('service')
    expect(getCortexGraph).toHaveBeenLastCalledWith(500, 'service')

    await store.showStarmapConstellation(profiles)

    expect(store.$starmapMode.get()).toBe('constellation')
    expect(store.$starmapBrainProfile.get()).toBe('default')
  })
})
