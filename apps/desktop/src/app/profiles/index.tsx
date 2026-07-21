import { useStore } from '@nanostores/react'
import type * as React from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { CodeEditor } from '@/components/chat/code-editor'
import { PageLoader } from '@/components/page-loader'
import { StatusDot, type StatusTone } from '@/components/status-dot'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { RowButton } from '@/components/ui/row-button'
import { getProfileSoul, type ProfileInfo, updateProfileSoul } from '@/hermes'
import { useI18n } from '@/i18n'
import { AlertTriangle, Save } from '@/lib/icons'
import { displayModelName } from '@/lib/model-status-label'
import { normalize } from '@/lib/text'
import { cn } from '@/lib/utils'
import { notify, notifyError } from '@/store/notifications'
import { $activeGatewayProfile, normalizeProfileKey, refreshProfiles } from '@/store/profile'

import { useRefreshHotkey } from '../hooks/use-refresh-hotkey'
import { DetailColumn, ListColumn, ListStrip, MasterDetail } from '../master-detail'
import { PanelEmpty, PanelMeta, PanelPill, PanelRowMenu, PanelSectionLabel } from '../overlays/panel'
import { PageSearchShell } from '../page-search-shell'

import { CreateProfileDialog } from './create-profile-dialog'
import { DeleteProfileDialog } from './delete-profile-dialog'
import { IdentityEditor } from './identity-editor'
import { RenameProfileDialog } from './rename-profile-dialog'
import { WorkerAvatar } from './worker-avatar'
import { WorkerModelSection } from './worker-model-section'

export function ProfilesView(props: React.ComponentProps<'section'>) {
  const { t } = useI18n()
  const p = t.profiles
  const [profiles, setProfiles] = useState<null | ProfileInfo[]>(null)
  const [selectedName, setSelectedName] = useState<null | string>(null)
  const [query, setQuery] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [pendingRename, setPendingRename] = useState<null | ProfileInfo>(null)
  const [pendingDelete, setPendingDelete] = useState<null | ProfileInfo>(null)
  const activeProfile = useStore($activeGatewayProfile)

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
      [profile.name, profile.display_name, profile.role, profile.model ?? ''].some(value =>
        value.toLowerCase().includes(q)
      )
    )
  }, [profiles, query])

  return (
    <PageSearchShell
      {...props}
      onSearchChange={setQuery}
      searchHidden={(profiles?.length ?? 0) === 0}
      searchHints={profiles?.slice(0, 5).map(profile => p.searchHint(profile.display_name || profile.name))}
      searchPlaceholder={p.search}
      searchTrailingAction={
        <Button onClick={() => setCreateOpen(true)} size="sm">
          <Codicon name="add" size="0.875rem" />
          {p.newProfile}
        </Button>
      }
      searchValue={query}
    >
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
        <MasterDetail>
          <ListColumn
            header={
              <ListStrip
                left={
                  <span className="text-[0.68rem] font-medium text-muted-foreground/70">
                    {p.count(profiles.length)}
                  </span>
                }
              />
            }
          >
            <div className="space-y-1">
              {visibleProfiles.map(profile => (
                <WorkerRow
                  active={selected?.name === profile.name}
                  current={normalizeProfileKey(profile.name) === normalizeProfileKey(activeProfile)}
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
                      label={p.actionsFor(profile.display_name || profile.name)}
                    />
                  }
                  onSelect={() => setSelectedName(profile.name)}
                  profile={profile}
                />
              ))}
              {visibleProfiles.length === 0 ? (
                <p className="px-2 py-6 text-center text-xs text-muted-foreground/65">{p.noSearchResults(query)}</p>
              ) : null}
            </div>
          </ListColumn>

          <DetailColumn>
            {selected ? (
              <ProfileDetail
                current={normalizeProfileKey(selected.name) === normalizeProfileKey(activeProfile)}
                key={selected.name}
                onUpdated={refresh}
                profile={selected}
              />
            ) : (
              <PanelEmpty description={p.selectPrompt} icon="account" />
            )}
          </DetailColumn>
        </MasterDetail>
      )}

      <RenameProfileDialog
        currentName={pendingRename?.name ?? ''}
        onClose={() => setPendingRename(null)}
        onRenamed={async name => {
          setSelectedName(name)
          await refresh()
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

      <DeleteProfileDialog
        onClose={() => setPendingDelete(null)}
        onDeleted={async () => {
          setSelectedName(null)
          await refresh()
        }}
        open={pendingDelete !== null}
        profile={pendingDelete}
      />
    </PageSearchShell>
  )
}

