import type { ConnectionState, GatewayEvent } from '@hermes/shared'

import { PROMPT_SUBMIT_REQUEST_TIMEOUT_MS } from '@/hermes'
import {
  appendChannelTranscriptEntry,
  type AtlasChannel,
  type ChannelTranscriptEntry,
  clearChannelSessionBinding,
  setChannelSessionBinding
} from '@/store/channels'
import { ensureGatewayForProfile } from '@/store/gateway'
import type { SessionCreateResponse, SessionResumeResponse } from '@/types/hermes'

const MENTION_TOKEN_RE = /<@([^<>\s]+)>/g

export interface ChannelWorkerIdentity {
  id: string
  name: string
}

export interface ChannelRoutingCopy {
  emptyResponse: (worker: string) => string
  missingWorker: (workerId: string) => string
  workerFailed: (worker: string, detail: string) => string
}

export interface ChannelWorkerSession {
  runtimeSessionId: string
  storedSessionId: string
  workerId: string
}

export interface ChannelRoutingGateway {
  createSession: (channel: AtlasChannel, workerId: string) => Promise<ChannelWorkerSession>
  resumeSession: (workerId: string, storedSessionId: string) => Promise<ChannelWorkerSession>
  submitPrompt: (session: ChannelWorkerSession, prompt: string, onStream: (text: string) => void) => Promise<string>
}

export interface ChannelRoutingCallbacks {
  onWorkerStart?: (workerId: string) => void
  onWorkerStream?: (workerId: string, text: string) => void
}

export interface ChannelDeliveryResult {
  status: 'error' | 'response'
  text: string
  workerId: string
}

export interface ChannelTurnResult {
  deliveries: ChannelDeliveryResult[]
  targets: string[]
  turnId: string
  userEntry: ChannelTranscriptEntry
}

interface SendChannelTurnInput {
  callbacks?: ChannelRoutingCallbacks
  channel: AtlasChannel
  copy: ChannelRoutingCopy
  gateway?: ChannelRoutingGateway
  text: string
  workers: ReadonlyMap<string, ChannelWorkerIdentity>
}

interface ChannelGatewayClient {
  connectionState: ConnectionState
  on: <P = unknown>(type: string, handler: (event: GatewayEvent<P>) => void) => () => void
  onState: (handler: (state: ConnectionState) => void) => () => void
  request: <T>(method: string, params?: Record<string, unknown>, timeoutMs?: number) => Promise<T>
}

interface StreamPayload {
  message?: unknown
  rendered?: unknown
  status?: unknown
  text?: unknown
}

