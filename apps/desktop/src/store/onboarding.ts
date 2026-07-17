import { atom } from 'nanostores'

import {
  cancelOAuthSession,
  getCortexMemoryModelOptions,
  getGlobalModelOptions,
  getRecommendedDefaultModel,
  listOAuthProviders,
  pollOAuthSession,
  setCortexMemoryModelAssignment,
  setEnvVar,
  setModelAssignment,
  startOAuthLogin,
  submitOAuthCode,
  validateProviderCredential
} from '@/hermes'
import { evaluateRuntimeReadiness, type RuntimeReadinessResult } from '@/lib/runtime-readiness'
import { notify, notifyError } from '@/store/notifications'
import { $activeGatewayProfile, normalizeProfileKey } from '@/store/profile'
import type { ModelOptionProvider, OAuthProvider, OAuthStartResponse } from '@/types/hermes'

type PkceStart = Extract<OAuthStartResponse, { flow: 'pkce' }>
type DeviceStart = Extract<OAuthStartResponse, { flow: 'device_code' }>
type CortexMemoryModelOptions = Awaited<ReturnType<typeof getCortexMemoryModelOptions>>

export interface ConnectableMemoryProvider {
  authType: 'api_key'
  keyEnv: string
  name: string
  slug: string
}

export type OnboardingMode = 'apikey' | 'oauth'

export type OnboardingFlow =
  | { status: 'idle' }
  | { provider: OAuthProvider; status: 'starting' }
  | { code: string; provider: OAuthProvider; start: PkceStart; status: 'awaiting_user' }
  | { copied: boolean; provider: OAuthProvider; start: DeviceStart; status: 'polling' }
  | { provider: OAuthProvider; start: OAuthStartResponse; status: 'submitting' }
  | { copied: boolean; provider: OAuthProvider; status: 'external_pending' }
  | { provider: OAuthProvider; status: 'success' }
  | {
      // After successful credential acquisition, before completing
      // onboarding: show the user which model they're getting and let
      // them change it. providerSlug is the model.options slug for the
      // just-authenticated provider (used to persist the chosen model
      // via /api/model/set). The change-model UI uses the existing
      // ModelPickerDialog, which fetches its own model list from
      // /api/model/options — no need to cache the list here.
      currentModel: string
      label: string
      message: null | string
      providerSlug: string
      saving: boolean
      status: 'confirming_model'
    }
  | {
      label: string
      mainModel: string
      mainProvider: string
      profile: string
      status: 'loading_memory_model'
    }
  | {
      currentModel: string
      currentProvider: string
      label: string
      mainModel: string
      mainProvider: string
      message: null | string
      providers: ModelOptionProvider[]
      profile: string
      recommendedModel: string
      recommendedProvider: string
      saving: boolean
      status: 'confirming_memory_model'
    }
  | {
      label: string
      mainModel: string
      mainProvider: string
      message: string
      connectableProvider: ConnectableMemoryProvider | null
      connecting: boolean
      credentialMessage: null | string
      profile: string
      status: 'memory_model_error'
    }
  | { message: string; provider?: OAuthProvider; start?: OAuthStartResponse; status: 'error' }

export interface DesktopOnboardingState {
  /** null until the first runtime check resolves. Seeded from localStorage so
   *  returning users skip the boot overlay entirely instead of flashing it
   *  every reload. */
  configured: boolean | null
  flow: OnboardingFlow
  mode: OnboardingMode
  providers: null | OAuthProvider[]
  reason: null | string
  requested: boolean
  /** True when the user explicitly chose "I'll choose a provider later" on the
   *  first-run picker. Persisted to localStorage so the blocking overlay never
   *  re-nags on subsequent launches — the user can connect a provider any time
   *  from Settings → Providers (or the model picker's "Add provider"). Distinct
   *  from `configured`: the app still has no usable provider, so chat won't work
   *  until one is connected; we just stop forcing the choice up front. */
  firstRunSkipped: boolean
  /** True when the user explicitly opened the provider selector to add /
   *  switch providers from an already-configured app (e.g. via the model
   *  picker's "Add provider" button). Forces the overlay to show the picker
   *  even when configured === true, and adds a close affordance. */
  manual: boolean
  /** True when the overlay was opened specifically to configure a local /
   *  custom OpenAI-compatible endpoint (e.g. from Settings → Model's "Set up
   *  custom endpoint"). Forces the API-key form with the local option
   *  preselected instead of the OAuth picker. */
  localEndpoint: boolean
}

export interface OnboardingContext {
  onCompleted?: () => void
  requestGateway: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>
}

const CONFIGURED_CACHE_KEY = 'atlas-desktop-onboarded-v1'
const SKIP_CACHE_KEY = 'atlas-onboarding-skipped-v1'
const LEGACY_CONFIGURED_CACHE_KEY = 'hermes-desktop-onboarded-v1'
const LEGACY_SKIP_CACHE_KEY = 'hermes-onboarding-skipped-v1'
const POLL_MS = 2000
const COPY_FLASH_MS = 1500
export const DEFAULT_ONBOARDING_REASON = 'No inference provider is configured.'
export const DEFAULT_MANUAL_ONBOARDING_REASON = 'Add or switch inference provider.'

function configuredCacheKey(profile: string): string {
  return `${CONFIGURED_CACHE_KEY}:${encodeURIComponent(normalizeProfileKey(profile))}`
}

function readCachedConfigured(profile = 'default'): boolean | null {
  if (typeof window === 'undefined') {
    return null
  }

  try {
    const normalized = normalizeProfileKey(profile)

    if (window.localStorage.getItem(configuredCacheKey(normalized)) === '1') {
      return true
    }

    // The old unscoped bit can only describe the historical/default profile.
    // Never let it authorize a newly selected remote/customer profile.
    if (
      normalized === 'default' &&
      (window.localStorage.getItem(CONFIGURED_CACHE_KEY) === '1' ||
        window.localStorage.getItem(LEGACY_CONFIGURED_CACHE_KEY) === '1')
    ) {
      window.localStorage.setItem(configuredCacheKey(normalized), '1')

      return true
    }

    return null
  } catch {
    return null
  }
}

