import { atom } from 'nanostores'

import { readKey, writeKey } from '@/lib/storage'

export type ChannelKind = 'channel' | 'dm'
export type ChannelTurnPolicy = 'all-members' | 'mention-only'

export interface ChannelSettings {
  turnPolicy: ChannelTurnPolicy
}

export interface AtlasChannel {
  archived: boolean
  createdAt: string
  id: string
  kind: ChannelKind
  memberWorkerIds: string[]
  name: string
  settings: ChannelSettings
}

export interface CreateChannelInput {
  kind: ChannelKind
  memberWorkerIds: string[]
  name: string
}

interface CreateChannelOptions {
  createdAt?: string
  id?: string
}

export const CHANNELS_STORAGE_KEY = 'atlas.desktop.channels.v1'

const TURN_POLICIES = new Set<ChannelTurnPolicy>(['all-members', 'mention-only'])

function uniqueWorkerIds(values: unknown): string[] {
  if (!Array.isArray(values)) {
    return []
  }

  return [
    ...new Set(values.filter((value): value is string => typeof value === 'string').map(value => value.trim()))
  ].filter(Boolean)
}

function storedChannel(value: unknown): AtlasChannel | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null
  }

  const row = value as Record<string, unknown>
  const id = typeof row.id === 'string' ? row.id.trim() : ''
  const name = typeof row.name === 'string' ? row.name.trim() : ''
  const createdAt = typeof row.createdAt === 'string' ? row.createdAt : ''
  const kind = row.kind === 'channel' || row.kind === 'dm' ? row.kind : null
  const memberWorkerIds = uniqueWorkerIds(row.memberWorkerIds)
  const settings = row.settings && typeof row.settings === 'object' ? (row.settings as Record<string, unknown>) : null
  const turnPolicy = settings?.turnPolicy

  if (
    !id ||
    !name ||
    !kind ||
    !createdAt ||
    Number.isNaN(Date.parse(createdAt)) ||
    memberWorkerIds.length === 0 ||
    (kind === 'dm' && memberWorkerIds.length !== 1) ||
    !TURN_POLICIES.has(turnPolicy as ChannelTurnPolicy)
  ) {
    return null
  }

  return {
    archived: row.archived === true,
    createdAt,
    id,
    kind,
    memberWorkerIds,
    name,
    settings: { turnPolicy: turnPolicy as ChannelTurnPolicy }
  }
}

function readChannels(): AtlasChannel[] {
  const raw = readKey(CHANNELS_STORAGE_KEY)

  if (!raw) {
    return []
  }

  try {
    const parsed = JSON.parse(raw)

    if (!Array.isArray(parsed)) {
      return []
    }

    const ids = new Set<string>()

    return parsed.flatMap(value => {
      const channel = storedChannel(value)

      if (!channel || ids.has(channel.id)) {
        return []
      }

      ids.add(channel.id)

      return [channel]
    })
  } catch {
    return []
  }
}

function channelId(kind: ChannelKind): string {
  const id = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`

  return `${kind}-${id}`
}

export const $channels = atom<AtlasChannel[]>(readChannels())

$channels.subscribe(channels => {
  writeKey(CHANNELS_STORAGE_KEY, channels.length > 0 ? JSON.stringify(channels) : null)
})

export function normalizeChannelName(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}_-]+/gu, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64)
}

export function createChannel(input: CreateChannelInput, options: CreateChannelOptions = {}): AtlasChannel {
  const memberWorkerIds = uniqueWorkerIds(input.memberWorkerIds)
  const name = input.kind === 'channel' ? normalizeChannelName(input.name) : input.name.trim()

  if (!name) {
    throw new Error('Channel name is required')
  }

  if (memberWorkerIds.length === 0) {
    throw new Error('At least one worker is required')
  }

  if (input.kind === 'dm' && memberWorkerIds.length !== 1) {
    throw new Error('Direct messages require exactly one worker')
  }

  const channel: AtlasChannel = {
    archived: false,
    createdAt: options.createdAt ?? new Date().toISOString(),
    id: options.id ?? channelId(input.kind),
    kind: input.kind,
    memberWorkerIds,
    name,
    settings: { turnPolicy: 'mention-only' }
  }

  $channels.set([...$channels.get(), channel])

  return channel
}

export function setChannelTurnPolicy(id: string, turnPolicy: ChannelTurnPolicy): void {
  $channels.set(
    $channels
      .get()
      .map(channel => (channel.id === id ? { ...channel, settings: { ...channel.settings, turnPolicy } } : channel))
  )
}

export function archiveChannel(id: string): void {
  $channels.set($channels.get().map(channel => (channel.id === id ? { ...channel, archived: true } : channel)))
}
