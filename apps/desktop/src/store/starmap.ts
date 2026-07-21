import { atom } from 'nanostores'

import {
  buildConstellationScene,
  type ConstellationBrainInput,
  type ConstellationBrainStatus,
  constellationNodeBudget,
  type ConstellationScene,
  constellationStatusFromError,
  normalizeConstellationProfiles
} from '@/app/starmap/constellation'
import { cortexToStarmap } from '@/app/starmap/cortex'
import { getCortexDream, getCortexGraph, getCortexHealth, getStarmapGraph, runCortexDream } from '@/hermes'
import type {
  CortexGraphResponse,
  CortexHealthResponse,
  CortexJobResponse,
  ProfileInfo,
  StarmapGraph
} from '@/types/hermes'

// On-demand cache for the memory graph. Native Cortex is preferred; an older
// backend transparently falls back to /api/learning without changing that
// compatibility route or its mutation behavior.
export const $starmapGraph = atom<StarmapGraph | null>(null)
export const $starmapLoading = atom(false)
export const $starmapError = atom<null | string>(null)
export const $cortexGraph = atom<CortexGraphResponse | null>(null)
export const $cortexHealth = atom<CortexHealthResponse | null>(null)
export const $cortexDreamJob = atom<CortexJobResponse | null>(null)
export const $cortexStatusError = atom<null | string>(null)
export const $starmapBrainProfile = atom('default')
export const $starmapBrainStatus = atom<ConstellationBrainStatus | null>(null)
export const $starmapConstellation = atom<ConstellationScene | null>(null)
export const $starmapMode = atom<'brain' | 'constellation'>('constellation')

let brainInflight: Promise<void> | null = null
let constellationInflight: Promise<void> | null = null
let requestEpoch = 0

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

function mayUseLegacyGraph(err: unknown): boolean {
  const status =
    typeof err === 'object' && err !== null && 'statusCode' in err
      ? Number((err as { statusCode?: unknown }).statusCode)
      : Number(message(err).match(/^\s*(\d{3})\b/)?.[1])

  return status === 404
}

function loadLatestDream(health: CortexHealthResponse | null, epoch: number, brainProfile: string): void {
  const jobId = health?.jobs.latest_dream_job_id

  if (!jobId) {
    return
  }

  void getCortexDream(jobId, brainProfile).then(
    job => {
      if (epoch === requestEpoch) {
        $cortexDreamJob.set(job)
      }
    },
    () => {
      // Activity telemetry is additive; a missing historical job must never
      // make the graph itself unavailable.
    }
  )
}

export async function loadStarmapGraph(force = false): Promise<void> {
  if (brainInflight) {
    return brainInflight
  }

  if ($starmapGraph.get() && !force) {
    return
  }

  const epoch = requestEpoch
  const brainProfile = $starmapBrainProfile.get()
  $starmapLoading.set(true)
  $starmapError.set(null)
  $cortexStatusError.set(null)

  let task!: Promise<void>
  task = (async () => {
    try {
      // Start health with the graph so opening the panel costs one round trip.
      // Health is non-fatal: the visualization remains useful while maintenance
      // telemetry is temporarily unavailable.
      const healthRequest = getCortexHealth(brainProfile).catch(() => null)

      try {
        const cortex = await getCortexGraph(500, brainProfile)
        const health = await healthRequest

        if (epoch !== requestEpoch) {
          return
        }

        $cortexGraph.set(cortex)
        $cortexHealth.set(health)
        $starmapGraph.set(cortexToStarmap(cortex))
        $starmapBrainStatus.set(cortex.nodes.length ? 'ready' : 'empty')
        loadLatestDream(health, epoch, brainProfile)
      } catch (cortexError) {
        // Backward compatibility is only for the default brain on an
        // un-upgraded backend. Disabled Cortex is an explicit product state;
        // auth, corruption, server, and contract failures stay visible too.
        if (brainProfile !== 'default' || !mayUseLegacyGraph(cortexError)) {
          throw cortexError
        }

        try {
          const legacy = await getStarmapGraph()

          if (epoch !== requestEpoch) {
            return
          }

          $cortexGraph.set(null)
          $cortexHealth.set(null)
          $starmapGraph.set({ ...legacy, source: 'legacy' })
          $starmapBrainStatus.set(legacy.nodes.length ? 'ready' : 'empty')
        } catch (legacyError) {
          throw new Error(`Cortex: ${message(cortexError)}. Legacy graph: ${message(legacyError)}`)
        }
      }
    } catch (err) {
      if (epoch === requestEpoch) {
        $starmapError.set(message(err))
        $starmapBrainStatus.set(constellationStatusFromError(err))
      }
    } finally {
      if (epoch === requestEpoch) {
        $starmapLoading.set(false)
      }

      if (brainInflight === task) {
        brainInflight = null
      }
    }
  })()

  brainInflight = task

  return task
}