function writeCachedConfigured(value: boolean, profile = 'default') {
  if (typeof window === 'undefined') {
    return
  }

  try {
    const normalized = normalizeProfileKey(profile)
    const key = configuredCacheKey(normalized)

    if (value) {
      window.localStorage.setItem(key, '1')
    } else {
      window.localStorage.removeItem(key)
    }

    // Keep the legacy Atlas key in sync only for default so older builds can
    // still boot after a downgrade. Non-default profiles are scoped-only.
    if (normalized === 'default') {
      if (value) {
        window.localStorage.setItem(CONFIGURED_CACHE_KEY, '1')
      } else {
        window.localStorage.removeItem(CONFIGURED_CACHE_KEY)
      }
    }

    window.localStorage.removeItem(LEGACY_CONFIGURED_CACHE_KEY)
  } catch {
    // localStorage unavailable — degrade silently.
  }
}

function readCachedSkipped(): boolean {
  if (typeof window === 'undefined') {
    return false
  }

  try {
    return (
      window.localStorage.getItem(SKIP_CACHE_KEY) === '1' || window.localStorage.getItem(LEGACY_SKIP_CACHE_KEY) === '1'
    )
  } catch {
    return false
  }
}

function writeCachedSkipped(value: boolean) {
  if (typeof window === 'undefined') {
    return
  }

  try {
    if (value) {
      window.localStorage.setItem(SKIP_CACHE_KEY, '1')
    } else {
      window.localStorage.removeItem(SKIP_CACHE_KEY)
    }

    window.localStorage.removeItem(LEGACY_SKIP_CACHE_KEY)
  } catch {
    // localStorage unavailable — degrade silently.
  }
}

const INITIAL: DesktopOnboardingState = {
  configured: readCachedConfigured(),
  flow: { status: 'idle' },
  mode: 'oauth',
  providers: null,
  reason: null,
  requested: false,
  firstRunSkipped: readCachedSkipped(),
  manual: false,
  localEndpoint: false
}

export const $desktopOnboarding = atom<DesktopOnboardingState>(INITIAL)

let pollTimer: number | null = null
const providersRefreshPromises = new Map<string, Promise<OAuthProvider[]>>()
let memoryOptionsRequestId = 0
let onboardingRefreshGeneration = 0

const errMessage = (e: unknown) => (e instanceof Error ? e.message : String(e))

const patch = (update: Partial<DesktopOnboardingState>) =>
  $desktopOnboarding.set({ ...$desktopOnboarding.get(), ...update })

const setFlow = (flow: OnboardingFlow) => patch(flow.status === 'idle' ? { flow } : { flow, reason: null })

const sessionIdFor = (flow: OnboardingFlow) => ('start' in flow && flow.start ? flow.start.session_id : undefined)

function clearPoll() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

async function checkRuntime(ctx: OnboardingContext, requestedProvider?: string): Promise<RuntimeReadinessResult> {
  return evaluateRuntimeReadiness(ctx.requestGateway, {
    defaultReason: DEFAULT_ONBOARDING_REASON,
    requestedProvider,
    unknownReady: false
  })
}

function shouldPreserveConfiguredOnFallback(
  runtime: RuntimeReadinessResult,
  profile: string,
  requested: boolean
): boolean {
  // A fallback result means both runtime probes were non-authoritative
  // (transport timeout/disconnect). Keep a previously verified configured
  // state instead of forcing the blocking onboarding overlay.
  return runtime.source === 'fallback' && readCachedConfigured(profile) === true && !requested
}

function notifyReady(provider: string) {
  notify({ kind: 'success', title: 'Atlas is ready', message: `${provider} connected.` })
}

// Human-friendly labels for tools auto-routed through the Nous Tool Gateway,
// mirroring hermes_cli/nous_subscription._GATEWAY_TOOL_LABELS so the GUI and
// CLI describe the same thing.
const GATEWAY_TOOL_LABELS: Record<string, string> = {
  browser: 'browser automation',
  image_gen: 'image generation',
  tts: 'text-to-speech',
  video_gen: 'video generation',
  web: 'web search & extract'
}

// When switching to Nous auto-routes unconfigured tools through the Tool
// Gateway, tell the user which ones — same information the CLI prints. Silent
// when nothing changed (subscriber already configured, has own keys, etc.).
function notifyGatewayTools(tools: string[] | undefined) {
  if (!tools || tools.length === 0) {
    return
  }

  const labels = tools.map(t => GATEWAY_TOOL_LABELS[t] ?? t)
  const list = labels.length === 1 ? labels[0] : `${labels.slice(0, -1).join(', ')} and ${labels[labels.length - 1]}`

  notify({
    durationMs: 8000,
    kind: 'info',
    message: `${list} now run through your Nous subscription — no separate API keys needed.`,
    title: 'Tool Gateway enabled'
  })
}

function requireModelAssignment(response: { ok: boolean }) {
  if (response.ok === true) {
    return
  }

  const detail = response as { confirm_required?: boolean; message?: string }

  if (detail.confirm_required) {
    throw new Error('This model requires an additional cost confirmation. Choose another model to continue setup.')
  }

  throw new Error(detail.message?.trim() || 'Atlas rejected the model assignment.')
}

function sameModelPair(first: { model: string; provider: string }, second: { model: string; provider: string }) {
  return (
    first.provider.trim().toLowerCase() === second.provider.trim().toLowerCase() &&
    first.model.trim() === second.model.trim()
  )
}

function eligibleMemoryModel(
  providers: ModelOptionProvider[],
  providerSlug: string,
  model: string,
  main: { model: string; provider: string }
) {
  const provider = providers.find(row => row.slug.trim().toLowerCase() === providerSlug.trim().toLowerCase())

  if (!provider || provider.authenticated !== true) {
    return false
  }

  const candidate = model.trim()
  const unavailable = new Set((provider.unavailable_models ?? []).map(String))
  const capability = provider.memory_capabilities?.[candidate]

  return (
    candidate.length > 0 &&
    (provider.models ?? []).map(String).includes(candidate) &&
    !unavailable.has(candidate) &&
    capability?.selectable === true &&
    capability.structured_json === true &&
    !sameModelPair({ provider: provider.slug, model: candidate }, main)
  )
}

function firstEligibleMemoryModel(
  providers: ModelOptionProvider[],
  main: { model: string; provider: string }
): null | { model: string; provider: string } {
  for (const provider of providers) {
    if (provider.authenticated !== true) {
      continue
    }

    const unavailable = new Set((provider.unavailable_models ?? []).map(String))

    for (const rawModel of provider.models ?? []) {
      const model = String(rawModel).trim()
      const candidate = { provider: String(provider.slug), model }
      const capability = provider.memory_capabilities?.[model]
      const selectable = capability?.selectable === true && capability.structured_json === true

      if (model && selectable && !unavailable.has(model) && !sameModelPair(candidate, main)) {
        return candidate
      }
    }
  }

  return null
}

