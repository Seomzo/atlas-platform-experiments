import { useCallback } from 'react'

import { $composerAttachments, clearComposerAttachments, stashSessionDraft, takeSessionDraft } from '@/store/composer'
import {
  listRepoBranches,
  requestStartWorkSession,
  switchBranchInRepo,
  type WorkspaceSessionTarget
} from '@/store/projects'

interface UseComposerBranchOptions {
  activeQueueSessionKey: null | string
  clearDraft: () => void
  cwd: null | string | undefined
  syncDraftFromEditor: () => string
}

const appendTransferredDraft = (current: string, incoming: string) => {
  const base = current.trimEnd()
  const next = incoming.trim()

  if (!base) {
    return next
  }

  return next ? `${base}\n\n${next}` : base
}

const mergeTransferredAttachments = (
  current: ReturnType<typeof takeSessionDraft>['attachments'],
  incoming: ReturnType<typeof takeSessionDraft>['attachments']
) => {
  const merged = current.map(attachment => ({ ...attachment }))

  for (const attachment of incoming) {
    const index = merged.findIndex(candidate => candidate.id === attachment.id)

    if (index >= 0) {
      merged[index] = { ...attachment }
    } else {
      merged.push({ ...attachment })
    }
  }

  return merged
}

/**
 * Branch / worktree engine — the `CodingStatusRow` hand-offs. Each action emits
 * an immutable target intent carrying the current source-session identity. The
 * controller performs any Git mutation only after semantic close, then moves
 * the draft into the fresh workspace.
 */
export function useComposerBranch({
  activeQueueSessionKey,
  clearDraft,
  cwd,
  syncDraftFromEditor
}: UseComposerBranchOptions) {
  // Hand a worktree off to the controller: open a fresh session anchored there,
  // carrying the composer draft as its first turn. Clearing here means the draft
  // travels to the new session instead of getting stashed under this one.
  const openInWorktree = useCallback(
    (target: WorkspaceSessionTarget) => {
      // Do not mutate the current composer while semantic close is pending.
      // The controller invokes this commit hook immediately before a confirmed
      // fresh-session reset. If close is rejected, the draft and attachments
      // remain exactly where the user left them.
      const beforeReset = activeQueueSessionKey
        ? () => {
            const incomingText = syncDraftFromEditor()
            const incomingAttachments = $composerAttachments.get().map(attachment => ({ ...attachment }))
            const target = takeSessionDraft(null)

            clearDraft()
            clearComposerAttachments()
            stashSessionDraft(
              null,
              appendTransferredDraft(target.text, incomingText),
              mergeTransferredAttachments(target.attachments, incomingAttachments)
            )
          }
        : undefined

      requestStartWorkSession(target, { beforeReset, sourceSessionKey: activeQueueSessionKey })
    },
    [activeQueueSessionKey, clearDraft, syncDraftFromEditor]
  )

  // Branch off into a NEW worktree (base = branch name, or current HEAD). Git
  // creation remains encoded as data until the controller confirms close.
  const handleBranchOff = useCallback(
    async (branch: string, base?: string) => {
      const repoPath = cwd?.trim()

      if (repoPath) {
        openInWorktree({
          kind: 'create-worktree',
          options: { base, branch, name: branch },
          repoPath
        })
      }
    },
    [cwd, openInWorktree]
  )

  // Convert an EXISTING branch into a fresh worktree + session (no new branch).
  // Existing paths need no preparation; switch/create operations stay deferred.
  const handleConvertBranch = useCallback(
    async (branch: string, path?: null | string, isDefault?: boolean) => {
      if (path?.trim()) {
        openInWorktree(path)

        return
      }

      const repoPath = cwd?.trim()

      if (repoPath && isDefault) {
        // Carry branch intent to the controller. It switches only after the
        // current session has passed semantic close, so a rejected boundary
        // cannot mutate the checkout underneath the still-open transcript.
        openInWorktree({ branch, kind: 'switch-branch', repoPath })

        return
      }

      if (repoPath) {
        openInWorktree({
          kind: 'create-worktree',
          options: { existingBranch: branch },
          repoPath
        })
      }
    },
    [cwd, openInWorktree]
  )

  const handleListBranches = useCallback(async () => {
    const repoPath = cwd?.trim()

    return repoPath ? listRepoBranches(repoPath) : []
  }, [cwd])

  const handleSwitchBranch = useCallback(
    async (branch: string) => {
      const repoPath = cwd?.trim()

      if (repoPath) {
        await switchBranchInRepo(repoPath, branch)
      }
    },
    [cwd]
  )

  return { handleBranchOff, handleConvertBranch, handleListBranches, handleSwitchBranch, openInWorktree }
}
