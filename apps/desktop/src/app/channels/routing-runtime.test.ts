import { beforeEach, describe, expect, it, vi } from 'vitest'

import { $channels, createChannel } from '@/store/channels'

import { channelMentionToken, type ChannelRoutingCopy, type ChannelRoutingGateway, sendChannelTurn } from './routing'

const copy: ChannelRoutingCopy = {
  emptyResponse: worker => `${worker} returned nothing`,
  missingWorker: workerId => `Missing ${workerId}`,
  workerFailed: (worker, detail) => `${worker} failed: ${detail}`
}

const workers = new Map([
  ['service', { id: 'service', name: 'Service Advisor' }],
  ['parts', { id: 'parts', name: 'Parts Manager' }]
])

describe('channel routing runtime', () => {
  beforeEach(() => {
    $channels.set([])
    window.localStorage.clear()
  })

  it('isolates a partial failure while preserving the other streamed response and transcript order', async () => {
    const channel = createChannel(
      { kind: 'channel', memberWorkerIds: ['service', 'parts'], name: 'service-reports' },
      { createdAt: '2026-07-20T12:00:00.000Z', id: 'channel-service' }
    )

    const streams: Array<[string, string]> = []

    const gateway: ChannelRoutingGateway = {
      createSession: vi.fn(async (_channel, workerId) => {
        if (workerId === 'parts') {
          throw new Error('profile backend failed to start')
        }

        return {
          runtimeSessionId: 'runtime-service',
          storedSessionId: 'stored-service',
          workerId
        }
      }),
      resumeSession: vi.fn(),
      submitPrompt: vi.fn(async (session, _prompt, onStream) => {
        onStream('Real streamed answer')

        return session.workerId === 'service' ? 'Real streamed answer' : 'unexpected'
      })
    }

    const result = await sendChannelTurn({
      callbacks: { onWorkerStream: (workerId, text) => streams.push([workerId, text]) },
      channel,
      copy,
      gateway,
      text: `${channelMentionToken('service')} ${channelMentionToken('parts')} inspect the RO`,
      workers
    })

    expect(result.targets).toEqual(['service', 'parts'])
    expect(streams).toEqual([['service', 'Real streamed answer']])
    expect(result.deliveries).toEqual([
      { status: 'response', text: 'Real streamed answer', workerId: 'service' },
      { status: 'error', text: 'Parts Manager failed: profile backend failed to start', workerId: 'parts' }
    ])

    const persisted = $channels.get()[0]

    expect(persisted.sessionBindings).toEqual({
      service: expect.objectContaining({ sessionId: 'stored-service' })
    })
    expect(persisted.transcript).toEqual([
      expect.objectContaining({ sender: { kind: 'user' } }),
      expect.objectContaining({ sender: { kind: 'worker', workerId: 'service' }, status: 'message' }),
      expect.objectContaining({ sender: { kind: 'worker', workerId: 'parts' }, status: 'error' })
    ])
  })

  it('resumes the same durable per-worker session on subsequent channel turns', async () => {
    const created = createChannel(
      { kind: 'channel', memberWorkerIds: ['service'], name: 'service-reports' },
      { createdAt: '2026-07-20T12:00:00.000Z', id: 'channel-service' }
    )

    const gateway: ChannelRoutingGateway = {
      createSession: vi.fn(async (_channel, workerId) => ({
        runtimeSessionId: 'runtime-first',
        storedSessionId: 'stored-service',
        workerId
      })),
      resumeSession: vi.fn(async (workerId, storedSessionId) => ({
        runtimeSessionId: 'runtime-resumed',
        storedSessionId,
        workerId
      })),
      submitPrompt: vi.fn(async () => 'Done')
    }

    await sendChannelTurn({
      channel: created,
      copy,
      gateway,
      text: `${channelMentionToken('service')} first`,
      workers
    })

    await sendChannelTurn({
      channel: $channels.get()[0],
      copy,
      gateway,
      text: `${channelMentionToken('service')} second`,
      workers
    })

    expect(gateway.createSession).toHaveBeenCalledTimes(1)
    expect(gateway.resumeSession).toHaveBeenCalledWith('service', 'stored-service')
    expect(gateway.submitPrompt).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ runtimeSessionId: 'runtime-resumed', storedSessionId: 'stored-service' }),
      expect.stringContaining('second'),
      expect.any(Function)
    )
  })

  it('records an unmentioned channel message once and wakes no worker', async () => {
    const channel = createChannel(
      { kind: 'channel', memberWorkerIds: ['service'], name: 'service-reports' },
      { createdAt: '2026-07-20T12:00:00.000Z', id: 'channel-service' }
    )

    const gateway: ChannelRoutingGateway = {
      createSession: vi.fn(),
      resumeSession: vi.fn(),
      submitPrompt: vi.fn()
    }

    const result = await sendChannelTurn({ channel, copy, gateway, text: 'FYI only', workers })

    expect(result.targets).toEqual([])
    expect($channels.get()[0].transcript).toEqual([expect.objectContaining({ text: 'FYI only' })])
    expect(gateway.createSession).not.toHaveBeenCalled()
    expect(gateway.resumeSession).not.toHaveBeenCalled()
    expect(gateway.submitPrompt).not.toHaveBeenCalled()
  })

  it('routes an unmentioned DM through the same real single-worker session pipeline', async () => {
    const dm = createChannel(
      { kind: 'dm', memberWorkerIds: ['service'], name: 'Service Advisor' },
      { createdAt: '2026-07-20T12:00:00.000Z', id: 'dm-service' }
    )

    const gateway: ChannelRoutingGateway = {
      createSession: vi.fn(async (_channel, workerId) => ({
        runtimeSessionId: 'runtime-dm',
        storedSessionId: 'stored-dm',
        workerId
      })),
      resumeSession: vi.fn(),
      submitPrompt: vi.fn(async () => 'DM response')
    }

    const result = await sendChannelTurn({ channel: dm, copy, gateway, text: 'Status?', workers })

    expect(result.targets).toEqual(['service'])
    expect(gateway.createSession).toHaveBeenCalledWith(dm, 'service')
    expect($channels.get()[0].transcript).toEqual([
      expect.objectContaining({ sender: { kind: 'user' }, text: 'Status?' }),
      expect.objectContaining({ sender: { kind: 'worker', workerId: 'service' }, text: 'DM response' })
    ])
  })
})
