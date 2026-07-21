import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  $channels,
  appendChannelTranscriptEntry,
  archiveChannel,
  CHANNELS_STORAGE_KEY,
  createChannel,
  LEGACY_CHANNELS_STORAGE_KEY,
  normalizeChannelName,
  setChannelSessionBinding
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
      sessionBindings: {},
      settings: { turnPolicy: 'mention-only' },
      transcript: []
    })
    expect(JSON.parse(window.localStorage.getItem(CHANNELS_STORAGE_KEY) ?? '[]')).toEqual([created])
  })

  it('requires exactly one worker for a direct message', () => {
    expect(() => createChannel({ kind: 'dm', memberWorkerIds: [], name: 'Riley' })).toThrow()
    expect(() => createChannel({ kind: 'dm', memberWorkerIds: ['riley', 'sam'], name: 'Riley' })).toThrow()
  })

  it('appends attributed transcript events and persists durable worker session bindings', () => {
    createChannel(
      { kind: 'channel', memberWorkerIds: ['service'], name: 'service' },
      { createdAt: '2026-07-20T12:00:00.000Z', id: 'channel-service' }
    )

    appendChannelTranscriptEntry(
      'channel-service',
      { sender: { kind: 'user' }, text: 'Check the RO', turnId: 'turn-1' },
      { createdAt: '2026-07-20T12:01:00.000Z', id: 'message-user' }
    )
    appendChannelTranscriptEntry(
      'channel-service',
      {
        sender: { kind: 'worker', workerId: 'service' },
        status: 'error',
        text: 'Could not reach DMS',
        turnId: 'turn-1'
      },
      { createdAt: '2026-07-20T12:02:00.000Z', id: 'message-worker' }
    )
    setChannelSessionBinding('channel-service', 'service', 'stored-session-service', '2026-07-20T12:01:01.000Z')

    const stored = JSON.parse(window.localStorage.getItem(CHANNELS_STORAGE_KEY) ?? '[]')[0]

    expect(stored.sessionBindings.service).toEqual({
      createdAt: '2026-07-20T12:01:01.000Z',
      sessionId: 'stored-session-service'
    })
    expect(stored.transcript).toEqual([
      expect.objectContaining({ id: 'message-user', sender: { kind: 'user' }, status: 'message' }),
      expect.objectContaining({
        id: 'message-worker',
        sender: { kind: 'worker', workerId: 'service' },
        status: 'error'
      })
    ])
  })

  it('persists archive state without changing other channels', () => {
    createChannel(
      { kind: 'channel', memberWorkerIds: ['service'], name: 'service' },
      { createdAt: '2026-07-20T12:00:00.000Z', id: 'channel-service' }
    )
    createChannel(
      { kind: 'dm', memberWorkerIds: ['parts'], name: 'Parts worker' },
      { createdAt: '2026-07-20T12:01:00.000Z', id: 'dm-parts' }
    )

    archiveChannel('channel-service')

    expect($channels.get()).toEqual([
      expect.objectContaining({ archived: true, id: 'channel-service' }),
      expect.objectContaining({ archived: false, id: 'dm-parts' })
    ])
  })

  it('normalizes channel names without erasing non-Latin letters', () => {
    expect(normalizeChannelName('  Parts & Service  ')).toBe('parts-service')
    expect(normalizeChannelName('サービス 報告')).toBe('サービス-報告')
  })

  it('hydrates transcript attribution and bindings after a renderer restart', async () => {
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
          sessionBindings: {
            service: { createdAt: '2026-07-20T12:00:01.000Z', sessionId: 'stored-service' }
          },
          settings: { turnPolicy: 'mention-only' },
          transcript: [
            {
              createdAt: '2026-07-20T12:00:02.000Z',
              id: 'message-service',
              sender: { kind: 'worker', workerId: 'service' },
              status: 'message',
              text: 'Ready.'
            }
          ]
        },
        { id: 'invalid-row' }
      ])
    )
    vi.resetModules()

    const reloaded = await import('./channels')

    expect(reloaded.$channels.get()).toEqual([
      expect.objectContaining({
        id: 'dm-service',
        sessionBindings: { service: expect.objectContaining({ sessionId: 'stored-service' }) },
        transcript: [expect.objectContaining({ sender: { kind: 'worker', workerId: 'service' }, text: 'Ready.' })]
      })
    ])
  })

  it('migrates v1 metadata into v2 and enforces mention-only routing', async () => {
    window.localStorage.setItem(
      LEGACY_CHANNELS_STORAGE_KEY,
      JSON.stringify([
        {
          archived: false,
          createdAt: '2026-07-20T12:00:00.000Z',
          id: 'channel-service',
          kind: 'channel',
          memberWorkerIds: ['service'],
          name: 'service',
          settings: { turnPolicy: 'all-members' }
        }
      ])
    )
    vi.resetModules()

    const reloaded = await import('./channels')

    expect(reloaded.$channels.get()[0]).toMatchObject({
      id: 'channel-service',
      sessionBindings: {},
      settings: { turnPolicy: 'mention-only' },
      transcript: []
    })
    expect(window.localStorage.getItem(reloaded.CHANNELS_STORAGE_KEY)).not.toBeNull()
    expect(window.localStorage.getItem(reloaded.LEGACY_CHANNELS_STORAGE_KEY)).toBeNull()
  })
})
