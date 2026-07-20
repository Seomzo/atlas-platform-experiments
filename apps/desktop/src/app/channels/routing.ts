import type { AtlasChannel } from '@/store/channels'

export function directMessageWorkerId(channel: AtlasChannel): string | null {
  return channel.kind === 'dm' && channel.memberWorkerIds.length === 1 ? channel.memberWorkerIds[0] : null
}

/**
 * Pure policy helper for the future routing engine. It never sends a message;
 * it only resolves which channel members would be eligible for one turn.
 */
export function channelTurnTargets(channel: AtlasChannel, mentionedWorkerIds: readonly string[]): string[] {
  const members = new Set(channel.memberWorkerIds)

  if (channel.kind === 'dm' || channel.settings.turnPolicy === 'all-members') {
    return [...members]
  }

  return [...new Set(mentionedWorkerIds)].filter(workerId => members.has(workerId))
}