function entityId(prefix: string): string {
  const id = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`

  return `${prefix}-${id}`
}

function decodeWorkerId(value: string): string | null {
  try {
    return decodeURIComponent(value).trim() || null
  } catch {
    return null
  }
}

function payloadText(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function errorDetail(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function isSessionMissingError(error: unknown): boolean {
  return /session not found/i.test(errorDetail(error))
}

async function openWorkerGateway(workerId: string): Promise<ChannelGatewayClient> {
  const gateway = await ensureGatewayForProfile(workerId, false)

  if (!gateway || gateway.connectionState !== 'open') {
    throw new Error('worker gateway is not connected')
  }

  return gateway
}

async function streamWorkerPrompt(
  gateway: ChannelGatewayClient,
  runtimeSessionId: string,
  prompt: string,
  onStream: (text: string) => void
): Promise<string> {
  return await new Promise<string>((resolve, reject) => {
    let streamed = ''
    let settled = false

    let offState = () => {}

    const cleanup = () => {
      offDelta()
      offComplete()
      offError()
      offState()
    }

    const finish = (action: () => void) => {
      if (settled) {
        return
      }

      settled = true
      cleanup()
      action()
    }

    const offDelta = gateway.on<StreamPayload>('message.delta', event => {
      if (event.session_id !== runtimeSessionId) {
        return
      }

      streamed += payloadText(event.payload?.text)
      onStream(streamed)
    })

    const offComplete = gateway.on<StreamPayload>('message.complete', event => {
      if (event.session_id !== runtimeSessionId) {
        return
      }

      const text = payloadText(event.payload?.text) || streamed || payloadText(event.payload?.rendered)

      if (event.payload?.status === 'error') {
        finish(() => reject(new Error(text || 'worker turn failed')))
      } else {
        finish(() => resolve(text))
      }
    })

    const offError = gateway.on<StreamPayload>('error', event => {
      if (event.session_id !== runtimeSessionId) {
        return
      }

      const message = payloadText(event.payload?.message) || payloadText(event.payload?.text) || 'worker turn failed'
      finish(() => reject(new Error(message)))
    })

    offState = gateway.onState(state => {
      if (state === 'closed' || state === 'error') {
        finish(() => reject(new Error('worker gateway connection closed')))
      }
    })

    void gateway
      .request('prompt.submit', { session_id: runtimeSessionId, text: prompt }, PROMPT_SUBMIT_REQUEST_TIMEOUT_MS)
      .catch(error => finish(() => reject(error)))
  })
}

export const desktopChannelGateway: ChannelRoutingGateway = {
  async createSession(channel, workerId) {
    const gateway = await openWorkerGateway(workerId)
    const title = channel.kind === 'channel' ? `#${channel.name}` : channel.name

    const created = await gateway.request<SessionCreateResponse>('session.create', {
      cols: 96,
      profile: workerId,
      source: 'desktop',
      title: `${title} · Atlas Teams`
    })

    const storedSessionId = created.stored_session_id?.trim()

    if (!storedSessionId) {
      throw new Error('worker session did not return a durable session id')
    }

    return { runtimeSessionId: created.session_id, storedSessionId, workerId }
  },

  async resumeSession(workerId, storedSessionId) {
    const gateway = await openWorkerGateway(workerId)

    const resumed = await gateway.request<SessionResumeResponse>('session.resume', {
      profile: workerId,
      session_id: storedSessionId,
      source: 'desktop'
    })

    return { runtimeSessionId: resumed.session_id, storedSessionId, workerId }
  },

  async submitPrompt(session, prompt, onStream) {
    const gateway = await openWorkerGateway(session.workerId)

    return await streamWorkerPrompt(gateway, session.runtimeSessionId, prompt, onStream)
  }
}

export function channelMentionToken(workerId: string): string {
  return `<@${encodeURIComponent(workerId.trim())}>`
}

export function mentionedWorkerIds(text: string): string[] {
  const workerIds = new Set<string>()

  for (const match of text.matchAll(MENTION_TOKEN_RE)) {
    const workerId = decodeWorkerId(match[1] || '')

    if (workerId) {
      workerIds.add(workerId)
    }
  }

  return [...workerIds]
}

export function directMessageWorkerId(channel: AtlasChannel): string | null {
  return channel.kind === 'dm' && channel.memberWorkerIds.length === 1 ? channel.memberWorkerIds[0] : null
}

export function channelTurnTargets(channel: AtlasChannel, mentioned: readonly string[]): string[] {
  if (channel.kind === 'dm') {
    return [...channel.memberWorkerIds]
  }

  const members = new Set(channel.memberWorkerIds)

  return [...new Set(mentioned)].filter(workerId => members.has(workerId))
}

export function channelEventTargets(channel: AtlasChannel, entry: Pick<ChannelTranscriptEntry, 'sender' | 'text'>) {
  if (entry.sender.kind === 'worker') {
    return []
  }

  return channelTurnTargets(channel, mentionedWorkerIds(entry.text))
}

export function channelMessageParts(
  text: string
): Array<{ kind: 'mention'; workerId: string } | { kind: 'text'; text: string }> {
  const parts: Array<{ kind: 'mention'; workerId: string } | { kind: 'text'; text: string }> = []
  let cursor = 0

  for (const match of text.matchAll(MENTION_TOKEN_RE)) {
    const index = match.index ?? 0
    const workerId = decodeWorkerId(match[1] || '')

    if (index > cursor) {
      parts.push({ kind: 'text', text: text.slice(cursor, index) })
    }

    parts.push(workerId ? { kind: 'mention', workerId } : { kind: 'text', text: match[0] })
    cursor = index + match[0].length
  }

  if (cursor < text.length) {
    parts.push({ kind: 'text', text: text.slice(cursor) })
  }

  return parts
}

