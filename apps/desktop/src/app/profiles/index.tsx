import type * as React from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { CodeEditor } from '@/components/chat/code-editor'
import { PageLoader } from '@/components/page-loader'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { SanitizedInput } from '@/components/ui/sanitized-input'
import {
  deleteProfile,
  getProfileSoul,
  type ProfileInfo,
  renameProfile,
  updateProfileSoul
} from '@/hermes'
import { useI18n } from '@/i18n'
import { AlertTriangle, Save } from '@/lib/icons'
import { slug } from '@/lib/sanitize'
import { normalize } from '@/lib/text'
import { cn } from '@/lib/utils'
import { notify, notifyError } from '@/store/notifications'
import { refreshProfiles } from '@/store/profile'

import { useRefreshHotkey } from '../hooks/use-refresh-hotkey'
import {
  Panel,
  PanelAddButton,
  PanelBody,
  PanelDetail,
  PanelEmpty,
  PanelHeader,
  PanelList,
  PanelListRow,
  PanelMeta,
  PanelPill,
  PanelRowMenu,
  PanelSectionLabel
} from '../overlays/panel'

import { CreateProfileDialog } from './create-profile-dialog'
import { IdentityEditor } from './identity-editor'
import { WorkerAvatar } from './worker-avatar'

const PROFILE_NAME_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/

function isValidProfileName(name: string): boolean {
  return PROFILE_NAME_RE.test(name.trim())
}

interface ProfilesViewProps {
  onClose: () => void
}

export function ProfilesView({ onClose }: ProfilesViewProps) {
  const { t } = useI18n()
  const p = t.profiles
  const [profiles, setProfiles] = useState<null | ProfileInfo[]>(null)
  const [selectedName, setSelectedName] = useState<null | string>(null)
  const [query, setQuery] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [pendingRename, setPendingRename] = useState<null | ProfileInfo>(null)
  const [pendingDelete, setPendingDelete] = useState<null | ProfileInfo>(null)
  const [deleting, setDeleting] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const list = await refreshProfiles()
      setProfiles(list)
      setSelectedName(current => {
        if (current && list.some(p => p.name === current)) {
          return current
        }

        return list.find(p => p.is_default)?.name ?? list[0]?.name ?? null
      })
    } catch (err) {
      notifyError(err, p.failedLoad)
    }
  }, [p])

  useRefreshHotkey(refresh)

  useEffect(() => {
    void refresh()
  }, [refresh])

  const selected = useMemo(() => {
    if (!profiles) {
      return null
    }

    return profiles.find(p => p.name === selectedName) ?? profiles[0] ?? null
  }, [profiles, selectedName])

  const visibleProfiles = useMemo(() => {
    const q = normalize(query)

    if (!profiles || !q) {
      return profiles ?? []
    }

    return profiles.filter(profile =>
      [profile.name, profile.display_name, profile.role, profile.model ?? ''].some(value => value.toLowerCase().includes(q))
    )
  }, [profiles, query])

  const handleRename = useCallback(
    async (from: string, to: string): Promise<void> => {
      const target = to.trim()

      if (target === from) {
        return
      }

      if (!isValidProfileName(target)) {
        throw new Error(p.nameHint)
      }

      await renameProfile(from, target)
      notify({ kind: 'success', title: p.renamed, message: `${from} → ${target}` })
      setSelectedName(target)
      await refresh()
    },
    [p, refresh]
  )

  const handleConfirmDelete = useCallback(async () => {
    if (!pendingDelete) {
      return
    }

    setDeleting(true)

    try {
      await deleteProfile(pendingDelete.name)
      notify({ kind: 'success', title: p.deleted, message: pendingDelete.name })
      setPendingDelete(null)
      setSelectedName(null)
      await refresh()
    } catch (err) {
      notifyError(err, p.failedDelete)
    } finally {
      setDeleting(false)
    }
  }, [p, pendingDelete, refresh])

  return (
    <Panel closeLabel={p.close} onClose={onClose}>
      {!profiles ? (
        <PageLoader label={p.loading} />
      ) : profiles.length === 0 ? (
        <PanelEmpty
          action={
            <Button onClick={() => setCreateOpen(true)} size="sm">
              {p.newProfile}
            </Button>
          }
          description={p.createDesc}
          icon="organization"
          title={p.noProfiles}
        />
      ) : (
        <>
          <PanelHeader subtitle={p.count(profiles.length)} title={p.title} />
          <PanelBody>
            <PanelList
              onSearchChange={setQuery}
              searchLabel={p.search}
              searchPlaceholder={p.search}
              searchValue={query}
            >
              {visibleProfiles.map(profile => (
                <ProfileRow
                  active={selected?.name === profile.name}
                  key={profile.name}
                  menu={
                    <PanelRowMenu
                      items={
                        profile.is_default
                          ? []
                          : [
                              { icon: 'edit', label: p.renameMenu, onSelect: () => setPendingRename(profile) },
                              {
                                icon: 'trash',
                                label: t.common.delete,
                                onSelect: () => setPendingDelete(profile),
                                tone: 'danger'
                              }
                            ]
                      }
                    />
                  }
                  onSelect={() => setSelectedName(profile.name)}
                  profile={profile}
                />
              ))}
              <PanelAddButton label={p.newProfile} onClick={() => setCreateOpen(true)} />
            </PanelList>

            {selected ? (
              <ProfileDetail key={selected.name} onUpdated={refresh} profile={selected} />
            ) : (
              <PanelEmpty description={p.selectPrompt} icon="account" />
            )}
          </PanelBody>
        </>
      )}

      <RenameProfileDialog
        currentName={pendingRename?.name ?? ''}
        onClose={() => setPendingRename(null)}
        onRename={async newName => {
          if (pendingRename) {
            await handleRename(pendingRename.name, newName)
            setPendingRename(null)
          }
        }}
        open={pendingRename !== null}
      />

      <CreateProfileDialog
        onClose={() => setCreateOpen(false)}
        onCreated={async name => {
          setSelectedName(name)
          await refresh()
        }}
        open={createOpen}
        profiles={profiles ?? []}
      />

      <Dialog onOpenChange={open => !open && !deleting && setPendingDelete(null)} open={pendingDelete !== null}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{p.deleteTitle}</DialogTitle>
            <DialogDescription>
              {pendingDelete ? (
                <>
                  {p.deleteDescPrefix}
                  <span className="font-medium text-foreground">{pendingDelete.name}</span>
                  {p.deleteDescMid}
                  <span className="font-mono text-xs">{pendingDelete.path}</span>
                  {p.deleteDescSuffix}
                </>
              ) : null}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button disabled={deleting} onClick={() => setPendingDelete(null)} variant="outline">
              {t.common.cancel}
            </Button>
            <Button disabled={deleting} onClick={() => void handleConfirmDelete()} variant="destructive">
              {deleting ? p.deleting : t.common.delete}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Panel>
  )
}

