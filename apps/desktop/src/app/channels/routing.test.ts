import { describe, expect, it } from 'vitest'

import type { AtlasChannel } from '@/store/channels'

import { appViewForPath, channelRoute, routeChannelId } from '../routes'

import { channelTurnTargets, directMessageWorkerId } from './routing'

const channel: AtlasChannel = {
  archived: false,
  createdAt: '2026-07-20T12:00:00.000Z',
  id: 'channel-service',
  kind: 'channel',
  memberWorkerIds: ['service', 'parts'],
  name: 'service-reports',
  settings: { turnPolicy: 'mention-only' }
}

describe('channel routing policy helpers', () => {
  it('wakes only mentioned members under the default policy', () => {
    expect(channelTurnTargets(channel, [])).toEqual([])
    expect(channelTurnTargets(channel, ['outside', 'parts', 'parts'])).toEqual(['parts'])
  })

  it('fans out to every member only when the channel opts in', () => {
    expect(channelTurnTargets({ ...channel, settings: { turnPolicy: 'all-members' } }, [])).toEqual([
      'service',
      'parts'
    ])
  })

  it('resolves one real worker target for a direct message', () => {
    const dm: AtlasChannel = { ...channel, id: 'dm-service', kind: 'dm', memberWorkerIds: ['service'] }

    expect(directMessageWorkerId(dm)).toBe('service')
    expect(channelTurnTargets(dm, [])).toEqual(['service'])
    expect(directMessageWorkerId(channel)).toBeNull()
  })

  it('round-trips channel routes without treating them as session ids', () => {
    const path = channelRoute('service / reports')

    expect(path).toBe('/channels/service%20%2F%20reports')
    expect(routeChannelId(path)).toBe('service / reports')
    expect(appViewForPath(path)).toBe('channels')
  })
})
