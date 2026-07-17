import type { SessionInfo } from '@/hermes'

export async function closeThenPrepareWorkspace(
  closeCurrentSession: () => Promise<boolean>,
  prepareWorkspace?: () => Promise<void>,
  isCurrentIntent: () => boolean = () => true
): Promise<boolean> {
  const closed = await closeCurrentSession()

  if (!closed || !isCurrentIntent()) {
    return false
  }

  await prepareWorkspace?.()

  return isCurrentIntent()
}

export function workspaceHandoffSourceMatches(
  expected: null | string | undefined,
  storedSessionId: null | string,
  runtimeSessionId: null | string
): boolean {
  return expected === undefined || expected === (storedSessionId || runtimeSessionId)
}

export async function runSerializedWorkspacePreparation(
  queue: { current: Promise<void> },
  isCurrentIntent: () => boolean,
  prepare: () => Promise<void>,
  active?: { current: boolean }
): Promise<boolean> {
  const run = queue.current
    .catch(() => undefined)
    .then(async () => {
      if (!isCurrentIntent()) {
        return false
      }

      if (active) {
        active.current = true
      }

      try {
        await prepare()
      } finally {
        if (active) {
          active.current = false
        }
      }

      return isCurrentIntent()
    })

  // Keep the queue usable after a failed Git operation; the owning caller still
  // receives the rejection from `run` and reports it.
  queue.current = run.then(
    () => undefined,
    () => undefined
  )

  return run
}

// Cheap signature compare so a poll only swaps the atom (and re-renders the
// sidebar) when the visible rows actually changed.
export function sameCronSignature(a: SessionInfo[], b: SessionInfo[]): boolean {
  if (a.length !== b.length) {
    return false
  }

  return a.every((session, i) => {
    const other = b[i]

    return (
      other != null &&
      session.id === other.id &&
      session._lineage_root_id === other._lineage_root_id &&
      session.title === other.title &&
      session.source === other.source &&
      session.profile === other.profile &&
      session.preview === other.preview &&
      session.message_count === other.message_count &&
      session.last_active === other.last_active &&
      session.ended_at === other.ended_at
    )
  })
}
