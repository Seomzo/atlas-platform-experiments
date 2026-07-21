import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { WorkerAvatar } from '@/app/profiles/worker-avatar'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { Tip } from '@/components/ui/tooltip'
import { useI18n } from '@/i18n'
import { AtSign, Hash, MessageCircle, Settings2, Users } from '@/lib/icons'
import { $channels, archiveChannel, type AtlasChannel } from '@/store/channels'
import { $profiles, refreshProfiles } from '@/store/profile'
import type { ProfileInfo } from '@/types/hermes'

import { NEW_CHAT_ROUTE } from '../routes'

import { ChannelComposer } from './channel-composer'
import { type ChannelWorkerIdentity, directMessageWorkerId, sendChannelTurn } from './routing'
import { type ChannelPendingResponse, ChannelTranscript } from './transcript'

export function ChannelsView() {
  const { channelId } = useParams<{ channelId: string }>()
  const channels = useStore($channels)
  const profiles = useStore($profiles)
  const channel = channels.find(item => item.id === channelId && !item.archived) ?? null
  const profilesById = useMemo(() => new Map(profiles.map(profile => [profile.name, profile])), [profiles])

  useEffect(() => {
    void refreshProfiles().catch(() => undefined)
  }, [])

  if (!channel) {
    return <ChannelNotFound />
  }

  return <ChannelWorkspace channel={channel} profilesById={profilesById} />
}

function ChannelWorkspace({
  channel,
  profilesById
}: {
  channel: AtlasChannel
  profilesById: Map<string, ProfileInfo>
}) {
  const { t } = useI18n()
  const c = t.channels
  const navigate = useNavigate()
  const scrollRef = useRef<HTMLElement>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [sending, setSending] = useState(false)
  const [pending, setPending] = useState<ChannelPendingResponse[]>([])

  const memberProfiles = channel.memberWorkerIds
    .map(id => profilesById.get(id))
    .filter((profile): profile is ProfileInfo => Boolean(profile))

  const workers = useMemo(
    () =>
      new Map<string, ChannelWorkerIdentity>(
        memberProfiles.map(profile => [profile.name, { id: profile.name, name: profile.display_name || profile.name }])
      ),
    [memberProfiles]
  )

  const dmWorkerId = directMessageWorkerId(channel)
  const dmWorker = dmWorkerId ? profilesById.get(dmWorkerId) : null
  const allMembersDeleted = memberProfiles.length === 0

  useEffect(() => {
    const viewport = scrollRef.current

    if (viewport) {
      viewport.scrollTop = viewport.scrollHeight
    }
  }, [channel.transcript.length, pending])

  const send = async (text: string) => {
    setSending(true)
    setPending([])

    try {
      return await sendChannelTurn({
        callbacks: {
          onWorkerStart: workerId =>
            setPending(current =>
              current.some(item => item.workerId === workerId)
                ? current
                : [...current, { id: `pending-${channel.id}-${workerId}`, text: '', workerId }]
            ),
          onWorkerStream: (workerId, streamedText) =>
            setPending(current =>
              current.map(item => (item.workerId === workerId ? { ...item, text: streamedText } : item))
            )
        },
        channel,
        copy: {
          emptyResponse: c.workerEmptyResponse,
          missingWorker: c.workerMissing,
          workerFailed: c.workerFailed
        },
        text,
        workers
      })
    } finally {
      setPending([])
      setSending(false)
    }
  }

  return (
    <section className="relative flex h-full min-h-0 flex-col overflow-hidden bg-[#060B1A] text-[#F4F2EC]">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 opacity-55"
        style={{
          backgroundImage:
            'radial-gradient(circle at 20% 0%, rgba(79,123,232,.22), transparent 32rem), linear-gradient(rgba(127,163,245,.035) 1px, transparent 1px), linear-gradient(90deg, rgba(127,163,245,.035) 1px, transparent 1px)',
          backgroundSize: 'auto, 28px 28px, 28px 28px'
        }}
      />

      <header className="relative z-10 shrink-0 border-b border-[#7FA3F5]/15 bg-[#0A1228]/92 px-5 pb-3 pt-[calc(var(--titlebar-height)+0.75rem)] backdrop-blur-xl">
        <div className="mx-auto flex max-w-5xl items-center gap-4">
          <div className="grid size-10 shrink-0 place-items-center rounded-xl border border-[#4F7BE8]/35 bg-[#101C3D] shadow-[inset_0_1px_0_rgba(255,255,255,.08),0_12px_30px_rgba(0,0,0,.25)]">
            {channel.kind === 'channel' ? (
              <Hash className="size-5 text-[#7FA3F5]" />
            ) : dmWorker ? (
              <WorkerAvatar className="size-full rounded-xl text-sm" profile={dmWorker} />
            ) : (
              <MessageCircle className="size-5 text-[#7FA3F5]" />
            )}
          </div>

          <div className="min-w-0 flex-1">
            <div className="font-mono text-[0.5625rem] font-semibold uppercase tracking-[0.2em] text-[#7FA3F5]/65">
              {c.title} / {channel.kind === 'channel' ? c.kindChannel : c.kindDm}
            </div>
            <h1 className="mt-0.5 truncate text-lg font-semibold tracking-tight text-[#F4F2EC]">
              {channel.kind === 'channel' ? `# ${channel.name}` : channel.name}
            </h1>
          </div>

          <div className="flex shrink-0 items-center">
            <div className="mr-3 hidden items-center sm:flex">
              <span className="relative z-10 grid size-7 place-items-center rounded-full border-2 border-[#0A1228] bg-[#4F7BE8] font-mono text-[0.5rem] font-bold tracking-wide text-white">
                {c.userAvatarLabel}
              </span>
              {memberProfiles.slice(0, 4).map((profile, index) => (
                <WorkerAvatar
                  className="-ml-2 size-7 rounded-full border-2 border-[#0A1228] text-[0.5625rem]"
                  key={profile.name}
                  profile={profile}
                  style={{ zIndex: 9 - index }}
                />
              ))}
              <span className="ml-2 font-mono text-[0.625rem] text-[#F4F2EC]/50">
                {c.memberCount(channel.memberWorkerIds.length + 1)}
              </span>
            </div>
            <Tip label={c.settings}>
              <Button
                aria-label={c.settings}
                className="border-[#7FA3F5]/20 bg-[#101C3D]/80 text-[#7FA3F5] hover:bg-[#14244C] hover:text-white"
                onClick={() => setSettingsOpen(true)}
                size="icon-sm"
                variant="outline"
              >
                <Settings2 className="size-4" />
              </Button>
            </Tip>
          </div>
        </div>
      </header>

      <main className="relative z-0 min-h-0 flex-1 overflow-y-auto" ref={scrollRef}>
        {allMembersDeleted && (
          <div className="mx-auto mt-6 flex max-w-3xl items-start gap-3 rounded-xl border border-[#F3C77A]/22 bg-[#F3C77A]/7 px-4 py-3 text-[#F7DEAE]">
            <Users className="mt-0.5 size-4 shrink-0" />
            <div>
              <div className="text-xs font-semibold">{c.noActiveMembersTitle}</div>
              <p className="mt-0.5 text-xs leading-5 text-[#F7DEAE]/68">{c.noActiveMembersDescription}</p>
            </div>
          </div>
        )}

        {channel.transcript.length === 0 && pending.length === 0 && (
          <div className="mx-auto grid max-w-xl place-items-center px-6 py-16 text-center">
            <div className="grid size-12 place-items-center rounded-2xl border border-[#4F7BE8]/35 bg-[#101C3D] text-[#7FA3F5]">
              {channel.kind === 'dm' ? <MessageCircle className="size-6" /> : <Hash className="size-6" />}
            </div>
            <h2 className="mt-4 text-base font-semibold text-[#F4F2EC]">{c.emptyTranscriptTitle}</h2>
            <p className="mt-2 max-w-md text-sm leading-6 text-[#F4F2EC]/52">
              {channel.kind === 'dm'
                ? c.emptyDmTranscriptDescription(dmWorker?.display_name || dmWorker?.name || channel.name)
                : c.emptyChannelTranscriptDescription}
            </p>
          </div>
        )}

        <ChannelTranscript messages={channel.transcript} pending={pending} profilesById={profilesById} />
      </main>

      <ChannelComposer channel={channel} memberProfiles={memberProfiles} onSend={send} sending={sending} />
      <ChannelSettingsDialog channel={channel} onClose={() => setSettingsOpen(false)} open={settingsOpen} />
    </section>
  )
}

