import { WorkerAvatar } from '@/app/profiles/worker-avatar'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import type { ChannelTranscriptEntry } from '@/store/channels'
import type { ProfileInfo } from '@/types/hermes'

import { channelMessageParts } from './routing'

export interface ChannelPendingResponse {
  id: string
  text: string
  workerId: string
}

interface ChannelTranscriptProps {
  messages: ChannelTranscriptEntry[]
  pending?: ChannelPendingResponse[]
  profilesById: Map<string, ProfileInfo>
}

function MessageText({ profilesById, text }: { profilesById: Map<string, ProfileInfo>; text: string }) {
  return (
    <>
      {channelMessageParts(text).map((part, index) => {
        if (part.kind === 'text') {
          return <span key={`${index}-${part.text.slice(0, 12)}`}>{part.text}</span>
        }

        const worker = profilesById.get(part.workerId)

        return (
          <span
            className="mx-0.5 inline-flex items-center rounded-md border border-[#7FA3F5]/24 bg-[#4F7BE8]/13 px-1.5 py-0.5 align-middle font-mono text-[0.78em] font-semibold leading-none text-[#AFC4FA]"
            key={`${index}-${part.workerId}`}
          >
            @{worker?.display_name || worker?.name || part.workerId}
          </span>
        )
      })}
    </>
  )
}

function WorkerPortrait({ profile, workerId }: { profile?: ProfileInfo; workerId: string }) {
  return (
    <WorkerAvatar
      className="size-9 rounded-lg text-xs"
      profile={
        profile ?? {
          display_name: workerId,
          has_avatar: false,
          name: workerId
        }
      }
    />
  )
}

export function ChannelTranscript({ messages, pending = [], profilesById }: ChannelTranscriptProps) {
  const { t } = useI18n()

  return (
    <ol
      aria-label={t.channels.transcriptLabel}
      aria-live="polite"
      className="mx-auto grid w-full max-w-3xl gap-5 px-6 py-8"
    >
      {messages.map(message => {
        const workerId = message.sender.kind === 'worker' ? message.sender.workerId : null
        const worker = workerId ? profilesById.get(workerId) : null
        const senderName = workerId ? worker?.display_name || worker?.name || workerId : t.channels.userName

        return (
          <li className="group/message grid grid-cols-[2.25rem_minmax(0,1fr)] gap-3" key={message.id}>
            {workerId ? (
              <WorkerPortrait profile={worker ?? undefined} workerId={workerId} />
            ) : (
              <span className="grid size-9 place-items-center rounded-lg border border-[#4F7BE8]/35 bg-[#101C3D] font-mono text-[0.5625rem] font-bold tracking-wider text-[#7FA3F5]">
                {t.channels.userAvatarLabel}
              </span>
            )}
            <div className="min-w-0">
              <div className="flex items-baseline gap-2">
                <span className="truncate text-sm font-semibold text-[#F4F2EC]">{senderName}</span>
                <time className="font-mono text-[0.625rem] text-[#7FA3F5]/55" dateTime={message.createdAt}>
                  {new Date(message.createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                </time>
                {message.status === 'error' && (
                  <span className="font-mono text-[0.5625rem] uppercase tracking-[0.12em] text-[#F28B82]/72">
                    {t.channels.deliveryError}
                  </span>
                )}
              </div>
              <p
                className={cn(
                  'mt-1 whitespace-pre-wrap text-sm leading-6',
                  message.status === 'error' ? 'text-[#F6B1AA]/82' : 'text-[#F4F2EC]/78'
                )}
              >
                <MessageText profilesById={profilesById} text={message.text} />
              </p>
            </div>
          </li>
        )
      })}

      {pending.map(response => {
        const worker = profilesById.get(response.workerId)

        return (
          <li
            className="grid grid-cols-[2.25rem_minmax(0,1fr)] gap-3"
            data-worker-stream={response.workerId}
            key={response.id}
          >
            <WorkerPortrait profile={worker} workerId={response.workerId} />
            <div className="min-w-0">
              <div className="flex items-baseline gap-2">
                <span className="truncate text-sm font-semibold text-[#F4F2EC]">
                  {worker?.display_name || worker?.name || response.workerId}
                </span>
                <span className="font-mono text-[0.5625rem] uppercase tracking-[0.12em] text-[#7FA3F5]/55">
                  {t.channels.responding}
                </span>
              </div>
              {response.text ? (
                <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-[#F4F2EC]/78">{response.text}</p>
              ) : (
                <div aria-label={t.channels.responding} className="mt-2 flex items-center gap-1.5">
                  {[0, 1, 2].map(index => (
                    <span
                      className="size-1.5 animate-pulse rounded-full bg-[#7FA3F5]/55"
                      key={index}
                      style={{ animationDelay: `${index * 140}ms` }}
                    />
                  ))}
                </div>
              )}
            </div>
          </li>
        )
      })}
    </ol>
  )
}
