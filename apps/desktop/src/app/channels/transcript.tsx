import { WorkerAvatar } from '@/app/profiles/worker-avatar'
import { useI18n } from '@/i18n'
import type { ProfileInfo } from '@/types/hermes'

export interface ChannelTranscriptMessage {
  createdAt: string
  id: string
  sender: { kind: 'user' } | { kind: 'worker'; workerId: string }
  text: string
}

interface ChannelTranscriptProps {
  messages: ChannelTranscriptMessage[]
  profilesById: Map<string, ProfileInfo>
}

export function ChannelTranscript({ messages, profilesById }: ChannelTranscriptProps) {
  const { t } = useI18n()

  return (
    <ol aria-label={t.channels.transcriptLabel} className="mx-auto grid w-full max-w-3xl gap-5 px-6 py-8">
      {messages.map(message => {
        const worker = message.sender.kind === 'worker' ? profilesById.get(message.sender.workerId) : null

        const senderName =
          message.sender.kind === 'worker'
            ? worker?.display_name || worker?.name || message.sender.workerId
            : t.channels.userName

        return (
          <li className="group/message grid grid-cols-[2.25rem_minmax(0,1fr)] gap-3" key={message.id}>
            {worker ? (
              <WorkerAvatar className="size-9 rounded-lg text-xs" profile={worker} />
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
              </div>
              <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-[#F4F2EC]/78">{message.text}</p>
            </div>
          </li>
        )
      })}
    </ol>
  )
}