function ChannelSettingsDialog({
  channel,
  onClose,
  open
}: {
  channel: AtlasChannel
  onClose: () => void
  open: boolean
}) {
  const { t } = useI18n()
  const c = t.channels
  const navigate = useNavigate()

  return (
    <Dialog onOpenChange={value => !value && onClose()} open={open}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle icon={Settings2}>{c.settings}</DialogTitle>
          <DialogDescription>{c.settingsDescription}</DialogDescription>
        </DialogHeader>

        <div className="rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-bg-quaternary) px-3 py-2.5">
          <div className="flex items-center gap-2 text-xs font-semibold text-(--ui-text-secondary)">
            <AtSign className="size-3.5 text-primary" />
            {c.turnPolicyMentionOnly}
          </div>
          <p className="mt-1 text-[0.6875rem] leading-relaxed text-(--ui-text-tertiary)">
            {channel.kind === 'dm' ? c.dmTurnPolicyDescription : c.turnPolicyMentionOnlyDescription}
          </p>
        </div>

        <DialogFooter className="mt-2 justify-between sm:justify-between">
          <Button
            className="text-destructive hover:bg-destructive/10 hover:text-destructive"
            onClick={() => {
              archiveChannel(channel.id)
              onClose()
              navigate(NEW_CHAT_ROUTE)
            }}
            variant="ghost"
          >
            {c.archive}
          </Button>
          <Button onClick={onClose}>{t.common.done}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ChannelNotFound() {
  const { t } = useI18n()
  const navigate = useNavigate()

  return (
    <section className="grid h-full place-items-center bg-[#060B1A] px-6 text-center text-[#F4F2EC]">
      <div>
        <Hash className="mx-auto size-8 text-[#7FA3F5]/60" />
        <h1 className="mt-4 text-base font-semibold">{t.channels.notFoundTitle}</h1>
        <p className="mt-2 text-sm text-[#F4F2EC]/55">{t.channels.notFoundDescription}</p>
        <Button className="mt-5" onClick={() => navigate(NEW_CHAT_ROUTE)}>
          {t.channels.backToChat}
        </Button>
      </div>
    </section>
  )
}
