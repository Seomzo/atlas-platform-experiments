import { useStore } from '@nanostores/react'
import { useMemo, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import { WorkerAvatar } from '@/app/profiles/worker-avatar'
import { Button } from '@/components/ui/button'
import { SidebarGroup, SidebarGroupContent } from '@/components/ui/sidebar'
import { Tip } from '@/components/ui/tooltip'
import { useI18n } from '@/i18n'
import { Hash, Plus } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { $channels, type AtlasChannel } from '@/store/channels'
import { $profiles } from '@/store/profile'

import { CreateChannelDialog } from '../../channels/create-channel-dialog'
import { channelRoute, routeChannelId } from '../../routes'
import { SidebarPanelLabel } from '../../shell/sidebar-label'

export function SidebarChannelsSection() {
  const { t } = useI18n()
  const c = t.channels
  const channels = useStore($channels)
  const profiles = useStore($profiles)
  const location = useLocation()
  const navigate = useNavigate()
  const [createOpen, setCreateOpen] = useState(false)
  const activeChannelId = routeChannelId(location.pathname)

  const visible = useMemo(
    () => channels.filter(channel => !channel.archived).sort((a, b) => a.createdAt.localeCompare(b.createdAt)),
    [channels]
  )

  const namedChannels = visible.filter(channel => channel.kind === 'channel')
  const directMessages = visible.filter(channel => channel.kind === 'dm')
  const profilesById = useMemo(() => new Map(profiles.map(profile => [profile.name, profile])), [profiles])

  const openChannel = (channel: AtlasChannel) => navigate(channelRoute(channel.id))

  return (
    <>
      <SidebarGroup className="shrink-0 p-0 pb-1">
        <div className="group/section flex shrink-0 items-center justify-between pb-1 pt-1.5">
          <div className="flex min-w-0 items-center gap-1.5">
            <SidebarPanelLabel>{c.sidebarTitle}</SidebarPanelLabel>
            <span className="text-[0.6875rem] font-medium text-(--ui-text-quaternary)">{visible.length}</span>
          </div>
          <Tip label={c.newChannel}>
            <Button
              aria-label={c.newChannel}
              className="size-5 text-(--ui-text-tertiary) opacity-0 transition-opacity group-hover/section:opacity-100 focus-visible:opacity-100"
              onClick={() => setCreateOpen(true)}
              size="icon"
              variant="ghost"
            >
              <Plus className="size-3.5" />
            </Button>
          </Tip>
        </div>

        <SidebarGroupContent className="grid gap-2 pb-1.5">
          <ChannelGroup
            activeChannelId={activeChannelId}
            channels={namedChannels}
            emptyLabel={c.emptyChannels}
            label={c.channelsLabel}
            onOpen={openChannel}
            profilesById={profilesById}
          />
          <ChannelGroup
            activeChannelId={activeChannelId}
            channels={directMessages}
            emptyLabel={c.emptyDms}
            label={c.directMessages}
            onOpen={openChannel}
            profilesById={profilesById}
          />
        </SidebarGroupContent>
      </SidebarGroup>

      <CreateChannelDialog
        onClose={() => setCreateOpen(false)}
        onCreated={channel => {
          setCreateOpen(false)
          openChannel(channel)
        }}
        open={createOpen}
      />
    </>
  )
}

function ChannelGroup({
  activeChannelId,
  channels,
  emptyLabel,
  label,
  onOpen,
  profilesById
}: {
  activeChannelId: null | string
  channels: AtlasChannel[]
  emptyLabel: string
  label: string
  onOpen: (channel: AtlasChannel) => void
  profilesById: Map<string, ReturnType<typeof $profiles.get>[number]>
}) {
  return (
    <div>
      <div className="px-2 pb-0.5 text-[0.625rem] font-semibold uppercase tracking-[0.14em] text-(--ui-text-quaternary)">
        {label}
      </div>
      {channels.length === 0 ? (
        <div className="min-h-6 px-2 py-1 text-[0.6875rem] text-(--ui-text-quaternary)">{emptyLabel}</div>
      ) : (
        <div className="grid gap-px">
          {channels.map(channel => {
            const active = activeChannelId === channel.id
            const workerId = channel.kind === 'dm' ? channel.memberWorkerIds[0] : null
            const profile = workerId ? profilesById.get(workerId) : null

            return (
              <button
                className={cn(
                  'group/channel flex min-h-[1.75rem] min-w-0 items-center gap-2 rounded-md border border-transparent px-2 py-1 text-left text-[0.8125rem] text-(--ui-text-secondary) transition-colors hover:bg-(--ui-control-hover-background) hover:text-foreground',
                  active &&
                    'border-(--ui-stroke-tertiary) bg-(--ui-control-active-background) text-foreground shadow-[inset_2px_0_0_0_#4F7BE8]'
                )}
                key={channel.id}
                onClick={() => onOpen(channel)}
                type="button"
              >
                {channel.kind === 'channel' ? (
                  <Hash className="size-3.5 shrink-0 text-[#7FA3F5]" />
                ) : (
                  <WorkerAvatar
                    className="size-4 rounded-[4px] text-[0.5rem]"
                    profile={
                      profile ?? {
                        display_name: channel.name,
                        has_avatar: false,
                        name: workerId ?? channel.id
                      }
                    }
                  />
                )}
                <span className="min-w-0 flex-1 truncate">{channel.name}</span>
                <span
                  aria-label="0"
                  className="min-w-4 rounded-full border border-(--ui-stroke-quaternary) px-1 text-center font-mono text-[0.5625rem] leading-3.5 text-(--ui-text-quaternary)"
                >
                  0
                </span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