function connectableMemoryProvider(
  providers: ModelOptionProvider[],
  main: { model: string; provider: string }
): ConnectableMemoryProvider | null {
  for (const provider of providers) {
    if (
      provider.authenticated !== false ||
      provider.auth_type !== 'api_key' ||
      !String(provider.key_env ?? '').trim()
    ) {
      continue
    }

    const unavailable = new Set((provider.unavailable_models ?? []).map(String))

    const hasEligibleModel = (provider.models ?? []).some(rawModel => {
      const model = String(rawModel).trim()
      const capability = provider.memory_capabilities?.[model]

      return (
        model.length > 0 &&
        !unavailable.has(model) &&
        capability?.selectable === true &&
        capability.structured_json === true &&
        !sameModelPair({ provider: provider.slug, model }, main)
      )
    })

    if (hasEligibleModel) {
      return {
        authType: 'api_key',
        keyEnv: String(provider.key_env).trim(),
        name: provider.name,
        slug: provider.slug
      }
    }
  }

  return null
}

function memoryLoadFailure(
  main: { model: string; provider: string },
  label: string,
  message: string,
  requestId: number,
  profile: string,
  providers: ModelOptionProvider[] = []
) {
  if (requestId !== memoryOptionsRequestId) {
    return
  }

  setFlow({
    status: 'memory_model_error',
    label,
    mainProvider: main.provider,
    mainModel: main.model,
    message,
    profile,
    connectableProvider: connectableMemoryProvider(providers, main),
    connecting: false,
    credentialMessage: null
  })
}

/** Load a bounded Cortex catalog snapshot and select a distinct default. */
export async function prepareOnboardingMemoryModel(
  mainProvider: string,
  mainModel: string,
  label: string,
  optionsSnapshot?: CortexMemoryModelOptions,
  requestedProfile?: string
) {
  const main = { provider: mainProvider.trim(), model: mainModel.trim() }
  const profile = normalizeProfileKey(requestedProfile ?? $activeGatewayProfile.get())
  const requestId = ++memoryOptionsRequestId

  setFlow({
    status: 'loading_memory_model',
    label,
    mainProvider: main.provider,
    mainModel: main.model,
    profile
  })

  try {
    const options = optionsSnapshot ?? (await getCortexMemoryModelOptions({ profile }))

    if (requestId !== memoryOptionsRequestId || normalizeProfileKey($activeGatewayProfile.get()) !== profile) {
      return false
    }

    const providers = Array.isArray(options.providers) ? options.providers : []

    const recommended = {
      provider: String(options.recommended?.provider ?? '').trim(),
      model: String(options.recommended?.model ?? '').trim()
    }

    const recommendedUsable = eligibleMemoryModel(providers, recommended.provider, recommended.model, main)
    const selected = recommendedUsable ? recommended : firstEligibleMemoryModel(providers, main)

    if (!selected) {
      memoryLoadFailure(
        main,
        label,
        'Atlas could not find a connected memory model that is different from your chat model. Connect another model provider, then retry.',
        requestId,
        profile,
        providers
      )

      return false
    }

    setFlow({
      status: 'confirming_memory_model',
      label,
      mainProvider: main.provider,
      mainModel: main.model,
      currentProvider: selected.provider,
      currentModel: selected.model,
      recommendedProvider: recommendedUsable ? recommended.provider : '',
      recommendedModel: recommendedUsable ? recommended.model : '',
      providers,
      profile,
      saving: false,
      message: null
    })

    return true
  } catch (error) {
    memoryLoadFailure(main, label, `Could not load memory models: ${errMessage(error)}`, requestId, profile)

    return false
  }
}

async function prepareMemoryForConfiguredMain(optionsSnapshot?: CortexMemoryModelOptions, requestedProfile?: string) {
  const profile = normalizeProfileKey(requestedProfile ?? $activeGatewayProfile.get())
  const requestId = ++memoryOptionsRequestId
  const emptyMain = { provider: '', model: '' }

  setFlow({ status: 'loading_memory_model', label: 'Atlas', mainProvider: '', mainModel: '', profile })

  try {
    const options = await getGlobalModelOptions({ includeUnconfigured: true, explicitOnly: false, profile })

    if (requestId !== memoryOptionsRequestId || normalizeProfileKey($activeGatewayProfile.get()) !== profile) {
      return false
    }

    const provider = String(options.provider ?? '').trim()
    const model = String(options.model ?? '').trim()

    if (!provider || !model) {
      memoryLoadFailure(
        emptyMain,
        'Atlas',
        'Atlas is running, but its current chat model could not be identified. Retry after confirming the chat model in Settings.',
        requestId,
        profile
      )

      return false
    }

    const label =
      options.providers?.find(row => row.slug.trim().toLowerCase() === provider.toLowerCase())?.name ?? provider

    return prepareOnboardingMemoryModel(provider, model, label, optionsSnapshot, profile)
  } catch (error) {
    memoryLoadFailure(
      emptyMain,
      'Atlas',
      `Could not identify the current chat model: ${errMessage(error)}`,
      requestId,
      profile
    )

    return false
  }
}

export async function retryOnboardingMemoryModel() {
  const { flow } = $desktopOnboarding.get()

  if (flow.status !== 'memory_model_error') {
    return false
  }

  if (!flow.mainProvider || !flow.mainModel) {
    return prepareMemoryForConfiguredMain(undefined, flow.profile)
  }

  return prepareOnboardingMemoryModel(flow.mainProvider, flow.mainModel, flow.label, undefined, flow.profile)
}

