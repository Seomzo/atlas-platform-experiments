import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  $channels,
  archiveChannel,
  CHANNELS_STORAGE_KEY,
  createChannel,
  normalizeChannelName,
  setChannelTurnPolicy
} from './channels'

describe('channels store', () => {
  beforeEach(() => {
    $channels.set([])
    window.localStorage.clear()
  })

  it('creates a mention-only channel with normalized, unique members', () => {
    const created = createChannel(
      {
        kind: 'channel',
        memberWorkerIds: ['service', 'parts', 'service'],
        name: ' Service Reports '
      },
      { createdAt: '2026-07-20T12:00:00.000Z', id: 'channel-service' }
    )

    expect(created).toMatchObject({
      archived: false,
      id: 'channel-service',
      memberWorkerIds: ['service', 'parts'],
      name: 'service-reports',
      settings: { turnPolicy: 'mention-only' }
    })
    expect(JSON.parse(window.localStorage.getItem(CHANNELS_STORAGE_KEY) ?? '[]')).toEqual([created])
  })

  it('requires exactly one worker for a direct message', () => {
    expect(() => createChannel({ kind: 'dm', memberWorkerIds: [], name: 'Riley' })).toThrow()
    expect(() => createChannel({ kind: 'dm', memberWorkerIds: ['riley', 'sam'], name: 'Riley' })).toThrow()
  })

  it('persists settings and archive state without changing other channels', () => {
    createChannel(
      { kind: 'channel', memberWorkerIds: ['service'], name: 'service' },
      { createdAt: '2026-07-20T12:00:00.000Z', id: 'channel-service' }
    )
    createChannel(
      { kind: 'dm', memberWorkerIds: ['parts'], name: 'Parts worker' },
      { createdAt: '2026-07-20T12:01:00.000Z', id: 'dm-parts' }
    )

    setChannelTurnPolicy('channel-service', 'all-members')
    archiveChannel('channel-service')

    expect($channels.get()).toEqual([
      expect.objectContaining({ archived: true, id: 'channel-service', settings: { turnPolicy: 'all-members' } }),
      expect.objectContaining({ archived: false, id: 'dm-parts', settings: { turnPolicy: 'mention-only' } })
    ])
  })

  it('normalizes channel names without erasing non-Latin letters', () => {
    expect(normalizeChannelName('  Parts & Service  ')).toBe('parts-service')
    expect(normalizeChannelName('サービス 報告')).toBe('サービス-報告')
  })

  it('hydrates valid persisted channels after a renderer restart', async () => {
    window.localStorage.setItem(
      CHANNELS_STORAGE_KEY,
      JSON.stringify([
        {
          archived: false,
          createdAt: '2026-07-20T12:00:00.000Z',
          id: 'dm-service',
          kind: 'dm',
          memberWorkerIds: ['service'],
          name: 'Service worker',
          settings: { turnPolicy: 'mention-only' }
        },
        { id: 'invalid-row' }
      ])
    )
    vi.resetModules()

    const reloaded = await import('./channels')

    expect(reloaded.$channels.get()).toEqual([
      expect.objectContaining({ id: 'dm-service', memberWorkerIds: ['service'] })
    ])
  })
})
