import { describe, expect, it } from 'vitest'

import type { AtlasChannel, ChannelTranscriptEntry } from '@/store/channels'

import { appViewForPath, channelRoute, routeChannelId } from '../routes'

import {
  channelContextPrompt,
  channelEventTargets,
  channelMentionToken,
  channelTurnTargets,
  directMessageWorkerId,
  mentionedWorkerIds
} from './routing'

const channel: AtlasChannel = {
  archived: false,
  createdAt: '2026-07-20T12:00:00.000Z',
  id: 'channel-service',
  kind: 'channel',
  memberWorkerIds: ['service-id', 'parts-id'],
  name: 'service-reports',
  sessionBindings: {},
  settings: { turnPolicy: 'mention-only' },
  transcript: []
}

describe('channel routing policy helpers', () => {
  it('resolves stable mention tokens by worker id, never display name', () => {
    const text = `Ask ${channelMentionToken('parts-id')} and ${channelMentionToken('parts-id')} to check it.`

    expect(mentionedWorkerIds(text)).toEqual(['parts-id'])
    expect(mentionedWorkerIds('Ask @Parts Worker to check it.')).toEqual([])
    expect(channelTurnTargets(channel, mentionedWorkerIds(text))).toEqual(['parts-id'])
  })

  it('wakes only mentioned channel members under the v1 policy', () => {
    expect(channelTurnTargets(channel, [])).toEqual([])
    expect(channelTurnTargets(channel, ['outside', 'parts-id', 'parts-id'])).toEqual(['parts-id'])
  })

  it('resolves one real worker target for a direct message without a mention', () => {
    const dm: AtlasChannel = { ...channel, id: 'dm-service', kind: 'dm', memberWorkerIds: ['service-id'] }

    expect(directMessageWorkerId(dm)).toBe('service-id')
    expect(channelTurnTargets(dm, [])).toEqual(['service-id'])
    expect(directMessageWorkerId(channel)).toBeNull()
  })

  it('never routes worker output, even when it contains a valid member mention', () => {
    const workerResponse: ChannelTranscriptEntry = {
      createdAt: '2026-07-20T12:01:00.000Z',
      id: 'response-parts',
      sender: { kind: 'worker', workerId: 'parts-id' },
      status: 'message',
      text: `Please ask ${channelMentionToken('service-id')} next.`
    }

    expect(channelEventTargets(channel, workerResponse)).toEqual([])
  })

  it('builds a stable user-prompt header and replaces routing tokens with readable identities', () => {
    const workers = new Map([
      ['service-id', { id: 'service-id', name: 'Service Advisor' }],
      ['parts-id', { id: 'parts-id', name: 'Parts Advisor' }]
    ])

    const prompt = channelContextPrompt(
      channel,
      `Check with ${channelMentionToken('parts-id')}.`,
      ['parts-id'],
      workers
    )

    expect(prompt).toBe(
      '[Atlas channel v1 | channel=#service-reports | sender=user | mentioned-worker-ids=parts-id]\n' +
        'Check with [mention:Parts Advisor (parts-id)].'
    )
  })

  it('round-trips channel routes without treating them as session ids', () => {
    const path = channelRoute('service / reports')

    expect(path).toBe('/channels/service%20%2F%20reports')
    expect(routeChannelId(path)).toBe('service / reports')
    expect(appViewForPath(path)).toBe('channels')
  })
})