export async function connectOnboardingMemoryProvider(apiKey: string) {
  const state = $desktopOnboarding.get()
  const { flow } = state

  if (flow.status !== 'memory_model_error' || !flow.connectableProvider || flow.connecting) {
    return false
  }

  const value = apiKey.trim()

  if (!value) {
    setFlow({ ...flow, credentialMessage: 'Enter an API key to continue.' })

    return false
  }

  const { connectableProvider, profile } = flow
  const operationId = ++memoryOptionsRequestId
  setFlow({ ...flow, connecting: true, credentialMessage: null })

  const stillConnecting = () => {
    const current = $desktopOnboarding.get().flow

    return (
      operationId === memoryOptionsRequestId &&
      normalizeProfileKey($activeGatewayProfile.get()) === profile &&
      current.status === 'memory_model_error' &&
      current.profile === profile &&
      current.connecting &&
      current.connectableProvider?.slug === connectableProvider.slug
    )
  }

  try {
    let verificationWarning = ''

    try {
      const probe = await validateProviderCredential(connectableProvider.keyEnv, value, undefined, { profile })

      if (!stillConnecting()) {
        return false
      }

      if (probe.reachable && !probe.ok) {
        const current = $desktopOnboarding.get().flow

        if (current.status === 'memory_model_error') {
          setFlow({
            ...current,
            connecting: false,
            credentialMessage: probe.message || `${connectableProvider.name} rejected that API key.`
          })
        }

        return false
      }

      if (!probe.reachable) {
        verificationWarning =
          probe.message || `${connectableProvider.name} could not be reached for live key verification.`
      }
    } catch (error) {
      if (!stillConnecting()) {
        return false
      }

      verificationWarning = `Live key verification was unavailable: ${errMessage(error)}`
    }

    const saved = await setEnvVar(connectableProvider.keyEnv, value, { profile })

    if (!stillConnecting()) {
      return false
    }

    if (saved.ok !== true) {
      throw new Error(`Atlas did not confirm ${connectableProvider.keyEnv} was saved.`)
    }

    const options = await getCortexMemoryModelOptions({ refresh: true, profile })

    if (!stillConnecting()) {
      return false
    }

    const main = { provider: flow.mainProvider, model: flow.mainModel }

    const exactProvider = (options.providers ?? []).find(
      provider => provider.slug.trim().toLowerCase() === connectableProvider.slug.trim().toLowerCase()
    )

    const exactSelection = exactProvider ? firstEligibleMemoryModel([exactProvider], main) : null

    if (!exactProvider || exactProvider.authenticated !== true || !exactSelection) {
      const current = $desktopOnboarding.get().flow

      if (current.status === 'memory_model_error') {
        setFlow({
          ...current,
          connecting: false,
          credentialMessage: `${connectableProvider.name} was saved, but no verified structured memory model became available. Check the key and retry.`
        })
      }

      return false
    }

    if (verificationWarning) {
      notify({
        kind: 'info',
        title: 'Memory provider saved without live verification',
        message: verificationWarning
      })
    }

    const orderedProviders = [
      exactProvider,
      ...(options.providers ?? []).filter(provider => provider !== exactProvider)
    ]

    return prepareOnboardingMemoryModel(
      flow.mainProvider,
      flow.mainModel,
      flow.label,
      {
        ...options,
        providers: orderedProviders,
        recommended: exactSelection
      },
      profile
    )
  } catch (error) {
    const current = $desktopOnboarding.get().flow

    if (current.status === 'memory_model_error' && current.profile === profile) {
      setFlow({
        ...current,
        connecting: false,
        credentialMessage: `Could not connect ${connectableProvider.name}: ${errMessage(error)}`
      })
    }

    return false
  }
}

// After credentials are persisted, ask the backend which provider+models
// are now authenticated. Pick the first curated model for the matching
// provider as a sensible default, persist it via /api/model/set, and
// transition to the model-confirmation step. A concrete main pair is now a
// prerequisite for the required Cortex route, so failure to resolve one must
// remain visible instead of silently completing onboarding.
async function fetchProviderDefaultModel(
  preferredSlugs: string[]
): Promise<null | { providerSlug: string; defaultModel: string }> {
  let options

  try {
    options = await getGlobalModelOptions({ includeUnconfigured: true, explicitOnly: false })
  } catch {
    return null
  }

  const providers = options?.providers ?? []

  if (providers.length === 0) {
    return null
  }

  // Try each preferred slug (lowercased), fall back to the first provider
  // returned (model.options orders by recency / authenticated state, so
  // the just-authenticated provider is usually first anyway).
  const lower = preferredSlugs.map(s => s.toLowerCase())

  const matched =
    providers.find((p: ModelOptionProvider) => lower.includes(String(p.slug).toLowerCase())) ?? providers[0]

  const models = matched.models ?? []

  if (models.length === 0) {
    return null
  }

  // Prefer the backend's recommended default — it mirrors the curation
  // `hermes model` does (for Nous it honors the user's free/paid tier, so a
  // free user gets a free model rather than a paid default like opus). Fall
  // back to the first curated model if the endpoint can't resolve one.
  let defaultModel = String(models[0])

  try {
    const recommended = await getRecommendedDefaultModel(String(matched.slug))

    if (recommended.model && models.map(String).includes(recommended.model)) {
      defaultModel = recommended.model
    } else if (recommended.model) {
      // Recommended model isn't in the curated options list (e.g. a Portal
      // free-recommendation the picker list didn't include); trust it anyway.
      defaultModel = recommended.model
    }
  } catch {
    // Endpoint unavailable — keep models[0]. Non-fatal: the confirm card still
    // shows and the user can change it.
  }

  return {
    providerSlug: String(matched.slug),
    defaultModel
  }
}

// After OAuth/API-key success: reload the backend env, verify runtime,
// then either show the model-confirm step or fall straight through to
// completion if we can't determine a default.
//
// onFail receives the runtime-readiness `reason` from checkRuntime so
// the caller can fold it into a user-facing error — same contract as
// reloadAndConnect used to have (which this replaces).
async function completeWithModelConfirm(
  ctx: OnboardingContext,
  providerLabel: string,
  preferredSlugs: string[],
  onFail: (reason: null | string) => void,
  // When true, a failing runtime check no longer blocks progression — the
  // user is allowed through onboarding regardless. Used by the API-key path,
  // where we intentionally don't validate the key (it blocked too many users).
  ignoreRuntimeGate = false
) {
  await ctx.requestGateway('reload.env').catch(() => undefined)

  const defaults = await fetchProviderDefaultModel(preferredSlugs)
  let persistenceError: null | string = null

  if (defaults) {
    // Persist the chosen provider/model before the runtime gate so a stale
    // config provider (e.g. anthropic from a prior failed setup) cannot make
    // setup.runtime_check validate the wrong backend after a fresh OAuth login.
    try {
      const res = await setModelAssignment({
        scope: 'main',
        provider: defaults.providerSlug,
        model: defaults.defaultModel
      })

      requireModelAssignment(res)
      notifyGatewayTools(res.gateway_tools)
    } catch (error) {
      // The confirm action retries this write before Cortex setup, but surface
      // the first failure now so the user understands why setup is not done.
      persistenceError = `Could not save the chat model: ${errMessage(error)}`
    }
  }

  const runtime = await checkRuntime(ctx, preferredSlugs[0])

  if (!runtime.ready && !ignoreRuntimeGate) {
    onFail(runtime.reason)

    return
  }

  if (!defaults) {
    onFail('Atlas could not load a usable chat model. Retry provider setup before continuing.')

    return
  }

  setFlow({
    status: 'confirming_model',
    providerSlug: defaults.providerSlug,
    currentModel: defaults.defaultModel,
    label: providerLabel,
    saving: false,
    message: persistenceError
  })
}