export function channelContextPrompt(
  channel: AtlasChannel,
  text: string,
  mentioned: readonly string[],
  workers: ReadonlyMap<string, ChannelWorkerIdentity>
): string {
  const channelName = `${channel.kind === 'channel' ? '#' : 'DM:'}${channel.name}`.replace(/\s+/g, ' ').trim()
  const mentionedIds = mentioned.length > 0 ? mentioned.join(',') : 'none'

  const message = channelMessageParts(text)
    .map(part => {
      if (part.kind === 'text') {
        return part.text
      }

      const worker = workers.get(part.workerId)

      return `[mention:${worker?.name || part.workerId} (${part.workerId})]`
    })
    .join('')

  return `[Atlas channel v1 | channel=${channelName} | sender=user | mentioned-worker-ids=${mentionedIds}]\n${message}`
}

async function deliverToWorker(
  channel: AtlasChannel,
  workerId: string,
  prompt: string,
  workers: ReadonlyMap<string, ChannelWorkerIdentity>,
  copy: ChannelRoutingCopy,
  callbacks: ChannelRoutingCallbacks,
  gateway: ChannelRoutingGateway
): Promise<ChannelDeliveryResult> {
  callbacks.onWorkerStart?.(workerId)

  const worker = workers.get(workerId)

  if (!worker) {
    return { status: 'error', text: copy.missingWorker(workerId), workerId }
  }

  const binding = channel.sessionBindings[workerId]
  let session: ChannelWorkerSession

  try {
    if (binding) {
      try {
        session = await gateway.resumeSession(workerId, binding.sessionId)
      } catch (error) {
        // A deleted durable row cannot be resumed. Clear only that stale
        // binding so the next user turn may establish a new channel session;
        // transport/startup failures keep the binding and its history intact.
        if (isSessionMissingError(error)) {
          clearChannelSessionBinding(channel.id, workerId)
        }

        throw error
      }
    } else {
      session = await gateway.createSession(channel, workerId)
      setChannelSessionBinding(channel.id, workerId, session.storedSessionId)
    }

    const response = await gateway.submitPrompt(session, prompt, text => callbacks.onWorkerStream?.(workerId, text))

    return response.trim()
      ? { status: 'response', text: response, workerId }
      : { status: 'error', text: copy.emptyResponse(worker.name), workerId }
  } catch (error) {
    return { status: 'error', text: copy.workerFailed(worker.name, errorDetail(error)), workerId }
  }
}

export async function sendChannelTurn({
  callbacks = {},
  channel,
  copy,
  gateway = desktopChannelGateway,
  text,
  workers
}: SendChannelTurnInput): Promise<ChannelTurnResult> {
  const turnId = entityId('turn')

  const userEntry = appendChannelTranscriptEntry(channel.id, {
    sender: { kind: 'user' },
    text,
    turnId
  })

  if (!userEntry) {
    throw new Error('Channel message could not be recorded')
  }

  const mentioned = mentionedWorkerIds(userEntry.text)
  const targets = channelEventTargets(channel, userEntry)

  if (targets.length === 0) {
    return { deliveries: [], targets, turnId, userEntry }
  }

  const prompt = channelContextPrompt(channel, userEntry.text, mentioned, workers)

  const deliveries = await Promise.all(
    targets.map(workerId => deliverToWorker(channel, workerId, prompt, workers, copy, callbacks, gateway))
  )

  for (const delivery of deliveries) {
    appendChannelTranscriptEntry(channel.id, {
      sender: { kind: 'worker', workerId: delivery.workerId },
      status: delivery.status === 'error' ? 'error' : 'message',
      text: delivery.text,
      turnId
    })
  }

  return { deliveries, targets, turnId, userEntry }
}
