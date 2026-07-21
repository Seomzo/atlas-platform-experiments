import type { GatewayEvent } from '@hermes/shared'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AtlasChannel } from '@/store/channels'

const mocks = vi.hoisted(() => {
  const handlers = new Map<string, Set<(event: GatewayEvent) => void>>()
  const request = vi.fn()

  const gateway = {
    connectionState: 'open' as const,
    on: vi.fn((type: string, handler: (event: GatewayEvent) => void) => {
      const current = handlers.get(type) ?? new Set()
      current.add(handler)
      handlers.set(type, current)

      return () => current.delete(handler)
    }),
    onState: vi.fn((handler: (state: 'open') => void) => {
      handler('open')

      return () => undefined
    }),
    request
  }

  return {
    emit(event: GatewayEvent) {
      for (const handler of handlers.get(event.type) ?? []) {
        handler(event)
      }
    },
    ensureGatewayForProfile: vi.fn(async () => gateway),
    gateway,
    handlers,
    request
  }
})

vi.mock('@/store/gateway', () => ({ ensureGatewayForProfile: mocks.ensureGatewayForProfile }))

import { desktopChannelGateway } from './routing'

const channel: AtlasChannel = {
  archived: false,
  createdAt: '2026-07-20T12:00:00.000Z',
  id: 'channel-service',
  kind: 'channel',
  memberWorkerIds: ['service'],
  name: 'service-reports',
  sessionBindings: {},
  settings: { turnPolicy: 'mention-only' },
  transcript: []
}

describe('desktop channel gateway', () => {
  beforeEach(() => {
    mocks.handlers.clear()
    mocks.request.mockReset()
    mocks.ensureGatewayForProfile.mockClear()
  })

  it('creates and resumes sessions through a non-activating profile gateway', async () => {
    mocks.request
      .mockResolvedValueOnce({ session_id: 'runtime-created', stored_session_id: 'stored-service' })
      .mockResolvedValueOnce({ session_id: 'runtime-resumed', resumed: 'stored-service' })

    await expect(desktopChannelGateway.createSession(channel, 'service')).resolves.toEqual({
      runtimeSessionId: 'runtime-created',
      storedSessionId: 'stored-service',
      workerId: 'service'
    })
    await expect(desktopChannelGateway.resumeSession('service', 'stored-service')).resolves.toEqual({
      runtimeSessionId: 'runtime-resumed',
      storedSessionId: 'stored-service',
      workerId: 'service'
    })

    expect(mocks.ensureGatewayForProfile).toHaveBeenNthCalledWith(1, 'service', false)
    expect(mocks.ensureGatewayForProfile).toHaveBeenNthCalledWith(2, 'service', false)
    expect(mocks.request).toHaveBeenNthCalledWith(1, 'session.create', {
      cols: 96,
      profile: 'service',
      source: 'desktop',
      title: '#service-reports · Atlas Teams'
    })
    expect(mocks.request).toHaveBeenNthCalledWith(2, 'session.resume', {
      profile: 'service',
      session_id: 'stored-service',
      source: 'desktop'
    })
  })

  it('streams only matching-session deltas and resolves the real completion text', async () => {
    mocks.request.mockImplementation(async (method: string) => {
      if (method === 'prompt.submit') {
        queueMicrotask(() => {
          mocks.emit({ payload: { text: 'ignored' }, session_id: 'another-session', type: 'message.delta' })
          mocks.emit({ payload: { text: 'Real ' }, session_id: 'runtime-service', type: 'message.delta' })
          mocks.emit({ payload: { text: 'answer' }, session_id: 'runtime-service', type: 'message.delta' })
          mocks.emit({
            payload: { status: 'complete', text: 'Real answer' },
            session_id: 'runtime-service',
            type: 'message.complete'
          })
        })
      }

      return { status: 'streaming' }
    })
    const streamed: string[] = []

    const response = await desktopChannelGateway.submitPrompt(
      { runtimeSessionId: 'runtime-service', storedSessionId: 'stored-service', workerId: 'service' },
      'prompt',
      text => streamed.push(text)
    )

    expect(streamed).toEqual(['Real ', 'Real answer'])
    expect(response).toBe('Real answer')
    expect(mocks.request).toHaveBeenCalledWith(
      'prompt.submit',
      { session_id: 'runtime-service', text: 'prompt' },
      1_800_000
    )
  })
})