function providerResolutionFailure(reason: null | string) {
  const detail = reason?.trim()

  return detail
    ? `Connected, but Atlas still cannot resolve a usable provider. ${detail}`
    : 'Connected, but Atlas still cannot resolve a usable provider.'
}

async function refreshProviders(options?: { profile?: string; shouldCommit?: () => boolean }) {
  const profile = normalizeProfileKey(options?.profile ?? $activeGatewayProfile.get())

  const shouldCommit = () =>
    normalizeProfileKey($activeGatewayProfile.get()) === profile && (options?.shouldCommit?.() ?? true)

  let request = providersRefreshPromises.get(profile)

  if (!request) {
    request = listOAuthProviders({ profile }).then(result => result.providers)
    providersRefreshPromises.set(profile, request)
  }

  try {
    const providers = await request

    if (shouldCommit()) {
      patch({ mode: providers.length > 0 ? 'oauth' : 'apikey', providers })
    }
  } catch {
    if (shouldCommit()) {
      patch({ mode: 'apikey', providers: [] })
    }
  } finally {
    if (providersRefreshPromises.get(profile) === request) {
      providersRefreshPromises.delete(profile)
    }
  }
}

export function requestDesktopOnboarding(reason = DEFAULT_ONBOARDING_REASON) {
  patch({ reason: reason.trim() || DEFAULT_ONBOARDING_REASON, requested: true })
}

// Open the onboarding provider selector on demand from an already-configured
// app — e.g. the model picker's "Add provider" button. Reuses the entire
// onboarding flow (OAuth rows, API-key form, model-confirm) instead of
// duplicating provider UI. Sets manual=true so the overlay shows the picker
// even though configured===true, and refreshes the provider list.
export function startManualOnboarding(reason: null | string = DEFAULT_MANUAL_ONBOARDING_REASON) {
  onboardingRefreshGeneration += 1
  memoryOptionsRequestId += 1
  patch({
    manual: true,
    requested: true,
    localEndpoint: false,
    // `null` opts out of the prompt banner entirely (e.g. when the user already
    // picked a specific provider and we auto-start its sign-in).
    reason: reason ? reason.trim() || DEFAULT_ONBOARDING_REASON : null,
    flow: { status: 'idle' }
  })
  void refreshProviders()
}

// Open the onboarding overlay directly on the local / custom endpoint form
// (URL + optional API key), bypassing the OAuth picker. Used by Settings →
// Model's "Set up custom endpoint" so it lands on a form that can actually
// configure the endpoint instead of dead-ending on the OAuth provider list
// (`custom` is not an OAuth provider, so the generic manual flow would just
// re-show the picker — the original "booted back to the first screen" loop).
export function startManualLocalEndpoint(reason: null | string = null) {
  pendingProviderOAuthId = null
  onboardingRefreshGeneration += 1
  memoryOptionsRequestId += 1
  patch({
    manual: true,
    requested: true,
    localEndpoint: true,
    mode: 'apikey',
    reason: reason ? reason.trim() || DEFAULT_ONBOARDING_REASON : null,
    flow: { status: 'idle' }
  })
}

// One-shot hand-off used when the dedicated Providers settings page launches a
// specific provider's sign-in: we open the manual onboarding overlay AND
// remember which provider to start, so the overlay drives that exact OAuth
// flow instead of re-showing the picker the user just clicked through.
// Module-level (not store state) because it's consumed immediately on the next
// overlay render and never needs to persist or re-render anything itself.
let pendingProviderOAuthId: null | string = null

export function startManualProviderOAuth(providerId: string, reason: null | string = null) {
  pendingProviderOAuthId = providerId
  startManualOnboarding(reason)
}

// Read the pending provider id without clearing it. The overlay only clears it
// (via clearPendingProviderOAuth) once it has actually launched that provider,
// so a transient empty/failed provider fetch doesn't drop the hand-off and the
// deep-link can still auto-start after the list loads.
export function peekPendingProviderOAuth(): null | string {
  return pendingProviderOAuthId
}

export function clearPendingProviderOAuth() {
  pendingProviderOAuthId = null
}

// Dismiss a manually-opened provider selector without touching the existing
// (working) configuration. Only valid in the manual path — the unconfigured
// first-run flow has no close affordance because the app can't run yet.
export function closeManualOnboarding() {
  pendingProviderOAuthId = null

  patch({ manual: false, requested: false, localEndpoint: false, flow: { status: 'idle' } })
}

export function completeDesktopOnboarding(requestedProfile?: string) {
  const currentFlow = $desktopOnboarding.get().flow

  const profile = normalizeProfileKey(
    requestedProfile ?? ('profile' in currentFlow ? currentFlow.profile : $activeGatewayProfile.get())
  )

  // A delayed UI transition must not complete profile A after the customer
  // has already switched to profile B. Callers that pin a profile are asking
  // for compare-and-complete semantics, not merely a scoped cache write.
  if (requestedProfile !== undefined && normalizeProfileKey($activeGatewayProfile.get()) !== profile) {
    return false
  }

  clearPoll()
  onboardingRefreshGeneration += 1
  memoryOptionsRequestId += 1
  writeCachedConfigured(true, profile)
  // A real provider is now connected, so any earlier "choose later" skip is
  // moot — clear it so the flag never lingers in a configured install.
  writeCachedSkipped(false)
  $desktopOnboarding.set({
    configured: true,
    flow: { status: 'idle' },
    mode: 'oauth',
    providers: null,
    reason: null,
    requested: false,
    firstRunSkipped: false,
    manual: false,
    localEndpoint: false
  })

  return true
}

// "I'll choose a provider later" on the first-run picker. Persists the skip so
// the blocking overlay never re-nags on future launches, and dismisses it now
// so the user lands in the app. Chat won't work until a provider is connected
// (from Settings → Providers or the model picker's "Add provider") — this only
// stops forcing the choice up front. Distinct from completeDesktopOnboarding,
// which marks the app actually configured.
export function dismissFirstRunOnboarding() {
  clearPoll()
  onboardingRefreshGeneration += 1
  memoryOptionsRequestId += 1
  writeCachedSkipped(true)
  patch({ firstRunSkipped: true, requested: false, manual: false, localEndpoint: false, flow: { status: 'idle' } })
}

