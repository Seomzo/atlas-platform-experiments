import { type ReactNode, useEffect, useMemo, useRef, useState } from 'react'

import { PageLoader } from '@/components/page-loader'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import {
  generateProfileAvatar,
  getProfileIdentity,
  PROFILE_AVATAR_MAX_BYTES,
  type ProfileIdentity,
  type ProfileInfo,
  updateProfileIdentity,
  uploadProfileAvatar
} from '@/hermes'
import { useI18n } from '@/i18n'
import { AlertTriangle, Save, Upload, Zap } from '@/lib/icons'
import { notify } from '@/store/notifications'
import { markProfileAvatarUpdated } from '@/store/profile'

import { PanelSectionLabel } from '../overlays/panel'

import { WorkerAvatar } from './worker-avatar'

const IDENTITY_LIMITS = {
  display_name: 80,
  role: 80,
  tagline: 200
} as const

const AVATAR_MEDIA_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp'])

interface IdentityEditorProps {
  onUpdated: () => Promise<void> | void
  profile: ProfileInfo
}

function errorDetail(error: unknown, fallback: string): string {
  if (!(error instanceof Error) || !error.message) {
    return fallback
  }

  const payload = error.message.match(/^\d+:\s*(.+)$/s)?.[1]

  if (payload) {
    try {
      const parsed = JSON.parse(payload) as { detail?: unknown }

      if (typeof parsed.detail === 'string' && parsed.detail) {
        return parsed.detail
      }
    } catch {
      // Keep the original error when the backend did not return JSON.
    }
  }

  return error.message
}

