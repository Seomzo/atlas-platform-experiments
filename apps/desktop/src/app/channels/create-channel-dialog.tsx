import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useState } from 'react'

import { WorkerAvatar } from '@/app/profiles/worker-avatar'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { useI18n } from '@/i18n'
import { Hash, MessageCircle, Users } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { type AtlasChannel, type ChannelKind, createChannel, normalizeChannelName } from '@/store/channels'
import { $profiles, refreshProfiles } from '@/store/profile'

interface CreateChannelDialogProps {
  onClose: () => void
  onCreated: (channel: AtlasChannel) => void
  open: boolean
}

export function CreateChannelDialog({ onClose, onCreated, open }: CreateChannelDialogProps) {
  const { t } = useI18n()
  const c = t.channels
  const profiles = useStore($profiles)
  const [kind, setKind] = useState<ChannelKind>('channel')
  const [name, setName] = useState('')
  const [selectedWorkerIds, setSelectedWorkerIds] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [loadFailed, setLoadFailed] = useState(false)
  const [submitted, setSubmitted] = useState(false)

  useEffect(() => {
    if (!open) {
      return
    }

    setKind('channel')
    setName('')
    setSelectedWorkerIds([])
    setSubmitted(false)
    setLoadFailed(false)
    setLoading(true)

    void refreshProfiles()
      .catch(() => setLoadFailed(true))
      .finally(() => setLoading(false))
  }, [open])

  const selectedProfile = useMemo(
    () => profiles.find(profile => profile.name === selectedWorkerIds[0]),
    [profiles, selectedWorkerIds]
  )

  const normalizedName = normalizeChannelName(name)
  const nameMissing = submitted && kind === 'channel' && !normalizedName
  const membersMissing = submitted && selectedWorkerIds.length === 0

  const selectKind = (next: ChannelKind) => {
    setKind(next)

    if (next === 'dm' && selectedWorkerIds.length > 1) {
      setSelectedWorkerIds(selectedWorkerIds.slice(0, 1))
    }
  }

  const toggleWorker = (workerId: string) => {
    if (kind === 'dm') {
      setSelectedWorkerIds([workerId])

      return
    }

    setSelectedWorkerIds(current =>
      current.includes(workerId) ? current.filter(id => id !== workerId) : [...current, workerId]
    )
  }

  const submit = () => {
    setSubmitted(true)

    if ((kind === 'channel' && !normalizedName) || selectedWorkerIds.length === 0) {
      return
    }

    const channel = createChannel({
      kind,
      memberWorkerIds: selectedWorkerIds,
      name: kind === 'dm' ? selectedProfile?.display_name || selectedProfile?.name || selectedWorkerIds[0] : name
    })

    onCreated(channel)
  }

  return (
    <Dialog onOpenChange={value => !value && onClose()} open={open}>
      <DialogContent className="max-w-xl gap-4 overflow-hidden p-0">
        <DialogHeader className="border-b border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary) px-5 py-4 pr-12">
          <DialogTitle icon={Users}>{c.createTitle}</DialogTitle>
          <DialogDescription>{c.createDescription}</DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 px-5">
          <fieldset className="grid gap-2">
            <legend className="mb-1 text-xs font-medium text-(--ui-text-secondary)">{c.kindLabel}</legend>
            <div className="grid grid-cols-2 gap-2">
              {([
                ['channel', Hash, c.kindChannel, c.kindChannelDescription],
                ['dm', MessageCircle, c.kindDm, c.kindDmDescription]
              ] as const).map(([value, Icon, label, description]) => (
                <button
                  aria-pressed={kind === value}
                  className={cn(
                    'flex min-h-16 items-start gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors',
                    kind === value
                      ? 'border-primary/55 bg-primary/10 text-foreground'
                      : 'border-(--ui-stroke-tertiary) bg-(--ui-bg-quaternary) text-(--ui-text-secondary) hover:border-(--ui-stroke-secondary) hover:bg-(--ui-bg-tertiary)'
                  )}
                  key={value}
                  onClick={() => selectKind(value)}
                  type="button"
                >
                  <Icon className="mt-0.5 size-4 shrink-0 text-primary" />
                  <span className="grid gap-0.5">
                    <span className="text-xs font-semibold">{label}</span>
                    <span className="text-[0.6875rem] leading-relaxed text-(--ui-text-tertiary)">{description}</span>
                  </span>
                </button>
              ))}
            </div>
          </fieldset>

          {kind === 'channel' && (
            <label className="grid gap-1.5 text-xs font-medium text-(--ui-text-secondary)" htmlFor="channel-name">
              {c.nameLabel}
              <div className="relative">
                <Hash className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-(--ui-text-quaternary)" />
                <Input
                  aria-invalid={nameMissing}
                  autoFocus
                  className="h-9 pl-8"
                  id="channel-name"
                  maxLength={64}
                  onChange={event => setName(event.target.value)}
                  placeholder={c.namePlaceholder}
                  value={name}
                />
              </div>
              {nameMissing ? (
                <span className="font-normal text-destructive">{c.nameRequired}</span>
              ) : normalizedName && normalizedName !== name.trim() ? (
                <span className="font-mono text-[0.65rem] font-normal text-(--ui-text-quaternary)">#{normalizedName}</span>
              ) : null}
            </label>
          )}

          <fieldset className="grid min-h-0 gap-2">
            <div>
              <legend className="text-xs font-medium text-(--ui-text-secondary)">{c.membersLabel}</legend>
              <p className="mt-0.5 text-[0.6875rem] text-(--ui-text-tertiary)">
                {kind === 'dm' ? c.dmMemberHint : c.membersHint}
              </p>
            </div>

            <div className="max-h-56 overflow-y-auto rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-bg-quinary) p-1">
              {loading && profiles.length === 0 ? (
                <div className="px-3 py-5 text-center text-xs text-(--ui-text-tertiary)">{c.loadingWorkers}</div>
              ) : loadFailed && profiles.length === 0 ? (
                <div className="px-3 py-5 text-center text-xs text-destructive">{c.failedLoadWorkers}</div>
              ) : profiles.length === 0 ? (
                <div className="px-3 py-5 text-center text-xs text-(--ui-text-tertiary)">{c.noWorkers}</div>
              ) : (
                profiles.map(profile => {
                  const checked = selectedWorkerIds.includes(profile.name)

                  return (
                    <label
                      className={cn(
                        'flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-2 transition-colors hover:bg-(--ui-control-hover-background)',
                        checked && 'bg-(--ui-control-active-background)'
                      )}
                      key={profile.name}
                    >
                      <Checkbox
                        checked={checked}
                        onCheckedChange={() => toggleWorker(profile.name)}
                        role={kind === 'dm' ? 'radio' : 'checkbox'}
                      />
                      <WorkerAvatar className="size-7 rounded-md text-[0.6875rem]" profile={profile} />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-xs font-medium text-foreground">
                          {profile.display_name || profile.name}
                        </span>
                        <span className="block truncate text-[0.65rem] text-(--ui-text-tertiary)">
                          {profile.role || c.workerFallbackRole}
                        </span>
                      </span>
                    </label>
                  )
                })
              )}
            </div>
            {membersMissing && <span className="text-xs text-destructive">{c.membersRequired}</span>}
          </fieldset>
        </div>

        <DialogFooter className="border-t border-(--ui-stroke-tertiary) bg-(--ui-bg-quinary) px-5 py-3">
          <Button onClick={onClose} variant="ghost">
            {t.common.cancel}
          </Button>
          <Button onClick={submit}>{kind === 'dm' ? c.createDmAction : c.createAction}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