export function setOnboardingMode(mode: OnboardingMode) {
  patch({ mode })
}

export async function refreshOnboarding(ctx: OnboardingContext) {
  const generation = ++onboardingRefreshGeneration
  const profile = normalizeProfileKey($activeGatewayProfile.get())
  memoryOptionsRequestId += 1

  const isCurrent = () =>
    generation === onboardingRefreshGeneration && normalizeProfileKey($activeGatewayProfile.get()) === profile

  // Manual mode (user opened the selector from a working app): never
  // auto-dismiss on runtime-ready — the whole point is to let them add /
  // switch a provider while already configured. Just ensure the provider
  // list is loaded and show the picker.
  if ($desktopOnboarding.get().manual) {
    await refreshProviders({ profile, shouldCommit: isCurrent })

    return false
  }

  const runtime = await checkRuntime(ctx)

  if (!isCurrent()) {
    return false
  }

  if (runtime.ready) {
    // The profile-scoped browser cache is intentionally only a paint
    // optimization. Always ask the captured backend profile for route truth
    // so an old bit cannot override a newly valid or invalid backend route.
    let cortexOptions: CortexMemoryModelOptions

    try {
      cortexOptions = await getCortexMemoryModelOptions({ profile })
    } catch {
      if (!isCurrent()) {
        return false
      }

      // A previously verified profile keeps running through a transient status
      // outage. A not-yet-configured profile remains in explicit setup/error.
      if (readCachedConfigured(profile) === true) {
        notify({
          id: 'cortex-route-validation-unavailable',
          kind: 'error',
          title: 'Memory route not verified',
          message:
            'Atlas could not verify the dedicated memory route. Your existing setup will continue and Atlas will check again on a later launch.'
        })
        completeDesktopOnboarding(profile)
        ctx.onCompleted?.()

        return true
      }

      writeCachedConfigured(false, profile)
      writeCachedSkipped(false)
      patch({ configured: false, firstRunSkipped: false })
      await prepareMemoryForConfiguredMain(undefined, profile)

      return false
    }

    if (!isCurrent()) {
      return false
    }

    if (cortexOptions.current?.valid === true) {
      completeDesktopOnboarding(profile)
      ctx.onCompleted?.()

      return true
    }

    writeCachedConfigured(false, profile)
    writeCachedSkipped(false)
    patch({ configured: false, firstRunSkipped: false })
    await prepareMemoryForConfiguredMain(cortexOptions, profile)

    return false
  }

  const state = $desktopOnboarding.get()

  if (shouldPreserveConfiguredOnFallback(runtime, profile, state.requested)) {
    // Gateway probes timed out but the user was already configured — don't
    // downgrade to the blocking onboarding overlay. Surface a non-blocking
    // notification with a stable id so repeated calls during an outage dedup
    // instead of stacking toasts.
    notify({
      id: 'runtime-not-ready',
      kind: 'error',
      title: 'Runtime not ready',
      message:
        'Atlas Desktop could not verify the running backend on startup. Some features may be unavailable until the gateway is reachable.'
    })

    return false
  }

  const reason = runtime.reason || state.reason || DEFAULT_ONBOARDING_REASON

  writeCachedConfigured(false, profile)
  patch({ configured: false, reason })

  if (state.providers !== null && !state.requested) {
    return false
  }

  await refreshProviders({ profile, shouldCommit: isCurrent })

  return false
}

// Open a sign-in URL via the desktop bridge, falling back to window.open
// when the bridge isn't present (e.g. the web dashboard / dev preview) so
// the flow never silently stalls in a waiting state. Mirrors the pattern in
// apps/desktop/src/app/artifacts/index.tsx.
async function openSignInUrl(url: string) {
  if (window.hermesDesktop?.openExternal) {
    try {
      await window.hermesDesktop.openExternal(url)

      return
    } catch {
      // Bridge present but failed (no OS handler, user denied, etc.). Fall
      // through to window.open so the sign-in URL still opens and the flow
      // doesn't strand a pending OAuth session in a waiting state.
    }
  }

  window.open(url, '_blank', 'noopener,noreferrer')
}

export async function startProviderOAuth(provider: OAuthProvider, ctx: OnboardingContext) {
  clearPoll()

  if (provider.flow === 'external') {
    setFlow({ status: 'external_pending', provider, copied: false })

    return
  }

  setFlow({ status: 'starting', provider })

  try {
    const start = await startOAuthLogin(provider.id)
    const browserUrl = start.flow === 'device_code' ? start.verification_url : start.auth_url
    await openSignInUrl(browserUrl)

    if (start.flow === 'pkce') {
      setFlow({ status: 'awaiting_user', provider, start, code: '' })

      return
    }

    setFlow({ status: 'polling', provider, start, copied: false })
    pollTimer = window.setInterval(() => void pollSession(provider, start, ctx), POLL_MS)
  } catch (error) {
    setFlow({ status: 'error', provider, message: `Could not start sign-in: ${errMessage(error)}` })
  }
}

// Poll a session-backed device-code flow until it resolves.
async function pollSession(provider: OAuthProvider, start: DeviceStart, ctx: OnboardingContext) {
  try {
    const { error_message, status } = await pollOAuthSession(provider.id, start.session_id)

    if (status === 'approved') {
      clearPoll()
      setFlow({ status: 'success', provider })
      await completeWithModelConfirm(ctx, provider.name, [provider.id], reason =>
        setFlow({
          status: 'error',
          provider,
          message: providerResolutionFailure(reason)
        })
      )
    } else if (status !== 'pending') {
      clearPoll()
      setFlow({ status: 'error', provider, start, message: error_message || `Sign-in ${status}.` })
    }
  } catch (error) {
    clearPoll()
    setFlow({ status: 'error', provider, start, message: `Polling failed: ${errMessage(error)}` })
  }
}

export function setOnboardingCode(code: string) {
  const { flow } = $desktopOnboarding.get()

  if (flow.status === 'awaiting_user') {
    setFlow({ ...flow, code })
  }
}

