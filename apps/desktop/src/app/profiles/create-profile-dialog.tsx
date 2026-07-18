import { useEffect, useState } from 'react'

import { ActionStatus } from '@/components/ui/action-status'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { createProfile, updateProfileIdentity, updateProfileSoul } from '@/hermes'
import { useI18n } from '@/i18n'
import { AlertTriangle } from '@/lib/icons'
import { cn } from '@/lib/utils'
import type { ProfileInfo } from '@/types/hermes'

const PROFILE_NAME_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/

export function isValidProfileName(name: string): boolean {
  return PROFILE_NAME_RE.test(name.trim())
}

// Self-contained create flow (name + clone toggle + optional SOUL.md). Owns the
// createProfile/updateProfileSoul calls so every caller just refreshes/selects
// via onCreated. SOUL left blank keeps the cloned/blank persona untouched.
export function CreateProfileDialog({
  onClose,
  onCreated,
  open,
  profiles = []
}: {
  onClose: () => void
  onCreated?: (name: string) => Promise<void> | void
  open: boolean
  profiles?: ProfileInfo[]
}) {
  const { t } = useI18n()
  const p = t.profiles
  const [name, setName] = useState('')
  const [cloneFrom, setCloneFrom] = useState<null | string>('default')
  const [soul, setSoul] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [role, setRole] = useState('')
  const [tagline, setTagline] = useState('')
  const [step, setStep] = useState<'identity' | 'profile'>('profile')
  const [status, setStatus] = useState<'done' | 'idle' | 'saving'>('idle')
  const [error, setError] = useState<null | string>(null)

  useEffect(() => {
    if (!open) {
      return
    }

    setName('')
    setCloneFrom('default')
    setSoul('')
    setDisplayName('')
    setRole('')
    setTagline('')
    setStep('profile')
    setError(null)
    setStatus('idle')
  }, [open])

  const trimmed = name.trim()
  const invalid = trimmed !== '' && !isValidProfileName(trimmed)
  const busy = status === 'saving' || status === 'done'

  function continueToIdentity() {
    if (!trimmed || invalid) {
      setError(invalid ? p.invalidName(p.nameHint) : p.nameRequired)

      return
    }

    setDisplayName(current => current || trimmed)
    setError(null)
    setStep('identity')
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()

    if (step === 'profile') {
      continueToIdentity()

      return
    }

    if (!trimmed || invalid) {
      setError(invalid ? p.invalidName(p.nameHint) : p.nameRequired)

      return
    }

    if (!displayName.trim()) {
      setError(p.nameRequired)

      return
    }

    setStatus('saving')
    setError(null)

    try {
      await createProfile({ name: trimmed, clone_from: cloneFrom })

      await updateProfileIdentity(trimmed, {
        display_name: displayName,
        role,
        tagline
      })

      if (soul.trim()) {
        await updateProfileSoul(trimmed, soul)
      }

      await onCreated?.(trimmed)
      setStatus('done')
      window.setTimeout(onClose, 800)
    } catch (err) {
      setStatus('idle')
      setError(err instanceof Error ? err.message : p.failedCreate)
    }
  }

  return (
    <Dialog onOpenChange={value => !value && !busy && onClose()} open={open}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{p.newProfile}</DialogTitle>
          <DialogDescription>{step === 'profile' ? p.createDesc : p.createIdentityDesc}</DialogDescription>
        </DialogHeader>

        <form className="grid gap-4" onSubmit={handleSubmit}>
          {step === 'profile' ? (
            <>
              <div className="grid gap-1.5">
                <label className="text-xs font-medium" htmlFor="new-profile-name">
                  {p.workerIdLabel}
                </label>
                <Input
                  aria-invalid={invalid}
                  autoFocus
                  id="new-profile-name"
                  onChange={event => setName(event.target.value)}
                  placeholder={p.namePlaceholder}
                  value={name}
                />
                <p className={cn('text-[0.66rem] leading-4', invalid ? 'text-destructive' : 'text-muted-foreground')}>
                  {p.nameHint}
                </p>
              </div>

              <div className="grid gap-1.5">
                <label className="text-xs font-medium" htmlFor="new-profile-clone-from">
                  {p.cloneFrom}
                </label>
                <Select
                  onValueChange={value => setCloneFrom(value === '__none__' ? null : value)}
                  value={cloneFrom ?? '__none__'}
                >
                  <SelectTrigger className="h-9 rounded-md" id="new-profile-clone-from">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">{p.cloneFromNone}</SelectItem>
                    {profiles.map(profile => (
                      <SelectItem key={profile.name} value={profile.name}>
                        {profile.display_name || profile.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">{p.cloneFromDesc}</p>
              </div>

              <div className="grid gap-1.5">
                <label className="text-xs font-medium" htmlFor="new-profile-soul">
                  SOUL.md <span className="font-normal text-muted-foreground">- {p.soulOptional}</span>
                </label>
                <Textarea
                  className="min-h-28 font-mono text-xs leading-5"
                  id="new-profile-soul"
                  onChange={event => setSoul(event.target.value)}
                  placeholder={p.soulPlaceholder(cloneFrom ? p.soulPlaceholderCloned : p.soulPlaceholderEmpty)}
                  value={soul}
                />
              </div>
            </>
          ) : (
            <>
              <div className="grid gap-1.5">
                <label className="text-xs font-medium" htmlFor="new-worker-display-name">
                  {p.displayNameLabel}
                </label>
                <Input
                  autoFocus
                  id="new-worker-display-name"
                  maxLength={80}
                  onChange={event => setDisplayName(event.target.value)}
                  placeholder={p.displayNamePlaceholder}
                  value={displayName}
                />
              </div>
              <div className="grid gap-1.5">
                <label className="text-xs font-medium" htmlFor="new-worker-role">
                  {p.roleLabel}
                </label>
                <Input
                  id="new-worker-role"
                  maxLength={80}
                  onChange={event => setRole(event.target.value)}
                  placeholder={p.rolePlaceholder}
                  value={role}
                />
              </div>
              <div className="grid gap-1.5">
                <label className="text-xs font-medium" htmlFor="new-worker-tagline">
                  {p.taglineLabel}
                </label>
                <Textarea
                  id="new-worker-tagline"
                  maxLength={200}
                  onChange={event => setTagline(event.target.value)}
                  placeholder={p.taglinePlaceholder}
                  value={tagline}
                />
              </div>
            </>
          )}

          {error && (
            <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <DialogFooter>
            <Button disabled={busy} onClick={onClose} type="button" variant="ghost">
              {t.common.cancel}
            </Button>
            {step === 'identity' ? (
              <Button disabled={busy} onClick={() => setStep('profile')} type="button" variant="outline">
                {p.back}
              </Button>
            ) : null}
            {step === 'profile' ? (
              <Button disabled={busy || !trimmed || invalid} onClick={continueToIdentity} type="button">
                {p.next}
              </Button>
            ) : (
              <Button disabled={busy || !displayName.trim()} type="submit">
                <ActionStatus busy={p.creating} done={p.created} idle={p.createAction} state={status} />
              </Button>
            )}
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