function ProfileRow({
  active,
  menu,
  onSelect,
  profile
}: {
  active: boolean
  menu?: React.ReactNode
  onSelect: () => void
  profile: ProfileInfo
}) {
  const { t } = useI18n()
  const p = t.profiles
  const displayName = profile.display_name.trim() || profile.name

  const metadata = [profile.model, profile.skill_count > 0 ? p.skillsShort(profile.skill_count) : null]
    .filter(Boolean)
    .join(' · ')

  return (
    <PanelListRow
      active={active}
      lead={<WorkerAvatar className="size-5 rounded-[4px] text-[0.56rem]" profile={profile} />}
      menu={menu}
      meta={metadata || undefined}
      onSelect={onSelect}
      rowKey={profile.name}
      title={
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="truncate">{displayName}</span>
          {profile.role ? (
            <span className="max-w-20 shrink truncate rounded-full bg-foreground/10 px-1.5 py-0.5 text-[0.56rem] font-medium text-muted-foreground">
              {profile.role}
            </span>
          ) : null}
        </span>
      }
    />
  )
}

function ProfileDetail({ onUpdated, profile }: { onUpdated: () => Promise<void>; profile: ProfileInfo }) {
  const { t } = useI18n()
  const p = t.profiles
  const displayName = profile.display_name.trim() || profile.name

  return (
    <PanelDetail>
      <header className="space-y-3">
        <div className="flex min-w-0 items-center gap-3">
          <WorkerAvatar className="size-12 rounded-xl text-base" profile={profile} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-[0.95rem] font-semibold tracking-tight text-foreground">{displayName}</h3>
              {profile.role ? <PanelPill>{profile.role}</PanelPill> : null}
              {profile.is_default && <PanelPill tone="good">{p.defaultBadge}</PanelPill>}
              {profile.has_env && <PanelPill tone="muted">.env</PanelPill>}
            </div>
            <p className="mt-0.5 font-mono text-[0.66rem] text-muted-foreground/55">{profile.name}</p>
          </div>
        </div>

        <p className="truncate font-mono text-[0.66rem] text-muted-foreground/55" title={profile.path}>
          {profile.path}
        </p>

        <PanelMeta
          rows={[
            {
              label: p.modelLabel,
              value: profile.model ? (
                <span className="font-mono">
                  {profile.model}
                  {profile.provider ? <span className="text-muted-foreground/55"> · {profile.provider}</span> : null}
                </span>
              ) : (
                <span className="text-muted-foreground/55">{p.notSet}</span>
              )
            },
            { label: p.skillsLabel, value: profile.skill_count }
          ]}
        />
      </header>

      <IdentityEditor onUpdated={onUpdated} profile={profile} />
      <SoulEditor profileName={profile.name} />
    </PanelDetail>
  )
}