export async function submitOnboardingCode(ctx: OnboardingContext) {
  const { flow } = $desktopOnboarding.get()

  if (flow.status !== 'awaiting_user' || !flow.code.trim()) {
    return
  }

  const { provider, start, code } = flow
  setFlow({ status: 'submitting', provider, start })

  try {
    const resp = await submitOAuthCode(provider.id, start.session_id, code.trim())

    if (resp.ok && resp.status === 'approved') {
      setFlow({ status: 'success', provider })
      await completeWithModelConfirm(ctx, provider.name, [provider.id], reason =>
        setFlow({
          status: 'error',
          provider,
          message: providerResolutionFailure(reason)
        })
      )
    } else {
      setFlow({ status: 'error', provider, start, message: resp.message || 'Token exchange failed.' })
    }
  } catch (error) {
    setFlow({ status: 'error', provider, start, message: errMessage(error) })
  }
}

export function cancelOnboardingFlow() {
  clearPoll()
  onboardingRefreshGeneration += 1
  memoryOptionsRequestId += 1
  const sessionId = sessionIdFor($desktopOnboarding.get().flow)

  if (sessionId) {
    cancelOAuthSession(sessionId).catch(() => undefined)
  }

  setFlow({ status: 'idle' })
}

async function copyAndFlash(text: string, predicate: (flow: OnboardingFlow) => boolean) {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    return
  }

  const { flow } = $desktopOnboarding.get()

  if (!predicate(flow) || !('copied' in flow)) {
    return
  }

  setFlow({ ...flow, copied: true })
  window.setTimeout(() => {
    const current = $desktopOnboarding.get().flow

    if (predicate(current) && 'copied' in current) {
      setFlow({ ...current, copied: false })
    }
  }, COPY_FLASH_MS)
}

export async function copyDeviceCode() {
  const { flow } = $desktopOnboarding.get()

  if (flow.status !== 'polling') {
    return
  }

  const sid = flow.start.session_id
  await copyAndFlash(flow.start.user_code, f => f.status === 'polling' && f.start.session_id === sid)
}

export async function copyExternalCommand() {
  const { flow } = $desktopOnboarding.get()

  if (flow.status !== 'external_pending') {
    return
  }

  const id = flow.provider.id
  await copyAndFlash(flow.provider.cli_command, f => f.status === 'external_pending' && f.provider.id === id)
}

export async function recheckExternalSignin(ctx: OnboardingContext) {
  const { flow } = $desktopOnboarding.get()

  if (flow.status !== 'external_pending') {
    return
  }

  const { provider } = flow
  await completeWithModelConfirm(ctx, provider.name, [provider.id], reason =>
    setFlow({
      status: 'error',
      provider,
      message:
        reason?.trim() ||
        `Atlas still cannot reach ${provider.name}. Run \`${provider.cli_command}\` in a terminal first.`
    })
  )
}

export async function saveOnboardingApiKey(
  envKey: string,
  value: string,
  label: string,
  ctx: OnboardingContext,
  // Optional endpoint key — only meaningful for the "Local / custom endpoint"
  // option, whose primary `value` is the base URL. Ignored for plain API-key
  // providers (their key IS `value`).
  endpointApiKey?: string
) {
  const trimmed = value.trim()

  if (!trimmed) {
    return { ok: false, message: 'Enter a value first.' }
  }

  // The "Local / custom endpoint" option carries a base URL (in `value`) plus
  // an optional API key. It must be wired into config (provider=custom +
  // base_url + model + api_key), not dropped into .env — runtime resolution
  // ignores OPENAI_BASE_URL.
  if (envKey === 'OPENAI_BASE_URL') {
    return saveOnboardingLocalEndpoint(trimmed, endpointApiKey?.trim() ?? '', ctx)
  }

  // No key validation here on purpose: we previously live-probed the key and
  // hard-blocked on a runtime check after saving, which rejected too many
  // legitimate users (corporate proxies, regional blocks, flaky/rate-limited
  // provider probes, self-hosted endpoints). We now save the value as-is and
  // let the user proceed; an actually-bad key surfaces later at chat time.
  try {
    await setEnvVar(envKey, trimmed)
    // For API-key flows we don't have a definitive provider id (the
    // user picked which API key they're entering, but the corresponding
    // backend slug — e.g. OPENROUTER_API_KEY → "openrouter" — is the
    // env-key prefix stripped). Pass a couple of likely candidates;
    // fetchProviderDefaultModel falls back to the first authenticated
    // provider returned by /api/model/options if none match.
    const slugCandidates = [envKey.replace(/_API_KEY$/, '').toLowerCase(), label.toLowerCase()]
    // ignoreRuntimeGate=true: never block onboarding on the runtime check.
    await completeWithModelConfirm(
      ctx,
      label,
      slugCandidates,
      reason => setFlow({ status: 'error', message: providerResolutionFailure(reason) }),
      true
    )

    return { ok: true }
  } catch (error) {
    notifyError(error, `Could not save ${label}`)

    return { ok: false, message: errMessage(error) }
  }
}

// Configure a local / self-hosted OpenAI-compatible endpoint (vLLM, llama.cpp,
// Ollama, …). Unlike API-key providers, a local endpoint is defined by its URL
// and usually needs NO key. The runtime resolver reads model.base_url from
// config (it ignores the OPENAI_BASE_URL env var), so we persist
// provider=custom + base_url + model via /api/model/set rather than dropping an
// env var that resolution never consults.
//
// The model is auto-discovered from the endpoint's /v1/models (surfaced by the
// validate probe). The optional API key is forwarded to the probe (so hosted
// endpoints that gate /v1/models behind auth still enumerate models) and
// persisted to model.api_key so the runtime can authenticate.
//
// We deliberately don't route through completeWithModelConfirm: that path
// re-assigns the model from /api/model/options WITHOUT a base_url, which would
// wipe the base_url we just wrote. We have a concrete model already, so after
// runtime verification a first run advances directly to Cortex model setup.
export async function saveOnboardingLocalEndpoint(baseUrl: string, apiKey: string, ctx: OnboardingContext) {
  const url = baseUrl.trim()
  const key = apiKey.trim()

  if (!url) {
    return { ok: false, message: 'Enter the endpoint URL first.' }
  }

  // Probe connectivity + discover the served models. Any HTTP response proves
  // the endpoint is up; an unreachable probe hard-blocks because we can't
  // resolve a model to route to.
  let model = ''

  try {
    const probe = await validateProviderCredential('OPENAI_BASE_URL', url, key)

    if (!probe.ok && probe.reachable) {
      return { ok: false, message: probe.message || 'Could not reach that endpoint.' }
    }

    if (!probe.reachable) {
      return { ok: false, message: probe.message || `Could not reach ${url}.` }
    }

    model = (probe.models?.[0] ?? '').trim()
  } catch {
    return { ok: false, message: `Could not reach ${url}.` }
  }

  if (!model) {
    return {
      ok: false,
      message: `Connected to ${url}, but it advertised no models at /v1/models. Start a model on that endpoint and try again.`
    }
  }

  try {
    const assignment = await setModelAssignment({
      scope: 'main',
      provider: 'custom',
      model,
      base_url: url,
      api_key: key
    })

    requireModelAssignment(assignment)
    await ctx.requestGateway('reload.env').catch(() => undefined)

    const runtime = await checkRuntime(ctx)

    if (!runtime.ready) {
      const detail = (runtime.reason ?? '').trim()

      return { ok: false, message: detail || `Saved, but Atlas still cannot reach ${url}.` }
    }

    const currentState = $desktopOnboarding.get()

    // Manual means “opened from Settings,” not necessarily “already set up.”
    // A customer who deferred first run can enter through that same surface;
    // they still need the required Cortex route before Atlas is marked ready.
    if (currentState.manual && currentState.configured === true) {
      notifyReady('Local / custom endpoint')
      completeDesktopOnboarding()
      ctx.onCompleted?.()

      return { ok: true }
    }

    await prepareOnboardingMemoryModel('custom', model, 'Local / custom endpoint')

    return { ok: true }
  } catch (error) {
    notifyError(error, 'Could not save local endpoint')

    return { ok: false, message: errMessage(error) }
  }
}

