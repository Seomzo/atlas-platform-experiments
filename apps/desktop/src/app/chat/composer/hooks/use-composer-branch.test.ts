import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { $composerAttachments, clearSessionDraft, stashSessionDraft, takeSessionDraft } from '@/store/composer'

import { useComposerBranch } from './use-composer-branch'

const projectMocks = vi.hoisted(() => ({
  listRepoBranches: vi.fn(async () => []),
  requestStartWorkSession: vi.fn(),
  startWorkInRepo: vi.fn(),
  switchBranchInRepo: vi.fn()
}))

vi.mock('@/store/projects', () => projectMocks)

describe('useComposerBranch semantic handoff', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    clearSessionDraft(null)
    $composerAttachments.set([])
  })

  it('preserves text and attachments until the confirmed pre-reset commit', () => {
    const clearDraft = vi.fn()
    const syncDraftFromEditor = vi.fn(() => 'latest source draft')
    stashSessionDraft(null, 'existing new-chat draft', [
      { id: 'shared', kind: 'file', label: 'old.pdf', path: '/old.pdf' }
    ])
    $composerAttachments.set([
      { id: 'shared', kind: 'file', label: 'new.pdf', path: '/new.pdf' },
      { id: 'image', kind: 'image', label: 'image.png', path: '/image.png' }
    ])

    const { result } = renderHook(() =>
      useComposerBranch({
        activeQueueSessionKey: 'stored-session',
        clearDraft,
        cwd: '/repo',
        syncDraftFromEditor
      })
    )

    act(() => result.current.openInWorktree('/repo/.worktrees/task'))

    expect(clearDraft).not.toHaveBeenCalled()
    expect($composerAttachments.get()).toHaveLength(2)

    const options = projectMocks.requestStartWorkSession.mock.calls[0]?.[1] as {
      beforeReset?: () => void
    }

    act(() => options.beforeReset?.())

    expect(clearDraft).toHaveBeenCalledTimes(1)
    expect($composerAttachments.get()).toEqual([])
    expect(takeSessionDraft(null)).toEqual({
      attachments: [
        { id: 'shared', kind: 'file', label: 'new.pdf', path: '/new.pdf' },
        { id: 'image', kind: 'image', label: 'image.png', path: '/image.png' }
      ],
      text: 'existing new-chat draft\n\nlatest source draft'
    })
  })

  it('defers default-branch switching to the controller request', async () => {
    const { result } = renderHook(() =>
      useComposerBranch({
        activeQueueSessionKey: 'stored-session',
        clearDraft: vi.fn(),
        cwd: '/repo',
        syncDraftFromEditor: () => 'draft'
      })
    )

    await act(() => result.current.handleConvertBranch('main', null, true))

    expect(projectMocks.switchBranchInRepo).not.toHaveBeenCalled()
    expect(projectMocks.requestStartWorkSession).toHaveBeenCalledWith(
      { branch: 'main', kind: 'switch-branch', repoPath: '/repo' },
      expect.objectContaining({ beforeReset: expect.any(Function), sourceSessionKey: 'stored-session' })
    )
  })

  it('represents worktree creation as post-close intent instead of mutating Git', async () => {
    const { result } = renderHook(() =>
      useComposerBranch({
        activeQueueSessionKey: 'stored-session',
        clearDraft: vi.fn(),
        cwd: '/repo',
        syncDraftFromEditor: () => 'draft'
      })
    )

    await act(() => result.current.handleBranchOff('feature/memory', 'main'))

    expect(projectMocks.startWorkInRepo).not.toHaveBeenCalled()
    expect(projectMocks.requestStartWorkSession).toHaveBeenCalledWith(
      {
        kind: 'create-worktree',
        options: { base: 'main', branch: 'feature/memory', name: 'feature/memory' },
        repoPath: '/repo'
      },
      expect.objectContaining({ beforeReset: expect.any(Function), sourceSessionKey: 'stored-session' })
    )
  })
})
