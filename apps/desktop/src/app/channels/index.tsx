import { useStore } from '@nanostores/react'
import { useMemo, useState } from 'react'
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { Tip } from '@/components/ui/tooltip'
import { useI18n } from '@/i18n'
import { AtSign, Hash, MessageCircle, Send, Settings2, Users } from '@/lib/icons'
import { cn } from '@/lib/utils'
import {
  $channels,
  archiveChannel,
  type AtlasChannel,
  type ChannelTurnPolicy,
  setChannelTurnPolicy
} from '@/store/channels'
import { $profiles, newSessionInProfile } from '@/store/profile'
import type { ProfileInfo } from '@/types/hermes'

import { NEW_CHAT_ROUTE } from '../routes'

import { directMessageWorkerId } from './routing'
import { ChannelTranscript, type ChannelTranscriptMessage } from './transcript'

const EMPTY_TRANSCRIPT: ChannelTranscriptMessage[] = []

export function ChannelsView() {
  const { channelId } = useParams<{ channelId: string }>()
  const channels = useStore($channels)
  const profiles = useStore($profiles)
  const channel = channels.find(item => item.id === channelId && !item.archived) ?? null
  const profilesById = useMemo(() => new Map(profiles.map(profile => [profile.name, profile])), [profiles])

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
  const [settingsOpen, setSettingsOpen] = useState(false)

  const memberProfiles = channel.memberWorkerIds
    .map(id => profilesById.get(id))
    .filter((profile): profile is ProfileInfo => Boolean(profile))

  const dmWorkerId = directMessageWorkerId(channel)
  const dmWorker = dmWorkerId ? profilesById.get(dmWorkerId) : null

  const openLiveWorkerChat = () => {
    if (!dmWorkerId || !newSessionInProfile(dmWorkerId)) {
      return
    }

    navigate(NEW_CHAT_ROUTE)
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

      <main className="relative z-0 flex min-h-0 flex-1 flex-col overflow-y-auto">
        <ChannelTranscript messages={EMPTY_TRANSCRIPT} profilesById={profilesById} />
        <div className="grid min-h-0 flex-1 place-items-center px-6 py-10">
          <div className="w-full max-w-xl overflow-hidden rounded-2xl border border-[#7FA3F5]/18 bg-[#0A1228]/82 shadow-[0_28px_80px_rgba(0,0,0,.35)] backdrop-blur-xl">
            <div className="h-px bg-linear-to-r from-transparent via-[#4F7BE8]/80 to-transparent" />
            <div className="px-7 py-8 text-center">
              <div className="mx-auto grid size-12 place-items-center rounded-2xl border border-[#4F7BE8]/35 bg-[#101C3D] text-[#7FA3F5]">
                {channel.kind === 'dm' ? <MessageCircle className="size-6" /> : <Users className="size-6" />}
              </div>
              <div className="mt-4 font-mono text-[0.5625rem] font-semibold uppercase tracking-[0.22em] text-[#7FA3F5]/60">
                {c.scaffoldBadge}
              </div>
              <h2 className="mt-2 text-base font-semibold text-[#F4F2EC]">
                {channel.kind === 'dm' ? c.dmReadyTitle : c.routingInactiveTitle}
              </h2>
              <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-[#F4F2EC]/58">
                {channel.kind === 'dm'
                  ? c.dmReadyDescription(dmWorker?.display_name || dmWorker?.name || channel.name)
                  : c.routingInactiveDescription}
              </p>

              {channel.kind === 'dm' && dmWorkerId ? (
                <Button
                  className="mt-5 bg-[#4F7BE8] text-white shadow-[0_12px_28px_rgba(79,123,232,.24)] hover:bg-[#628CF0]"
                  onClick={openLiveWorkerChat}
                >
                  <MessageCircle className="size-4" />
                  {c.openWorkerChat(dmWorker?.display_name || dmWorker?.name || channel.name)}
                </Button>
              ) : (
                <div className="mt-5 inline-flex items-center gap-2 rounded-full border border-[#7FA3F5]/15 bg-[#101C3D]/72 px-3 py-1.5 font-mono text-[0.625rem] text-[#7FA3F5]/75">
                  <AtSign className="size-3.5" />
                  {c.turnPolicyMentionOnly}
                </div>
              )}
            </div>
          </div>
        </div>
      </main>

      <ChannelComposer channel={channel} memberProfiles={memberProfiles} />
      <ChannelSettingsDialog channel={channel} onClose={() => setSettingsOpen(false)} open={settingsOpen} />
    </section>
  )
}

function ChannelComposer({ channel, memberProfiles }: { channel: AtlasChannel; memberProfiles: ProfileInfo[] }) {
  const { t } = useI18n()
  const c = t.channels
  const [draft, setDraft] = useState('')
  const [mentionsOpen, setMentionsOpen] = useState(false)

  const insertMention = (name: string) => {
    setDraft(value => `${value}${value && !value.endsWith(' ') ? ' ' : ''}@${name} `)
    setMentionsOpen(false)
  }

  return (
    <footer className="relative z-20 shrink-0 border-t border-[#7FA3F5]/14 bg-[#0A1228]/94 px-4 pb-4 pt-3 backdrop-blur-xl">
      <div className="relative mx-auto max-w-3xl">
        {mentionsOpen && (
          <div className="absolute bottom-[calc(100%+0.5rem)] left-0 w-64 overflow-hidden rounded-xl border border-[#7FA3F5]/20 bg-[#101C3D] p-1 shadow-2xl">
            <div className="px-2 py-1.5 font-mono text-[0.5625rem] font-semibold uppercase tracking-[0.15em] text-[#7FA3F5]/60">
              {c.mentionWorker}
            </div>
            {memberProfiles.map(profile => (
              <button
                className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs text-[#F4F2EC]/78 hover:bg-[#4F7BE8]/15 hover:text-white"
                key={profile.name}
                onClick={() => insertMention(profile.display_name || profile.name)}
                type="button"
              >
                <WorkerAvatar className="size-6 rounded-md text-[0.5625rem]" profile={profile} />
                <span className="truncate">{profile.display_name || profile.name}</span>
              </button>
            ))}
          </div>
        )}

        <div className="rounded-2xl border border-[#7FA3F5]/20 bg-[#101C3D]/88 p-2 shadow-[0_16px_45px_rgba(0,0,0,.28)] focus-within:border-[#4F7BE8]/65">
          <Textarea
            aria-label={c.composerPlaceholder(channel.name)}
            className="min-h-16 resize-none border-0 bg-transparent px-2 py-1.5 text-sm text-[#F4F2EC] shadow-none placeholder:text-[#F4F2EC]/28 focus-visible:ring-0"
            onChange={event => setDraft(event.target.value)}
            placeholder={c.composerPlaceholder(channel.name)}
            spellCheck
            value={draft}
          />
          <div className="flex items-center gap-2 border-t border-[#7FA3F5]/10 px-1 pt-2">
            <Tip label={c.mentionWorker}>
              <Button
                aria-expanded={mentionsOpen}
                aria-label={c.mentionWorker}
                className={cn(
                  'text-[#7FA3F5]/70 hover:bg-[#4F7BE8]/15 hover:text-[#7FA3F5]',
                  mentionsOpen && 'bg-[#4F7BE8]/15 text-[#7FA3F5]'
                )}
                onClick={() => setMentionsOpen(value => !value)}
                size="icon-xs"
                variant="ghost"
              >
                <AtSign className="size-4" />
              </Button>
            </Tip>
            <span className="min-w-0 flex-1 truncate font-mono text-[0.5625rem] text-[#F4F2EC]/32">
              {channel.kind === 'dm' ? c.dmComposerPending : c.composerPending}
            </span>
            <Tip label={c.sendUnavailable}>
              <span>
                <Button
                  aria-label={c.sendUnavailable}
                  className="bg-[#4F7BE8]/25 text-[#7FA3F5]/45"
                  disabled
                  size="icon-sm"
                >
                  <Send className="size-4" />
                </Button>
              </span>
            </Tip>
          </div>
        </div>
      </div>
    </footer>
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

  const updatePolicy = (value: string) => setChannelTurnPolicy(channel.id, value as ChannelTurnPolicy)

  return (
    <Dialog onOpenChange={value => !value && onClose()} open={open}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle icon={Settings2}>{c.settings}</DialogTitle>
          <DialogDescription>{c.settingsDescription}</DialogDescription>
        </DialogHeader>

        <label className="grid gap-1.5 text-xs font-medium text-(--ui-text-secondary)">
          {c.turnPolicy}
          <Select onValueChange={updatePolicy} value={channel.settings.turnPolicy}>
            <SelectTrigger className="h-9">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="mention-only">{c.turnPolicyMentionOnly}</SelectItem>
              <SelectItem value="all-members">{c.turnPolicyAllMembers}</SelectItem>
            </SelectContent>
          </Select>
          <span className="font-normal leading-relaxed text-(--ui-text-tertiary)">
            {channel.settings.turnPolicy === 'mention-only'
              ? c.turnPolicyMentionOnlyDescription
              : c.turnPolicyAllMembersDescription}
          </span>
        </label>

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