// User picked a different main model from the dropdown on the confirm card.
// Persist both fields: the global picker can cross provider boundaries.
export async function setOnboardingModel(provider: string, model: string) {
  const { flow } = $desktopOnboarding.get()

  if (flow.status !== 'confirming_model') {
    return false
  }

  // Optimistic update so the dropdown feels instant; revert on failure.
  const previous = { provider: flow.providerSlug, model: flow.currentModel }
  setFlow({ ...flow, providerSlug: provider, currentModel: model, saving: true, message: null })

  try {
    const response = await setModelAssignment({
      scope: 'main',
      provider,
      model
    })

    requireModelAssignment(response)
    const current = $desktopOnboarding.get().flow

    if (current.status === 'confirming_model') {
      setFlow({ ...current, providerSlug: provider, currentModel: model, saving: false, message: null })
    }

    return true
  } catch (error) {
    notifyError(error, 'Could not change model')
    const current = $desktopOnboarding.get().flow

    if (current.status === 'confirming_model') {
      setFlow({
        ...current,
        providerSlug: previous.provider,
        currentModel: previous.model,
        saving: false,
        message: `Could not save the chat model: ${errMessage(error)}`
      })
    }

    return false
  }
}

export function setOnboardingMemoryModel(provider: string, model: string) {
  const { flow } = $desktopOnboarding.get()

  if (flow.status !== 'confirming_memory_model' || flow.saving) {
    return false
  }

  const main = { provider: flow.mainProvider, model: flow.mainModel }

  if (sameModelPair({ provider, model }, main)) {
    setFlow({ ...flow, message: 'Choose a memory model that is different from your chat model.' })

    return false
  }

  if (!eligibleMemoryModel(flow.providers, provider, model, main)) {
    setFlow({ ...flow, message: 'That memory model is not available with a connected provider.' })

    return false
  }

  setFlow({ ...flow, currentProvider: provider, currentModel: model, message: null })

  return true
}

/** Persist both Cortex slots through the backend's single atomic write. */
export async function confirmOnboardingMemoryModel() {
  const { flow } = $desktopOnboarding.get()

  if (flow.status !== 'confirming_memory_model' || flow.saving) {
    return false
  }

  const selected = { provider: flow.currentProvider, model: flow.currentModel }
  const main = { provider: flow.mainProvider, model: flow.mainModel }

  if (!eligibleMemoryModel(flow.providers, selected.provider, selected.model, main)) {
    setFlow({ ...flow, message: 'Choose an available memory model that is different from your chat model.' })

    return false
  }

  setFlow({ ...flow, saving: true, message: null })

  try {
    const response = await setCortexMemoryModelAssignment(selected, { profile: flow.profile })
    const currentFlow = $desktopOnboarding.get().flow

    if (
      normalizeProfileKey($activeGatewayProfile.get()) !== flow.profile ||
      currentFlow.status !== 'confirming_memory_model' ||
      currentFlow.profile !== flow.profile ||
      !currentFlow.saving ||
      !sameModelPair({ provider: currentFlow.currentProvider, model: currentFlow.currentModel }, selected)
    ) {
      return false
    }

    const triage = response.triage
    const reasoning = response.reasoning

    if (
      response.ok !== true ||
      !triage ||
      !reasoning ||
      !sameModelPair(triage, selected) ||
      !sameModelPair(reasoning, selected)
    ) {
      throw new Error('Atlas did not confirm both Cortex memory routes. No onboarding state was changed.')
    }

    notifyGatewayTools(response.gateway_tools)

    return true
  } catch (error) {
    const current = $desktopOnboarding.get().flow

    if (
      current.status === 'confirming_memory_model' &&
      current.profile === flow.profile &&
      normalizeProfileKey($activeGatewayProfile.get()) === flow.profile
    ) {
      setFlow({ ...current, saving: false, message: `Could not save the memory model: ${errMessage(error)}` })
    }

    return false
  }
}

// Re-persist the selected main pair before advancing. This turns the prior
// best-effort default write into a hard first-run invariant: Cortex setup never
// begins against a chat model that failed to reach disk.
export async function confirmOnboardingModel(_ctx: OnboardingContext): Promise<'complete' | 'memory' | false> {
  const state = $desktopOnboarding.get()
  const { flow } = state

  if (flow.status !== 'confirming_model' || flow.saving) {
    return false
  }

  setFlow({ ...flow, saving: true, message: null })

  try {
    const response = await setModelAssignment({
      scope: 'main',
      provider: flow.providerSlug,
      model: flow.currentModel
    })

    requireModelAssignment(response)
    notifyGatewayTools(response.gateway_tools)
  } catch (error) {
    const current = $desktopOnboarding.get().flow

    if (current.status === 'confirming_model') {
      setFlow({ ...current, saving: false, message: `Could not save the chat model: ${errMessage(error)}` })
    }

    return false
  }

  // Existing configured installs may add/switch providers without replaying
  // first run. A deferred first-run customer can also arrive through the
  // manual Settings surface, but must not bypass Cortex setup.
  if (state.manual && state.configured === true) {
    return 'complete'
  }

  await prepareOnboardingMemoryModel(flow.providerSlug, flow.currentModel, flow.label)

  return 'memory'
}
