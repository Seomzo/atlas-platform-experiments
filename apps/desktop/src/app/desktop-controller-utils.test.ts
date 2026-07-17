import { describe, expect, it, vi } from 'vitest'

import type { SessionInfo } from '@/hermes'

import {
  closeThenPrepareWorkspace,
  runSerializedWorkspacePreparation,
  sameCronSignature,
  workspaceHandoffSourceMatches
} from './desktop-controller-utils'

const session = (id: string, title: string | null): SessionInfo => ({ id, title }) as SessionInfo

describe('sameCronSignature', () => {
  it('is false when the lengths differ', () => {
    expect(sameCronSignature([session('a', 't')], [])).toBe(false)
  })

  it('is true when ids and titles match in order', () => {
    const a = [session('a', 'one'), session('b', 'two')]
    const b = [session('a', 'one'), session('b', 'two')]
    expect(sameCronSignature(a, b)).toBe(true)
  })

  it('is false when a title changed', () => {
    const a = [session('a', 'one')]
    const b = [session('a', 'renamed')]
    expect(sameCronSignature(a, b)).toBe(false)
  })

  it('is false when order differs', () => {
    const a = [session('a', 't'), session('b', 't')]
    const b = [session('b', 't'), session('a', 't')]
    expect(sameCronSignature(a, b)).toBe(false)
  })
})

describe('closeThenPrepareWorkspace', () => {
  it('does not switch workspaces when semantic session close is rejected', async () => {
    const switchWorkspace = vi.fn(async () => undefined)

    await expect(closeThenPrepareWorkspace(async () => false, switchWorkspace)).resolves.toBe(false)

    expect(switchWorkspace).not.toHaveBeenCalled()
  })

  it('prepares the workspace only after semantic session close succeeds', async () => {
    const order: string[] = []

    await expect(
      closeThenPrepareWorkspace(
        async () => {
          order.push('close')

          return true
        },
        async () => {
          order.push('switch')
        }
      )
    ).resolves.toBe(true)

    expect(order).toEqual(['close', 'switch'])
  })

  it('lets only the latest coalesced handoff run its continuation', async () => {
    let resolveClose: ((closed: boolean) => void) | null = null

    const sharedClose = new Promise<boolean>(resolve => {
      resolveClose = resolve
    })

    let latestIntent = 1
    const firstSwitch = vi.fn(async () => undefined)
    const secondSwitch = vi.fn(async () => undefined)

    const first = closeThenPrepareWorkspace(
      () => sharedClose,
      firstSwitch,
      () => latestIntent === 1
    )

    latestIntent = 2

    const second = closeThenPrepareWorkspace(
      () => sharedClose,
      secondSwitch,
      () => latestIntent === 2
    )

    resolveClose!(true)

    await expect(Promise.all([first, second])).resolves.toEqual([false, true])
    expect(firstSwitch).not.toHaveBeenCalled()
    expect(secondSwitch).toHaveBeenCalledTimes(1)
  })
})

describe('workspaceHandoffSourceMatches', () => {
  it('rejects a delayed composer request after the visible session changes', () => {
    expect(workspaceHandoffSourceMatches('stored-old', 'stored-new', 'runtime-new')).toBe(false)
  })

  it('matches stored, runtime-only, and explicitly new-draft sources', () => {
    expect(workspaceHandoffSourceMatches('stored-1', 'stored-1', 'runtime-1')).toBe(true)
    expect(workspaceHandoffSourceMatches('runtime-1', null, 'runtime-1')).toBe(true)
    expect(workspaceHandoffSourceMatches(null, null, null)).toBe(true)
  })
})

describe('runSerializedWorkspacePreparation', () => {
  it('serializes Git mutations and lets the newer intent deterministically finish last', async () => {
    let latestIntent = 1
    let releaseFirst: (() => void) | null = null
    const order: string[] = []
    const queue = { current: Promise.resolve() }

    const first = runSerializedWorkspacePreparation(
      queue,
      () => latestIntent === 1,
      async () => {
        order.push('first:start')
        await new Promise<void>(resolve => {
          releaseFirst = resolve
        })
        order.push('first:end')
      }
    )

    await vi.waitFor(() => expect(order).toEqual(['first:start']))
    latestIntent = 2

    const second = runSerializedWorkspacePreparation(
      queue,
      () => latestIntent === 2,
      async () => {
        order.push('second:start', 'second:end')
      }
    )

    releaseFirst!()

    await expect(Promise.all([first, second])).resolves.toEqual([false, true])
    expect(order).toEqual(['first:start', 'first:end', 'second:start', 'second:end'])
  })

  it('marks an acquired Git mutation busy until its critical section finishes', async () => {
    let release: (() => void) | null = null
    const active = { current: false }
    const queue = { current: Promise.resolve() }

    const workspace = runSerializedWorkspacePreparation(
      queue,
      () => true,
      () =>
        new Promise<void>(resolve => {
          release = resolve
        }),
      active
    )

    await vi.waitFor(() => expect(active.current).toBe(true))

    // Plain/profile/sidebar destinations use this same flag to reject their
    // continuation rather than claiming they superseded an irreversible Git
    // operation halfway through it.
    expect(active.current).toBe(true)
    release!()
    await expect(workspace).resolves.toBe(true)
    expect(active.current).toBe(false)
  })
})