function SoulEditor({ profileName }: { profileName: string }) {
  const { t } = useI18n()
  const p = t.profiles
  const [content, setContent] = useState('')
  const [original, setOriginal] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<null | string>(null)
  const requestRef = useRef<string>(profileName)

  useEffect(() => {
    requestRef.current = profileName
    setLoading(true)
    setError(null)
    setContent('')
    setOriginal('')

    void (async () => {
      try {
        const soul = await getProfileSoul(profileName)

        if (requestRef.current === profileName) {
          setContent(soul.content)
          setOriginal(soul.content)
        }
      } catch (err) {
        if (requestRef.current === profileName) {
          setError(err instanceof Error ? err.message : p.failedLoadSoul)
        }
      } finally {
        if (requestRef.current === profileName) {
          setLoading(false)
        }
      }
    })()
  }, [p, profileName])

  const dirty = content !== original

  async function handleSave() {
    setSaving(true)
    setError(null)

    try {
      await updateProfileSoul(profileName, content)
      setOriginal(content)
      notify({ kind: 'success', title: p.soulSaved, message: profileName })
    } catch (err) {
      setError(err instanceof Error ? err.message : p.failedSaveSoul)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <PanelSectionLabel className="text-[0.7rem] tracking-[0.14em]">SOUL.md</PanelSectionLabel>
          <p className="text-xs text-muted-foreground">{p.soulDesc}</p>
        </div>
        {dirty && <span className="text-[0.65rem] text-muted-foreground">{p.unsavedChanges}</span>}
      </div>

      {loading ? (
        <PageLoader className="min-h-44" label={p.loadingSoul} />
      ) : (
        <div className="min-h-48">
          <CodeEditor
            filePath="SOUL.md"
            framed
            initialValue={content}
            key={profileName}
            onChange={setContent}
            onSave={() => void handleSave()}
          />
        </div>
      )}

      {error && (
        <div className="flex items-start gap-2 rounded bg-destructive/10 px-3 py-2 text-xs text-destructive">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <div className="flex justify-end">
        <Button disabled={!dirty || saving || loading} onClick={() => void handleSave()} size="sm">
          <Save />
          {saving ? p.saving : p.saveSoul}
        </Button>
      </div>
    </section>
  )
}

function RenameProfileDialog({
  currentName,
  onClose,
  onRename,
  open
}: {
  currentName: string
  onClose: () => void
  onRename: (newName: string) => Promise<void>
  open: boolean
}) {
  const { t } = useI18n()
  const p = t.profiles
  const [name, setName] = useState(currentName)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<null | string>(null)

  useEffect(() => {
    if (!open) {
      return
    }

    setName(currentName)
    setError(null)
    setSaving(false)
  }, [currentName, open])

  const trimmed = name.trim()
  const unchanged = trimmed === currentName
  const invalid = trimmed !== '' && !unchanged && !isValidProfileName(trimmed)

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()

    if (unchanged) {
      onClose()

      return
    }

    if (!trimmed || invalid) {
      setError(invalid ? p.invalidName(p.nameHint) : p.nameRequired)

      return
    }

    setSaving(true)
    setError(null)

    try {
      await onRename(trimmed)
    } catch (err) {
      setError(err instanceof Error ? err.message : p.failedRename)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog onOpenChange={value => !value && !saving && onClose()} open={open}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{p.renameTitle}</DialogTitle>
          <DialogDescription>
            {p.renameDescPrefix}
            <span className="font-mono">~/.local/bin</span>
            {p.renameDescSuffix}
          </DialogDescription>
        </DialogHeader>

        <form className="grid gap-3" onSubmit={handleSubmit}>
          <div className="grid gap-1.5">
            <label className="text-xs font-medium" htmlFor="rename-profile-name">
              {p.newNameLabel}
            </label>
            <SanitizedInput
              aria-invalid={invalid}
              autoFocus
              id="rename-profile-name"
              onValueChange={setName}
              sanitize={slug}
              value={name}
            />
            <p className={cn('text-[0.66rem] leading-4', invalid ? 'text-destructive' : 'text-muted-foreground')}>
              {p.nameHint}
            </p>
          </div>

          {error && (
            <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <DialogFooter>
            <Button disabled={saving} onClick={onClose} type="button" variant="outline">
              {t.common.cancel}
            </Button>
            <Button disabled={saving || invalid || unchanged} type="submit">
              {saving ? p.renaming : p.rename}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
