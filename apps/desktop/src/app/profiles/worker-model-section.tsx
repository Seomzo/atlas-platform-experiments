import { useEffect, useRef, useState } from 'react'

import { ModelPickerDialog } from '@/components/model-picker'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  getGlobalModelInfo,
  getGlobalModelOptions,
  getHermesConfigRecordForProfile,
  saveHermesConfigForProfile
} from '@/hermes'
import { useI18n } from '@/i18n'
import { AlertTriangle, Loader2 } from '@/lib/icons'
import { displayModelName } from '@/lib/model-status-label'
import { notify } from '@/store/notifications'
import type { HermesConfigRecord, ModelOptionProvider, ProfileInfo } from '@/types/hermes'

import { PanelPill, PanelSectionLabel } from '../overlays/panel'

interface WorkerModelSelection {
  model: string
  provider: string
}

interface WorkerModelSnapshot extends WorkerModelSelection {
  authType: string
  providerName: string
  ready: boolean | null
}

export function workerModelConfig(selection: WorkerModelSelection): HermesConfigRecord {
  return {
    model: {
      default: selection.model,
      provider: selection.provider
    }
  }
}

function configuredModel(config: HermesConfigRecord): WorkerModelSelection {
  const value = config.model

  if (typeof value === 'string') {
    return { model: value, provider: '' }
  }

  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const model = value as Record<string, unknown>

    return {
      model: String(model.default ?? model.name ?? ''),
      provider: String(model.provider ?? '')
    }
  }

  return { model: '', provider: '' }
}

function providerRow(providers: ModelOptionProvider[], provider: string): ModelOptionProvider | undefined {
  const key = provider.trim().toLowerCase()

  return providers.find(row => row.slug.trim().toLowerCase() === key)
}

async function requestWorkerModel(profile: ProfileInfo): Promise<{
  providers: ModelOptionProvider[]
  snapshot: WorkerModelSnapshot
}> {
  const [config, info, options] = await Promise.all([
    getHermesConfigRecordForProfile(profile.name),
    getGlobalModelInfo(profile.name),
    getGlobalModelOptions({
      explicitOnly: false,
      includeUnconfigured: true,
      profile: profile.name
    }).catch(() => null)
  ])

  const configured = configuredModel(config)
  const model = info.model || configured.model || profile.model || ''
  const provider = info.provider || configured.provider || profile.provider || ''
  const providers = options?.providers ?? []
  const row = providerRow(providers, provider)

  return {
    providers,
    snapshot: {
      authType: row?.auth_type ?? '',
      model,
      provider,
      providerName: row?.name || provider,
      ready: row ? row.authenticated !== false : null
    }
  }
}

export function WorkerModelSection({ onUpdated, profile }: { onUpdated: () => Promise<void>; profile: ProfileInfo }) {
  const { t } = useI18n()
  const p = t.profiles
  const requestRef = useRef(0)

  const [snapshot, setSnapshot] = useState<WorkerModelSnapshot>({
    authType: '',
    model: profile.model || '',
    provider: profile.provider || '',
    providerName: profile.provider || '',
    ready: null
  })

  const [providers, setProviders] = useState<ModelOptionProvider[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [error, setError] = useState<null | string>(null)

  useEffect(() => {
    const request = ++requestRef.current
    setLoading(true)
    setError(null)

    void requestWorkerModel(profile).then(
      result => {
        if (request === requestRef.current) {
          setSnapshot(result.snapshot)
          setProviders(result.providers)
          setLoading(false)
        }
      },
      () => {
        if (request === requestRef.current) {
          setError(p.failedLoadModel)
          setLoading(false)
        }
      }
    )
  }, [p.failedLoadModel, profile])

  async function saveSelection(selection: WorkerModelSelection) {
    const selectedProvider = providerRow(providers, selection.provider)

    if (selectedProvider?.authenticated === false) {
      setError(p.providerNotReady(selectedProvider.name))

      return
    }

    const previous = snapshot

    const optimistic: WorkerModelSnapshot = {
      authType: selectedProvider?.auth_type ?? '',
      ...selection,
      providerName: selectedProvider?.name || selection.provider,
      ready: selectedProvider ? true : null
    }

    setSnapshot(optimistic)
    setSaving(true)
    setError(null)

    try {
      const saved = await saveHermesConfigForProfile(workerModelConfig(selection), profile.name)

      if (saved.ok !== true) {
        throw new Error('Worker model save was not confirmed')
      }

      const confirmed = await requestWorkerModel(profile)

      if (confirmed.snapshot.model !== selection.model || confirmed.snapshot.provider !== selection.provider) {
        throw new Error('Worker model did not match after save')
      }

      setSnapshot(confirmed.snapshot)
      setProviders(confirmed.providers)
      notify({ kind: 'success', title: p.modelSaved, message: profile.display_name || profile.name })
      await onUpdated().catch(() => undefined)
    } catch {
      setSnapshot(previous)
      setError(p.failedSaveModel)
    } finally {
      setSaving(false)
    }
  }

  const friendlyModel = snapshot.model ? displayModelName(snapshot.model) : p.notSet
  const providerName = snapshot.providerName || snapshot.provider || p.notSet
  const rawIdentity = [snapshot.provider, snapshot.model].filter(Boolean).join(' · ')
  const needsSetup = snapshot.ready === false

  return (
    <section className="space-y-2.5" data-testid="worker-model-section">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <PanelSectionLabel className="text-[0.7rem] tracking-[0.14em]">{p.modelLabel}</PanelSectionLabel>
            {needsSetup ? <PanelPill tone="warn">{p.modelNeedsSetupBadge}</PanelPill> : null}
          </div>
          <p className="text-xs text-muted-foreground">{p.modelSectionDesc}</p>
        </div>
        <Button disabled={loading || saving} onClick={() => setPickerOpen(true)} size="sm" variant="outline">
          {saving ? <Loader2 className="size-3.5 animate-spin" /> : null}
          {saving ? p.savingModel : p.changeModel}
        </Button>
      </div>

      <div className="rounded-lg bg-foreground/[0.025] p-3">
        {loading ? (
          <div className="space-y-2">
            <Skeleton className="h-4 w-36" />
            <Skeleton className="h-3 w-56 max-w-full" />
          </div>
        ) : (
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-foreground" title={snapshot.model}>
              {friendlyModel}
            </p>
            <p className="mt-0.5 truncate font-mono text-[0.66rem] text-muted-foreground" title={rawIdentity}>
              {providerName}
              {snapshot.model ? ` · ${snapshot.model}` : ''}
            </p>
          </div>
        )}
      </div>

      {needsSetup ? (
        <div className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          <span>
            {snapshot.authType === 'api_key' ? p.modelNeedsApiKey(providerName) : p.providerNotReady(providerName)}
          </span>
        </div>
      ) : null}

      {error ? (
        <div className="flex items-start gap-2 rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}

      <p className="text-[0.66rem] text-muted-foreground/70">{p.configuredProvidersOnly}</p>

      {pickerOpen ? (
        <ModelPickerDialog
          currentModel={snapshot.model}
          currentProvider={snapshot.provider}
          onOpenChange={setPickerOpen}
          onSelect={selection => void saveSelection(selection)}
          open
          profile={profile.name}
          showAddProvider={false}
        />
      ) : null}
    </section>
  )
}