export async function loadStarmapConstellation(profiles: ProfileInfo[], force = false): Promise<void> {
  if (constellationInflight) {
    return constellationInflight
  }

  if ($starmapConstellation.get() && !force) {
    return
  }

  const epoch = requestEpoch
  const brains = normalizeConstellationProfiles(profiles)
  const limit = constellationNodeBudget(brains.length)
  $starmapLoading.set(true)
  $starmapError.set(null)

  let task!: Promise<void>
  task = (async () => {
    const inputs = await Promise.all(
      brains.map(async (profile): Promise<ConstellationBrainInput> => {
        try {
          const graph = await getCortexGraph(limit, profile.name)

          return { graph, profile, status: graph.nodes.length ? 'ready' : 'empty' }
        } catch (error) {
          return { graph: null, profile, status: constellationStatusFromError(error) }
        }
      })
    )

    if (epoch === requestEpoch) {
      $starmapConstellation.set(buildConstellationScene(inputs))
    }
  })().finally(() => {
    if (epoch === requestEpoch) {
      $starmapLoading.set(false)
    }

    if (constellationInflight === task) {
      constellationInflight = null
    }
  })

  constellationInflight = task

  return task
}

export async function refreshCortexHealth(): Promise<void> {
  const epoch = requestEpoch
  const brainProfile = $starmapBrainProfile.get()

  try {
    const health = await getCortexHealth(brainProfile)

    if (epoch === requestEpoch) {
      $cortexHealth.set(health)
      $cortexStatusError.set(null)
      loadLatestDream(health, epoch, brainProfile)
    }
  } catch (err) {
    if (epoch === requestEpoch) {
      $cortexStatusError.set(message(err))
    }
  }
}

export async function runCortexDreamNow(): Promise<void> {
  const epoch = requestEpoch
  const brainProfile = $starmapBrainProfile.get()

  try {
    const job = await runCortexDream(brainProfile)

    if (epoch === requestEpoch) {
      $cortexDreamJob.set(job)
      $cortexStatusError.set(null)
    }
  } catch (err) {
    if (epoch === requestEpoch) {
      $cortexStatusError.set(message(err))
    }
  }
}

export async function refreshCortexDreamStatus(jobId: string): Promise<void> {
  const epoch = requestEpoch
  const brainProfile = $starmapBrainProfile.get()

  try {
    const job = await getCortexDream(jobId, brainProfile)

    if (epoch === requestEpoch) {
      $cortexDreamJob.set(job)
      $cortexStatusError.set(null)

      if (!['queued', 'running'].includes(job.job.status)) {
        await Promise.all([refreshCortexHealth(), loadStarmapGraph(true)])
      }
    }
  } catch (err) {
    if (epoch === requestEpoch) {
      $cortexStatusError.set(message(err))
    }
  }
}

export function selectStarmapBrain(profile: string): Promise<void> {
  const selected = profile.trim() || 'default'

  if ($starmapMode.get() === 'brain' && selected === $starmapBrainProfile.get() && brainInflight) {
    return brainInflight
  }

  if ($starmapMode.get() === 'brain' && selected === $starmapBrainProfile.get() && $starmapGraph.get()) {
    return Promise.resolve()
  }

  requestEpoch += 1
  brainInflight = null
  constellationInflight = null
  $starmapMode.set('brain')
  $starmapBrainProfile.set(selected)
  $starmapGraph.set(null)
  $cortexGraph.set(null)
  $cortexHealth.set(null)
  $cortexDreamJob.set(null)
  $cortexStatusError.set(null)
  $starmapError.set(null)
  $starmapBrainStatus.set(null)

  return loadStarmapGraph(true)
}

export function showStarmapConstellation(profiles: ProfileInfo[], force = false): Promise<void> {
  if ($starmapMode.get() === 'constellation' && !force) {
    if (constellationInflight) {
      return constellationInflight
    }

    if ($starmapConstellation.get()) {
      return Promise.resolve()
    }
  }

  requestEpoch += 1
  brainInflight = null
  constellationInflight = null
  $starmapMode.set('constellation')
  $starmapBrainProfile.set('default')
  $starmapGraph.set(null)
  $cortexGraph.set(null)
  $cortexHealth.set(null)
  $cortexDreamJob.set(null)
  $cortexStatusError.set(null)
  $starmapError.set(null)
  $starmapBrainStatus.set(null)
  $starmapLoading.set(false)

  return loadStarmapConstellation(profiles, force)
}

/** Drop one legacy node from the cached graph immediately; return rollback. */
export function evictStarmapNode(id: string): () => void {
  const prev = $starmapGraph.get()

  // Cortex mutations use typed APIs and must never be routed through the
  // legacy learning delete path. The Cortex UI is read-only in this phase.
  if (!prev || prev.source === 'cortex') {
    return () => {}
  }

  const next: StarmapGraph = {
    ...prev,
    nodes: prev.nodes.filter(node => node.id !== id),
    edges: prev.edges.filter(edge => edge.source !== id && edge.target !== id)
  }

  $starmapGraph.set(next)

  return () => $starmapGraph.set(prev)
}

/** Drop every profile-scoped response and invalidate all in-flight writes. */
export function resetStarmapGraph(): void {
  requestEpoch += 1
  brainInflight = null
  constellationInflight = null
  $starmapGraph.set(null)
  $cortexGraph.set(null)
  $cortexHealth.set(null)
  $cortexDreamJob.set(null)
  $cortexStatusError.set(null)
  $starmapError.set(null)
  $starmapLoading.set(false)
  $starmapBrainProfile.set('default')
  $starmapBrainStatus.set(null)
  $starmapConstellation.set(null)
  $starmapMode.set('constellation')
}
