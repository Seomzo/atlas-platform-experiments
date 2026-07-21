import { atom } from 'nanostores'

import { readKey, writeKey } from '@/lib/storage'

export type ChannelKind = 'channel' | 'dm'
export type ChannelTurnPolicy = 'mention-only'
export type ChannelTranscriptStatus = 'error' | 'message'

export interface ChannelSettings {
  turnPolicy: ChannelTurnPolicy
}

export type ChannelTranscriptSender = { kind: 'user' } | { kind: 'worker'; workerId: string }

export interface ChannelTranscriptEntry {
  createdAt: string
  id: string
  sender: ChannelTranscriptSender
  status: ChannelTranscriptStatus
  text: string
  turnId?: string
}

export interface ChannelSessionBinding {
  createdAt: string
  sessionId: string
}

export interface AtlasChannel {
  archived: boolean
  createdAt: string
  id: string
  kind: ChannelKind
  memberWorkerIds: string[]
  name: string
  sessionBindings: Record<string, ChannelSessionBinding>
  settings: ChannelSettings
  transcript: ChannelTranscriptEntry[]
}

export interface CreateChannelInput {
  kind: ChannelKind
  memberWorkerIds: string[]
  name: string
}

export interface AppendChannelTranscriptInput {
  sender: ChannelTranscriptSender
  status?: ChannelTranscriptStatus
  text: string
  turnId?: string
}

interface CreateChannelOptions {
  createdAt?: string
  id?: string
}

interface AppendChannelTranscriptOptions {
  createdAt?: string
  id?: string
}

export const CHANNELS_STORAGE_KEY = 'atlas.desktop.channels.v2'
export const LEGACY_CHANNELS_STORAGE_KEY = 'atlas.desktop.channels.v1'

function uniqueWorkerIds(values: unknown): string[] {
  if (!Array.isArray(values)) {
    return []
  }

  return [
    ...new Set(values.filter((value): value is string => typeof value === 'string').map(value => value.trim()))
  ].filter(Boolean)
}

function validTimestamp(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0 && !Number.isNaN(Date.parse(value))
}

function storedTranscriptEntry(value: unknown): ChannelTranscriptEntry | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null
  }

  const row = value as Record<string, unknown>
  const id = typeof row.id === 'string' ? row.id.trim() : ''
  const text = typeof row.text === 'string' ? row.text : ''
  const sender = row.sender && typeof row.sender === 'object' ? (row.sender as Record<string, unknown>) : null
  const workerId = typeof sender?.workerId === 'string' ? sender.workerId.trim() : ''

  const storedSender: ChannelTranscriptSender | null =
    sender?.kind === 'user'
      ? { kind: 'user' }
      : sender?.kind === 'worker' && workerId
        ? { kind: 'worker', workerId }
        : null

  if (!id || !text || !validTimestamp(row.createdAt) || !storedSender) {
    return null
  }

  return {
    createdAt: row.createdAt,
    id,
    sender: storedSender,
    status: row.status === 'error' ? 'error' : 'message',
    text,
    ...(typeof row.turnId === 'string' && row.turnId.trim() ? { turnId: row.turnId.trim() } : {})
  }
}

function storedTranscript(value: unknown): ChannelTranscriptEntry[] {
  if (!Array.isArray(value)) {
    return []
  }

  const ids = new Set<string>()

  return value.flatMap(item => {
    const entry = storedTranscriptEntry(item)

    if (!entry || ids.has(entry.id)) {
      return []
    }

    ids.add(entry.id)

    return [entry]
  })
}

function storedSessionBindings(
  value: unknown,
  memberWorkerIds: readonly string[]
): Record<string, ChannelSessionBinding> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return {}
  }

  const members = new Set(memberWorkerIds)

  return Object.fromEntries(
    Object.entries(value).flatMap(([workerId, binding]) => {
      if (!members.has(workerId) || !binding || typeof binding !== 'object' || Array.isArray(binding)) {
        return []
      }

      const row = binding as Record<string, unknown>
      const sessionId = typeof row.sessionId === 'string' ? row.sessionId.trim() : ''

      return sessionId && validTimestamp(row.createdAt)
        ? [[workerId, { createdAt: row.createdAt, sessionId }] as const]
        : []
    })
  )
}

