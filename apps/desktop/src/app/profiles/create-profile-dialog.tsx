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
import { createProfile, getProfileNameSuggestion, updateProfileIdentity, updateProfileSoul } from '@/hermes'
import { useI18n } from '@/i18n'
import { AlertTriangle } from '@/lib/icons'
import type { ProfileInfo } from '@/types/hermes'

const PROFILE_NAME_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/

export function isValidProfileName(name: string): boolean {
  return PROFILE_NAME_RE.test(name.trim())
}

export function deriveProfileName(displayName: string): string {
  const derived = displayName
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64)
    .replace(/-+$/g, '')

  return derived || 'worker'
}

type ProfileCreateErrorKind = 'generic' | 'invalid' | 'reserved' | 'taken'

export function profileCreateErrorKind(error: unknown): ProfileCreateErrorKind {
  let message = error instanceof Error ? error.message : typeof error === 'string' ? error : ''

  if (!message && error && typeof error === 'object') {
    try {
      message = JSON.stringify(error)
    } catch {
      message = ''
    }
  }

  if (/\breserved\b/i.test(message)) {
    return 'reserved'
  }

  if (/already exists|already in use|file exists/i.test(message)) {
    return 'taken'
  }

  if (/invalid profile name|must match \[a-z0-9\]/i.test(message)) {
    return 'invalid'
  }

  return 'generic'
}

// Self-contained create flow (identity + clone toggle + optional SOUL.md). Owns the
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
  const [workerName, setWorkerName] = useState('')
  const [cloneFrom, setCloneFrom] = useState<null | string>('default')
  const [soul, setSoul] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [role, setRole] = useState('')
  const [tagline, setTagline] = useState('')
  const [step, setStep] = useState<'identity' | 'profile'>('identity')
  const [status, setStatus] = useState<'done' | 'idle' | 'saving'>('idle')
  const [error, setError] = useState<null | string>(null)

  useEffect(() => {
    if (!open) {
      return
    }

    setWorkerName('')
    setCloneFrom('default')
    setSoul('')
    setDisplayName('')
    setRole('')
    setTagline('')
    setStep('identity')
    setError(null)
    setStatus('idle')
  }, [open])

  const derivedName = deriveProfileName(displayName)
  const busy = status === 'saving' || status === 'done'

  useEffect(() => {
    if (!open || !displayName.trim()) {
      return
    }

    let cancelled = false
    const candidate = deriveProfileName(displayName)

    setWorkerName(candidate)

    const timer = window.setTimeout(() => {
      void getProfileNameSuggestion(candidate).then(
        result => {
          if (!cancelled) {
            setWorkerName(result.suggestion)
          }
        },
        () => {
          // The create request repeats this inexpensive check. If an older or
          // temporarily unavailable backend cannot preview it, keep the local
          // derivation and let the branded submit fallback handle any reject.
        }
      )
    }, 150)

    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [displayName, open])

  function continueToProfile() {
    if (!displayName.trim()) {
      setError(p.nameRequired)

      return
    }

    setError(null)
    setStep('profile')
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()

    if (step === 'identity') {
      continueToProfile()

      return
    }

    if (!displayName.trim()) {
      setError(p.nameRequired)

      return
    }

    setStatus('saving')
    setError(null)

    let finalName = workerName || derivedName

    try {
      const result = await getProfileNameSuggestion(derivedName)

      finalName = result.suggestion
      setWorkerName(finalName)
    } catch {
      // Continue with the live preview. The create error path below never
      // exposes transport details if the backend still rejects the name.
    }

    try {
      await createProfile({ name: finalName, clone_from: cloneFrom })

      await updateProfileIdentity(finalName, {
        display_name: displayName,
        role,
        tagline
      })

      if (soul.trim()) {
        await updateProfileSoul(finalName, soul)
      }

      await onCreated?.(finalName)
      setStatus('done')
      window.setTimeout(onClose, 800)
    } catch (err) {
      setStatus('idle')
      const kind = profileCreateErrorKind(err)

      setError(
        kind === 'reserved'
          ? p.reservedName
          : kind === 'taken'
            ? p.takenName
            : kind === 'invalid'
              ? p.invalidName(p.nameHint)
              : p.failedCreate
      )
    }
  }

  return (
    <Dialog onOpenChange={value => !value && !busy && onClose()} open={open}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{p.newProfile}</DialogTitle>
          <DialogDescription>{step === 'identity' ? p.createIdentityDesc : p.createDesc}</DialogDescription>
        </DialogHeader>

        <form className="grid gap-4" onSubmit={handleSubmit}>
          {step === 'profile' ? (
            <>
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
                  onChange={event => {
                    setDisplayName(event.target.value)
                    setError(null)
                  }}
                  placeholder={p.displayNamePlaceholder}
                  value={displayName}
                />
                {displayName.trim() ? (
                  <p aria-live="polite" className="font-mono text-[0.66rem] leading-4 text-muted-foreground">
                    {p.workerIdPreview(workerName || derivedName)}
                  </p>
                ) : null}
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
            {step === 'profile' ? (
              <Button disabled={busy} onClick={() => setStep('identity')} type="button" variant="outline">
                {p.back}
              </Button>
            ) : null}
            {step === 'identity' ? (
              <Button disabled={busy || !displayName.trim()} onClick={continueToProfile} type="button">
                {p.next}
              </Button>
            ) : (
              <Button disabled={busy} type="submit">
                <ActionStatus busy={p.creating} done={p.created} idle={p.createAction} state={status} />
              </Button>
            )}
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