export function IdentityEditor({ onUpdated, profile }: IdentityEditorProps) {
  const { t } = useI18n()
  const p = t.profiles
  const [identity, setIdentity] = useState<null | ProfileIdentity>(null)
  const [original, setOriginal] = useState<null | ProfileIdentity>(null)
  const [prompt, setPrompt] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState<null | string>(null)
  const requestRef = useRef(profile.name)

  useEffect(() => {
    requestRef.current = profile.name
    setLoading(true)
    setError(null)
    setIdentity(null)
    setOriginal(null)
    setPrompt('')

    void getProfileIdentity(profile.name)
      .then(next => {
        if (requestRef.current === profile.name) {
          setIdentity(next)
          setOriginal(next)
        }
      })
      .catch(err => requestRef.current === profile.name && setError(errorDetail(err, p.failedLoadIdentity)))
      .finally(() => requestRef.current === profile.name && setLoading(false))
  }, [p, profile.name])

  const dirty = useMemo(
    () =>
      Boolean(
        identity &&
          original &&
          (identity.display_name !== original.display_name ||
            identity.role !== original.role ||
            identity.tagline !== original.tagline)
      ),
    [identity, original]
  )

  const avatarProfile = identity
    ? { ...profile, display_name: identity.display_name || profile.name, has_avatar: Boolean(identity.avatar) }
    : profile

  function setField(field: keyof typeof IDENTITY_LIMITS, value: string) {
    setIdentity(current => (current ? { ...current, [field]: value } : current))
  }

  function validationError(): null | string {
    if (!identity) {
      return null
    }

    const fields = [
      { label: p.displayNameLabel, limit: IDENTITY_LIMITS.display_name, value: identity.display_name },
      { label: p.roleLabel, limit: IDENTITY_LIMITS.role, value: identity.role },
      { label: p.taglineLabel, limit: IDENTITY_LIMITS.tagline, value: identity.tagline }
    ]

    const invalid = fields.find(field => field.value.length > field.limit)

    return invalid ? p.identityFieldTooLong(invalid.label, invalid.limit) : null
  }

  async function saveIdentity() {
    if (!identity) {
      return
    }

    const invalid = validationError()

    if (invalid) {
      setError(invalid)

      return
    }

    setSaving(true)
    setError(null)

    try {
      const updated = await updateProfileIdentity(profile.name, {
        display_name: identity.display_name,
        role: identity.role,
        tagline: identity.tagline
      })

      setIdentity(updated)
      setOriginal(updated)
      await onUpdated()
      notify({ kind: 'success', title: p.identitySaved, message: updated.display_name || profile.name })
    } catch (err) {
      setError(errorDetail(err, p.failedSaveIdentity))
    } finally {
      setSaving(false)
    }
  }

  async function uploadAvatar(file: File) {
    if (!AVATAR_MEDIA_TYPES.has(file.type)) {
      setError(p.avatarInvalidType)

      return
    }

    if (file.size > PROFILE_AVATAR_MAX_BYTES) {
      setError(p.avatarTooLarge)

      return
    }

    setUploading(true)
    setError(null)

    try {
      const updated = await uploadProfileAvatar(profile.name, file)

      setIdentity(updated)
      setOriginal(updated)
      markProfileAvatarUpdated(profile.name)
      await onUpdated()
      notify({ kind: 'success', title: p.avatarUploaded, message: updated.display_name || profile.name })
    } catch (err) {
      const key = err instanceof Error ? err.message : ''

      const clientErrors: Record<string, string> = {
        avatar_too_large: p.avatarTooLarge,
        unsupported_avatar_type: p.avatarInvalidType
      }

      const message = clientErrors[key] ?? errorDetail(err, p.failedUploadAvatar)

      setError(message)
    } finally {
      setUploading(false)
    }
  }

  async function generateAvatar() {
    const description = prompt.trim()

    if (!description) {
      setError(p.generatePromptRequired)

      return
    }

    setGenerating(true)
    setError(null)

    try {
      const updated = await generateProfileAvatar(profile.name, description)

      setIdentity(updated)
      setOriginal(updated)
      markProfileAvatarUpdated(profile.name)
      await onUpdated()
      notify({ kind: 'success', title: p.avatarGenerated, message: updated.display_name || profile.name })
    } catch (err) {
      setError(errorDetail(err, p.failedGenerateAvatar))
    } finally {
      setGenerating(false)
    }
  }

  return (
    <section className="space-y-3">
      <div>
        <PanelSectionLabel className="text-[0.7rem] tracking-[0.14em]">{p.identitySection}</PanelSectionLabel>
        <p className="text-xs text-muted-foreground">{p.identityDesc}</p>
      </div>

      {loading ? (
        <PageLoader className="min-h-44" label={p.loadingIdentity} />
      ) : identity ? (
        <div className="space-y-4 rounded-lg bg-foreground/[0.035] p-3">
          <div className="flex items-center gap-3">
            <WorkerAvatar className="size-16 rounded-xl text-xl" profile={avatarProfile} />
            <div className="min-w-0 flex-1 space-y-1">
              <p className="text-xs font-medium text-foreground">{p.avatarLabel}</p>
              <p className="text-[0.68rem] leading-4 text-muted-foreground">{p.avatarDesc}</p>
              <label className="inline-flex">
                <input
                  accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp"
                  className="sr-only"
                  disabled={uploading || generating}
                  onChange={event => {
                    const file = event.target.files?.[0]

                    event.target.value = ''

                    if (file) {
                      void uploadAvatar(file)
                    }
                  }}
                  type="file"
                />
                <span className="inline-flex h-7 cursor-pointer items-center gap-1.5 rounded-md border border-input bg-background px-2.5 text-xs font-medium shadow-xs transition hover:bg-accent hover:text-accent-foreground">
                  <Upload className="size-3.5" />
                  {uploading ? p.uploadingAvatar : identity.avatar ? p.replaceAvatar : p.uploadAvatar}
                </span>
              </label>
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <IdentityField
              count={identity.display_name.length}
              id={`worker-display-name-${profile.name}`}
              label={p.displayNameLabel}
              limit={IDENTITY_LIMITS.display_name}
            >
              <Input
                id={`worker-display-name-${profile.name}`}
                maxLength={IDENTITY_LIMITS.display_name}
                onChange={event => setField('display_name', event.target.value)}
                placeholder={p.displayNamePlaceholder}
                value={identity.display_name}
              />
            </IdentityField>
            <IdentityField
              count={identity.role.length}
              id={`worker-role-${profile.name}`}
              label={p.roleLabel}
              limit={IDENTITY_LIMITS.role}
            >
              <Input
                id={`worker-role-${profile.name}`}
                maxLength={IDENTITY_LIMITS.role}
                onChange={event => setField('role', event.target.value)}
                placeholder={p.rolePlaceholder}
                value={identity.role}
              />
            </IdentityField>
          </div>

          <IdentityField
            count={identity.tagline.length}
            id={`worker-tagline-${profile.name}`}
            label={p.taglineLabel}
            limit={IDENTITY_LIMITS.tagline}
          >
            <Textarea
              className="min-h-20 resize-y"
              id={`worker-tagline-${profile.name}`}
              maxLength={IDENTITY_LIMITS.tagline}
              onChange={event => setField('tagline', event.target.value)}
              placeholder={p.taglinePlaceholder}
              value={identity.tagline}
            />
          </IdentityField>

          <div className="flex justify-end">
            <Button disabled={!dirty || saving} onClick={() => void saveIdentity()} size="sm">
              <Save />
              {saving ? p.saving : p.saveIdentity}
            </Button>
          </div>

          <div className="space-y-2 border-t border-border/50 pt-3">
            <div>
              <p className="text-xs font-medium text-foreground">{p.generateAvatar}</p>
              <p className="text-[0.68rem] leading-4 text-muted-foreground">{p.generateAvatarDesc}</p>
            </div>
            <Textarea
              className="min-h-20 resize-y"
              disabled={generating}
              onChange={event => setPrompt(event.target.value)}
              placeholder={p.generatePromptPlaceholder}
              value={prompt}
            />
            <div className="flex justify-end">
              <Button
                disabled={generating || uploading || !prompt.trim()}
                onClick={() => void generateAvatar()}
                size="sm"
                variant="outline"
              >
                <Zap />
                {generating ? p.generatingAvatar : identity.avatar ? p.regenerateAvatar : p.generateAction}
              </Button>
            </div>
          </div>
        </div>
      ) : null}

      {error ? (
        <div className="flex items-start gap-2 rounded bg-destructive/10 px-3 py-2 text-xs text-destructive">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}
    </section>
  )
}

interface IdentityFieldProps {
  children: ReactNode
  count: number
  id: string
  label: string
  limit: number
}

function IdentityField({ children, count, id, label, limit }: IdentityFieldProps) {
  const { t } = useI18n()

  return (
    <div className="grid gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <label className="text-xs font-medium" htmlFor={id}>
          {label}
        </label>
        <span className="text-[0.62rem] tabular-nums text-muted-foreground/60">
          {t.profiles.characterCount(count, limit)}
        </span>
      </div>
      {children}
    </div>
  )
}