function storedChannel(value: unknown, legacy = false): AtlasChannel | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null
  }

  const row = value as Record<string, unknown>
  const id = typeof row.id === 'string' ? row.id.trim() : ''
  const name = typeof row.name === 'string' ? row.name.trim() : ''
  const kind = row.kind === 'channel' || row.kind === 'dm' ? row.kind : null
  const memberWorkerIds = uniqueWorkerIds(row.memberWorkerIds)
  const settings = row.settings && typeof row.settings === 'object' ? (row.settings as Record<string, unknown>) : null
  const turnPolicy = settings?.turnPolicy

  if (
    !id ||
    !name ||
    !kind ||
    !validTimestamp(row.createdAt) ||
    memberWorkerIds.length === 0 ||
    (kind === 'dm' && memberWorkerIds.length !== 1) ||
    (!legacy && turnPolicy !== 'mention-only') ||
    (legacy && turnPolicy !== 'mention-only' && turnPolicy !== 'all-members')
  ) {
    return null
  }

  return {
    archived: row.archived === true,
    createdAt: row.createdAt,
    id,
    kind,
    memberWorkerIds,
    name,
    sessionBindings: legacy ? {} : storedSessionBindings(row.sessionBindings, memberWorkerIds),
    settings: { turnPolicy: 'mention-only' },
    transcript: legacy ? [] : storedTranscript(row.transcript)
  }
}

function parseChannels(raw: string, legacy: boolean): AtlasChannel[] {
  try {
    const parsed = JSON.parse(raw)

    if (!Array.isArray(parsed)) {
      return []
    }

    const ids = new Set<string>()

    return parsed.flatMap(value => {
      const channel = storedChannel(value, legacy)

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

function readChannels(): { channels: AtlasChannel[]; migrated: boolean } {
  const current = readKey(CHANNELS_STORAGE_KEY)

  if (current !== null) {
    return { channels: parseChannels(current, false), migrated: false }
  }

  const legacy = readKey(LEGACY_CHANNELS_STORAGE_KEY)

  return legacy === null ? { channels: [], migrated: false } : { channels: parseChannels(legacy, true), migrated: true }
}

function entityId(prefix: string): string {
  const id = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`

  return `${prefix}-${id}`
}

const stored = readChannels()

export const $channels = atom<AtlasChannel[]>(stored.channels)

$channels.subscribe(channels => {
  writeKey(CHANNELS_STORAGE_KEY, channels.length > 0 ? JSON.stringify(channels) : null)
})

if (stored.migrated) {
  writeKey(LEGACY_CHANNELS_STORAGE_KEY, null)
}

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
    id: options.id ?? entityId(input.kind),
    kind: input.kind,
    memberWorkerIds,
    name,
    sessionBindings: {},
    settings: { turnPolicy: 'mention-only' },
    transcript: []
  }

  $channels.set([...$channels.get(), channel])

  return channel
}

export function appendChannelTranscriptEntry(
  channelId: string,
  input: AppendChannelTranscriptInput,
  options: AppendChannelTranscriptOptions = {}
): ChannelTranscriptEntry | null {
  const text = input.text.trim()

  if (!text) {
    return null
  }

  const entry: ChannelTranscriptEntry = {
    createdAt: options.createdAt ?? new Date().toISOString(),
    id: options.id ?? entityId('message'),
    sender: input.sender,
    status: input.status ?? 'message',
    text,
    ...(input.turnId ? { turnId: input.turnId } : {})
  }

  let appended = false

  $channels.set(
    $channels.get().map(channel => {
      if (channel.id !== channelId) {
        return channel
      }

      appended = true

      return { ...channel, transcript: [...channel.transcript, entry] }
    })
  )

  return appended ? entry : null
}

export function setChannelSessionBinding(
  channelId: string,
  workerId: string,
  sessionId: string,
  createdAt = new Date().toISOString()
): void {
  const cleanSessionId = sessionId.trim()

  if (!cleanSessionId) {
    return
  }

  $channels.set(
    $channels.get().map(channel => {
      if (channel.id !== channelId || !channel.memberWorkerIds.includes(workerId)) {
        return channel
      }

      const previous = channel.sessionBindings[workerId]

      return {
        ...channel,
        sessionBindings: {
          ...channel.sessionBindings,
          [workerId]: { createdAt: previous?.createdAt ?? createdAt, sessionId: cleanSessionId }
        }
      }
    })
  )
}

export function clearChannelSessionBinding(channelId: string, workerId: string): void {
  $channels.set(
    $channels.get().map(channel => {
      if (channel.id !== channelId || !channel.sessionBindings[workerId]) {
        return channel
      }

      const sessionBindings = { ...channel.sessionBindings }
      delete sessionBindings[workerId]

      return { ...channel, sessionBindings }
    })
  )
}

export function archiveChannel(id: string): void {
  $channels.set($channels.get().map(channel => (channel.id === id ? { ...channel, archived: true } : channel)))
}