function WorkerRow({
  active,
  current,
  menu,
  onSelect,
  profile
}: {
  active: boolean
  current: boolean
  menu?: React.ReactNode
  onSelect: () => void
  profile: ProfileInfo
}) {
  const { t } = useI18n()
  const p = t.profiles
  const displayName = profile.display_name.trim() || profile.name

  const state = current
    ? { label: p.currentBadge, tone: 'good' as StatusTone }
    : profile.gateway_running
      ? { label: p.onlineBadge, tone: 'good' as StatusTone }
      : { label: p.standbyBadge, tone: 'muted' as StatusTone }

  return (
    <div
      className={cn(
        'group/row row-hover flex min-h-13 w-full items-center rounded-lg pr-1 transition-colors hover:text-foreground',
        active ? 'bg-(--ui-row-active-background) text-foreground' : 'text-(--ui-text-secondary)'
      )}
      data-worker-row={profile.name}
    >
      <RowButton
        className="flex min-w-0 flex-1 items-center gap-2.5 rounded-lg px-2 py-1.5 text-left"
        onClick={onSelect}
      >
        <WorkerAvatar className="size-9 rounded-lg text-[0.7rem] ring-1 ring-white/5" profile={profile} />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[0.78rem] font-medium text-foreground/90">{displayName}</span>
          <span className="block truncate text-[0.65rem] text-muted-foreground/60">
            {profile.role || p.roleNotSet}
            {profile.model ? ` · ${displayModelName(profile.model)}` : ''}
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-1 text-[0.6rem] font-medium text-muted-foreground/60">
          <StatusDot tone={state.tone} />
          {state.label}
        </span>
      </RowButton>
      {menu ? <div className="shrink-0">{menu}</div> : null}
    </div>
  )
}

function ProfileDetail({
  current,
  onUpdated,
  profile
}: {
  current: boolean
  onUpdated: () => Promise<void>
  profile: ProfileInfo
}) {
  const { t } = useI18n()
  const p = t.profiles
  const displayName = profile.display_name.trim() || profile.name

  const state = current
    ? { label: p.currentBadge, tone: 'good' as const }
    : profile.gateway_running
      ? { label: p.onlineBadge, tone: 'good' as const }
      : { label: p.standbyBadge, tone: 'muted' as const }

  return (
    <div className="space-y-5">
      <header className="relative overflow-hidden rounded-xl bg-(--ui-bg-quaternary) p-4">
        <span aria-hidden="true" className="absolute inset-y-0 left-0 w-px bg-primary/70" />
        <div className="flex min-w-0 items-center gap-4">
          <WorkerAvatar className="size-16 rounded-xl text-xl ring-1 ring-white/8" profile={profile} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-base font-semibold tracking-tight text-foreground">{displayName}</h2>
              <PanelPill tone={state.tone}>
                <StatusDot className="mr-1" tone={state.tone} />
                {state.label}
              </PanelPill>
              {profile.is_default && <PanelPill tone="good">{p.defaultBadge}</PanelPill>}
            </div>
            <p className="mt-1 text-xs text-muted-foreground">{profile.role || p.roleNotSet}</p>
            <p className="mt-1 font-mono text-[0.64rem] text-muted-foreground/45">{profile.name}</p>
          </div>
        </div>
      </header>

      <section className="space-y-2.5">
        <div>
          <PanelSectionLabel className="text-[0.7rem] tracking-[0.14em]">{p.runtimeSection}</PanelSectionLabel>
          <p className="text-xs text-muted-foreground">{p.runtimeDesc}</p>
        </div>
        <PanelMeta
          className="grid-cols-[6.5rem_1fr] rounded-lg bg-foreground/[0.025] p-3"
          rows={[
            {
              label: p.statusLabel,
              value: (
                <span className="inline-flex items-center gap-1.5">
                  <StatusDot tone={state.tone} />
                  {state.label}
                </span>
              )
            },
            { label: p.skillsLabel, value: p.skills(profile.skill_count) },
            {
              label: p.workerHomeLabel,
              value: (
                <span className="break-all font-mono text-[0.66rem] text-foreground/70" title={profile.path}>
                  {profile.path}
                </span>
              )
            }
          ]}
        />
      </section>

      <WorkerModelSection onUpdated={onUpdated} profile={profile} />
      <IdentityEditor onUpdated={onUpdated} profile={profile} />
      <SoulEditor profileName={profile.name} />
    </div>
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
